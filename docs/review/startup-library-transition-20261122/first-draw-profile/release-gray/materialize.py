"""Prepare fresh stock/candidate copies for an x86 release test with the vendored Gray8 scene."""

from pathlib import Path
from runpy import run_path
import sys
import tempfile


ROOT = Path('/work')
SOURCE = ROOT / 'source'


def change(text, before, after):
    if text.count(before) != 1:
        raise ValueError(f'Unexpected frozen input: {before}')
    return text.replace(before, after)


def prepare(arm):
    fixture = SOURCE / 'src/attach_trial.rs'
    base = ROOT / 'gray/baseline-fixture.rs'
    if arm == 'stock':
        if base.exists():
            raise ValueError('Stock must be prepared once in a fresh workspace')
        base.write_bytes(fixture.read_bytes())
        platform = SOURCE / 'src/platform.rs'
        platform.write_text(change(platform.read_text(), 'let physical_scale = if self.kindle { 2 } else { 1 };', 'let physical_scale = 2;'))
    text = base.read_text()
    if arm == 'candidate':
        core = ROOT / 'local-slint/textlayout/sharedparley.rs'
        with tempfile.TemporaryDirectory() as temp:
            scratch = Path(temp) / 'fixture.rs'
            scratch.write_text(text)
            run_path(str(ROOT / 'study/preline-materialize.py'))['patch'](core, scratch)
        helper = core.with_name('preline-cache.rs')
        cache = helper.read_text().replace('[usize; 6]', '[usize; 8]').replace('counts: [0; 6]', 'counts: [0; 8]')
        cache = change(cache, 'paragraphs: paragraphs.to_vec()});', '''paragraphs: paragraphs.to_vec()});
        frame.counts[6] = frame.counts[6].max(frame.entries.len());
        frame.counts[7] += paragraphs.len();''')
        cache += '''\npub fn assert_empty() { FRAME.with_borrow(|slot| assert!(slot.is_none())); }\n'''
        helper.write_text(cache)
    text = '#[path = "../vendor/slint-backend-kindle/src/gray_target.rs"]\nmod native_gray;\n' + text
    text = change(text, '    store.save_preferences(&Preferences { language: "en".into()',
                  '    let cjk = std::env::var_os("AIDOKU_PRELINE_CJK").is_some();\n    store.save_preferences(&Preferences { language: if cjk {"zh-CN"} else {"en"}.into()')
    text = change(text, 'title: format!("Study volume {number}"),', 'title: if cjk { format!("中文书名与较长的排版测试 第{number}卷") } else { format!("Study volume {number}") },')
    text = change(text, '    let size = profile.logical_size(false);', '    let size = profile.native_size();')
    text = change(text, 'MinimalSoftwareWindow::new(RepaintBufferType::NewBuffer)', 'MinimalSoftwareWindow::new(RepaintBufferType::ReusedBuffer)')
    text = change(text, '        let mut pixels = SharedPixelBuffer::<slint::Rgb8Pixel>::new(self.size.0, self.size.1);',
                  '''        let mut pixels = native_gray::RetainedScene::new(self.size.0 as usize * self.size.1 as usize, true);
        let mut next_frame_checked = false;\n''' + ('''        let reuse = match std::env::var("AIDOKU_PRELINE_ARM").unwrap().as_str() {"off" => false, "reuse" => true, _ => panic!("Invalid trial arm")};
        let negatives = std::env::var_os("AIDOKU_PRELINE_CJK").is_some();''' if arm == 'candidate' else ''))
    text = change(text, 'assert!(first && (shelf || CONFIG.get().unwrap().check == "stop"));', 'assert!(first && shelf && next_frame_checked);')
    before = '                let region = renderer.render(pixels.make_mut_slice(), self.size.0 as usize);'
    text = change(text, before, '''                let measure = first && !shelf && context(|ctx| !ctx.ui.unwrap().global::<State>().get_booting());
                if shelf && !next_frame_checked {
                    NEXT_FRAME_CHECK
                    next_frame_checked = true;
                }
                if measure { assert_eq!(crate::FONT_INITIALIZATIONS.load(Ordering::Acquire), 1); }
                if !first { renderer.set_repaint_buffer_type(RepaintBufferType::NewBuffer); }
                let draw = Instant::now();
                BEGIN_CACHE
                let region = pixels.render(renderer, self.size.0 as usize);
                END_CACHE
                let draw_ns = draw.elapsed().as_nanos();
                if !first { renderer.set_repaint_buffer_type(RepaintBufferType::ReusedBuffer); }
                let damage_pixels = region.iter().map(|(_, area)| u64::from(area.width) * u64::from(area.height)).sum::<u64>();
                let total = u64::from(self.size.0) * u64::from(self.size.1);'''
                  .replace('BEGIN_CACHE', 'if measure { i_slint_core::textlayout::sharedparley::preline::begin(reuse, negatives); }' if arm == 'candidate' else '')
                  .replace('END_CACHE', 'let counts = measure.then(i_slint_core::textlayout::sharedparley::preline::finish);' if arm == 'candidate' else '')
                  .replace('NEXT_FRAME_CHECK', 'i_slint_core::textlayout::sharedparley::preline::assert_empty();' if arm == 'candidate' else ''))
    text = change(text, 'assert!(state.get_booting() && (size.width, size.height) == self.size);',
                  'assert!(state.get_booting() && (size.width, size.height) == self.size && damage_pixels == total);')
    text = change(text, '                        assert_eq!((size.width, size.height), self.size);',
                  '''                        assert!(size.width > 0 && size.height > 0 && damage_pixels > 0 && damage_pixels <= total);
                        assert_eq!(ui.window().scale_factor(), 2.0);
                        assert_eq!((ui.get_canvas_width() as u32 * 2, ui.get_canvas_height() as u32 * 2), self.size);
                        assert_eq!((ui.window().size().width, ui.window().size().height), self.size);
                        assert_eq!(crate::FONT_INITIALIZATIONS.load(Ordering::Acquire), 1);''')
    before = '''                        let ink = pixels.as_slice().iter().filter(|pixel| pixel.r < 96).count();
                        assert!(ink > 1000 && ink < pixels.as_slice().len() * 3 / 4);
                        fs::write(CONFIG.get().unwrap().directory.join("library.rgb"), pixels.as_bytes()).unwrap();'''
    text = change(text, before, '''                        let native_gray::ScenePixels::Gray(values) = pixels.pixels() else { panic!("Native scene fell back to RGB") };
                        let output = values.iter().map(|pixel| pixel.value()).collect::<Vec<_>>();
                        assert_eq!(output.len() as u64, total);
                        let ink = output.iter().filter(|value| **value < 96).count();
                        assert!(ink > 1000 && ink < output.len() * 3 / 4);
                        let case = &CONFIG.get().unwrap().directory;
                        fs::write(case.join("library.gray"), &output).unwrap();
                        fs::write(case.join("damage"), damage_pixels.to_string()).unwrap();
                        fs::write(case.join("draw-ns"), draw_ns.to_string()).unwrap();'''
                  + ('\n                        fs::write(case.join("preline-counts"), format!("{:?}", counts.unwrap())).unwrap();' if arm == 'candidate' else ''))
    text = change(text, 'self.size.0 as f32 - theme.get_margin()', 'ui.get_canvas_width() - theme.get_margin()')
    text = change(text, '                        shelf = true;', '                        shelf = true;\n                        ui.window().request_redraw();')
    fixture.write_text(text)


if __name__ == '__main__':
    if sys.argv[1:] not in (['stock'], ['candidate']):
        raise SystemExit('Use stock, then candidate, in a fresh private Ubuntu workspace')
    prepare(sys.argv[1])
