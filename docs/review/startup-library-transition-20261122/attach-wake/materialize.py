"""Instrument a disposable copy, not the production checkout, for the software UI trial."""

from pathlib import Path
import shutil
import sys


def replace(text, old, new):
    if text.count(old) != 1:
        raise ValueError('Unexpected or ambiguous source anchor: ' + old.splitlines()[0])
    return text.replace(old, new)


def materialize(root):
    if str(root.resolve()) != '/work/source' or not Path('/work/study').is_dir():
        raise ValueError('A private Ubuntu runner workspace is required')
    main, startup, lifecycle = (root / 'src' / name for name in ('main.rs', 'bootstrap_startup.rs', 'bootstrap_lifecycle.rs'))
    main_src, start_src, life_src = (path.read_text() for path in (main, startup, lifecycle))
    begin = '    let process_entry = Instant::now();'
    main_src = replace(main_src, begin, '    attach_trial::prepare().expect("fixture initialization");\n' + begin)
    main_src = 'mod attach_trial;\n' + main_src
    main_src = replace(main_src, '    platform.install_backend(Fonts::bootstrap_bytes())?;',
                       '    attach_trial::install(arguments.device)?;\n    platform.set_startup_render_priority(true);')
    main_src = replace(main_src, '    platform.install_input_observer(ui.window(), pointer_observer.clone())?;', '')
    begin = '    bootstrap_metrics::observe('
    main_src = replace(main_src, begin, '    attach_trial::bind(&ui, &app, &lifecycle);\n' + begin)
    begin = '            let result = startup.borrow_mut().advance('
    main_src = replace(main_src, begin, '            attach_trial::enter_dispatch();\n' + begin)
    begin = '            if !lifecycle.borrow().application_work_allowed() {\n                if let Some(app) = application {'
    main_src = replace(main_src, begin, begin.replace('                if let', '                attach_trial::blocked();\n                if let'))
    old = '            aidoku_kindle::reader_timing::mark("application_attached");\n            self.schedule();\n            return Ok(false);'
    start_src = replace(start_src, old, old.replace('            self.schedule();', '''            crate::attach_trial::attached();
            if crate::attach_trial::direct() { self.wake.request(); } else { self.schedule(); }'''))
    old = '        let app = attached.expect("The startup application is attached");'
    start_src = replace(start_src, old, '        crate::attach_trial::verify_deferred();\n' + old)
    old = '    pub fn application_work_allowed(&self) -> bool {'
    life_src = replace(life_src, old, '''    pub(crate) fn trial_suspend(&mut self, suspended: bool) {
        self.phase = if suspended { Phase::Suspended } else { Phase::Active };
    }

''' + old)
    fixture = root / 'src/attach_trial.rs'
    if fixture.exists():
        raise ValueError('Refusing to overwrite an existing study')
    shutil.copyfile(Path(__file__).with_name('fixture.rs'), fixture)
    for path, contents in ((main, main_src), (startup, start_src), (lifecycle, life_src)):
        path.write_text(contents)


if __name__ == '__main__':
    materialize(Path(sys.argv[1]))
