#!/usr/bin/env python3
"""Materialize an early metadata readiness probe, not a direct-render build."""

from pathlib import Path
import sys

source = Path(sys.argv[1]).resolve(strict=True)
assert source.name == 'source' and source.parent.name.startswith('aidoku-first-screen-sequenced-')


def change(text, before, after):
    assert text.count(before) == 1, before
    return text.replace(before, after)


startup = source / 'src/bootstrap_startup.rs'
s = startup.read_text()
begin = s.index('        let options = self\n', s.index('    fn start_workers(&mut self) -> Result<()> {'))
fonts = s.index('        self.spawn("aidoku-fonts"', begin)
metadata = s.index('        self.spawn("aidoku-startup"', fonts)
end = s.index('        aidoku_kindle::reader_timing::mark("startup_workers_started");', metadata)
closed = s.index('    fn spawn(', end)
profile = s.index('        let profile = self.profile;\n', begin, fonts)
s = s[:begin] + s[fonts:metadata] + '''        self.start_metadata()?;
''' + s[end:closed] + '''    #[cfg(feature = "startup-stage-diagnostics")]
    pub fn probe_metadata_before_feedback(&mut self) -> Result<()> {
        if crate::bootstrap_lifecycle::startup_speculation_allowed() {
            self.start_metadata()?;
        }
        Ok(())
    }

    fn start_metadata(&mut self) -> Result<()> {
        let Some(options) = self.options.take() else {
            return Ok(());
        };
''' + s[profile:fonts] + s[metadata:end] + '''        aidoku_kindle::reader_timing::mark("startup_metadata_worker_launched");
        Ok(())
    }

''' + s[closed:]
assert s.count('App::prepare_with_cover_hints(') == 1
assert s.count('"aidoku-startup"') == 1
startup.write_text(s)

main = source / 'src/main.rs'
s = main.read_text()
begin = s.index('    let app: ApplicationSlot = Rc::new(RefCell::new(None));')
end = s.index('    {\n        let ui_weak = ui.as_weak();', begin)
startup_binding = s[begin:end]
s = s[:begin] + s[end:]
startup_binding = change(startup_binding, '            data_dir,\n', '            data_dir: data_dir.clone(),\n')
s = change(s, '    let ui = AppWindow::new().context("Create the application window")?;\n', '''    let ui = AppWindow::new().context("Create the application window")?;
''' + startup_binding + '''    #[cfg(feature = "startup-stage-diagnostics")]
    if prime_library
        && startup_options.resident_seconds == 0
        && !arguments.landscape
        && std::env::var_os("AIDOKU_TEST_EARLY_METADATA").as_deref() == Some("1".as_ref())
    {
        aidoku_kindle::reader_timing::mark("startup_early_metadata_admitted");
        startup.borrow_mut().probe_metadata_before_feedback()?;
    }
''')
# Keep both control and probe using the exact same executable and startup path.
main.write_text(s)

print('Prepared opt-in early metadata readiness probe; initial scene, fonts, input and attach remain unchanged.')
