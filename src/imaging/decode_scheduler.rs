//! A single temporary-bitmap budget with bounded foreground preference.

use std::{
    cell::RefCell,
    collections::VecDeque,
    fmt,
    sync::{
        Arc, Condvar, Mutex,
        atomic::{AtomicBool, Ordering},
    },
    time::Duration,
};

use anyhow::Result;

const MAX_FOREGROUND_BURST: usize = 4;
const CANCELLATION_POLL: Duration = Duration::from_millis(20);

static SCHEDULER: DecodeScheduler = DecodeScheduler::new();

/// A cancellation fence for obsolete image preparation, independent of network
/// transfer ownership. It never interrupts a library decoder while it runs.
#[derive(Clone, Debug, Default)]
pub struct DecodeCancellation(Arc<AtomicBool>);

impl DecodeCancellation {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn cancel(&self) {
        self.0.store(true, Ordering::Release);
        SCHEDULER.changed.notify_all();
    }

    pub fn is_cancelled(&self) -> bool {
        self.0.load(Ordering::Acquire)
    }

    pub fn check(&self) -> Result<()> {
        if self.is_cancelled() {
            Err(DecodeCancelled.into())
        } else {
            Ok(())
        }
    }
}

#[derive(Debug)]
struct DecodeCancelled;

impl fmt::Display for DecodeCancelled {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("The background image work was superseded")
    }
}

impl std::error::Error for DecodeCancelled {}

pub fn is_decode_cancelled(error: &anyhow::Error) -> bool {
    error.downcast_ref::<DecodeCancelled>().is_some()
}

#[derive(Clone, Default)]
struct WorkContext {
    background: bool,
    cancellation: Option<DecodeCancellation>,
}

thread_local! {
    static WORK_CONTEXT: RefCell<WorkContext> = const { RefCell::new(WorkContext {
        background: false,
        cancellation: None,
    }) };
}

struct ContextGuard(Option<WorkContext>);

impl Drop for ContextGuard {
    fn drop(&mut self) {
        if let Some(previous) = self.0.take() {
            WORK_CONTEXT.with(|context| context.replace(previous));
        }
    }
}

/// Mark all image validation and decoding in this closure as background work.
/// Nested scopes inherit an existing cancellation fence when none is supplied.
pub fn with_background_decode<T>(
    cancellation: Option<&DecodeCancellation>,
    operation: impl FnOnce() -> Result<T>,
) -> Result<T> {
    let previous = WORK_CONTEXT.with(|context| {
        let inherited = context.borrow().cancellation.clone();
        context.replace(WorkContext {
            background: true,
            cancellation: cancellation.cloned().or(inherited),
        })
    });
    let _context = ContextGuard(Some(previous));
    check_decode_cancelled()?;
    operation()
}

/// Finish publishing an already completed image without making its validity
/// depend on the generation that originally requested it. Priority is retained.
pub(crate) fn without_decode_cancellation<T>(operation: impl FnOnce() -> Result<T>) -> Result<T> {
    let previous = WORK_CONTEXT.with(|context| {
        let background = context.borrow().background;
        context.replace(WorkContext {
            background,
            cancellation: None,
        })
    });
    let _context = ContextGuard(Some(previous));
    operation()
}

pub fn check_decode_cancelled() -> Result<()> {
    WORK_CONTEXT.with(|context| match context.borrow().cancellation.as_ref() {
        Some(cancellation) => cancellation.check(),
        None => Ok(()),
    })
}

pub(crate) fn decode_permit() -> Result<DecodePermit<'static>> {
    let context = WORK_CONTEXT.with(|context| context.borrow().clone());
    SCHEDULER.acquire(context.background, context.cancellation.as_ref())
}

struct Waiter {
    ticket: u64,
    background: bool,
}

struct State {
    active: bool,
    next_ticket: u64,
    foreground_burst: usize,
    waiters: VecDeque<Waiter>,
}

struct DecodeScheduler {
    state: Mutex<State>,
    changed: Condvar,
}

