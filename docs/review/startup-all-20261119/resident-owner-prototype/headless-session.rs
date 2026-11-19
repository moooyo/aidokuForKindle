//! Headless Linux test of the actual Slint event loop, widget and resource destructors.
//! Native calibration, evdev grabs and EPDC completion are not supplied by the fixture.

use super::*;
use crate::lifecycle::LifecycleError;
use crate::{NativeFramebufferObservation, NativePortraitOrientation};
use slint::ComponentHandle;
use std::cell::Cell;
use std::os::unix::net::UnixStream;
use std::sync::mpsc;

slint::slint! {
    export component SessionWidget inherits Window {
        width: 64px;
        height: 64px;
        in-out property <int> warm-value;
        background: root.warm-value == 41 ? #123456 : #654321;
    }
}

fn fd_closed(fd: i32) -> bool {
    // SAFETY: F_GETFD has no pointer arguments.
    unsafe {
        libc::fcntl(fd, libc::F_GETFD) == -1
            && io::Error::last_os_error().raw_os_error() == Some(libc::EBADF)
    }
}

fn mapping_absent(address: usize) -> bool {
    let mut residency = 0u8;
    // SAFETY: mincore observes one page of this previously owned, page-aligned mapping.
    // The syscall returns ENOMEM for an unmapped address; it does not dereference it.
    let result = unsafe { libc::mincore(address as *mut libc::c_void, 1, &mut residency) };
    result == -1 && io::Error::last_os_error().raw_os_error() == Some(libc::ENOMEM)
}

fn accounting() -> (u64, u64) {
    let stat = std::fs::read_to_string("/proc/self/stat").unwrap();
    let fields = stat
        .rsplit_once(") ")
        .unwrap()
        .1
        .split_ascii_whitespace()
        .collect::<Vec<_>>();
    let cpu = fields[11].parse::<u64>().unwrap() + fields[12].parse::<u64>().unwrap();
    let status = std::fs::read_to_string("/proc/self/status").unwrap();
    let rss_kib = status
        .lines()
        .find(|line| line.starts_with("VmRSS:"))
        .unwrap()
        .split_ascii_whitespace()
        .nth(1)
        .unwrap()
        .parse::<u64>()
        .unwrap();
    (cpu, rss_kib)
}

