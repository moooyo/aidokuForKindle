"""Apply identical timing hooks, then remove only the empty category Text in B."""

import argparse
from pathlib import Path


ROOT = Path('/work/source')
NODE = '''    category-count := Text {
        x: category-label.width + (State.large-screen ? 10px : 8px); y: 1px;
        width: self.preferred-width; height: parent.height;
        text: ""; font-family: Theme.numbers;
        font-size: State.large-screen ? 15px : 13px; font-weight: 600;
        color: touch.pressed ? Theme.paper : root.selected ? Theme.ink : Theme.secondary;
        vertical-alignment: center;
    }
'''


def replace(text, before, after):
    if text.count(before) != 1:
        raise ValueError(f'Missing or ambiguous anchor: {before!r}')
    return text.replace(before, after, 1)


def instrument():
    target = ROOT / 'src/attach_trial.rs'
    source = target.read_text()
    before = '        let mut first = false;'
    source = replace(source, before, '''        let diagnostic = std::env::var_os("AIDOKU_FIRST_DRAW_GPROFNG").as_deref()
            == Some(std::ffi::OsStr::new("1"));
        if diagnostic {
            let mut handler: libc::sigaction = unsafe { std::mem::zeroed() };
            assert_eq!(unsafe { libc::sigaction(libc::SIGURG, std::ptr::null(), &mut handler) }, 0);
            assert!(handler.sa_sigaction != libc::SIG_DFL && handler.sa_sigaction != libc::SIG_IGN);
        }
''' + before)
    before = '                let region = renderer.render(pixels.make_mut_slice(), self.size.0 as usize);'
    source = replace(source, before, '''                let profile = diagnostic && first && !shelf
                    && context(|ctx| !ctx.ui.unwrap().global::<State>().get_booting());
                let buffer = pixels.make_mut_slice();
                let stride = self.size.0 as usize;
                if profile { assert_eq!(unsafe { libc::kill(libc::getpid(), libc::SIGURG) }, 0); }
                let before_render = Instant::now();
                let region = renderer.render(buffer, stride);
                let render_ns = before_render.elapsed().as_nanos();
                if profile { assert_eq!(unsafe { libc::kill(libc::getpid(), libc::SIGURG) }, 0); }''')
    before = '                        fs::write(CONFIG.get().unwrap().directory.join("library.rgb"), pixels.as_bytes()).unwrap();'
    source = replace(source, before, '''                        assert!(render_ns > 0);
                        fs::write(CONFIG.get().unwrap().directory.join("first-render-ns"), render_ns.to_string()).unwrap();
''' + before)
    target.write_text(source)


def remove_node():
    target = ROOT / 'ui/library.slint'
    original = target.read_bytes()
    needle = NODE.encode()
    if b'\r\n' in original:
        needle = needle.replace(b'\n', b'\r\n')
    changed = replace(original, needle, b'')
    with (Path('/work/category') / 'control-library.slint').open('xb') as capture:
        capture.write(original)
    target.write_bytes(changed)
    assert target.read_bytes() == original.replace(needle, b'')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('instrument', 'remove-node'))
    options = parser.parse_args()
    if ROOT.resolve() != ROOT or not (Path('/work/study') / 'fixture.rs').is_file():
        raise ValueError('Run inside the disposable Ubuntu workspace')
    (instrument if options.action == 'instrument' else remove_node)()
