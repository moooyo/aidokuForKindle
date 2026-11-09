//! Coalesced stock idle notifications outside the rendering and input threads.

use std::{
    process::{Command, Stdio},
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
        mpsc::{self, Receiver, SyncSender},
    },
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};

use anyhow::{Context, Result};

#[derive(Default)]
pub(super) struct Shared {
    paused: AtomicBool,
    pause_acknowledged: AtomicBool,
    stop: AtomicBool,
    user_pending: AtomicBool,
    download_active: AtomicBool,
    keep_download_awake: AtomicBool,
}

pub(super) struct ActivityWorker {
    shared: Arc<Shared>,
    sender: SyncSender<()>,
    done: Mutex<Receiver<()>>,
    thread: Mutex<Option<JoinHandle<()>>>,
}

impl ActivityWorker {
    pub(super) fn start() -> Result<Self> {
        Self::start_with(Duration::from_secs(20), touch_idle)
    }

    fn start_with(interval: Duration, touch: impl Fn(&Shared) + Send + 'static) -> Result<Self> {
        let shared = Arc::new(Shared::default());
        let (sender, receiver) = mpsc::sync_channel(1);
        let (done_sender, done) = mpsc::channel();
        let worker_shared = shared.clone();
        let thread = thread::Builder::new()
            .name("aidoku-idle-activity".into())
            .spawn(move || {
                run_loop(&worker_shared, receiver, interval, touch);
                let _ = done_sender.send(());
            })
            .context("Start coalesced idle notification worker")?;
        Ok(Self {
            shared,
            sender,
            done: Mutex::new(done),
            thread: Mutex::new(Some(thread)),
        })
    }

    fn notify(&self) {
        let _ = self.sender.try_send(());
    }

    pub(super) fn note_user(&self) {
        if self.shared.paused.load(Ordering::Acquire) {
            return;
        }
        self.shared.user_pending.store(true, Ordering::Release);
        self.notify();
    }

    pub(super) fn set_download_active(&self, active: bool) {
        let active = active && !self.shared.paused.load(Ordering::Acquire);
        if self.shared.download_active.swap(active, Ordering::AcqRel) != active {
            self.notify();
        }
    }

    pub(super) fn set_keep_download_awake(&self, keep: bool) {
        if self.shared.keep_download_awake.swap(keep, Ordering::AcqRel) != keep {
            self.notify();
        }
    }

    pub(super) fn pause(&self) {
        self.shared
            .pause_acknowledged
            .store(false, Ordering::Release);
        self.shared.paused.store(true, Ordering::Release);
        self.shared.user_pending.store(false, Ordering::Release);
        self.shared.download_active.store(false, Ordering::Release);
        self.notify();
    }

    pub(super) fn resume(&self) {
        self.shared.paused.store(false, Ordering::Release);
        self.shared
            .pause_acknowledged
            .store(false, Ordering::Release);
        self.notify();
    }

    pub(super) fn paused_ready(&self) -> bool {
        self.shared.paused.load(Ordering::Acquire)
            && self.shared.pause_acknowledged.load(Ordering::Acquire)
    }
}

impl Drop for ActivityWorker {
    fn drop(&mut self) {
        self.shared.stop.store(true, Ordering::Release);
        self.shared.paused.store(true, Ordering::Release);
        self.notify();
        let completed = self
            .done
            .get_mut()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .recv_timeout(Duration::from_millis(150))
            .is_ok();
        if let Some(thread) = self
            .thread
            .get_mut()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .take()
        {
            if completed {
                let _ = thread.join();
            }
        }
    }
}

