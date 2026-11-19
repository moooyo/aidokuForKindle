//! Isolated host measurement; never compiled into a distributed application.

use super::*;
use crate::{platform::DeviceProfile, startup_options::StartupOptions};
use slint::platform::software_renderer::{MinimalSoftwareWindow, RepaintBufferType};

struct Headless(Rc<MinimalSoftwareWindow>);

impl slint::platform::Platform for Headless {
    fn create_window_adapter(&self) -> std::result::Result<Rc<dyn slint::platform::WindowAdapter>, slint::PlatformError> {
        Ok(self.0.clone())
    }
}

fn spin(app: &mut App, done: impl Fn(&App) -> bool) -> Result<()> {
    let start = Instant::now();
    loop {
        app.poll();
        if done(app) { return Ok(()); }
        anyhow::ensure!(start.elapsed() < Duration::from_secs(30), "Controller wait timed out: {}", app.message);
        std::thread::sleep(Duration::from_millis(2));
    }
}

fn complete_first_page(app: &App) -> bool {
    app.visible_cover_ids().len() == 8 && app.cover_paths.len() == 8 && !app.cover_work_pending()
}

fn elapsed_ms(start: Instant) -> f64 { start.elapsed().as_secs_f64() * 1000.0 }

fn ingest(app: &mut App, root: &Path) -> Result<Value> {
    app.dispatch("library", "sort", "title")?;
    app.dispatch("library", "sort-direction", "descending")?;
    let start = Instant::now();
    app.import_local(&root.join("comics"))?;
    spin(app, |a| a.ui.upgrade().unwrap().global::<State>().get_library_count() == 9)?;
    let metadata_ms = elapsed_ms(start);
    spin(app, complete_first_page)?;
    let visible_workers_ms = elapsed_ms(start);
    spin(app, |a| a.startup_cover_projection().is_some_and(|(slots, images, pending)| slots > 0 && images == slots && !pending))?;
    let visible_model_ms = elapsed_ms(start);
    let visible = app.visible_cover_ids();
    let books = app.store.books()?;
    let target = books.iter().find(|b| b.title == "Fixture S04").context("Missing hidden target")?;
    anyhow::ensure!(!visible.contains(&target.id) && !app.cover_paths.contains_key(&target.id), "Target was visible during import");
    let initial = books.iter().find(|b| b.title == "Fixture S05").context("Missing current book")?;
    anyhow::ensure!(app.current_book_id.as_deref() == Some(initial.id.as_str()), "Unexpected imported selection");
    spin(app, |a| a.import_thumbnails.pending.is_empty() && a.import_thumbnails.running.is_none())?;
    let settled_ms = elapsed_ms(start);
    // Keep the hero the same across timestamp boundaries on the next launch.
    let chapter = app.store.chapters(&initial.id)?.remove(0);
    app.store.save_progress(&Progress { book_id: initial.id.clone(), chapter_id: chapter.id, page: 1, total: 2, updated_at: 1, completed: false })?;
    Ok(json!({"metadata_ms": metadata_ms, "visible_workers_ms": visible_workers_ms, "visible_model_ms": visible_model_ms,
        "settled_ms": settled_ms, "target_hidden": true, "disk_entries": app.disk_cache.stats().entries,
        "target_source_sha256": format!("{:x}", Sha256::digest(fs::read(&target.cover)?))}))
}

fn reopen(app: &mut App) -> Result<Value> {
    spin(app, complete_first_page)?;
    if std::env::var("AIDOKU_NATIVE_IMPORT_TURN")? == "quiet" {
        spin(app, |a| a.startup_cover_projection().is_some_and(|(slots, images, pending)| slots > 0 && images == slots && !pending))?;
        // The user can also turn immediately; report both boundaries, without
        // charging the scripted reading time to the navigation itself.
        let quiet = Instant::now() + Duration::from_millis(400);
        spin(app, |_| Instant::now() >= quiet)?;
    }
    let b = app.store.books()?.into_iter().find(|b| b.title == "Fixture S04").context("Missing hidden target")?;
    anyhow::ensure!(!app.visible_cover_ids().contains(&b.id), "Target was visible before the page turn");
    let ui = app.ui.upgrade().context("Lost test window")?;
    let start = Instant::now();
    app.dispatch("page", "turn", "1")?;
    app.refresh_visible_covers()?;
    anyhow::ensure!(app.page == 2 && app.cover_pending.contains(&b.id) && app.cover_inflight.is_some(), "Target worker did not start");
    // Observe the actual foreground worker result without synthesizing it, then
    // return every message in arrival order for the normal UI acceptance path.
    let mut received = Vec::new();
    let (ready_ms, reused, dimensions, pixels) = loop {
        let message = app.job_rx.recv_timeout(Duration::from_secs(15))?;
        let result = if let Ok(JobResult::Cover { id, image, local_derivative_hit, .. }) = &message.result
            && id == &b.id {
            Some((elapsed_ms(start), *local_derivative_hit, [image.width(), image.height()], image.clone()))
        } else { None };
        received.push(message);
        if let Some(result) = result { break result; }
    };
    for msg in received { app.job_tx.send(msg).map_err(|_| anyhow::anyhow!("Lost worker channel"))?; }
    spin(app, |_| {
        let rows = ui.global::<State>().get_books();
        rows.row_data(0).is_some_and(|row| row.id.as_str() == b.id && row.cover.size().width > 0)
    })?;
    let model_ms = elapsed_ms(start);
    anyhow::ensure!(app.cover_paths.contains_key(&b.id), "Target texture was not accepted");
    Ok(json!({"page_turn_to_worker_ready_ms": ready_ms, "page_turn_to_model_ms": model_ms,
        "disk_reused": reused, "dimensions": dimensions, "rgb_sha256": format!("{:x}", Sha256::digest(pixels.as_bytes()))}))
}

#[test]
#[ignore = "spawned separately for each phase by the synthetic benchmark driver"]
fn synthetic_phase() -> Result<()> {
    let root = PathBuf::from(std::env::var("AIDOKU_NATIVE_IMPORT_BENCH")?);
    anyhow::ensure!(root.join("synthetic-bench-only").is_file(), "The driver must create a private fixture");
    let phase = std::env::var("AIDOKU_NATIVE_IMPORT_PHASE")?;
    let data = root.join("data");
    slint::platform::set_platform(Box::new(Headless(MinimalSoftwareWindow::new(RepaintBufferType::ReusedBuffer))))?;
    let window = AppWindow::new()?;
    let app = App::attach(&window, AppOptions {
        startup_options: StartupOptions::load(&data), data_dir: data, demo: false, offline: true,
        initial_screen: "library".into(), landscape: false, resume: false,
    }, Rc::new(Platform::new(DeviceProfile::Pw5, false)?))?;
    let mut app = app.borrow_mut();
    let result = match phase.as_str() { "ingest" => ingest(&mut app, &root), "reopen" => reopen(&mut app), _ => bail!("Unknown fixture phase") }?;
    app.shutdown();
    anyhow::ensure!(app.finish_shutdown(), "Application cleanup failed");
    fs::write(root.join(format!("{phase}.json")), serde_json::to_vec_pretty(&result)?)?;
    Ok(())
}
