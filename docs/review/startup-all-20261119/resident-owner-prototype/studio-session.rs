//! Research-only software backing for the real Slint event loop and receipt.

use super::*;
use crate::{LibraryIoClosed, NativeFramebufferObservation, studio_observation};
use slint::ComponentHandle;
use std::cell::Cell;
use std::os::unix::net::UnixStream;
use std::sync::mpsc::{self, Receiver, Sender, SyncSender};
use std::thread::{self, JoinHandle};

const CONTROL_BUDGET: Duration = Duration::from_secs(8);
const SIDE: u32 = 64;

slint::slint! {
    export component StudioWindow inherits Window {
        width: 64px;
        height: 64px;
        in-out property <int> warm-value;
        background: root.warm-value == 41 ? #123456 : #654321;
    }
}

enum Command {
    Close,
    Shutdown,
}

#[derive(Clone, Copy, Eq, PartialEq)]
enum Frame {
    First,
    Second,
}

pub struct StudioControl {
    commands: Sender<Command>,
    frames: Receiver<Frame>,
    released: Receiver<io::Result<LibraryIoClosed>>,
    proxy: Box<dyn EventLoopProxy>,
    thread: Option<JoinHandle<io::Result<()>>>,
    close_requested: bool,
}

/// Start one real Slint window on its own thread in the calling process.
pub fn start_studio() -> io::Result<StudioControl> {
    let (commands, commands_rx) = mpsc::channel();
    let (frames_tx, frames) = mpsc::sync_channel(2);
    let (released_tx, released) = mpsc::sync_channel(1);
    let (boot_tx, boot_rx) = mpsc::sync_channel(1);
    let thread = thread::Builder::new()
        .name("resident-studio".into())
        .spawn(move || run_studio(commands_rx, frames_tx, released_tx, boot_tx))?;
    let proxy = match boot_rx.recv_timeout(CONTROL_BUDGET) {
        Ok(proxy) => proxy,
        Err(error) => {
            let _ = commands.send(Command::Shutdown);
            let joined = thread
                .join()
                .map_err(|_| invalid("studio thread panicked"))?;
            joined?;
            return Err(invalid(error));
        }
    };
    let control = StudioControl {
        commands,
        frames,
        released,
        proxy,
        thread: Some(thread),
        close_requested: false,
    };
    control.wait_frame(Frame::First)?;
    Ok(control)
}

impl StudioControl {
    /// Ask the renderer to drop the first session and hand back its unique receipt.
    pub fn close(&mut self) -> io::Result<LibraryIoClosed> {
        if self.close_requested {
            return Err(invalid("studio close was already requested"));
        }
        self.close_requested = true;
        self.commands.send(Command::Close).map_err(invalid)?;
        self.released
            .recv_timeout(CONTROL_BUDGET)
            .map_err(invalid)?
    }

    /// Wait for a different frame on the original window after the receipt is consumed.
    pub fn second_frame(&self) -> io::Result<()> {
        self.wait_frame(Frame::Second)
    }

    pub fn shutdown(mut self) -> io::Result<()> {
        self.stop()
    }

    fn wait_frame(&self, expected: Frame) -> io::Result<()> {
        if self.frames.recv_timeout(CONTROL_BUDGET).map_err(invalid)? != expected {
            return Err(invalid("studio frame arrived out of order"));
        }
        Ok(())
    }

    fn stop(&mut self) -> io::Result<()> {
        if let Some(thread) = self.thread.take() {
            let _ = self.commands.send(Command::Shutdown);
            let _ = self.proxy.quit_event_loop();
            return thread
                .join()
                .map_err(|_| invalid("studio thread panicked"))?;
        }
        Ok(())
    }
}

impl Drop for StudioControl {
    fn drop(&mut self) {
        let _ = self.stop();
    }
}

