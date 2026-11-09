//! Offline input adapted from the sibling WeRead Kindle project.
//! Dictionary data retains its independent Apache-2.0 notice in assets/pinyin.

use std::{
    collections::{HashMap, HashSet},
    sync::OnceLock,
};

const PAGE_SIZE: usize = 8;
const MAX_COMPOSITION: usize = 64;
const MAX_CANDIDATES: usize = 128;
const BEAM_WIDTH: usize = 12;

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum InputLanguage {
    #[default]
    Chinese,
    English,
    Numeric,
}

impl InputLanguage {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Chinese => "zh",
            Self::English => "en",
            Self::Numeric => "numeric",
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum InputIntent {
    None,
    Changed,
    Submit,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Candidate {
    pub text: String,
    /// Number of original ASCII composition characters consumed by this choice.
    pub consumed: usize,
    pub spelling: String,
}

#[derive(Clone, Copy)]
struct Entry {
    text: &'static str,
    rank: u32,
}

struct Dictionary {
    exact: HashMap<&'static str, Vec<Entry>>,
    keys: Vec<&'static str>,
    max_key_length: usize,
}

impl Dictionary {
    fn bundled() -> &'static Self {
        static DICTIONARY: OnceLock<Dictionary> = OnceLock::new();
        DICTIONARY.get_or_init(|| {
            let mut exact: HashMap<&'static str, Vec<Entry>> = HashMap::new();
            let mut max_key_length = 1;
            for line in include_str!("../assets/pinyin/dictionary.tsv").lines() {
                let mut fields = line.split('\t');
                let (Some(key), Some(text), Some(rank)) =
                    (fields.next(), fields.next(), fields.next())
                else {
                    continue;
                };
                if key.is_empty() || text.is_empty() || !key.bytes().all(|c| c.is_ascii_lowercase())
                {
                    continue;
                }
                let Ok(rank) = rank.parse::<u32>() else {
                    continue;
                };
                max_key_length = max_key_length.max(key.len());
                exact.entry(key).or_default().push(Entry { text, rank });
            }
            for entries in exact.values_mut() {
                entries.sort_by_key(|entry| entry.rank);
                entries.dedup_by_key(|entry| entry.text);
            }
            let mut keys: Vec<_> = exact.keys().copied().collect();
            keys.sort_unstable();
            Self {
                exact,
                keys,
                max_key_length,
            }
        })
    }

    fn suggestions(&self, composition: &str) -> Vec<Candidate> {
        let normalized: String = composition
            .chars()
            .filter(char::is_ascii_alphabetic)
            .map(|character| character.to_ascii_lowercase())
            .collect();
        if normalized.is_empty() {
            return Vec::new();
        }
        let mut results: Vec<RankedCandidate> = Vec::new();
        if let Some(entries) = self.exact.get(normalized.as_str()) {
            for entry in entries.iter().take(MAX_CANDIDATES) {
                results.push(RankedCandidate::new(
                    entry.text.to_owned(),
                    normalized.len(),
                    0,
                    entry.rank as u64,
                ));
            }
        }

        // A small beam composes real dictionary words instead of requiring a canned phrase.
        let mut beams: Vec<Vec<Segment>> = vec![Vec::new(); normalized.len() + 1];
        beams[0].push(Segment {
            text: String::new(),
            score: 0,
            words: 0,
        });
        for start in 0..normalized.len() {
            if beams[start].is_empty() {
                continue;
            }
            let previous = beams[start].clone();
            for end in start + 1..=(start + self.max_key_length).min(normalized.len()) {
                // This buffer contains only ASCII letters, so these are also scalar boundaries.
                let Some(entries) = self.exact.get(&normalized[start..end]) else {
                    continue;
                };
                for prefix in &previous {
                    for entry in entries.iter().take(4) {
                        if prefix.words == 0 && end == normalized.len() {
                            continue;
                        }
                        let score =
                            prefix.score + ((entry.rank as f64 + 1.0).log2() * 100.0) as u64 + 800;
                        let mut text = prefix.text.clone();
                        text.push_str(entry.text);
                        beams[end].push(Segment {
                            text,
                            score,
                            words: prefix.words + 1,
                        });
                    }
                }
                retain_best(&mut beams[end]);
            }
        }
        for segment in &beams[normalized.len()] {
            results.push(RankedCandidate::new(
                segment.text.clone(),
                normalized.len(),
                1,
                segment.score,
            ));
        }

        // Prefix completion handles an unfinished final syllable without a network service.
        let first = self.keys.partition_point(|key| *key < normalized.as_str());
        let mut completions: Vec<(&str, Entry)> = self
            .keys
            .iter()
            .skip(first)
            .take_while(|key| key.starts_with(&normalized))
            .flat_map(|key| {
                self.exact[*key]
                    .iter()
                    .take(3)
                    .map(move |entry| (*key, *entry))
            })
            .collect();
        completions.sort_by_key(|(_, entry)| entry.rank);
        for (key, entry) in completions.into_iter().take(24) {
            if key != normalized {
                results.push(RankedCandidate::new(
                    entry.text.to_owned(),
                    normalized.len(),
                    1,
                    entry.rank as u64 + 10_000,
                ));
            }
        }

        // Leading words can be committed while the unconverted suffix stays editable.
        for end in (1..normalized.len()).rev() {
            if let Some(entries) = self.exact.get(&normalized[..end]) {
                for entry in entries.iter().take(8) {
                    results.push(RankedCandidate::new(
                        entry.text.to_owned(),
                        end,
                        2,
                        ((normalized.len() - end) as u64) * 100_000 + entry.rank as u64,
                    ));
                }
            }
        }
        results.push(RankedCandidate::new(
            normalized.clone(),
            normalized.len(),
            3,
            0,
        ));
        results.sort_by(|left, right| {
            (left.priority, left.score, &left.text).cmp(&(right.priority, right.score, &right.text))
        });
        let mut seen = HashSet::new();
        results
            .into_iter()
            .filter(|candidate| seen.insert(candidate.text.clone()))
            .take(MAX_CANDIDATES)
            .map(|candidate| Candidate {
                text: candidate.text,
                consumed: original_consumed(composition, candidate.consumed),
                spelling: normalized.chars().take(candidate.consumed).collect(),
            })
            .collect()
    }
}

#[derive(Clone)]
struct Segment {
    text: String,
    score: u64,
    words: usize,
}

fn retain_best(segments: &mut Vec<Segment>) {
    if segments.len() <= BEAM_WIDTH {
        return;
    }
    segments.sort_by(|left, right| (left.score, &left.text).cmp(&(right.score, &right.text)));
    segments.dedup_by(|left, right| left.text == right.text);
    segments.truncate(BEAM_WIDTH);
}

struct RankedCandidate {
    text: String,
    consumed: usize,
    priority: u8,
    score: u64,
}

impl RankedCandidate {
    fn new(text: String, consumed: usize, priority: u8, score: u64) -> Self {
        Self {
            text,
            consumed,
            priority,
            score,
        }
    }
}

fn original_consumed(composition: &str, normalized_count: usize) -> usize {
    let mut letters = 0;
    for (index, character) in composition.chars().enumerate() {
        if character.is_ascii_alphabetic() {
            letters += 1;
        }
        if letters == normalized_count {
            return index + 1;
        }
    }
    composition.chars().count()
}

#[derive(Clone, Debug, Default)]
pub struct PinyinInput {
    pub query: String,
    pub composition: String,
    pub language: InputLanguage,
    pub caps: bool,
    pub candidate_page: usize,
    previous_language: InputLanguage,
}

impl PinyinInput {
    pub fn new(query: impl Into<String>) -> Self {
        Self {
            query: query.into(),
            ..Self::default()
        }
    }

