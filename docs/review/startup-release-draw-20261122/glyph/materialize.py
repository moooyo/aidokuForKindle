"""Build a diagnostic source copy only; never change the registry or the timed stock profile."""

import argparse
from pathlib import Path
import shutil


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('Unexpected locked source anchor: ' + old.splitlines()[0])
    return text.replace(old, new)


def patched_vector(source):
    text = once(source, '            if let Some(entry) = cache.get(&cache_key) {\n                return Some(entry.clone());',
                '''            let diag_key = (cache_key.0, cache_key.1, cache_key.2.get(), cache_key.3.get(), cache_key.4);
            let old_len = cache.len();
            let old_weight = cache.weight();
            if let Some(entry) = cache.get(&cache_key) {
                crate::aidoku_glyph_counts::lookup(diag_key, true, old_weight);
                return Some(entry.clone());''')
    text = once(text, '            let glyph = {\n                let font_ref = self.swash_font_ref();',
                '            crate::aidoku_glyph_counts::lookup(diag_key, false, old_weight);\n            let glyph = {\n                let font_ref = self.swash_font_ref();')
    text = once(text, '                let image = swash::scale::Render::new(', '                let Some(image) = swash::scale::Render::new(')
    text = once(text, '                    .render(&mut scaler, glyph_id.get())?;', '''                    .render(&mut scaler, glyph_id.get()) else {
                        crate::aidoku_glyph_counts::no_mask(diag_key);
                        return None;
                    };''')
    return once(text, '                cache.put_with_weight(cache_key, glyph.clone()).ok();', '''                let inserted = cache.put_with_weight(cache_key, glyph.clone()).is_ok();
                crate::aidoku_glyph_counts::stored(diag_key, glyph.alpha_map.is_empty(), inserted, old_len, cache.len(), cache.weight());''')


def fixture_source(source):
    source = once(source, '                let draw = Instant::now();',
                  '                if measure { i_slint_renderer_software::aidoku_glyph_counts::begin(); }\n                let draw = Instant::now();')
    return once(source, '                let draw_ns = draw.elapsed().as_nanos();', '''                let draw_ns = draw.elapsed().as_nanos();
                if measure {
                    let weight = i_slint_renderer_software::aidoku_glyph_counts::peak_alpha_bytes();
                    let counters = i_slint_renderer_software::aidoku_glyph_counts::finish();
                    eprintln!("AIDOKU_GLYPH_DIAG1 {:?} {}", counters, weight);
                }''')


def review(upstream):
    assert upstream.name == 'i-slint-renderer-software-1.17.1'
    patched = patched_vector((upstream / 'fonts/vectorfont.rs').read_text())
    manifest = (upstream / 'Cargo.toml').read_text()
    assert 'name = "i-slint-renderer-software"' in manifest and 'version = "1.17.1"' in manifest
    assert 'thread_local!(static GLYPH_CACHE' in patched and '1024 * 1024' in patched
    lib = once((upstream / 'lib.rs').read_text(), 'mod fonts;', 'mod fonts;\npub mod aidoku_glyph_counts;')
    return patched, lib


def prepare(upstream, source_name):
    root = Path('/work')
    destination = root / 'glyph-renderer'
    fixture, product = root / source_name / 'src/attach_trial.rs', root / source_name / 'Cargo.toml'
    if not (root / 'glyph-count-only').is_file() or destination.exists():
        raise ValueError('Use a new dedicated workspace marked glyph-count-only')
    if not all(path.resolve() == path for path in (root, fixture, product)):
        raise ValueError('Private ordinary sources are required')
    vector, lib = review(upstream)
    harness = fixture_source(fixture.read_text())
    manifest = product.read_text()
    if '[patch.crates-io]' in manifest or 'i-slint-renderer-software =' in manifest:
        raise ValueError('Expected the pristine-stock Gray8 fixture, not the previous shaping experiment')
    manifest = once(manifest, '[dependencies]\n', '[dependencies]\ni-slint-renderer-software = { version = "=1.17.1", default-features = false }\n')
    shutil.copytree(upstream, destination)
    (destination / 'fonts/vectorfont.rs').write_text(vector)
    (destination / 'lib.rs').write_text(lib)
    shutil.copyfile(Path(__file__).with_name('counts.rs'), destination / 'aidoku_glyph_counts.rs')
    fixture.write_text(harness)
    product.write_text(manifest + '\n[patch.crates-io]\ni-slint-renderer-software = { path = "../glyph-renderer" }\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['check', 'prepare'])
    parser.add_argument('upstream', type=Path)
    parser.add_argument('--source', choices=['source', 'glyph-source'], default='source')
    args = parser.parse_args()
    if args.mode == 'check':
        review(args.upstream)
        print('The locked renderer matches the aggregate diagnostic anchors; no files changed.')
    else:
        prepare(args.upstream, args.source)
