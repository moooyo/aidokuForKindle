use std::cell::RefCell;
use std::collections::BTreeMap;

pub(crate) type Key = (u64, u32, i16, u16, u64);

#[derive(Default)]
struct Seen {
    cached: bool,
    failed_raster: bool,
}

#[derive(Default)]
struct Frame {
    keys: BTreeMap<Key, Seen>,
    calls: usize,
    hits: usize,
    misses: usize,
    preexisting_hits: usize,
    repeat_after_cached: usize,
    repeat_after_failure: usize,
    rendered: usize,
    empty_masks: usize,
    raster_none: usize,
    key_refused: usize,
    evicted_entries: usize,
    peak_alpha_bytes: usize,
}

std::thread_local! { static FRAME: RefCell<Option<Frame>> = const { RefCell::new(None) }; }

pub fn begin() {
    FRAME.with_borrow_mut(|slot| assert!(slot.replace(Frame::default()).is_none()));
}

pub(crate) fn lookup(key: Key, hit: bool, weight: usize) {
    FRAME.with_borrow_mut(|slot| {
        let Some(frame) = slot else { return };
        assert!(frame.calls < 8192, "Unexpected synthetic Library glyph volume");
        frame.calls += 1;
        frame.peak_alpha_bytes = frame.peak_alpha_bytes.max(weight);
        let seen = frame.keys.entry(key).or_default();
        if hit {
            frame.hits += 1;
            frame.preexisting_hits += usize::from(!seen.cached);
            seen.cached = true;
        } else {
            frame.misses += 1;
            frame.repeat_after_cached += usize::from(seen.cached);
            frame.repeat_after_failure += usize::from(seen.failed_raster);
        }
    });
}

pub(crate) fn no_mask(key: Key) {
    FRAME.with_borrow_mut(|slot| {
        let Some(frame) = slot else { return };
        frame.raster_none += 1;
        frame.keys.get_mut(&key).unwrap().failed_raster = true;
    });
}

pub(crate) fn stored(key: Key, empty: bool, inserted: bool, before: usize, after: usize, weight: usize) {
    FRAME.with_borrow_mut(|slot| {
        let Some(frame) = slot else { return };
        frame.rendered += 1;
        frame.empty_masks += usize::from(empty);
        if inserted {
            frame.keys.get_mut(&key).unwrap().cached = true;
            frame.evicted_entries += (before + 1).checked_sub(after).unwrap();
        } else {
            frame.key_refused += 1;
        }
        frame.peak_alpha_bytes = frame.peak_alpha_bytes.max(weight);
    });
}

pub fn finish() -> [usize; 12] {
    FRAME.with_borrow_mut(|slot| {
        let frame = slot.take().expect("Missing synthetic Library frame");
        assert!(frame.calls > 0);
        assert_eq!(frame.calls, frame.hits + frame.misses);
        assert_eq!(frame.misses, frame.rendered + frame.raster_none);
        [frame.calls, frame.keys.len(), frame.hits, frame.misses, frame.preexisting_hits,
         frame.rendered, frame.empty_masks, frame.raster_none, frame.repeat_after_failure,
         frame.repeat_after_cached, frame.key_refused, frame.evicted_entries]
    })
}

pub fn peak_alpha_bytes() -> usize {
    FRAME.with_borrow(|slot| slot.as_ref().unwrap().peak_alpha_bytes)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn aggregate_observations_without_disclosing_keys() {
        let glyph = (11, 0, 26, 4, 7);
        let blank = (11, 0, 26, 8, 7);
        let alternate = (11, 0, 27, 4, 7);
        lookup(glyph, false, 0);
        begin();
        lookup(glyph, true, 12);
        lookup(glyph, true, 12);
        lookup(blank, false, 12);
        no_mask(blank);
        lookup(blank, false, 12);
        no_mask(blank);
        lookup(alternate, false, 12);
        stored(alternate, true, true, 1, 2, 12);
        lookup(alternate, true, 12);
        lookup(glyph, false, 12);
        stored(glyph, false, true, 2, 2, 14);
        assert_eq!(peak_alpha_bytes(), 14);
        assert_eq!(finish(), [7, 3, 3, 4, 1, 2, 1, 2, 1, 1, 0, 1]);
    }
}