impl DecodeScheduler {
    const fn new() -> Self {
        Self {
            state: Mutex::new(State {
                active: false,
                next_ticket: 0,
                foreground_burst: 0,
                waiters: VecDeque::new(),
            }),
            changed: Condvar::new(),
        }
    }

    fn acquire(
        &self,
        background: bool,
        cancellation: Option<&DecodeCancellation>,
    ) -> Result<DecodePermit<'_>> {
        if let Some(cancellation) = cancellation {
            cancellation.check()?;
        }
        let mut state = self
            .state
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let ticket = state.next_ticket;
        state.next_ticket = state.next_ticket.wrapping_add(1);
        state.waiters.push_back(Waiter { ticket, background });
        loop {
            if let Some(cancellation) = cancellation {
                if let Err(error) = cancellation.check() {
                    state.waiters.retain(|waiter| waiter.ticket != ticket);
                    self.changed.notify_all();
                    return Err(error);
                }
            }
            if !state.active && next_waiter(&state) == Some(ticket) {
                state.waiters.retain(|waiter| waiter.ticket != ticket);
                state.active = true;
                state.foreground_burst =
                    if background || !state.waiters.iter().any(|waiter| waiter.background) {
                        0
                    } else {
                        state.foreground_burst.saturating_add(1)
                    };
                return Ok(DecodePermit { scheduler: self });
            }
            // A cancellation token can also be used with an independently
            // constructed scheduler in tests. Timed waits avoid requiring a
            // token to own every scheduler on which it might be waiting.
            (state, _) = self
                .changed
                .wait_timeout(state, CANCELLATION_POLL)
                .unwrap_or_else(std::sync::PoisonError::into_inner);
        }
    }
}

fn next_waiter(state: &State) -> Option<u64> {
    let foreground = state.waiters.iter().find(|waiter| !waiter.background);
    let background = state.waiters.iter().find(|waiter| waiter.background);
    match (foreground, background) {
        (Some(foreground), Some(_)) if state.foreground_burst < MAX_FOREGROUND_BURST => {
            Some(foreground.ticket)
        }
        (_, Some(background)) => Some(background.ticket),
        (Some(foreground), None) => Some(foreground.ticket),
        (None, None) => None,
    }
}

pub(crate) struct DecodePermit<'a> {
    scheduler: &'a DecodeScheduler,
}

