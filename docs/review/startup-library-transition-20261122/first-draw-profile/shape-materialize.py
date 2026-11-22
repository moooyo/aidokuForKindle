"""Instrument only a private Slint 1.17.1 copy and the isolated six-book startup fixture."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def edit(text, before, after):
    if text.count(before) != 1:
        raise ValueError(f'Invalid locked Slint/fixture source: {before}')
    return text.replace(before, after)


def instrument(core, fixture):
    contents = core.read_text()
    contents = edit(contents, 'pub use parley;', '#[path = "shape-probe.rs"]\npub mod shape_probe;\npub use parley;')
    contents = edit(contents, '            builder.build(text)', '            shape_probe::built();\n            builder.build(text)')
    contents = edit(contents, '    let scale_factor = renderer.scale_factor()?;\n\n    // Evaluate properties',
        '    let _shape_scope = shape_probe::enter(shape_probe::Path::Measure, max_width);\n    let scale_factor = renderer.scale_factor()?;\n\n    // Evaluate properties')
    contents = edit(contents, '    let paragraphs_without_linebreaks =\n        create_text_paragraphs(&layout_builder, &mut font_ctx, text, None, Color::default());',
        '    shape_probe::key(Some(item_rc), &text, &layout_builder.font_request, scale_factor, text_wrap, None);\n    let paragraphs_without_linebreaks =\n        create_text_paragraphs(&layout_builder, &mut font_ctx, text, None, Color::default());')
    contents = edit(contents, '    let mut guard =\n        get_or_create_text_paragraphs(cache, item_rc, text, scale_factor, &mut font_ctx);',
        '    let shape_scope = shape_probe::enter(shape_probe::Path::Paint, Some(max_width));\n    let mut guard =\n        get_or_create_text_paragraphs(cache, item_rc, text, scale_factor, &mut font_ctx);\n    drop(shape_scope);')
    contents = edit(contents, '    create_text_paragraphs(&builder, font_context, text.text(), None, text.link_color())',
        '    let content = text.text();\n    shape_probe::key(item_rc, &content, &builder.font_request, scale_factor, builder.text_wrap, builder.stroke);\n    create_text_paragraphs(&builder, font_context, content, None, text.link_color())')
    core.write_text(contents)
    shutil.copyfile(Path(__file__).with_name('shape-probe.rs'), core.with_name('shape-probe.rs'))
    contents = fixture.read_text()
    anchor = '                let region = renderer.render(pixels.make_mut_slice(), self.size.0 as usize);'
    contents = edit(contents, anchor, '''                let profile = first && !shelf && context(|ctx| !ctx.ui.unwrap().global::<State>().get_booting());
                if profile { i_slint_core::textlayout::sharedparley::shape_probe::begin(); }
''' + anchor + '''
                if profile {
                    let counts = i_slint_core::textlayout::sharedparley::shape_probe::finish();
                    std::fs::write(CONFIG.get().unwrap().directory.join("shape-counts"), format!("{counts:?}")).unwrap();
                }''')
    fixture.write_text(contents)


def run():
    columns = ('measure_api measure_builds measure_plain_nonempty_built paint_api paint_builds '
               'paint_plain_nonempty_built earlier_same_item_text same_shape_inputs same_shape_and_width '
               'same_shape_unbounded_measure mismatched_wrap mismatched_font_scale_stroke same_text_other_item').split()
    result = {'scope': 'test-env x86_64 first Library software draw; synthetic six-book shelf; observation only, no added cache',
              'columns': columns, 'visits': {}}
    for profile in ('scribe', 'pw6', 'pw6', 'scribe'):
        with tempfile.TemporaryDirectory() as scratch:
            case = Path(scratch)
            trace = case / 'trace.json'
            env = dict(os.environ, AIDOKU_ATTACH_TRIAL_CASE=scratch, AIDOKU_ATTACH_TRIAL_CHECK='normal',
                       AIDOKU_ATTACH_TRIAL_ARM='control', TZ='UTC')
            subprocess.run(['/work/target/debug/aidoku', '--offline', '--device', profile, '--data-dir', case / 'data',
                            '--exit-after-ms', '100', '--performance-trace', trace], check=True, timeout=20, env=env, capture_output=True)
            observation = json.loads(trace.read_bytes())
            names = [event['name'] for event in observation['events']]
            markers = ['attach_trial_first_full_draw', 'startup_ready', 'attach_trial_first_library_draw', 'attach_trial_interaction_verified']
            if not (observation['enabled'] and observation['finished'] and observation['success'] and not observation['dropped_events']
                    and all(names.count(name) == 1 for name in markers) and [names.index(name) for name in markers] == sorted(names.index(name) for name in markers)):
                raise ValueError('Missing same-visit software frame or pointer interaction')
            if (case / 'library.rgb').stat().st_size != (930 * 1240 if profile == 'scribe' else 632 * 840) * 3:
                raise ValueError('Invalid software frame geometry')
            counts = json.loads((case / 'shape-counts').read_bytes())
            if len(counts) != len(columns) or any(type(value) is not int or value < 0 for value in counts):
                raise ValueError('Invalid count projection')
            result['visits'].setdefault(profile, []).append(counts)
    with Path('/work/shape-report.json').open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')


if __name__ == '__main__':
    if sys.argv[1:] == ['run']:
        run()
    elif sys.argv[1:] == ['prepare']:
        root = Path('/work')
        cargo = root / 'source/Cargo.toml'
        contents = cargo.read_text()
        if '[patch.crates-io]' in contents or 'i-slint-core' in contents:
            raise ValueError('Expected the original private application manifest')
        contents = edit(contents, '[dependencies]', '''[patch.crates-io]
i-slint-core = { path = "../local-slint" }

[dependencies]
i-slint-core = { version = "=1.17.1", default-features = false }''')
        instrument(root / 'local-slint/textlayout/sharedparley.rs', root / 'source/src/attach_trial.rs')
        cargo.write_text(contents)
    else:
        raise SystemExit('Use prepare or run in a private Ubuntu runner workspace')
