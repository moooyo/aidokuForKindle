"""Instrument only the private, stock Slint release Gray8 fixture's draw boundary."""

from pathlib import Path


def replace(source, original, replacement):
    if source.count(original) != 1:
        raise ValueError(f'Unexpected or ambiguous Gray8 fixture: {original}')
    return source.replace(original, replacement)


def prepare(fixture):
    source = fixture.read_text()
    source = replace(source, 'type Callback = Box<dyn FnOnce() + Send>;', '''fn release_draw_clock_ns() -> u64 {
    let mut value: libc::timespec = unsafe { std::mem::zeroed() };
    assert_eq!(unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut value) }, 0);
    value.tv_sec as u64 * 1_000_000_000 + value.tv_nsec as u64
}

type Callback = Box<dyn FnOnce() + Send>;''')
    source = replace(source, '        let mut first = false;', '''        let sample = std::env::var_os("AIDOKU_RELEASE_DRAW_PROFILE").is_some();
        if sample {
            let mut action: libc::sigaction = unsafe { std::mem::zeroed() };
            assert_eq!(unsafe { libc::sigaction(libc::SIGURG, std::ptr::null(), &mut action) }, 0);
            assert!(action.sa_sigaction != libc::SIG_IGN && action.sa_sigaction != libc::SIG_DFL);
        }
        let mut first = false;''')
    source = replace(source, '                let draw = Instant::now();', '''                if sample && measure { assert_eq!(unsafe { libc::raise(libc::SIGURG) }, 0); }
                let draw = Instant::now();''')
    source = replace(source, '                let region = pixels.render(renderer, self.size.0 as usize);', '''                let draw_begin = if sample && measure { release_draw_clock_ns() } else { 0 };
                let region = pixels.render(renderer, self.size.0 as usize);
                let draw_end = if sample && measure { release_draw_clock_ns() } else { 0 };''')
    source = replace(source, '                let draw_ns = draw.elapsed().as_nanos();', '''                let draw_ns = draw.elapsed().as_nanos();
                if sample && measure { assert_eq!(unsafe { libc::raise(libc::SIGURG) }, 0); }''')
    source = replace(source, '                        let case = &CONFIG.get().unwrap().directory;', '''                        let case = &CONFIG.get().unwrap().directory;
                        if sample { fs::write(case.join("draw-window"), format!("{draw_begin} {draw_end}")).unwrap(); }''')
    fixture.write_text(source)


if __name__ == '__main__':
    prepare(Path('/work/source/src/attach_trial.rs'))
