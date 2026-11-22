use aidoku_kindle::{
    domain::{Book, Chapter, Preferences}, platform::DeviceProfile, reader_timing, store::Store,
    ui::{AppWindow, Commands, State, Theme},
};
use anyhow::{Result, ensure};
use slint::{ComponentHandle, Model, SharedPixelBuffer, platform::{
    EventLoopProxy, PointerEventButton, WindowAdapter, WindowEvent,
    software_renderer::{MinimalSoftwareWindow, RepaintBufferType},
}};
use std::{
    cell::{Cell, RefCell}, fs, path::PathBuf, rc::Rc,
    sync::{OnceLock, atomic::Ordering, mpsc::{self, Receiver, Sender}}, time::{Duration, Instant},
};

struct Configuration { directory: PathBuf, direct: bool, check: String }
static CONFIG: OnceLock<Configuration> = OnceLock::new();

struct Context {
    ui: slint::Weak<AppWindow>, app: crate::bootstrap_startup::ApplicationSlot,
    lifecycle: Rc<RefCell<crate::bootstrap_lifecycle::Coordinator>>,
    turn: Cell<u64>, attachment: Cell<Option<u64>>, blocked: Cell<bool>, verified: Cell<bool>,
}
thread_local! { static CONTEXT: std::cell::OnceCell<Context> = const { std::cell::OnceCell::new() }; }

fn context<ResultValue>(action: impl FnOnce(&Context) -> ResultValue) -> ResultValue {
    CONTEXT.with(|slot| action(slot.get().expect("study context")))
}

pub fn prepare() -> Result<()> {
    let directory = PathBuf::from(std::env::var("AIDOKU_ATTACH_TRIAL_CASE")?);
    let check = std::env::var("AIDOKU_ATTACH_TRIAL_CHECK")?;
    ensure!(matches!(check.as_str(), "normal" | "suspend" | "stop"));
    let direct = match std::env::var("AIDOKU_ATTACH_TRIAL_ARM")?.as_str() {
        "control" => false, "direct" => true, _ => anyhow::bail!("Invalid trial arm"),
    };
    let data = directory.join("data");
    fs::create_dir(&data)?;
    let store = Store::open(data.join("library.sqlite3"))?;
    store.save_preferences(&Preferences { language: "en".into(), ..Default::default() })?;
    for number in 1..=6 {
        let cover = data.join(format!("volume-{number}.png"));
        image::GrayImage::from_pixel(76, 104, image::Luma([number * 30])).save(&cover)?;
        let key = format!("5:local:trial-{number}");
        store.upsert_book(&Book { id: key.clone(), source_id: "local".into(), remote_id: format!("trial-{number}"),
            title: format!("Study volume {number}"), author: "Trial".into(), in_library: true,
            cover: cover.to_string_lossy().into_owned(), updated_at: 1700000000 + i64::from(number), ..Default::default() })?;
        store.save_chapters(&key, &[Chapter { id: format!("chapter-{number}"), book_id: key.clone(), source_id: "local".into(),
            remote_id: format!("chapter-{number}"), number: 1.0, title: "Trial chapter".into(), page_count: 6, ..Default::default() }])?;
    }
    ensure!(CONFIG.set(Configuration { directory, direct, check }).is_ok());
    Ok(())
}

pub fn direct() -> bool { CONFIG.get().unwrap().direct }
pub fn bind(ui: &AppWindow, app: &crate::bootstrap_startup::ApplicationSlot,
            lifecycle: &Rc<RefCell<crate::bootstrap_lifecycle::Coordinator>>) {
    CONTEXT.with(|slot| assert!(slot.set(Context {ui: ui.as_weak(), app: app.clone(), lifecycle: lifecycle.clone(),
        turn: Cell::new(0), attachment: Cell::new(None), blocked: Cell::new(false), verified: Cell::new(false)}).is_ok()));
}
pub fn enter_dispatch() { context(|ctx| ctx.turn.set(ctx.turn.get() + 1)); }
pub fn attached() {
    context(|ctx| {
        assert!(ctx.attachment.replace(Some(ctx.turn.get())).is_none());
        match CONFIG.get().unwrap().check.as_str() {
            "stop" => crate::STOP_REQUESTED.store(true, Ordering::Release),
            "suspend" => ctx.lifecycle.borrow_mut().trial_suspend(true), _ => (),
        }
    });
}
pub fn verify_deferred() {
    context(|ctx| {
        assert!(ctx.turn.get() > ctx.attachment.get().unwrap() && ctx.lifecycle.borrow().application_work_allowed());
        assert!(!crate::STOP_REQUESTED.load(Ordering::Acquire));
        assert!(CONFIG.get().unwrap().check != "suspend" || ctx.blocked.get());
        ctx.verified.set(true);
    });
}
pub fn blocked() {
    context(|ctx| {
        assert!(CONFIG.get().unwrap().check == "suspend" && !ctx.verified.get());
        assert!(!ctx.lifecycle.borrow().application_work_allowed());
        if !ctx.blocked.replace(true) {
            reader_timing::mark("attach_trial_suspended_dispatch");
            let ui = ctx.ui.clone();
            let lifecycle = ctx.lifecycle.clone();
            slint::Timer::single_shot(Duration::ZERO, move || {
                lifecycle.borrow_mut().trial_suspend(false);
                ui.unwrap().global::<Commands>().invoke_job_ready();
            });
        }
    });
}

