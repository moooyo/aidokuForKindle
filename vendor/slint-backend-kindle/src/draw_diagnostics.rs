use std::{cell::Cell, mem::MaybeUninit, time::Duration};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ThreadCounters {
    pub minor_faults: u64,
    pub major_faults: u64,
    pub voluntary_switches: u64,
    pub involuntary_switches: u64,
}

impl ThreadCounters {
    fn read() -> Option<Self> {
        let mut usage = MaybeUninit::<libc::rusage>::zeroed();
        if unsafe { libc::getrusage(libc::RUSAGE_THREAD, usage.as_mut_ptr()) } != 0 {
            return None;
        }
        let usage = unsafe { usage.assume_init() };
        Some(Self {
            minor_faults: usage.ru_minflt.try_into().ok()?,
            major_faults: usage.ru_majflt.try_into().ok()?,
            voluntary_switches: usage.ru_nvcsw.try_into().ok()?,
            involuntary_switches: usage.ru_nivcsw.try_into().ok()?,
        })
    }

    fn since(self, before: Self) -> Option<Self> {
        Some(Self {
            minor_faults: self.minor_faults.checked_sub(before.minor_faults)?,
            major_faults: self.major_faults.checked_sub(before.major_faults)?,
            voluntary_switches: self.voluntary_switches.checked_sub(before.voluntary_switches)?,
            involuntary_switches: self.involuntary_switches.checked_sub(before.involuntary_switches)?,
        })
    }
}

fn thread_time() -> Option<Duration> {
    let mut time = MaybeUninit::<libc::timespec>::zeroed();
    if unsafe { libc::clock_gettime(libc::CLOCK_THREAD_CPUTIME_ID, time.as_mut_ptr()) } != 0 {
        return None;
    }
    let time = unsafe { time.assume_init() };
    let nanos = u32::try_from(time.tv_nsec).ok()?;
    if nanos >= 1_000_000_000 {
        return None;
    }
    Some(Duration::new(time.tv_sec.try_into().ok()?, nanos))
}

#[derive(Clone, Copy, Debug)]
pub struct DrawAccounting {
    pub thread: Option<Duration>,
    pub counters: Option<ThreadCounters>,
    pub width: u32,
    pub height: u32,
    pub gray: bool,
}

#[derive(Clone, Copy)]
enum Window {
    Closed,
    PendingScene,
    Attempts(u8),
}

thread_local! { static WINDOW: Cell<Window> = const { Cell::new(Window::Closed) }; }

pub fn permit_managed_start() {
    WINDOW.set(Window::PendingScene);
}

pub fn scene_requested() {
    if matches!(WINDOW.get(), Window::PendingScene) {
        WINDOW.set(Window::Attempts(8));
    }
}

pub fn close() {
    WINDOW.set(Window::Closed);
}

pub(crate) struct Before {
    counters: Option<ThreadCounters>,
    thread: Option<Duration>,
}

pub(crate) fn begin() -> Option<Before> {
    if let Window::Attempts(remaining @ 1..) = WINDOW.get() {
        WINDOW.set(Window::Attempts(remaining - 1));
        let counters = ThreadCounters::read();
        let thread = thread_time();
        Some(Before { counters, thread })
    } else {
        None
    }
}

impl Before {
    pub(crate) fn finish(self, width: u32, height: u32, gray: impl FnOnce() -> bool) -> DrawAccounting {
        let after = thread_time();
        let counters = ThreadCounters::read();
        DrawAccounting {
            thread: after.zip(self.thread).and_then(|(end, start)| end.checked_sub(start)),
            counters: counters.zip(self.counters).and_then(|(end, start)| end.since(start)),
            width, height, gray: gray(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_managed_scene_can_sample_at_most_eight_draw_attempts() {
        close();
        scene_requested();
        assert!(begin().is_none());
        permit_managed_start();
        assert!(begin().is_none());
        scene_requested();
        for _ in 0..8 {
            assert!(begin().is_some());
        }
        scene_requested();
        assert!(begin().is_none());
        close();
    }

    #[test]
    fn a_backward_resource_counter_invalidates_the_entire_resource_group() {
        let before = ThreadCounters { minor_faults: 3, major_faults: 0, voluntary_switches: 2, involuntary_switches: 1 };
        assert!(ThreadCounters { minor_faults: 2, ..before }.since(before).is_none());
        let same = before.since(before).unwrap();
        assert_eq!([same.minor_faults, same.major_faults, same.voluntary_switches, same.involuntary_switches], [0; 4]);
    }

    #[test]
    fn thread_clock_advances_with_work_not_with_a_sleep() {
        permit_managed_start();
        scene_requested();
        let before = begin().unwrap();
        std::thread::sleep(Duration::from_millis(50));
        let asleep = before.finish(5, 6, || true);
        let before = begin().unwrap();
        let target = thread_time().unwrap() + Duration::from_millis(20);
        while thread_time().unwrap() < target {
            for number in 0..1000 {
                std::hint::black_box(number * 17);
            }
        }
        let busy = before.finish(5, 6, || false);
        assert!(busy.thread.unwrap() >= Duration::from_millis(20));
        assert!(busy.thread.unwrap() > asleep.thread.unwrap());
        assert!(asleep.counters.is_some() && busy.counters.is_some());
        close();
    }
}
