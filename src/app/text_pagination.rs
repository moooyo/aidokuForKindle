/// Splits text without changing its characters or exceeding the scalar budget.
/// A zero budget is treated as one. Empty text keeps one empty page.
pub(super) fn paginate_text(text: &str, budget: usize) -> Vec<String> {
    if text.is_empty() {
        return vec![String::new()];
    }

    let budget = budget.max(1);
    let window_limit = budget.saturating_add(1);
    let mut characters = Vec::with_capacity(text.len().min(window_limit));
    let mut pages = Vec::new();
    let mut remaining = text;

    while !remaining.is_empty() {
        characters.clear();
        characters.extend(remaining.char_indices().take(window_limit));
        if characters.len() <= budget {
            pages.push(remaining.to_owned());
            break;
        }

        // Avoid short pages for a paragraph ending near the start.
        let paragraph_start = budget / 2 + budget % 2;
        let end = (paragraph_start..=budget)
            .rev()
            .find(|&end| is_paragraph_boundary(&characters, end))
            .or_else(|| {
                (1..=budget)
                    .rev()
                    .find(|&end| is_word_boundary(&characters, end))
            })
            // A word longer than a page must be split to make progress.
            .unwrap_or(budget);
        let end_byte = characters[end].0;
        pages.push(remaining[..end_byte].to_owned());
        remaining = &remaining[end_byte..];
    }

    pages
}

fn is_paragraph_boundary(characters: &[(usize, char)], end: usize) -> bool {
    match characters[end - 1].1 {
        '\n' | '\u{0085}' | '\u{2028}' | '\u{2029}' => true,
        '\r' => characters.get(end).is_none_or(|&(_, next)| next != '\n'),
        _ => false,
    }
}

fn is_word_boundary(characters: &[(usize, char)], end: usize) -> bool {
    let previous = characters[end - 1].1;
    let next = characters.get(end).map(|&(_, next)| next);
    if previous == '\r' && next == Some('\n') {
        return false;
    }

    previous.is_whitespace() || next.is_some_and(char::is_whitespace)
}

#[cfg(test)]
mod tests {
    use super::paginate_text;

    #[test]
    fn empty_text_keeps_one_page() {
        assert_eq!(paginate_text("", 320), [""]);
    }

    #[test]
    fn keeps_words_that_end_exactly_at_the_budget() {
        assert_eq!(
            paginate_text("alpha beta gamma delta", 10),
            ["alpha beta", " gamma ", "delta"]
        );
    }

    #[test]
    fn prefers_a_paragraph_boundary_in_the_latter_half() {
        assert_eq!(
            paginate_text("alpha beta\n gamma delta epsilon", 20),
            ["alpha beta\n", " gamma delta epsilon"]
        );
    }

    #[test]
    fn skips_an_early_paragraph_boundary_for_a_fuller_page() {
        let pages = paginate_text("A\nbeta gamma delta epsilon", 16);
        assert_eq!(pages[0], "A\nbeta gamma ");
    }

    #[test]
    fn preserves_unicode_whitespace_and_line_endings() {
        let text = "  café\t東京\r\n\n🙂 alpha\u{00a0}beta \r\n尾  ";
        let pages = paginate_text(text, 8);

        assert_eq!(pages.concat(), text);
        assert!(pages.iter().all(|page| !page.is_empty()));
        assert!(pages.iter().all(|page| page.chars().count() <= 8));
        assert!(
            pages
                .windows(2)
                .all(|pair| { !(pair[0].ends_with('\r') && pair[1].starts_with('\n')) })
        );
    }

    #[test]
    fn splits_only_oversized_words_at_scalar_boundaries() {
        assert_eq!(paginate_text("abcdefghijk", 4), ["abcd", "efgh", "ijk"]);
        assert_eq!(
            paginate_text("東京の夜空を読む", 3),
            ["東京の", "夜空を", "読む"]
        );
    }

    #[test]
    fn keeps_a_normal_word_intact_after_leading_whitespace() {
        assert_eq!(paginate_text(" abcde", 5), [" ", "abcde"]);
    }

    #[test]
    fn zero_budget_still_makes_progress() {
        assert_eq!(paginate_text("é🙂", 0), ["é", "🙂"]);
    }

    #[test]
    fn an_unbounded_budget_does_not_overflow() {
        assert_eq!(paginate_text("alpha beta", usize::MAX), ["alpha beta"]);
    }
}