    pub fn set_query(&mut self, value: impl Into<String>) {
        self.query = value.into();
        self.composition.clear();
        self.candidate_page = 0;
    }

    pub fn clear(&mut self) {
        self.query.clear();
        self.composition.clear();
        self.candidate_page = 0;
    }

    pub fn all_candidates(&self) -> Vec<Candidate> {
        Dictionary::bundled().suggestions(&self.composition)
    }

    pub fn candidates(&self) -> Vec<String> {
        self.all_candidates()
            .into_iter()
            .skip(self.candidate_page * PAGE_SIZE)
            .take(PAGE_SIZE)
            .map(|candidate| candidate.text)
            .collect()
    }

    pub fn candidate_page_count(&self) -> usize {
        self.all_candidates().len().div_ceil(PAGE_SIZE).max(1)
    }

    pub fn choose(&mut self, text: &str) -> bool {
        let Some(candidate) = self
            .all_candidates()
            .into_iter()
            .find(|candidate| candidate.text == text)
        else {
            return false;
        };
        self.query.push_str(&candidate.text);
        self.composition = self.composition.chars().skip(candidate.consumed).collect();
        while self.composition.starts_with('\'') {
            self.composition.remove(0);
        }
        self.candidate_page = 0;
        true
    }

    pub fn commit_first(&mut self) {
        if self.composition.is_empty() {
            return;
        }
        if let Some(first) = self.all_candidates().into_iter().next() {
            self.query.push_str(&first.text);
            self.composition = self.composition.chars().skip(first.consumed).collect();
        }
        // A submission must not silently discard an unconvertible suffix.
        if !self.composition.is_empty() {
            self.query.push_str(&self.composition);
            self.composition.clear();
        }
        self.candidate_page = 0;
    }

