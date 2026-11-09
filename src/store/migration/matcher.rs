use std::collections::{BTreeMap, BTreeSet};

use crate::domain::Chapter;

use super::{ChapterMatchReason, ChapterMatchResult, ChapterMigrationMatch};

struct ChapterLabel {
    number: Option<u64>,
    title: Option<String>,
}

impl ChapterLabel {
    fn new(chapter: &Chapter) -> Self {
        Self {
            number: (chapter.number.is_finite() && chapter.number > 0.0)
                .then(|| chapter.number.to_bits()),
            title: meaningful_title(&chapter.title),
        }
    }
}

#[derive(Default)]
struct ChapterIndex<'a> {
    pairs: BTreeMap<(u64, &'a str), Vec<usize>>,
    titles: BTreeMap<&'a str, Vec<usize>>,
    numbers: BTreeMap<u64, Vec<usize>>,
}

impl<'a> ChapterIndex<'a> {
    fn new(labels: &'a [ChapterLabel]) -> Self {
        let mut index = Self::default();
        for (position, label) in labels.iter().enumerate() {
            if let Some(number) = label.number {
                index.numbers.entry(number).or_default().push(position);
                if let Some(title) = label.title.as_deref() {
                    index
                        .pairs
                        .entry((number, title))
                        .or_default()
                        .push(position);
                }
            }
            if let Some(title) = label.title.as_deref() {
                index.titles.entry(title).or_default().push(position);
            }
        }
        index
    }

    fn has_candidate(&self, label: &ChapterLabel) -> bool {
        label
            .number
            .is_some_and(|number| self.numbers.contains_key(&number))
            || label
                .title
                .as_deref()
                .is_some_and(|title| self.titles.contains_key(title))
    }
}

struct Matches<'a> {
    origin: &'a [Chapter],
    target: &'a [Chapter],
    origin_unique: Vec<bool>,
    target_unique: Vec<bool>,
    resolved: Vec<Option<ChapterMigrationMatch>>,
    used_target_ids: BTreeSet<&'a str>,
}

impl<'a> Matches<'a> {
    fn new(origin: &'a [Chapter], target: &'a [Chapter]) -> Self {
        Self {
            origin,
            target,
            origin_unique: unique_id_flags(origin),
            target_unique: unique_id_flags(target),
            resolved: (0..origin.len()).map(|_| None).collect(),
            used_target_ids: BTreeSet::new(),
        }
    }

    fn assign(&mut self, origin: usize, target: usize, reason: ChapterMatchReason) {
        if self.resolved[origin].is_some()
            || !self.origin_unique[origin]
            || !self.target_unique[target]
            || !self.used_target_ids.insert(self.target[target].id.as_str())
        {
            return;
        }
        self.resolved[origin] = Some(ChapterMigrationMatch {
            origin_chapter_id: self.origin[origin].id.clone(),
            target_chapter_id: self.target[target].id.clone(),
            reason,
        });
    }
}