impl Drop for DecodePermit<'_> {
    fn drop(&mut self) {
        self.scheduler
            .state
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .active = false;
        self.scheduler.changed.notify_all();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        sync::mpsc,
        thread,
        time::{Duration, Instant},
    };

    fn wait_for_waiters(scheduler: &DecodeScheduler, count: usize) {
        let deadline = Instant::now() + Duration::from_secs(5);
        loop {
            if scheduler.state.lock().unwrap().waiters.len() == count {
                return;
            }
            assert!(Instant::now() < deadline, "Decoder waiters did not arrive");
            thread::yield_now();
        }
    }

    #[test]
    fn foreground_overtakes_queued_background_without_parallel_decoding() {
        let scheduler = Arc::new(DecodeScheduler::new());
        let active = scheduler.acquire(true, None).unwrap();
        let (sender, receiver) = mpsc::channel();
        let background = {
            let scheduler = scheduler.clone();
            let sender = sender.clone();
            thread::spawn(move || {
                let _permit = scheduler.acquire(true, None).unwrap();
                sender.send("background").unwrap();
            })
        };
        wait_for_waiters(&scheduler, 1);
        let foreground = {
            let scheduler = scheduler.clone();
            thread::spawn(move || {
                let _permit = scheduler.acquire(false, None).unwrap();
                sender.send("foreground").unwrap();
            })
        };
        wait_for_waiters(&scheduler, 2);
        assert!(receiver.try_recv().is_err());
        drop(active);
        assert_eq!(
            receiver.recv_timeout(Duration::from_secs(5)).unwrap(),
            "foreground"
        );
        assert_eq!(
            receiver.recv_timeout(Duration::from_secs(5)).unwrap(),
            "background"
        );
        foreground.join().unwrap();
        background.join().unwrap();
    }

    #[test]
    fn continuous_foreground_work_does_not_starve_background() {
        let scheduler = Arc::new(DecodeScheduler::new());
        let active = scheduler.acquire(true, None).unwrap();
        let (sender, receiver) = mpsc::channel();
        let background = {
            let scheduler = scheduler.clone();
            let sender = sender.clone();
            thread::spawn(move || {
                let _permit = scheduler.acquire(true, None).unwrap();
                sender.send(true).unwrap();
            })
        };
        wait_for_waiters(&scheduler, 1);
        let foreground = (0..MAX_FOREGROUND_BURST + 2)
            .map(|_| {
                let scheduler = scheduler.clone();
                let sender = sender.clone();
                thread::spawn(move || {
                    let _permit = scheduler.acquire(false, None).unwrap();
                    sender.send(false).unwrap();
                })
            })
            .collect::<Vec<_>>();
        wait_for_waiters(&scheduler, MAX_FOREGROUND_BURST + 3);
        drop(active);
        for _ in 0..MAX_FOREGROUND_BURST {
            assert!(!receiver.recv_timeout(Duration::from_secs(5)).unwrap());
        }
        assert!(receiver.recv_timeout(Duration::from_secs(5)).unwrap());
        background.join().unwrap();
        for foreground in foreground {
            foreground.join().unwrap();
        }
    }

    #[test]
    fn cancelled_waiter_releases_its_queue_position_without_entering_decoder() {
        let scheduler = Arc::new(DecodeScheduler::new());
        let active = scheduler.acquire(false, None).unwrap();
        let cancellation = DecodeCancellation::new();
        let (sender, receiver) = mpsc::channel();
        let waiting = {
            let scheduler = scheduler.clone();
            let cancellation = cancellation.clone();
            thread::spawn(move || {
                let result = scheduler.acquire(true, Some(&cancellation));
                sender
                    .send(
                        result
                            .err()
                            .is_some_and(|error| is_decode_cancelled(&error)),
                    )
                    .unwrap();
            })
        };
        wait_for_waiters(&scheduler, 1);
        cancellation.cancel();
        assert!(receiver.recv_timeout(Duration::from_secs(5)).unwrap());
        assert!(scheduler.state.lock().unwrap().active);
        assert!(scheduler.state.lock().unwrap().waiters.is_empty());
        drop(active);
        waiting.join().unwrap();
        let _next = scheduler.acquire(false, None).unwrap();
    }

    #[test]
    fn nested_scopes_keep_cancellation_and_restore_after_unwind() {
        let cancellation = DecodeCancellation::new();
        cancellation.cancel();
        let error = with_background_decode(Some(&cancellation), || {
            panic!("Cancelled work must not start");
            #[allow(unreachable_code)]
            Ok(())
        })
        .unwrap_err();
        assert!(is_decode_cancelled(&error));
        assert!(check_decode_cancelled().is_ok());

        let cancellation = DecodeCancellation::new();
        let result = std::panic::catch_unwind(|| {
            let _: Result<()> = with_background_decode(Some(&cancellation), || {
                cancellation.cancel();
                let error = with_background_decode(None, || Ok(())).unwrap_err();
                assert!(is_decode_cancelled(&error));
                panic!("Restore the previous context on unwind");
            });
        });
        assert!(result.is_err());
        assert!(check_decode_cancelled().is_ok());
        assert!(!WORK_CONTEXT.with(|context| context.borrow().background));
    }

    #[test]
    fn finished_work_can_publish_after_its_original_reader_is_cancelled() {
        let cancellation = DecodeCancellation::new();
        let output = with_background_decode(Some(&cancellation), || {
            cancellation.cancel();
            assert!(check_decode_cancelled().is_err());
            without_decode_cancellation(|| {
                assert!(check_decode_cancelled().is_ok());
                assert!(WORK_CONTEXT.with(|context| context.borrow().background));
                let _permit = decode_permit()?;
                Ok(42)
            })
        })
        .unwrap();
        assert_eq!(output, 42);
        assert!(check_decode_cancelled().is_ok());
    }
}