fn run_studio(
    commands: Receiver<Command>,
    frames: SyncSender<Frame>,
    released: SyncSender<io::Result<LibraryIoClosed>>,
    boot: SyncSender<Box<dyn EventLoopProxy>>,
) -> io::Result<()> {
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
    .map_err(invalid)?;
    let handle = platform.lifecycle_handle();
    let component = Rc::new(RefCell::new(None::<slint::Weak<StudioWindow>>));
    let old_kernel = Rc::new(Cell::new(None::<KernelResources>));
    let receipt_handle = handle.clone();
    let destruction = old_kernel.clone();
    *callback.borrow_mut() = Some(Box::new(move || {
        if let Some(resources) = destruction.take() {
            let outcome = resources.verify_released().and_then(|()| {
                let receipt = receipt_handle.take_library_io_closed().map_err(invalid)?;
                if receipt.round() != 0 || receipt_handle.take_library_io_closed().is_ok() {
                    return Err(invalid("studio receipt is not unique to the first round"));
                }
                Ok(receipt)
            });
            let _ = released.send(outcome);
        }
    }));

    let widget = component.clone();
    let adapter = Rc::as_ptr(&platform.window);
    let process = std::process::id();
    let next = Cell::new(0u8);
    *platform.fixture_session.borrow_mut() = Some(Box::new(move |p| {
        let round = next.get();
        next.set(round + 1);
        if process != std::process::id() || adapter != Rc::as_ptr(&p.window) || round > 1 {
            return Err(platform_error("studio changed process or window"));
        }
        let widget = widget
            .borrow()
            .as_ref()
            .and_then(slint::Weak::upgrade)
            .ok_or_else(|| platform_error("original studio component is absent"))?;
        if widget.get_warm_value() != 41 {
            return Err(platform_error("original studio component state was lost"));
        }
        if round == 1 {
            let binding = p
                .lifecycle
                .portrait_calibration
                .lock()
                .expect("portrait calibration poisoned");
            let observed = binding
                .as_ref()
                .map(|value| value.framebuffer)
                .ok_or_else(|| {
                    platform_error("the reopened session has no new framebuffer observation")
                })?;
            if studio_observation(observed.rotation).map_err(platform_error)? != observed {
                return Err(platform_error(
                    "the reopened session has different framebuffer geometry",
                ));
            }
            widget.set_warm_value(42);
        }

        let mut framebuffer = Framebuffer::memory_for_test(SIDE, SIDE, SIDE as usize, 1, 0)
            .map_err(platform_error)?;
        let (socket, _peer) = UnixStream::pair().map_err(platform_error)?;
        let touch = TouchInput::idle_socket_for_session_test(socket);
        p.lifecycle.native_io_started.store(true, Ordering::Release);
        let resources = KernelResources {
            framebuffer: framebuffer.raw_fd_for_session_test(),
            touch: touch.fd(),
            mapped: framebuffer.mapped_bytes_for_test().as_ptr() as usize,
        };
        resources.verify_live().map_err(platform_error)?;
        draw(p, &mut framebuffer, round)?;
        frames
            .send(if round == 0 {
                Frame::First
            } else {
                Frame::Second
            })
            .map_err(platform_error)?;
        match commands.recv() {
            Ok(Command::Close) if round == 0 => {
                handle.begin_pause().map_err(platform_error)?;
                // Native evdev and EPDC are absent in this software adapter.
                p.lifecycle.applied_paused.store(true, Ordering::Release);
                p.lifecycle.input_released.store(true, Ordering::Release);
                handle.park().map_err(platform_error)?;
                handle.return_library_io().map_err(platform_error)?;
                if handle.take_library_io_closed().is_ok() {
                    return Err(platform_error(
                        "studio released a receipt before dropping its kernel resources",
                    ));
                }
                old_kernel.set(Some(resources));
                Ok(SessionEnd::Returned)
            }
            Ok(Command::Shutdown) | Err(_) => {
                slint::quit_event_loop().map_err(platform_error)?;
                Ok(SessionEnd::Quit)
            }
            Ok(Command::Close) => Err(platform_error("a second close was requested")),
        }
    }));

    let proxy = platform
        .new_event_loop_proxy()
        .ok_or_else(|| invalid("studio event loop proxy is absent"))?;
    slint::platform::set_platform(Box::new(platform)).map_err(invalid)?;
    let window = StudioWindow::new().map_err(invalid)?;
    window.set_warm_value(41);
    *component.borrow_mut() = Some(window.as_weak());
    window.show().map_err(invalid)?;
    boot.send(proxy).map_err(invalid)?;
    slint::run_event_loop().map_err(invalid)?;
    Ok(())
}