/// Match strong evidence globally before considering weaker evidence. Indexes
/// retain the complete inputs, including already-used targets and duplicates.
pub(super) fn match_chapters(origin: &[Chapter], target: &[Chapter]) -> ChapterMatchResult {
    let origin_labels = origin.iter().map(ChapterLabel::new).collect::<Vec<_>>();
    let target_labels = target.iter().map(ChapterLabel::new).collect::<Vec<_>>();
    let origin_index = ChapterIndex::new(&origin_labels);
    let target_index = ChapterIndex::new(&target_labels);
    let mut matched = Matches::new(origin, target);

    for (position, label) in origin_labels.iter().enumerate() {
        let (Some(number), Some(title)) = (label.number, label.title.as_deref()) else {
            continue;
        };
        let key = (number, title);
        if sole(origin_index.pairs.get(&key)) != Some(position) {
            continue;
        }
        if let Some(target) = sole(target_index.pairs.get(&key)) {
            matched.assign(position, target, ChapterMatchReason::NumberAndTitle);
        }
    }

    for (position, label) in origin_labels.iter().enumerate() {
        if matched.resolved[position].is_some() {
            continue;
        }
        let Some(title) = label.title.as_deref() else {
            continue;
        };
        if sole(origin_index.titles.get(title)) != Some(position) {
            continue;
        }
        if let Some(target) = sole(target_index.titles.get(title)) {
            matched.assign(position, target, ChapterMatchReason::UniqueTitle);
        }
    }

    for (position, label) in origin_labels.iter().enumerate() {
        if matched.resolved[position].is_some() {
            continue;
        }
        let Some(number) = label.number else {
            continue;
        };
        if sole(origin_index.numbers.get(&number)) != Some(position) {
            continue;
        }
        if let Some(target) = sole(target_index.numbers.get(&number)) {
            // Meaningful titles never lose to an equal chapter number. A title
            // present on both sides was eligible for a stronger phase already.
            if label.title.is_none() || target_labels[target].title.is_none() {
                matched.assign(position, target, ChapterMatchReason::UniqueNumber);
            }
        }
    }

    let mut result = ChapterMatchResult {
        matches: Vec::new(),
        unmatched_origin_chapter_ids: Vec::new(),
        ambiguous_origin_chapter_ids: Vec::new(),
    };
    let mut reported = BTreeSet::new();
    for (position, entry) in matched.resolved.into_iter().enumerate() {
        if let Some(entry) = entry {
            result.matches.push(entry);
        } else if reported.insert(origin[position].id.as_str()) {
            if !matched.origin_unique[position]
                || target_index.has_candidate(&origin_labels[position])
            {
                result
                    .ambiguous_origin_chapter_ids
                    .push(origin[position].id.clone());
            } else {
                result
                    .unmatched_origin_chapter_ids
                    .push(origin[position].id.clone());
            }
        }
    }
    result
}

fn sole(positions: Option<&Vec<usize>>) -> Option<usize> {
    match positions.map(Vec::as_slice) {
        Some([position]) => Some(*position),
        _ => None,
    }
}

fn unique_id_flags(chapters: &[Chapter]) -> Vec<bool> {
    let mut counts = BTreeMap::new();
    for chapter in chapters {
        *counts.entry(chapter.id.as_str()).or_insert(0usize) += 1;
    }
    chapters
        .iter()
        .map(|chapter| !chapter.id.is_empty() && counts.get(chapter.id.as_str()) == Some(&1))
        .collect()
}

fn meaningful_title(title: &str) -> Option<String> {
    let characters = title
        .chars()
        .flat_map(char::to_lowercase)
        .collect::<Vec<_>>();
    let mut normalized = String::new();
    for (index, character) in characters.iter().copied().enumerate() {
        if character.is_alphanumeric() || is_cjk(character) {
            normalized.push(character);
        } else if let Some(separator) = numeric_separator(character) {
            let before = characters[..index]
                .iter()
                .rev()
                .copied()
                .find(|value| !value.is_whitespace());
            let after = characters[index + 1..]
                .iter()
                .copied()
                .find(|value| !value.is_whitespace());
            if before.is_some_and(numeric_character) && after.is_some_and(numeric_character) {
                normalized.push(separator);
            }
        }
    }
    if normalized.is_empty()
        || numeric_token(&normalized)
        || matches!(
            normalized.as_str(),
            "chapter"
                | "ch"
                | "episode"
                | "\u{7ae0}"
                | "\u{8bdd}"
                | "\u{8a71}"
                | "\u{56de}"
                | "\u{8282}"
                | "\u{7bc0}"
                | "\u{96c6}"
                | "\u{7ae0}\u{8282}"
                | "\u{7ae0}\u{7bc0}"
                | "\u{7b2c}"
                | "\u{7b2c}\u{7ae0}"
                | "\u{7b2c}\u{8bdd}"
                | "\u{7b2c}\u{8a71}"
                | "\u{7b2c}\u{56de}"
                | "\u{7b2c}\u{8282}"
                | "\u{7b2c}\u{7bc0}"
                | "\u{7b2c}\u{96c6}"
        )
    {
        return None;
    }
    for prefix in ["chapter", "ch", "episode"] {
        if normalized.strip_prefix(prefix).is_some_and(numeric_token) {
            return None;
        }
    }
    if let Some(rest) = normalized.strip_prefix('\u{7b2c}') {
        if numeric_token(rest) {
            return None;
        }
        for suffix in [
            '\u{8bdd}', '\u{8a71}', '\u{7ae0}', '\u{56de}', '\u{8282}', '\u{7bc0}',
        ] {
            if rest.strip_suffix(suffix).is_some_and(numeric_token) {
                return None;
            }
        }
    }
    Some(normalized)
}

