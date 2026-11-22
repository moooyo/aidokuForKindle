const SURFACES: [&str; 10] = ["plain", "pressed", "filter", "dismissed", "night", "settings", "night-return", "light-return", "selection", "deselected"];

fn capture_composition_states(
    ui: &AppWindow,
    renderer: &slint::platform::software_renderer::SoftwareRenderer,
    scene: &mut native_gray::RetainedScene,
    size: (u32, u32),
    position: slint::LogicalPosition,
    case_dir: &std::path::Path,
) {
    let state = ui.global::<State>();
    let app = context(|ctx| ctx.app.borrow().clone().unwrap());
    let width = size.0 as usize;
    let area = u64::from(size.0) * u64::from(size.1);
    let mut damage_log = Vec::new();
    let mut previous = Vec::new();
    let mut original = Vec::new();
    let mut night = Vec::new();
    let mut partial = false;
    for (index, name) in SURFACES.iter().enumerate() {
        match *name {
            "plain" => (),
            "pressed" => ui.window().try_dispatch_event(WindowEvent::PointerPressed {position, button: PointerEventButton::Left}).unwrap(),
            "filter" => ui.window().try_dispatch_event(WindowEvent::PointerReleased {position, button: PointerEventButton::Left}).unwrap(),
            "dismissed" => app.borrow_mut().dispatch("panel", "close", "").unwrap(),
            "night" => app.borrow_mut().dispatch("settings", "set", "night-mode=true").unwrap(),
            "settings" => app.borrow_mut().dispatch("navigation", "open", "4").unwrap(),
            "night-return" => app.borrow_mut().dispatch("navigation", "open", "0").unwrap(),
            "light-return" => app.borrow_mut().dispatch("settings", "set", "night-mode=false").unwrap(),
            "selection" => {
                let book = state.get_books().row_data(0).unwrap();
                app.borrow_mut().dispatch("library", "select", book.id.as_str()).unwrap();
            }
            "deselected" => app.borrow_mut().dispatch("library", "finish-selection", "").unwrap(),
            _ => unreachable!(),
        }
        assert_eq!(state.get_route().as_str(), if *name == "settings" {"settings"} else {"library"});
        assert_eq!(state.get_modal() == "choices", *name == "filter");
        if *name == "filter" { assert_eq!(state.get_modal_context().as_str(), "library-filter"); }
        assert_eq!(state.get_night_mode(), matches!(*name, "night" | "settings" | "night-return"));
        assert_eq!(state.get_selection_mode(), *name == "selection");
        if index != 0 {
            assert!(matches!(renderer.repaint_buffer_type(), RepaintBufferType::ReusedBuffer));
            let region = scene.render(renderer, width);
            let dirty: u64 = region.iter().map(|(_, item)| u64::from(item.width) * u64::from(item.height)).sum();
            assert!(dirty > 0 && dirty <= area, "No valid damage for {name}");
            partial |= dirty < area;
            damage_log.push(format!("{name} {dirty}"));
        }
        let native_gray::ScenePixels::Gray(values) = scene.pixels() else { panic!("Non-gray {name}") };
        let bytes: Vec<_> = values.iter().map(|pixel| pixel.value()).collect();
        assert_eq!(bytes.len() as u64, area);
        if index > 0 { assert!(previous != bytes, "State {name} did not change any pixels"); }
        if *name == "plain" { original.clone_from(&bytes); }
        if *name == "night" { night.clone_from(&bytes); }
        if *name == "dismissed" || *name == "light-return" || *name == "deselected" { assert!(original == bytes, "{name} did not restore the Library"); }
        if *name == "night-return" { assert!(night == bytes, "Settings left stale pixels on the night Library"); }
        fs::write(case_dir.join(format!("{name}.gray")), &bytes).unwrap();
        previous = bytes;
    }
    assert!(partial, "The state proof must exercise a genuine partial repaint");
    fs::write(case_dir.join("state-damage"), damage_log.join("\n")).unwrap();
    reader_timing::mark("attach_trial_interaction_verified");
}