    pub fn press(&mut self, key: &str) -> InputIntent {
        match key {
            "backspace" | "delete" => {
                if !self.composition.is_empty() {
                    self.composition.pop();
                } else {
                    self.query.pop();
                }
                self.candidate_page = 0;
            }
            "shift" => {
                self.caps = !self.caps;
            }
            "language" => {
                self.commit_first();
                self.language = if self.language == InputLanguage::English {
                    InputLanguage::Chinese
                } else {
                    InputLanguage::English
                };
                self.caps = false;
            }
            "numeric" | "numbers" => {
                self.commit_first();
                if self.language == InputLanguage::Numeric {
                    self.language = self.previous_language;
                } else {
                    self.previous_language = self.language;
                    self.language = InputLanguage::Numeric;
                }
                self.caps = false;
            }
            "space" => {
                if self.composition.is_empty() {
                    self.query.push(' ');
                } else if let Some(candidate) = self.candidates().into_iter().next() {
                    self.choose(&candidate);
                }
            }
            "period" => {
                self.commit_first();
                self.query.push(if self.language == InputLanguage::Chinese {
                    '\u{3002}'
                } else {
                    '.'
                });
            }
            "submit" | "enter" | "search" => {
                self.commit_first();
                return InputIntent::Submit;
            }
            "candidate-next" | "next_candidates" => {
                if self.candidate_page + 1 < self.candidate_page_count() {
                    self.candidate_page += 1;
                }
            }
            "candidate-prev" | "previous_candidates" => {
                self.candidate_page = self.candidate_page.saturating_sub(1);
            }
            "clear" => self.clear(),
            _ => {
                let mut characters = key.chars();
                let Some(character) = characters.next() else {
                    return InputIntent::None;
                };
                if characters.next().is_some() {
                    return InputIntent::None;
                }
                if self.language == InputLanguage::Chinese
                    && !self.caps
                    && (character.is_ascii_alphabetic()
                        || character == '\''
                        || character == '\u{00fc}')
                {
                    if self.composition.len() < MAX_COMPOSITION {
                        self.composition.push(if character == '\u{00fc}' {
                            'v'
                        } else {
                            character.to_ascii_lowercase()
                        });
                    }
                } else {
                    self.commit_first();
                    if self.caps {
                        self.query.extend(character.to_uppercase());
                    } else {
                        self.query.push(character);
                    }
                }
                self.candidate_page = 0;
            }
        }
        InputIntent::Changed
    }
}

/// Input facade shared by the search, local import, and source configuration forms.
/// The committed text and editable Pinyin composition are deliberately separate.
#[derive(Clone, Debug, Default)]
pub struct KeyboardInput {
    input: PinyinInput,
}

impl KeyboardInput {
    pub fn set_text(&mut self, text: &str) {
        self.input.set_query(text);
    }

    /// Switching layouts commits an unfinished composition without dropping text.
    pub fn set_mode(&mut self, mode: &str) {
        let language = match mode {
            "english" | "en" => InputLanguage::English,
            "numbers" | "numeric" => InputLanguage::Numeric,
            _ => InputLanguage::Chinese,
        };
        if self.input.language != language {
            self.input.commit_first();
            self.input.language = language;
            self.input.caps = false;
        }
    }