type Callback = Box<dyn FnOnce() + Send>;
type Message = Option<Callback>;
struct Proxy(Sender<Message>);
impl EventLoopProxy for Proxy {
    fn quit_event_loop(&self) -> Result<(), slint::EventLoopError> {
        self.0.send(None).map_err(|_| slint::EventLoopError::EventLoopTerminated)
    }
    fn invoke_from_event_loop(&self, event: Callback) -> Result<(), slint::EventLoopError> {
        self.0.send(Some(event)).map_err(|_| slint::EventLoopError::EventLoopTerminated)
    }
}
struct Display { window: Rc<MinimalSoftwareWindow>, sender: Sender<Message>, receiver: Receiver<Message>, size: (u32,u32) }
impl slint::platform::Platform for Display {
    fn create_window_adapter(&self) -> Result<Rc<dyn WindowAdapter>, slint::PlatformError> { Ok(self.window.clone()) }
    fn new_event_loop_proxy(&self) -> Option<Box<dyn EventLoopProxy>> { Some(Box::new(Proxy(self.sender.clone()))) }
    fn run_event_loop(&self) -> Result<(), slint::PlatformError> {
        let mut first = false;
        let mut shelf = false;
        let mut pending = Vec::new();
        let mut pixels = SharedPixelBuffer::<slint::Rgb8Pixel>::new(self.size.0, self.size.1);
        let deadline = Instant::now() + Duration::from_secs(8);
        loop {
            assert!(Instant::now() < deadline, "No completed software visit");
            pending.extend(self.receiver.try_iter());
            for message in pending.drain(..) {
                if let Some(callback) = message { callback(); } else {
                    assert!(first && (shelf || CONFIG.get().unwrap().check == "stop"));
                    return Ok(());
                }
            }
            slint::platform::update_timers_and_animations();
            self.window.draw_if_needed(|renderer| {
                let region = renderer.render(pixels.make_mut_slice(), self.size.0 as usize);
                let size = region.bounding_box_size();
                if size.width == 0 || size.height == 0 { return; }
                context(|ctx| {
                    let ui = ctx.ui.unwrap();
                    let state = ui.global::<State>();
                    if !first {
                        assert!(state.get_booting() && (size.width, size.height) == self.size);
                        reader_timing::mark("attach_trial_first_full_draw");
                        first = true;
                    } else if !shelf && !state.get_booting() {
                        reader_timing::mark("attach_trial_first_library_draw");
                        assert_eq!((size.width, size.height), self.size);
                        assert!(ctx.verified.get() && state.get_library_count() == 6 && state.get_books().row_count() == 6);
                        let app = ctx.app.borrow().clone().unwrap();
                        assert_eq!(app.borrow().startup_library_log_projection(), Some((state.get_large_screen(), true, true)));
                        assert_eq!(app.borrow().startup_cover_projection(), Some((7, 0, true)));
                        let ink = pixels.as_slice().iter().filter(|pixel| pixel.r < 96).count();
                        assert!(ink > 1000 && ink < pixels.as_slice().len() * 3 / 4);
                        fs::write(CONFIG.get().unwrap().directory.join("library.rgb"), pixels.as_bytes()).unwrap();
                        let theme = ui.global::<Theme>();
                        let position = slint::LogicalPosition::new(self.size.0 as f32 - theme.get_margin() - 1.5 * theme.get_touch(),
                                                                  theme.get_status_height() + 0.5 * theme.get_touch());
                        ui.window().try_dispatch_event(WindowEvent::PointerPressed{ position, button: PointerEventButton::Left }).unwrap();
                        ui.window().try_dispatch_event(WindowEvent::PointerReleased{ position, button: PointerEventButton::Left }).unwrap();
                        assert!(state.get_modal() == "choices" && state.get_modal_context() == "library-filter");
                        app.borrow_mut().dispatch("panel", "close", "").unwrap();
                        assert!(state.get_modal().is_empty() && state.get_route() == "library");
                        reader_timing::mark("attach_trial_interaction_verified");
                        shelf = true;
                    }
                });
            });
            let timeout = slint::platform::duration_until_next_timer_update().unwrap_or(Duration::from_secs(1))
                .min(deadline.saturating_duration_since(Instant::now()));
            if let Ok(message) = self.receiver.recv_timeout(timeout) { pending.push(message); }
        }
    }
}
pub fn install(profile: DeviceProfile) -> Result<()> {
    let (sender, receiver) = mpsc::channel();
    let size = profile.logical_size(false);
    let window = MinimalSoftwareWindow::new(RepaintBufferType::NewBuffer);
    window.set_size(slint::PhysicalSize::new(size.0, size.1));
    slint::platform::set_platform(Box::new(Display { window, sender, receiver, size }))?;
    Ok(())
}