fn numeric_token(value: &str) -> bool {
    let characters = value.chars().collect::<Vec<_>>();
    !characters.is_empty()
        && characters
            .iter()
            .copied()
            .enumerate()
            .all(|(index, character)| {
                numeric_character(character)
                    || (numeric_separator(character).is_some()
                        && index > 0
                        && index + 1 < characters.len()
                        && numeric_character(characters[index - 1])
                        && numeric_character(characters[index + 1]))
            })
}

fn numeric_character(character: char) -> bool {
    character.is_numeric() || "\u{3007}\u{96f6}\u{4e00}\u{4e8c}\u{4e09}\u{56db}\u{4e94}\u{516d}\u{4e03}\u{516b}\u{4e5d}\u{5341}\u{767e}\u{5343}\u{4e07}\u{842c}\u{4ebf}\u{5104}\u{4e24}\u{5169}".contains(character)
}

fn numeric_separator(character: char) -> Option<char> {
    match character {
        '.' | '\u{ff0e}' => Some('.'),
        ',' | '\u{ff0c}' => Some(','),
        '-' | '\u{2010}'..='\u{2015}' | '\u{2212}' | '\u{ff0d}' => Some('-'),
        '/' | '\u{ff0f}' => Some('/'),
        ':' | '\u{ff1a}' => Some(':'),
        '~' | '\u{ff5e}' | '\u{301c}' => Some('~'),
        _ => None,
    }
}