fn draw(
    platform: &KindlePlatform,
    framebuffer: &mut Framebuffer,
    round: u8,
) -> Result<(), PlatformError> {
    platform
        .window
        .set_size(slint::PhysicalSize::new(SIDE, SIDE));
    platform.window.request_redraw();
    let side = SIDE as usize;
    let mut pixels = vec![slint::Rgb8Pixel::default(); side * side];
    if !platform.window.draw_if_needed(|renderer| {
        renderer.set_repaint_buffer_type(RepaintBufferType::NewBuffer);
        renderer.render(&mut pixels, side);
        renderer.set_repaint_buffer_type(RepaintBufferType::ReusedBuffer);
    }) {
        return Err(platform_error("studio Slint frame was not rendered"));
    }
    let center = side / 2;
    let center_pixel = pixels[center * side + center];
    let expected = if round == 0 {
        [0x12, 0x34, 0x56]
    } else {
        [0x65, 0x43, 0x21]
    };
    if [center_pixel.r, center_pixel.g, center_pixel.b] != expected {
        return Err(platform_error("studio Slint frame pixels do not match"));
    }
    framebuffer.write_line(center, center..center + 1, &[center_pixel.r]);
    if framebuffer.mapped_bytes_for_test()[center * side + center] != center_pixel.r {
        return Err(platform_error(
            "studio framebuffer did not receive the new pixels",
        ));
    }
    Ok(())
}

#[derive(Clone, Copy)]
struct KernelResources {
    framebuffer: i32,
    touch: i32,
    mapped: usize,
}

impl KernelResources {
    fn verify_live(self) -> io::Result<()> {
        if fd_closed(self.framebuffer)? || fd_closed(self.touch)? || mapping_absent(self.mapped)? {
            return Err(invalid("studio resource was absent before presenting"));
        }
        Ok(())
    }

    fn verify_released(self) -> io::Result<()> {
        if !fd_closed(self.framebuffer)? || !fd_closed(self.touch)? || !mapping_absent(self.mapped)?
        {
            return Err(invalid(
                "studio resource still exists after the presentation scope",
            ));
        }
        Ok(())
    }
}

fn fd_closed(fd: i32) -> io::Result<bool> {
    // SAFETY: F_GETFD has no pointer arguments.
    if unsafe { libc::fcntl(fd, libc::F_GETFD) } >= 0 {
        return Ok(false);
    }
    let error = io::Error::last_os_error();
    if error.raw_os_error() == Some(libc::EBADF) {
        Ok(true)
    } else {
        Err(error)
    }
}

fn mapping_absent(address: usize) -> io::Result<bool> {
    let mut residency = 0u8;
    // SAFETY: mincore observes one page of a previously page-aligned mapping.
    if unsafe { libc::mincore(address as *mut libc::c_void, 1, &mut residency) } == 0 {
        return Ok(false);
    }
    let error = io::Error::last_os_error();
    if error.raw_os_error() == Some(libc::ENOMEM) {
        Ok(true)
    } else {
        Err(error)
    }
}

fn invalid(error: impl std::fmt::Display) -> io::Error {
    io::Error::other(error.to_string())
}

fn platform_error(error: impl std::fmt::Display) -> PlatformError {
    PlatformError::Other(error.to_string())
}