#[test]
fn a_single_real_slint_window_survives_two_disposable_presentations() {
    let callback: ControlCallback = Rc::new(RefCell::new(None));
    let platform = KindlePlatform::new(
        Arc::new(Mutex::new(None)),
        Rc::new(RefCell::new(None)),
        Arc::new(AtomicBool::new(false)),
        Arc::new(AtomicU32::new(0)),
        Rc::new(RefCell::new(None)),
        callback.clone(),
        Rc::new(FrameObserverState::default()),
    )
    .unwrap();
    let lifecycle = platform.lifecycle_handle();
    let fixture_component = Rc::new(RefCell::new(None::<slint::Weak<SessionWidget>>));
    let old_kernel = Rc::new(Cell::new(None::<(i32, i32, usize)>));
    let calls = Rc::new(Cell::new(0u8));
    let (released_tx, released_rx) = mpsc::sync_channel(1);
    {
        let observed = lifecycle.clone();
        let old_kernel = old_kernel.clone();
        *callback.borrow_mut() = Some(Box::new(move || {
            if observed.home_for_fixture()
                && let Some(old) = old_kernel.take()
            {
                released_tx.send(old).unwrap();
            }
        }));
    }
    let process = std::process::id();
    let adapter = Rc::as_ptr(&platform.window) as usize;
    {
        let weak = fixture_component.clone();
        let old_kernel = old_kernel.clone();
        let calls = calls.clone();
        *platform.fixture_session.borrow_mut() = Some(Box::new(move |p| {
            let round = calls.get() + 1;
            calls.set(round);
            let handle = p.lifecycle_handle();
            let widget = weak.borrow().as_ref().unwrap().upgrade().unwrap();
            assert_eq!(process, std::process::id());
            assert_eq!(adapter, Rc::as_ptr(&p.window) as usize);
            assert_eq!(widget.get_warm_value(), 41);
            if round == 2 {
                let calibration = p.lifecycle.portrait_calibration.lock().unwrap().unwrap();
                assert_eq!(calibration.framebuffer.rotation, 2);
                widget.set_warm_value(42);
            }
            // Use the real owning types. Only the backing objects and native ACK are fixtures.
            let mut framebuffer = Framebuffer::memory_for_test(64, 64, 64, 1, 0).unwrap();
            let (socket, _peer) = UnixStream::pair().unwrap();
            let touch = TouchInput::idle_socket_for_session_test(socket);
            p.lifecycle.native_io_started.store(true, Ordering::Release);
            let resources = (
                framebuffer.raw_fd_for_session_test(),
                touch.fd(),
                framebuffer.mapped_bytes_for_test().as_ptr() as usize,
            );
            assert!(
                !fd_closed(resources.0) && !fd_closed(resources.1) && !mapping_absent(resources.2)
            );
            p.window.set_size(slint::PhysicalSize::new(64, 64));
            p.window.request_redraw();
            let mut pixels = vec![slint::Rgb8Pixel::default(); 64 * 64];
            assert!(p.window.draw_if_needed(|renderer| {
                renderer.set_repaint_buffer_type(RepaintBufferType::NewBuffer);
                renderer.render(&mut pixels, 64);
                renderer.set_repaint_buffer_type(RepaintBufferType::ReusedBuffer);
            }));
            let center = pixels[32 * 64 + 32];
            let expected = if round == 1 {
                [0x12, 0x34, 0x56]
            } else {
                [0x65, 0x43, 0x21]
            };
            assert_eq!([center.r, center.g, center.b], expected);
            framebuffer.write_line(32, 32..33, &[center.r]);
            assert_eq!(framebuffer.mapped_bytes_for_test()[32 * 64 + 32], center.r);
            match round {
                1 => {
                    assert_eq!(
                        handle.return_library_io(),
                        Err(LifecycleError::ResourcesNotReleased)
                    );
                    handle.begin_pause().unwrap();
                    // There is no evdev/EPDC on this path. These stand in for their completed ACKs.
                    p.lifecycle.applied_paused.store(true, Ordering::Release);
                    p.lifecycle.input_released.store(true, Ordering::Release);
                    handle.park().unwrap();
                    handle.return_library_io().unwrap();
                    assert_eq!(handle.resume(), Err(LifecycleError::LibraryTransition));
                    assert!(matches!(
                        handle.take_library_io_closed(),
                        Err(LifecycleError::LibraryTransition)
                    ));
                    assert!(!handle.home_for_fixture());
                    old_kernel.set(Some(resources));
                    Ok(SessionEnd::Returned)
                }
                2 => {
                    slint::quit_event_loop().unwrap();
                    Ok(SessionEnd::Quit)
                }
                _ => panic!("More than two native presentations were admitted"),
            }
        }));
    }
    let guard = platform.new_event_loop_proxy().unwrap();
    let monitor = {
        let observed = lifecycle.clone();
        std::thread::spawn(move || {
            let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                let (frame_fd, touch_fd, mapping) =
                    released_rx.recv_timeout(Duration::from_secs(3)).unwrap();
                assert!(observed.home_for_fixture());
                assert!(fd_closed(frame_fd) && fd_closed(touch_fd) && mapping_absent(mapping));
                assert_eq!(observed.resume(), Err(LifecycleError::LibraryTransition));
                assert_eq!(
                    observed.begin_pause(),
                    Err(LifecycleError::LibraryTransition)
                );
                assert_eq!(observed.park(), Err(LifecycleError::LibraryTransition));
                let receipt = observed.take_library_io_closed().unwrap();
                assert_eq!(receipt.round(), 0);
                assert!(matches!(
                    observed.clone().take_library_io_closed(),
                    Err(LifecycleError::LibraryTransition)
                ));
                let started = Instant::now();
                let (before_ticks, before_rss) = accounting();
                std::thread::sleep(Duration::from_millis(400));
                let (after_ticks, after_rss) = accounting();
                let elapsed = started.elapsed().as_millis();
                assert!(observed.home_for_fixture());
                receipt
                    .reopen(Some((
                        NativePortraitOrientation::Down,
                        NativePortraitOrientation::Down,
                        NativeFramebufferObservation::for_test_rotation(2),
                    )))
                    .unwrap();
                assert!(observed.take_library_io_closed().is_err());
                (elapsed, after_ticks - before_ticks, before_rss, after_rss)
            }));
            if result.is_err() {
                let _ = guard.quit_event_loop();
            }
            result.unwrap()
        })
    };
    slint::platform::set_platform(Box::new(platform)).unwrap();
    let window = SessionWidget::new().unwrap();
    window.set_warm_value(41);
    *fixture_component.borrow_mut() = Some(window.as_weak());
    window.show().unwrap();
    slint::run_event_loop().unwrap();
    let (elapsed, ticks, before_rss, after_rss) = monitor.join().unwrap();
    assert_eq!(calls.get(), 2);
    assert_eq!(window.get_warm_value(), 42);
    assert_eq!(lifecycle.resume(), Err(LifecycleError::EventLoopTerminated));
    assert!(matches!(
        lifecycle.take_library_io_closed(),
        Err(LifecycleError::EventLoopTerminated)
    ));
    println!(
        "SESSION_OBSERVATION {{\"presentations\":2,\"slint_event_loops\":1,\"same_component\":true,\"same_process\":true,\"two_distinct_software_frames\":true,\"framebuffer_fd_closed\":true,\"touch_fd_closed\":true,\"framebuffer_unmapped\":true,\"stale_and_ordinary_resume_refused\":true,\"receipt_after_native_scope_only\":true,\"receipt_round\":0,\"receipt_claim_once\":true,\"new_framebuffer_observation_installed\":true,\"ordinary_quit_terminal\":true,\"idle_interval_ms\":{elapsed},\"process_cpu_tick_delta\":{ticks},\"rss_before_kib\":{before_rss},\"rss_after_kib\":{after_rss},\"hardware_observation\":\"synthetic\"}}"
    );
}