fn is_cjk(character: char) -> bool {
    matches!(character as u32, 0x3400..=0x4dbf | 0x4e00..=0x9fff | 0xf900..=0xfaff | 0x20000..=0x323af)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn chapter(id: &str, number: f64, title: &str) -> Chapter {
        Chapter {
            id: id.into(),
            number,
            title: title.into(),
            ..Chapter::default()
        }
    }

    fn pairs(result: &ChapterMatchResult) -> BTreeMap<String, String> {
        result
            .matches
            .iter()
            .map(|entry| {
                (
                    entry.origin_chapter_id.clone(),
                    entry.target_chapter_id.clone(),
                )
            })
            .collect()
    }

    #[test]
    fn reversed_order_uses_evidence_and_unicode_titles_instead_of_position() {
        let origin = [
            chapter("o-1", 1.0, "Storm Before Dawn"),
            chapter("o-2", 2.0, "CAF\u{00c9}"),
        ];
        let target = [
            chapter("t-2", 2.0, "caf\u{00e9}"),
            chapter("t-1", 1.0, "Storm: Before Dawn!"),
        ];
        let result = match_chapters(&origin, &target);
        assert_eq!(
            pairs(&result),
            BTreeMap::from([("o-1".into(), "t-1".into()), ("o-2".into(), "t-2".into())])
        );
        assert!(
            result
                .matches
                .iter()
                .all(|entry| matches!(&entry.reason, ChapterMatchReason::NumberAndTitle))
        );
    }

    #[test]
    fn unique_titles_disambiguate_duplicate_numbers_without_resolving_duplicate_titles() {
        let origin = [chapter("o-a", 7.0, "North"), chapter("o-b", 7.0, "South")];
        let target = [chapter("t-b", 40.0, "South"), chapter("t-a", 20.0, "North")];
        assert_eq!(
            pairs(&match_chapters(&origin, &target)),
            BTreeMap::from([("o-a".into(), "t-a".into()), ("o-b".into(), "t-b".into())])
        );
        let origin = [
            chapter("o-1", 1.0, "Repeated"),
            chapter("o-2", 2.0, "Repeated"),
        ];
        let target = [
            chapter("t-1", 1.0, "Repeated"),
            chapter("t-3", 3.0, "Repeated"),
        ];
        let result = match_chapters(&origin, &target);
        assert_eq!(
            pairs(&result),
            BTreeMap::from([("o-1".into(), "t-1".into())])
        );
        assert_eq!(result.ambiguous_origin_chapter_ids, ["o-2"]);
    }

    #[test]
    fn meaningful_title_conflicts_are_ambiguous_even_with_equal_numbers() {
        let result = match_chapters(
            &[chapter("o", 5.0, "Storm")],
            &[chapter("t", 5.0, "Sunrise")],
        );
        assert!(result.matches.is_empty());
        assert_eq!(result.ambiguous_origin_chapter_ids, ["o"]);
        assert!(result.unmatched_origin_chapter_ids.is_empty());
    }

    #[test]
    fn unique_meaningful_titles_allow_renumbering_and_invalid_old_numbers() {
        let origin = [
            chapter("o-rain", 34.0, "Rain"),
            chapter("o-zero", 0.0, "Prologue"),
            chapter("o-nan", f64::NAN, "Signal"),
        ];
        let target = [
            chapter("t-signal", 99.0, "SIGNAL"),
            chapter("t-rain", 60.0, "RAIN"),
            chapter("t-prologue", 1.0, "Prologue"),
        ];
        let result = match_chapters(&origin, &target);
        assert_eq!(result.matches.len(), 3);
        assert!(
            result
                .matches
                .iter()
                .all(|entry| matches!(&entry.reason, ChapterMatchReason::UniqueTitle))
        );
    }

    #[test]
    fn generic_wrappers_allow_numbers_but_meaningful_suffixes_are_retained() {
        let origin = [
            chapter("o-34", 34.0, "Chapter 34"),
            chapter("o-4", 4.0, "EPISODE4"),
            chapter("o-12", 12.0, "\u{7b2c}12\u{8a71}"),
            chapter("o-final", 35.0, "Chapter35Finale"),
            chapter("o-last", 36.0, "\u{7b2c}36\u{8bdd}\u{7ec8}\u{7ae0}"),
        ];
        let target = [
            chapter("t-34", 34.0, "Tide"),
            chapter("t-4", 4.0, ""),
            chapter("t-12", 12.0, "North Light"),
            chapter("t-final", 35.0, "Other"),
            chapter("t-last", 36.0, "\u{7ec8}\u{7ae0}"),
        ];
        let result = match_chapters(&origin, &target);
        assert_eq!(result.matches.len(), 3);
        assert!(
            result
                .matches
                .iter()
                .all(|entry| matches!(&entry.reason, ChapterMatchReason::UniqueNumber))
        );
        assert_eq!(result.ambiguous_origin_chapter_ids, ["o-final", "o-last"]);
        assert!(meaningful_title("ch34").is_none());
        assert!(meaningful_title("34").is_none());
    }

    #[test]
    fn global_title_matching_claims_a_target_before_weak_number_matching() {
        let weak = chapter("o-weak", 10.0, "ch10");
        let strong = chapter("o-strong", 20.0, "Beacon");
        let target = [chapter("t", 10.0, "BEACON")];
        for origin in [
            [weak.clone(), strong.clone()],
            [strong.clone(), weak.clone()],
        ] {
            let result = match_chapters(&origin, &target);
            assert_eq!(
                pairs(&result),
                BTreeMap::from([("o-strong".into(), "t".into())])
            );
            assert_eq!(result.ambiguous_origin_chapter_ids, ["o-weak"]);
        }
        let target = [
            chapter("duplicate", 1.0, "North"),
            chapter("duplicate", 2.0, "South"),
        ];
        let origin = [chapter("a", 1.0, "North"), chapter("b", 2.0, "South")];
        assert!(match_chapters(&origin, &target).matches.is_empty());
    }

    #[test]
    fn absent_candidates_and_invalid_numeric_placeholders_remain_unmatched() {
        let origin = [
            chapter("zero", 0.0, ""),
            chapter("negative", -1.0, "Chapter1"),
            chapter("nan", f64::NAN, "Episode4"),
            chapter("infinite", f64::INFINITY, "ch5"),
            chapter("missing", 99.0, "Tide"),
        ];
        let target = [
            chapter("t-zero", 0.0, ""),
            chapter("t-negative", -1.0, "Chapter1"),
            chapter("t-nan", f64::NAN, "Episode4"),
            chapter("t-infinite", f64::INFINITY, "ch5"),
            chapter("other", 100.0, "Dawn"),
        ];
        let result = match_chapters(&origin, &target);
        assert!(result.matches.is_empty());
        assert!(result.ambiguous_origin_chapter_ids.is_empty());
        assert_eq!(
            result.unmatched_origin_chapter_ids,
            ["zero", "negative", "nan", "infinite", "missing"]
        );
    }

    #[test]
    fn bare_generic_source_titles_cannot_match_different_or_unknown_numbers() {
        for title in [
            "Chapter",
            " ch ",
            "EPISODE",
            "\u{7ae0}\u{8282}",
            "\u{7ae0}\u{7bc0}",
            "\u{7b2c}\u{8bdd}",
            "\u{8a71}",
        ] {
            assert!(meaningful_title(title).is_none());
            for (origin_number, target_number) in [(1.0, 99.0), (0.0, 0.0)] {
                let result = match_chapters(
                    &[chapter("origin", origin_number, title)],
                    &[chapter("target", target_number, title)],
                );
                assert!(
                    result.matches.is_empty(),
                    "Generic title {title:?} must not establish chapter identity"
                );
            }
        }
    }

    #[test]
    fn numeric_separators_and_adjacent_spaces_cannot_collapse_into_digits() {
        for (origin_title, target_title, origin_number, target_number) in [
            (
                "\u{7b2c}1.5\u{8bdd} \u{91cd}\u{9022}",
                "\u{7b2c}15\u{8bdd} \u{91cd}\u{9022}",
                1.5,
                15.0,
            ),
            (
                "\u{7b2c}1 . 5\u{8bdd} \u{91cd}\u{9022}",
                "\u{7b2c}15\u{8bdd} \u{91cd}\u{9022}",
                1.5,
                15.0,
            ),
            ("1-2 Finale", "12 Finale", 1.0, 12.0),
            ("1 - 2 Finale", "12 Finale", 1.0, 12.0),
            ("1 / 2 Finale", "12 Finale", 1.0, 12.0),
            ("1\u{2013}2 Finale", "12 Finale", 1.0, 12.0),
        ] {
            assert_ne!(
                meaningful_title(origin_title),
                meaningful_title(target_title)
            );
            assert!(
                match_chapters(
                    &[chapter("origin", origin_number, origin_title)],
                    &[chapter("target", target_number, target_title)]
                )
                .matches
                .is_empty()
            );
        }
        assert_eq!(
            meaningful_title("Storm: Before Dawn!"),
            meaningful_title("Storm Before Dawn")
        );
    }

    #[test]
    fn decimal_numeric_wrappers_still_require_the_actual_unique_number() {
        for title in [
            "Chapter1.5",
            "Chapter 1 . 5",
            "\u{7b2c}1.5\u{8bdd}",
            "Episode 1 - 2",
        ] {
            assert!(meaningful_title(title).is_none());
            let result = match_chapters(
                &[chapter("origin", 1.5, title)],
                &[chapter("target", 1.5, "Tide")],
            );
            assert_eq!(result.matches.len(), 1);
            assert_eq!(result.matches[0].reason, ChapterMatchReason::UniqueNumber);
        }
    }
}
