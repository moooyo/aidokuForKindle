"""Modify only the private Slint path dependency and the attach-wake six-book harness."""

from pathlib import Path
import shutil


def replace(text, before, after):
    if text.count(before) != 1:
        raise ValueError(f'Unexpected immutable source: {before}')
    return text.replace(before, after)


def patch(core, fixture):
    contents = core.read_text()
    contents = replace(contents, 'pub use parley;', '#[path = "preline-cache.rs"]\npub mod preline;\npub use parley;')
    contents = replace(contents, 'struct TextParagraph {', '#[derive(Clone)]\nstruct TextParagraph {')
    contents = replace(contents, '    create_text_paragraphs(&builder, font_context, text.text(), None, text.link_color())',
        '''    let content = text.text();
    if let Some(paragraphs) = preline::paint(item_rc, &content, &builder, font_context, width) { return paragraphs; }
    create_text_paragraphs(&builder, font_context, content, None, text.link_color())''')
    for name in ('shape_paragraphs', "get_or_create_text_paragraphs<'a>"):
        start = contents.index('fn ' + name + '(')
        end = contents.index(') ->', start)
        method = contents[start:end]
        contents = replace(contents, method, method + '    width: Option<LogicalLength>,\n')
    contents = replace(contents, 'shape_paragraphs(text, Some(item_rc), scale_factor, font_context)', 'shape_paragraphs(text, Some(item_rc), scale_factor, font_context, width)')
    contents = replace(contents, 'shape_paragraphs(text, item_rc, scale_factor, font_context)', 'shape_paragraphs(text, item_rc, scale_factor, font_context, width)')
    contents = replace(contents, 'get_or_create_text_paragraphs(cache, item_rc, text, scale_factor, &mut font_ctx);', 'get_or_create_text_paragraphs(cache, item_rc, text, scale_factor, &mut font_ctx, Some(max_width));')
    contents = replace(contents, 'get_or_create_text_paragraphs(cache, Some(item_rc), text, scale_factor, font_context);', 'get_or_create_text_paragraphs(cache, Some(item_rc), text, scale_factor, font_context, Some(size.width_length()));')
    contents = replace(contents, '    let paragraphs_without_linebreaks =\n        create_text_paragraphs(&layout_builder, &mut font_ctx, text, None, Color::default());',
        '''    let trial_label = preline::label(&text);
    let paragraphs_without_linebreaks =
        create_text_paragraphs(&layout_builder, &mut font_ctx, text, None, Color::default());
    preline::measured(item_rc, trial_label, &layout_builder, &font_ctx, max_width, &paragraphs_without_linebreaks);''')
    core.write_text(contents)
    shutil.copyfile(Path(__file__).with_name('preline-cache.rs'), core.with_name('preline-cache.rs'))
    contents = fixture.read_text()
    contents = replace(contents, '    store.save_preferences(&Preferences { language: "en".into()', '''    let cjk = std::env::var_os("AIDOKU_PRELINE_CJK").is_some();
    store.save_preferences(&Preferences { language: if cjk {"zh-CN"} else {"en"}.into()''')
    contents = replace(contents, 'title: format!("Study volume {number}"),', 'title: if cjk { format!("中文书名与较长的排版测试 第{number}卷") } else { format!("Study volume {number}") },')
    contents = replace(contents, '        let mut first = false;', '''        let reuse = match std::env::var("AIDOKU_PRELINE_ARM").unwrap().as_str() { "control" => false, "reuse" => true, _ => panic!("Invalid A/B arm") };
        let negatives = std::env::var_os("AIDOKU_PRELINE_CJK").is_some();
        let mut first = false;''')
    contents = replace(contents, '                let region = renderer.render(pixels.make_mut_slice(), self.size.0 as usize);', '''                let measure = first && !shelf && context(|ctx| !ctx.ui.unwrap().global::<State>().get_booting());
                let pixels_target = pixels.make_mut_slice();
                if measure { assert_eq!(crate::FONT_INITIALIZATIONS.load(Ordering::Acquire), 1); }
                let draw_start = Instant::now();
                if measure { i_slint_core::textlayout::sharedparley::preline::begin(reuse, negatives); }
                let region = renderer.render(pixels_target, self.size.0 as usize);
                let counts = measure.then(i_slint_core::textlayout::sharedparley::preline::finish);
                let draw_ns = draw_start.elapsed().as_nanos();''')
    anchor = '                        fs::write(CONFIG.get().unwrap().directory.join("library.rgb"), pixels.as_bytes()).unwrap();'
    contents = replace(contents, anchor, '''                        assert_eq!(crate::FONT_INITIALIZATIONS.load(Ordering::Acquire), 1);
                        let counts = counts.unwrap();
                        fs::write(CONFIG.get().unwrap().directory.join("preline-counts"), format!("{counts:?}")).unwrap();
                        fs::write(CONFIG.get().unwrap().directory.join("draw-ns"), draw_ns.to_string()).unwrap();
''' + anchor)
    fixture.write_text(contents)


if __name__ == '__main__':
    patch(Path('/work/local-slint/textlayout/sharedparley.rs'), Path('/work/source/src/attach_trial.rs'))
