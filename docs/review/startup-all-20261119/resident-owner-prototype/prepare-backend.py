#!/usr/bin/env python3
"""Apply the experimental backend seam to a new disposable copy, never in place."""

import argparse
import shutil
from pathlib import Path


def replace(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'Unsupported source layout: {old[:85]!r} has {text.count(old)} matches')
    return text.replace(old, new)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    root = args.source.resolve()
    out = args.destination.resolve()
    if root == out or root in out.parents or out.exists():
        raise ValueError('The destination must be a new directory outside the source')
    shutil.copytree(root, out, ignore=shutil.ignore_patterns('target', '.git'))
    here = Path(__file__).resolve().parent
    rs = out / 'src'
    shutil.copyfile(here / 'resident-session.rs', rs / 'resident_session.rs')
    shutil.copyfile(here / 'headless-session.rs', rs / 'resident_session_tests.rs')

    file = rs / 'lib.rs'
    s = replace(file.read_text(), 'mod render_control;', 'mod resident_session;\nmod render_control;')
    file.write_text(replace(s, 'pub use render_control::', 'pub use resident_session::LibraryIoClosed;\npub use render_control::'))

    file = rs / 'lifecycle.rs'
    s = replace(file.read_text(), '    EventLoopTerminated,\n', '    EventLoopTerminated,\n    LibraryTransition,\n')
    s = replace(s, '            Self::EventLoopTerminated =>',
                '            Self::LibraryTransition => "the native Library session needs a new grant",\n            Self::EventLoopTerminated =>')
    s = replace(s, 'pub(crate) struct LifecycleState {',
                'pub(crate) struct LifecycleState {\n    pub(crate) library: crate::resident_session::Sessions,')
    s = replace(s, '    fn ensure_live(&self)', '    pub(crate) fn ensure_live(&self)')
    s = replace(s, '        self.ensure_live()?;\n        if observed.rotation > 3 {', '''        self.ensure_live()?;
        self.state.library.require_showing(|| {
            self.apply_native_framebuffer_portrait_calibration(holding, scanout, observed)
        })?
    }

    pub(crate) fn apply_native_framebuffer_portrait_calibration(
        &self,
        holding: NativePortraitOrientation,
        scanout: NativePortraitOrientation,
        observed: NativeFramebufferObservation,
    ) -> Result<(), LifecycleError> {
        self.ensure_live()?;
        if observed.rotation > 3 {''')
    s = replace(s, '''        self.state.parked.store(false, Ordering::Release);
        self.state.pause_requested.store(true, Ordering::Release);
        let _ = self.state.sleep_presentation.cancel_pending();
        crate::wakeup::poke(&self.wake);
        Ok(())''', '''        self.state.library.require_showing(|| {
            self.state.parked.store(false, Ordering::Release);
            self.state.pause_requested.store(true, Ordering::Release);
            let _ = self.state.sleep_presentation.cancel_pending();
            crate::wakeup::poke(&self.wake);
        })''')
    s = replace(s, '''        let status = self.status();
        if !status.render_paused || !status.input_released {
            return Err(LifecycleError::ResourcesNotReleased);
        }
        self.state.parked.store(true, Ordering::Release);
        crate::wakeup::poke(&self.wake);
        Ok(())''', '''        self.state.library.require_showing(|| {
            let status = self.status();
            if !status.render_paused || !status.input_released {
                return Err(LifecycleError::ResourcesNotReleased);
            }
            self.state.parked.store(true, Ordering::Release);
            crate::wakeup::poke(&self.wake);
            Ok(())
        })?''')
    a = s.index('    pub fn resume(&self)')
    b = s.index('    pub fn status(&self)', a)
    segment = s[a:b]
    segment = replace(segment, '        self.ensure_live()?;\n',
        '        self.ensure_live()?;\n        self.state.library.require_showing(|| self.resume_flags())\n    }\n\n    pub(crate) fn resume_flags(&self) {\n')
    s = s[:a] + replace(segment, '        Ok(())\n    }', '    }') + s[b:]
    file.write_text(s)

    file = rs / 'portrait.rs'
    file.write_text(replace(file.read_text(), 'impl InputEpoch {', '''impl InputEpoch {
    pub(crate) fn following(value: u64) -> Self {
        Self { value, ..Default::default() }
    }
'''))

    file = rs / 'platform.rs'
    s = file.read_text()
    s = replace(s, 'impl KindlePlatform {', '''// Only this module can witness the end of the scope owning native I/O.
pub(crate) struct ClosedNativeSession(());

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum SessionEnd { Quit, Returned }

#[cfg(test)]
type FixtureSession = Box<dyn Fn(&KindlePlatform) -> Result<SessionEnd, PlatformError>>;

impl KindlePlatform {''')
    s = replace(s, '    clipboard: RefCell<ClipboardStore>,',
                '    clipboard: RefCell<ClipboardStore>,\n    #[cfg(test)]\n    fixture_session: RefCell<Option<FixtureSession>>,')
    s = replace(s, '            clipboard: RefCell::new(ClipboardStore::default()),',
                '            clipboard: RefCell::new(ClipboardStore::default()),\n            #[cfg(test)]\n            fixture_session: RefCell::new(None),')
    split = s.index('        if !self.wait_for_native_ownership()? {', s.index('    fn run_event_loop(&self)'))
    start = s.index('        // Serialize the pre-I/O calibration', split)
    end = s.index('\nfn duration_to_ms(', start)
    prefix = s[:split]
    original = s[start:end]
    original = original.replace('return self.event_loop_result();', 'return self.event_loop_result().map(|()| SessionEnd::Quit);')
    original = replace(original, '        let mut input_epoch = InputEpoch::default();',
        '        let mut input_epoch = InputEpoch::following(self.lifecycle.input_generation.load(Ordering::Acquire));')
    original = replace(original, '        let mut last_interaction = Instant::now();\n\n        loop {',
        '''        let mut last_interaction = Instant::now();

        loop {
            if !self.quit_flag.load(Ordering::SeqCst) && self.lifecycle.library.returning() {
                return Ok(SessionEnd::Returned);
            }''')
    original = replace(original, '        self.event_loop_result()\n    }\n}',
        '        self.event_loop_result().map(|()| SessionEnd::Quit)\n    }\n}')
    outer = '''        loop {
            if !self.wait_for_native_ownership()? {
                return self.event_loop_result();
            }
            #[cfg(test)]
            let end = match self.fixture_session.borrow().as_ref() {
                Some(fixture) => fixture(self)?,
                None => self.run_native_session()?,
            };
            #[cfg(not(test))]
            let end = self.run_native_session()?;
            match end {
                SessionEnd::Quit => return self.event_loop_result(),
                SessionEnd::Returned if self.quit_flag.load(Ordering::SeqCst) => return self.event_loop_result(),
                SessionEnd::Returned => self.lifecycle_handle().confirm_library_io_closed(ClosedNativeSession(()))
                    .map_err(|error| PlatformError::Other(error.to_string()))?,
            }
        }
    }
}

impl KindlePlatform {
    // Everything that can sample or write native devices is scoped to this call.
    fn run_native_session(&self) -> Result<SessionEnd, PlatformError> {
'''
    s = prefix + outer + original + s[end:]
    s += '\n#[cfg(test)]\n#[path = "resident_session_tests.rs"]\nmod resident_session_tests;\n'
    file.write_text(s)

    file = rs / 'framebuffer/test_support.rs'
    s = replace(file.read_text(), '    pub(crate) fn mapped_bytes_for_test', '''    pub(crate) fn raw_fd_for_session_test(&self) -> std::os::fd::RawFd {
        std::os::fd::AsRawFd::as_raw_fd(&self.file)
    }

    pub(crate) fn mapped_bytes_for_test''')
    file.write_text(s)
    file = rs / 'touch.rs'
    s = replace(file.read_text(), 'impl TouchInput {', '''impl TouchInput {
    #[cfg(test)]
    pub(crate) fn idle_socket_for_session_test(socket: std::os::unix::net::UnixStream) -> Self {
        Self { file_descriptor: std::os::fd::IntoRawFd::into_raw_fd(socket), active: false,
            state: TouchState::new(16, 16, 15.0, 15.0), reorientation: ReorientationDrain::default() }
    }
''')
    file.write_text(s)


if __name__ == '__main__':
    main()
