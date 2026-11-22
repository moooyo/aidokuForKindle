use super::{FontRequest, LogicalLength, PlainOrStyledText, ScaleFactor, TextStrokeStyle, TextWrap};
use crate::{item_tree::ItemRc, SharedString};
use std::{cell::RefCell, vec::Vec};

#[derive(Clone, Copy, PartialEq)]
pub(super) enum Path { Measure, Paint }

#[derive(Clone, PartialEq)]
struct Key {
    owner: usize,
    index: u32,
    text: SharedString,
    font: Option<FontRequest>,
    scale: f32,
    wrap: TextWrap,
    stroke: Option<TextStrokeStyle>,
}

struct Call {
    path: Path,
    width: Option<LogicalLength>,
    key: Option<Key>,
    builds: usize,
}

#[derive(Default)]
struct Observer { calls: Vec<Call>, stack: Vec<usize> }

std::thread_local! { static OBSERVER: RefCell<Option<Observer>> = const { RefCell::new(None) }; }

pub fn begin() {
    OBSERVER.with_borrow_mut(|slot| assert!(slot.replace(Observer::default()).is_none()));
}

pub(super) struct Scope(Option<usize>);

impl Drop for Scope {
    fn drop(&mut self) {
        if let Some(index) = self.0 {
            OBSERVER.with_borrow_mut(|slot| assert_eq!(slot.as_mut().unwrap().stack.pop(), Some(index)));
        }
    }
}

pub(super) fn enter(path: Path, width: Option<LogicalLength>) -> Scope {
    Scope(OBSERVER.with_borrow_mut(|slot| {
        let data = slot.as_mut()?;
        let index = data.calls.len();
        data.calls.push(Call { path, width, key: None, builds: 0 });
        data.stack.push(index);
        Some(index)
    }))
}

pub(super) fn key(item: Option<&ItemRc>, text: &PlainOrStyledText, font: &Option<FontRequest>,
                  scale: ScaleFactor, wrap: TextWrap, stroke: Option<TextStrokeStyle>) {
    OBSERVER.with_borrow_mut(|slot| {
        let Some(data) = slot else { return };
        let (Some(index), Some(item), PlainOrStyledText::Plain(value)) = (data.stack.last().copied(), item, text) else { return };
        if value.is_empty() || value.contains(['\n', '\r']) { return; }
        let owner = vtable::VRc::borrow_pin(item.item_tree()).as_ptr() as usize;
        data.calls[index].key = Some(Key { owner, index: item.index(), text: value.clone(), font: font.clone(), scale: scale.get(), wrap, stroke });
    });
}

pub(super) fn built() {
    OBSERVER.with_borrow_mut(|slot| {
        if let Some(data) = slot && let Some(index) = data.stack.last() { data.calls[*index].builds += 1; }
    });
}

pub fn finish() -> [usize; 13] {
    let data = OBSERVER.with_borrow_mut(|slot| slot.take().unwrap());
    assert!(data.stack.is_empty());
    let mut result = [0; 13];
    for (index, call) in data.calls.iter().enumerate() {
        let paint = call.path == Path::Paint;
        let offset = if paint { 3 } else { 0 };
        result[offset] += 1;
        result[offset + 1] += call.builds;
        let Some(key) = &call.key else { continue };
        if call.builds == 0 { continue; }
        result[offset + 2] += 1;
        if !paint { continue; }
        let preceding = || data.calls[..index].iter().filter(|prior| prior.path == Path::Measure && prior.builds > 0);
        let same_item = |other: &Key| other.owner == key.owner && other.index == key.index && other.text == key.text;
        if preceding().any(|prior| prior.key.as_ref().is_some_and(same_item)) { result[6] += 1; }
        if preceding().any(|prior| prior.key.as_ref() == Some(key)) { result[7] += 1; }
        if preceding().any(|prior| prior.key.as_ref() == Some(key) && prior.width == call.width) { result[8] += 1; }
        if preceding().any(|prior| prior.key.as_ref() == Some(key) && prior.width.is_none()) { result[9] += 1; }
        if preceding().any(|prior| prior.key.as_ref().is_some_and(|old| same_item(old) && old.wrap != key.wrap)) { result[10] += 1; }
        if preceding().any(|prior| prior.key.as_ref().is_some_and(|old| same_item(old) && (old.font != key.font || old.scale != key.scale || old.stroke != key.stroke))) { result[11] += 1; }
        if preceding().any(|prior| prior.key.as_ref().is_some_and(|old| old.text == key.text && !same_item(old))) { result[12] += 1; }
    }
    result
}
