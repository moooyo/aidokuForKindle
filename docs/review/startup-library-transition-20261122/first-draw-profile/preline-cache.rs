use super::{FontRequest, LayoutWithoutLineBreaksBuilder, LogicalLength, PlainOrStyledText, TextParagraph, TextWrap};
use crate::{SharedString, item_tree::ItemRc};
use std::{cell::RefCell, vec::Vec};

#[derive(Clone, PartialEq)]
struct Key {
    tree: usize,
    item: u32,
    context: usize,
    text: SharedString,
    font: FontRequest,
    scale: f32,
    wrap: TextWrap,
}

impl Key {
    fn new(item: Option<&ItemRc>, content: &PlainOrStyledText, builder: &LayoutWithoutLineBreaksBuilder,
           fonts: &parley::FontContext) -> Option<Self> {
        let item = item?;
        let PlainOrStyledText::Plain(text) = content else { return None };
        if text.is_empty() || text.len() > 512 || text.contains(['\r', '\n']) || builder.stroke.is_some() { return None; }
        Some(Self { tree: vtable::VRc::borrow_pin(item.item_tree()).as_ptr() as usize, item: item.index(),
            context: fonts as *const _ as usize, text: text.clone(), font: builder.font_request.clone()?,
            scale: builder.scale_factor.get(), wrap: builder.text_wrap })
    }
}

struct Entry { key: Key, width: Option<LogicalLength>, paragraphs: Vec<TextParagraph> }

struct Frame { reuse: bool, negatives: bool, entries: Vec<Entry>, counts: [usize; 6] }

std::thread_local! { static FRAME: RefCell<Option<Frame>> = const { RefCell::new(None) }; }

pub fn begin(reuse: bool, negatives: bool) {
    FRAME.with_borrow_mut(|frame| assert!(frame.replace(Frame { reuse, negatives, entries: Vec::new(), counts: [0; 6] }).is_none()));
}

pub fn finish() -> [usize; 6] { FRAME.with_borrow_mut(|frame| frame.take().unwrap().counts) }

pub(super) fn label(content: &PlainOrStyledText) -> Option<SharedString> {
    let PlainOrStyledText::Plain(text) = content else { return None };
    FRAME.with_borrow(|slot| slot.as_ref().is_some_and(|frame| frame.reuse).then(|| text.clone()))
}

pub(super) fn measured(item: &ItemRc, label: Option<SharedString>, builder: &LayoutWithoutLineBreaksBuilder,
                       fonts: &parley::FontContext, width: Option<LogicalLength>, paragraphs: &[TextParagraph]) {
    FRAME.with_borrow_mut(|slot| {
        let Some(frame) = slot else { return };
        frame.counts[0] += 1;
        if !frame.reuse || paragraphs.len() != 1 || frame.entries.len() >= 128 { return; }
        let Some(text) = label else { return };
        let Some(key) = Key::new(Some(item), &PlainOrStyledText::Plain(text), builder, fonts) else { return };
        if frame.entries.iter().any(|entry| entry.key == key) { return; }
        frame.entries.push(Entry {key, width, paragraphs: paragraphs.to_vec()});
    });
}

pub(super) fn paint(item: Option<&ItemRc>, content: &PlainOrStyledText, builder: &LayoutWithoutLineBreaksBuilder,
                    fonts: &parley::FontContext, size: Option<LogicalLength>) -> Option<Vec<TextParagraph>> {
    FRAME.with_borrow_mut(|slot| {
        let frame = slot.as_mut()?;
        if frame.reuse && let Some(key) = Key::new(item, content, builder, fonts)
            && let Some(at) = frame.entries.iter().position(|entry| entry.key == key) {
            if frame.negatives {
                let stored = &frame.entries[at];
                let mut changed = key.clone();
                changed.font.weight = Some(changed.font.weight.unwrap_or_default() + 1);
                assert!(frame.entries.iter().all(|entry| entry.key != changed));
                changed = key.clone(); changed.context = 0;
                assert!(frame.entries.iter().all(|entry| entry.key != changed));
                changed = key.clone(); changed.text = "⸘ absent ⸮".into();
                assert!(frame.entries.iter().all(|entry| entry.key != changed));
                changed = key.clone(); changed.wrap = if changed.wrap == TextWrap::NoWrap { TextWrap::WordWrap } else { TextWrap::NoWrap };
                assert!(frame.entries.iter().all(|entry| entry.key != changed));
                assert!(stored.paragraphs.len() == 1);
                frame.counts[4] += 4;
            }
            let hit = frame.entries.swap_remove(at);
            frame.counts[2] += 1;
            frame.counts[3] += usize::from(hit.width != size);
            frame.counts[5] += usize::from(key.text.chars().any(|ch| ('\u{3400}'..='\u{9fff}').contains(&ch)));
            Some(hit.paragraphs)
        } else {
            frame.counts[1] += 1;
            None
        }
    })
}