    pub fn key(&mut self, key: &str) -> InputIntent {
        self.input.press(match key {
            "done" => "submit",
            " " => "space",
            _ => key,
        })
    }

    pub fn backspace(&mut self) {
        self.input.press("backspace");
    }

    pub fn choose(&mut self, candidate: &str) -> bool {
        self.input.choose(candidate)
    }

    pub fn text(&self) -> &str {
        &self.input.query
    }

    pub fn composition(&self) -> &str {
        &self.input.composition
    }

    pub fn candidates(&self) -> Vec<String> {
        self.input.candidates()
    }

    /// Returns a one-based page and total count; an empty composition uses (1, 1).
    pub fn candidate_pagination(&self) -> (usize, usize) {
        let count = self.input.candidate_page_count();
        (self.input.candidate_page.min(count - 1) + 1, count)
    }

    /// Candidate pages are clamped at both ends; a signed delta never wraps.
    pub fn candidate_page(&mut self, delta: i32) {
        let last = self.input.candidate_page_count().saturating_sub(1);
        self.input.candidate_page = self
            .input
            .candidate_page
            .saturating_add_signed(delta as isize)
            .min(last);
    }

    pub fn commit(&mut self) -> String {
        self.input.commit_first();
        self.input.query.clone()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn type_pinyin(value: &str) -> PinyinInput {
        let mut input = PinyinInput::default();
        for character in value.chars() {
            input.press(&character.to_string());
        }
        input
    }

    #[test]
    fn bundled_dictionary_is_substantive_and_candidates_are_not_a_mock_table() {
        assert!(Dictionary::bundled().exact.len() > 10_000);
        for pinyin in ["zhongguo", "dushu", "tushuguan", "weixin", "shijie"] {
            let input = type_pinyin(pinyin);
            assert!(
                input.candidates().iter().any(|text| !text.is_ascii()),
                "{pinyin}"
            );
        }
    }

    #[test]
    fn choosing_a_candidate_preserves_a_continuous_unconverted_suffix() {
        let mut input = type_pinyin("zhongguoxyz");
        let candidate = input
            .all_candidates()
            .into_iter()
            .find(|candidate| candidate.consumed == 8 && !candidate.text.is_ascii())
            .expect("Leading dictionary phrase");
        assert!(input.choose(&candidate.text));
        assert_eq!(input.composition, "xyz");
        assert!(!input.query.is_empty());
    }

    #[test]
    fn backspace_removes_composition_before_committed_unicode() {
        let mut input = PinyinInput::new("\u{4e66}");
        input.press("a");
        input.press("backspace");
        assert_eq!(input.query, "\u{4e66}");
        assert!(input.composition.is_empty());
        input.press("backspace");
        assert!(input.query.is_empty());
    }

    #[test]
    fn english_numeric_and_caps_keys_have_real_effects() {
        let mut input = PinyinInput::default();
        input.press("language");
        input.press("shift");
        input.press("a");
        input.press("numeric");
        input.press("1");
        input.press("@");
        input.press("numeric");
        assert_eq!(input.language, InputLanguage::English);
        assert_eq!(input.query, "A1@");
    }

    #[test]
    fn submission_commits_candidates_and_retains_unknown_spelling() {
        let mut input = type_pinyin("zhongguo");
        assert_eq!(input.press("submit"), InputIntent::Submit);
        assert!(input.composition.is_empty());
        assert!(!input.query.is_ascii());
        let mut unknown = type_pinyin("qzxqzx");
        assert_eq!(unknown.press("submit"), InputIntent::Submit);
        assert!(!unknown.query.is_empty());
    }

    #[test]
    fn incomplete_syllables_produce_completion_candidates_and_can_page() {
        let mut input = type_pinyin("zhongg");
        assert!(
            input
                .candidates()
                .iter()
                .any(|candidate| !candidate.is_ascii())
        );
        input.set_query("");
        input.press("s");
        assert!(input.candidate_page_count() > 1);
        let first = input.candidates();
        input.press("candidate-next");
        assert_ne!(input.candidates(), first);
    }

    #[test]
    fn eight_candidate_pages_preserve_dictionary_order_and_navigation_bounds() {
        let mut input = type_pinyin("s");
        let expected: Vec<_> = input
            .all_candidates()
            .into_iter()
            .map(|candidate| candidate.text)
            .collect();
        assert!(expected.len() > 16);
        assert_eq!(input.candidate_page_count(), expected.len().div_ceil(8));
        input.press("candidate-prev");
        assert_eq!(input.candidate_page, 0);

        for (page, candidates) in expected.chunks(8).enumerate() {
            assert_eq!(input.candidate_page, page);
            assert_eq!(input.candidates(), candidates);
            input.press("candidate-next");
        }
        assert_eq!(input.candidate_page + 1, input.candidate_page_count());
        assert_eq!(input.candidates(), expected.chunks(8).last().unwrap());

        while input.candidate_page > 0 {
            input.press("candidate-prev");
        }
        assert_eq!(input.candidates(), &expected[..8]);
    }

    #[test]
    fn space_commits_the_ninth_candidate_after_advancing_to_the_second_page() {
        let mut input = type_pinyin("s");
        let ninth = input.all_candidates()[8].clone();
        input.press("candidate-next");
        assert_eq!(input.candidates().first(), Some(&ninth.text));
        input.press("space");
        assert_eq!(input.query, ninth.text);
        assert!(input.composition.is_empty());
        assert_eq!(input.candidate_page, 0);
        assert!(input.candidates().is_empty());
    }

    #[test]
    fn facade_preserves_existing_text_and_converts_a_continuous_phrase() {
        let mut input = KeyboardInput::default();
        input.set_text("Manga ");
        for character in "tushuguan".chars() {
            input.key(&character.to_string());
        }
        assert_eq!(input.text(), "Manga ");
        assert_eq!(input.composition(), "tushuguan");
        let candidate = input
            .candidates()
            .into_iter()
            .find(|text| !text.is_ascii())
            .expect("Dictionary phrase");
        assert!(input.choose(&candidate));
        assert_eq!(input.commit(), format!("Manga {candidate}"));
    }

    #[test]
    fn facade_switches_modes_without_losing_an_unconvertible_suffix() {
        let mut input = KeyboardInput::default();
        for character in "qzxqzx".chars() {
            input.key(&character.to_string());
        }
        input.set_mode("english");
        assert!(input.composition().is_empty());
        let prefix = input.text().to_owned();
        assert!(!prefix.is_empty());
        for key in ["@", "a", ".", "c", "o", "m"] {
            input.key(key);
        }
        input.set_mode("numbers");
        input.key("7");
        assert_eq!(input.commit(), format!("{prefix}@a.com7"));
    }

    #[test]
    fn facade_candidate_paging_is_bounded_and_backspace_is_unicode_safe() {
        let mut input = KeyboardInput::default();
        assert_eq!(input.candidate_pagination(), (1, 1));
        input.key("s");
        let page_count = input.input.all_candidates().len().div_ceil(8);
        assert!(page_count > 2);
        assert_eq!(input.candidate_pagination(), (1, page_count));
        let first = input.candidates();
        assert_eq!(first.len(), 8);
        input.candidate_page(-1);
        assert_eq!(input.candidates(), first);
        input.candidate_page(1);
        assert_eq!(input.candidate_pagination(), (2, page_count));
        let second = input.candidates();
        assert_eq!(second.len(), 8);
        assert!(second.iter().all(|candidate| !first.contains(candidate)));
        input.candidate_page(i32::MAX);
        assert_eq!(input.candidate_pagination(), (page_count, page_count));
        let last = input.candidates();
        assert!(!last.is_empty());
        input.candidate_page(1);
        assert_eq!(input.candidates(), last);
        input.candidate_page(i32::MIN);
        assert_eq!(input.candidate_pagination(), (1, page_count));
        assert_eq!(input.candidates(), first);
        input.set_text("\u{4e66}\u{67b6}");
        assert_eq!(input.candidate_pagination(), (1, 1));
        input.backspace();
        assert_eq!(input.text(), "\u{4e66}");
    }
}