fn run_loop(shared: &Shared, receiver: Receiver<()>, interval: Duration, touch: impl Fn(&Shared)) {
    let mut last_touch = Instant::now()
        .checked_sub(interval)
        .unwrap_or_else(Instant::now);
    loop {
        if shared.stop.load(Ordering::Acquire) {
            return;
        }
        if shared.paused.load(Ordering::Acquire) {
            shared.user_pending.store(false, Ordering::Release);
            shared.pause_acknowledged.store(true, Ordering::Release);
            if receiver.recv().is_err() {
                return;
            }
            continue;
        }
        shared.pause_acknowledged.store(false, Ordering::Release);
        let needed = shared.user_pending.load(Ordering::Acquire)
            || shared.download_active.load(Ordering::Acquire)
                && shared.keep_download_awake.load(Ordering::Acquire);
        if needed && last_touch.elapsed() >= interval {
            shared.user_pending.store(false, Ordering::Release);
            if !shared.paused.load(Ordering::Acquire) && !shared.stop.load(Ordering::Acquire) {
                touch(shared);
            }
            last_touch = Instant::now();
            continue;
        }
        if needed {
            let remaining = interval.saturating_sub(last_touch.elapsed());
            if matches!(
                receiver.recv_timeout(remaining),
                Err(mpsc::RecvTimeoutError::Disconnected)
            ) {
                return;
            }
        } else if receiver.recv().is_err() {
            return;
        }
    }
}

fn touch_idle(shared: &Shared) {
    if shared.paused.load(Ordering::Acquire) || shared.stop.load(Ordering::Acquire) {
        return;
    }
    let mut child = match Command::new("lipc-set-prop")
        .args(["-i", "com.lab126.powerd", "touchScreenSaverTimeout", "1"])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
    {
        Ok(child) => child,
        Err(_) => return,
    };
    let started = Instant::now();
    let mut killed = false;
    loop {
        match child.try_wait() {
            Ok(Some(_)) => return,
            Err(_) => {
                let _ = child.kill();
                let _ = child.wait();
                return;
            }
            Ok(None) => {}
        }
        if !killed
            && (shared.paused.load(Ordering::Acquire)
                || shared.stop.load(Ordering::Acquire)
                || started.elapsed() > Duration::from_millis(700))
        {
            let _ = child.kill();
            killed = true;
        }
        // A pause ACK is published only after this child has actually exited.
        thread::sleep(Duration::from_millis(10));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn user_events_coalesce_and_idle_observation_does_not_keep_awake() {
        let (events, receiver) = mpsc::channel();
        let worker = ActivityWorker::start_with(Duration::from_millis(60), move |_| {
            let _ = events.send(Instant::now());
        })
        .unwrap();
        worker.set_keep_download_awake(true);
        assert!(receiver.recv_timeout(Duration::from_millis(30)).is_err());
        worker.note_user();
        let first = receiver.recv_timeout(Duration::from_secs(2)).unwrap();
        for _ in 0..100 {
            worker.note_user();
        }
        let second = receiver.recv_timeout(Duration::from_secs(2)).unwrap();
        assert!(second.duration_since(first) >= Duration::from_millis(60));
        assert!(receiver.recv_timeout(Duration::from_millis(100)).is_err());
    }

    #[test]
    fn pause_ack_stops_activity_and_resume_does_not_replay_stale_download_flags() {
        let (events, receiver) = mpsc::channel();
        let worker = ActivityWorker::start_with(Duration::from_millis(30), move |_| {
            let _ = events.send(());
        })
        .unwrap();
        worker.set_keep_download_awake(true);
        worker.set_download_active(true);
        receiver.recv_timeout(Duration::from_secs(2)).unwrap();
        worker.pause();
        let deadline = Instant::now() + Duration::from_secs(2);
        while !worker.paused_ready() && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(2));
        }
        assert!(worker.paused_ready());
        while receiver.try_recv().is_ok() {}
        worker.note_user();
        worker.set_download_active(true);
        assert!(receiver.recv_timeout(Duration::from_millis(80)).is_err());
        worker.resume();
        assert!(receiver.recv_timeout(Duration::from_millis(80)).is_err());
        worker.set_download_active(true);
        receiver.recv_timeout(Duration::from_secs(2)).unwrap();
    }

    #[test]
    fn download_activity_needs_the_explicit_keep_awake_preference() {
        let (events, receiver) = mpsc::channel();
        let worker = ActivityWorker::start_with(Duration::from_millis(30), move |_| {
            let _ = events.send(());
        })
        .unwrap();
        worker.set_download_active(true);
        assert!(receiver.recv_timeout(Duration::from_millis(60)).is_err());
        worker.set_keep_download_awake(true);
        receiver.recv_timeout(Duration::from_secs(2)).unwrap();
    }
}
