"""Extend the frozen release Gray8 fixture with an untimed natural-damage state walk."""

from pathlib import Path


def prepare(source, extra):
    if '#[path = "../vendor/slint-backend-kindle/src/gray_target.rs"]' not in source:
        raise ValueError('The release Gray8 stock fixture is required')
    old = '                        ui.window().try_dispatch_event(WindowEvent::PointerPressed{ position, button: PointerEventButton::Left }).unwrap();'
    variant = '''                        if std::env::var_os("AIDOKU_COMPOSITION_STATES").is_some() {
                            capture_composition_states(&ui, renderer, &mut pixels, self.size, position, case);
                        } else {
''' + old
    close = '                        reader_timing::mark("attach_trial_interaction_verified");'
    if source.count(old) != 1 or source.count(close) != 1:
        raise ValueError('Changed pointer/return guards')
    return source.replace(old, variant).replace(close, close + '\n                        }') + '\n' + extra


if __name__ == '__main__':
    work = Path('/work')
    file = work / 'source/src/attach_trial.rs'
    if not (work / 'programs/stock').is_file() or file.resolve() != file:
        raise ValueError('The original shared stock must already have been frozen')
    file.write_text(prepare(file.read_text(), Path(__file__).with_name('states.rs').read_text()))
