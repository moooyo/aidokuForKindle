//! Read-only HTML host for the Aidoku source ABI.
//!
//! The ABI names and numeric kinds follow the MIT-licensed Aidoku Rust source
//! API (Copyright (c) 2022-2025 Skittyblock). This implementation uses scraper
//! documents directly; no Swift AidokuRunner code is included.

use std::{
    collections::{BTreeMap, HashSet},
    sync::Arc,
};

use anyhow::{Result, bail, ensure};
use ego_tree::{NodeId, NodeRef, iter::Edge};
use scraper::{ElementRef, Html, Node, Selector};

const FIRST_HTML_DESCRIPTOR: i32 = 1_000_000;
const MAX_RESOURCES: usize = 2_048;
const MAX_HTML_BYTES: usize = 8 * 1024 * 1024;
const MAX_HTML_NODES: usize = 200_000;
const STORE_LIMITS: StoreLimits = StoreLimits {
    resources: MAX_RESOURCES,
    input_bytes: 16 * 1024 * 1024,
    document_nodes: MAX_HTML_NODES,
    node_references: 500_000,
};

#[derive(Clone, Copy)]
struct StoreLimits {
    resources: usize,
    input_bytes: usize,
    document_nodes: usize,
    node_references: usize,
}

#[derive(Default)]
struct Usage {
    input_bytes: usize,
    document_nodes: usize,
    node_references: usize,
}

#[derive(Debug)]
pub enum HtmlOutput {
    Int(i32),
    Text(String),
}

#[derive(Debug)]
struct HtmlDocument {
    html: Html,
    base_url: String,
    input_bytes: usize,
    node_count: usize,
}

#[derive(Debug, Clone)]
struct Handle {
    document: Arc<HtmlDocument>,
    nodes: Vec<NodeId>,
    list: bool,
}

/// Owns documents independently of the temporary source-side descriptors.
/// Removing an element descriptor does not invalidate another selected node.
#[derive(Debug)]
pub struct HtmlStore {
    resources: BTreeMap<i32, Handle>,
    next_descriptor: i32,
}

impl Default for HtmlStore {
    fn default() -> Self {
        Self {
            resources: BTreeMap::new(),
            next_descriptor: FIRST_HTML_DESCRIPTOR,
        }
    }
}

impl HtmlStore {
    pub fn destroy(&mut self, descriptor: i32) -> bool {
        self.resources.remove(&descriptor).is_some()
    }

    /// String arguments are read from WASM memory by the caller before entering
    /// this method. `index` is used only by the list `get` operation.
    pub fn dispatch(
        &mut self,
        name: &str,
        descriptor: i32,
        index: i32,
        text: &str,
        other: &str,
    ) -> Result<HtmlOutput> {
        match name {
            "parse" | "parse_fragment" => return self.parse(text, other, name == "parse_fragment"),
            "escape" => return Ok(HtmlOutput::Text(escape_text(text)?)),
            "unescape" => {
                ensure!(
                    text.len() <= MAX_HTML_BYTES,
                    "Aidoku HTML output exceeds its byte limit"
                );
                return Ok(HtmlOutput::Text(unescape_text(text)));
            }
            "set_attr" | "remove_attr" | "set_text" | "set_html" | "prepend" | "append"
            | "add_class" | "remove_class" | "remove" => {
                bail!("Unsupported Aidoku HTML mutation: html::{name}")
            }
            _ => {}
        }

        let Some(handle) = self.resources.get(&descriptor).cloned() else {
            return Ok(HtmlOutput::Int(-1));
        };

        let first = handle.nodes.first().copied();
        let first_node = first.and_then(|id| handle.document.html.tree.get(id));

        let output = match name {
            "kind" => HtmlOutput::Int(if handle.list {
                6
            } else {
                first_node.map(node_kind).unwrap_or(0)
            }),
            "size" => HtmlOutput::Int(handle.nodes.len().min(i32::MAX as usize) as i32),
            "get" | "first" | "last" => {
                let id = match name {
                    "first" => first,
                    "last" => handle.nodes.last().copied(),
                    _ => usize::try_from(index)
                        .ok()
                        .and_then(|value| handle.nodes.get(value).copied()),
                };
                return match id {
                    Some(id) => self.store(Handle {
                        document: handle.document,
                        nodes: vec![id],
                        list: false,
                    }),
                    None => Ok(HtmlOutput::Int(-5)),
                };
            }
            "select" | "select_first" => {
                let Ok(selector) = Selector::parse(text) else {
                    return Ok(HtmlOutput::Int(-4));
                };
                let mut selected = Vec::new();
                let mut seen = HashSet::new();
                for id in &handle.nodes {
                    let Some(node) = handle.document.html.tree.get(*id) else {
                        continue;
                    };
                    let matches: Vec<_> =
                        if node.value().is_document() || node.value().is_fragment() {
                            handle
                                .document
                                .html
                                .select(&selector)
                                .map(|element| element.id())
                                .collect()
                        } else if let Some(element) = ElementRef::wrap(node) {
                            element
                                .select(&selector)
                                .map(|element| element.id())
                                .collect()
                        } else {
                            Vec::new()
                        };
                    for matching in matches {
                        if seen.insert(matching) {
                            selected.push(matching);
                        }
                    }
                }
                if name == "select_first" {
                    selected.truncate(1);
                    if selected.is_empty() {
                        return Ok(HtmlOutput::Int(-5));
                    }
                }
                return self.store(Handle {
                    document: handle.document,
                    nodes: selected,
                    list: name == "select",
                });
            }
            "children" | "child_nodes" | "parent" | "siblings" | "next" | "previous" => {
                let mut selected = Vec::new();
                let mut seen = HashSet::new();
                for id in &handle.nodes {
                    let Some(node) = handle.document.html.tree.get(*id) else {
                        continue;
                    };
                    let relatives: Vec<_> = match name {
                        "children" => node
                            .children()
                            .filter(|child| child.value().is_element())
                            .collect(),
                        "child_nodes" => node.children().collect(),
                        "parent" => node.parent().into_iter().collect(),
                        "siblings" => node
                            .parent()
                            .map(|parent| {
                                parent
                                    .children()
                                    .filter(|sibling| {
                                        sibling.id() != node.id() && sibling.value().is_element()
                                    })
                                    .collect()
                            })
                            .unwrap_or_default(),
                        "next" => node
                            .next_siblings()
                            .find(|sibling| sibling.value().is_element())
                            .into_iter()
                            .collect(),
                        "previous" => node
                            .prev_siblings()
                            .find(|sibling| sibling.value().is_element())
                            .into_iter()
                            .collect(),
                        _ => Vec::new(),
                    };
                    for relative in relatives {
                        if seen.insert(relative.id()) {
                            selected.push(relative.id());
                        }
                    }
                }
                let list = matches!(name, "children" | "child_nodes" | "siblings");
                if !list {
                    selected.truncate(1);
                    if selected.is_empty() {
                        return Ok(HtmlOutput::Int(-5));
                    }
                }
                return self.store(Handle {
                    document: handle.document,
                    nodes: selected,
                    list,
                });
            }
            "base_uri" => HtmlOutput::Text(handle.document.base_url.clone()),
            "has_attr" | "attr" => {
                let key = text.strip_prefix("abs:").unwrap_or(text);
                let attribute = handle
                    .nodes
                    .iter()
                    .filter_map(|id| handle.document.html.tree.get(*id))
                    .filter_map(ElementRef::wrap)
                    .find_map(|element| element.attr(key).map(str::to_owned));
                if name == "has_attr" {
                    HtmlOutput::Int(i32::from(attribute.is_some()))
                } else if let Some(value) = attribute {
                    HtmlOutput::Text(if text.starts_with("abs:") {
                        absolute_url(&handle.document.base_url, &value)
                    } else {
                        value
                    })
                } else {
                    HtmlOutput::Text(String::new())
                }
            }
            "id" | "tag_name" | "class_name" | "has_class" => {
                let element = first_node.and_then(ElementRef::wrap);
                match name {
                    "id" => HtmlOutput::Text(
                        element
                            .and_then(|value| value.value().id())
                            .unwrap_or_default()
                            .into(),
                    ),
                    "tag_name" => HtmlOutput::Text(
                        element
                            .map(|value| value.value().name().to_owned())
                            .unwrap_or_default(),
                    ),
                    "class_name" => HtmlOutput::Text(
                        element
                            .and_then(|value| value.attr("class"))
                            .unwrap_or_default()
                            .into(),
                    ),
                    _ => {
                        HtmlOutput::Int(i32::from(element.is_some_and(|value| {
                            value.value().classes().any(|class| class == text)
                        })))
                    }
                }
            }
            "text" | "untrimmed_text" | "own_text" | "data" => {
                let mut content = String::new();
                for id in &handle.nodes {
                    let Some(node) = handle.document.html.tree.get(*id) else {
                        continue;
                    };
                    if handle.list && !content.is_empty() && name != "data" {
                        append_limited(&mut content, " ", MAX_HTML_BYTES)?;
                    }
                    append_limited(&mut content, &node_text(node, name)?, MAX_HTML_BYTES)?;
                }
                if matches!(name, "text" | "own_text") {
                    let mut normalized = String::with_capacity(content.len());
                    for word in content.split_whitespace() {
                        if !normalized.is_empty() {
                            append_limited(&mut normalized, " ", MAX_HTML_BYTES)?;
                        }
                        append_limited(&mut normalized, word, MAX_HTML_BYTES)?;
                    }
                    content = normalized;
                }
                HtmlOutput::Text(content)
            }
            "html" | "outer_html" => {
                let mut output = String::new();
                for id in &handle.nodes {
                    let Some(node) = handle.document.html.tree.get(*id) else {
                        continue;
                    };
                    if node.value().is_document() || node.value().is_fragment() {
                        append_limited(&mut output, &handle.document.html.html(), MAX_HTML_BYTES)?;
                    } else if let Some(element) = ElementRef::wrap(node) {
                        append_limited(
                            &mut output,
                            &if name == "html" {
                                element.inner_html()
                            } else {
                                element.html()
                            },
                            MAX_HTML_BYTES,
                        )?;
                    } else if let Node::Text(value) = node.value() {
                        append_limited(&mut output, &escape_text(value)?, MAX_HTML_BYTES)?;
                    } else if let Node::Comment(value) = node.value() {
                        append_limited(&mut output, "<!--", MAX_HTML_BYTES)?;
                        append_limited(&mut output, value, MAX_HTML_BYTES)?;
                        append_limited(&mut output, "-->", MAX_HTML_BYTES)?;
                    }
                }
                HtmlOutput::Text(output)
            }
            _ => bail!("Unsupported Aidoku HTML capability: html::{name}"),
        };
        Ok(output)
    }

    fn parse(&mut self, input: &str, base_url: &str, fragment: bool) -> Result<HtmlOutput> {
        if input.len() > MAX_HTML_BYTES {
            bail!("Aidoku HTML exceeds the 8 MiB document limit");
        }
        ensure!(
            self.resources.len() < STORE_LIMITS.resources,
            "Aidoku HTML descriptor limit reached"
        );
        let usage = self.usage(None);
        ensure!(
            usage
                .input_bytes
                .saturating_add(input.len())
                .saturating_add(base_url.len())
                <= STORE_LIMITS.input_bytes,
            "Aidoku HTML document storage exceeds its byte limit"
        );
        ensure!(
            usage.document_nodes < STORE_LIMITS.document_nodes,
            "Aidoku HTML document storage exceeds its DOM node limit"
        );
        let html = if fragment {
            Html::parse_fragment(input)
        } else {
            Html::parse_document(input)
        };
        let node_count = html.tree.nodes().count();
        if node_count > MAX_HTML_NODES {
            bail!("Aidoku HTML exceeds the DOM node limit");
        }
        let base_selector =
            Selector::parse("base[href]").expect("The static base selector is valid");
        let base = html
            .select(&base_selector)
            .next()
            .and_then(|element| element.attr("href"))
            .map(|href| absolute_url(base_url, href))
            .filter(|value| !value.is_empty())
            .unwrap_or_else(|| base_url.to_owned());
        let root = html.tree.root().id();
        let input_bytes = input.len().saturating_add(base.len());
        self.store(Handle {
            document: Arc::new(HtmlDocument {
                html,
                base_url: base,
                input_bytes,
                node_count,
            }),
            nodes: vec![root],
            list: false,
        })
    }

    fn store(&mut self, handle: Handle) -> Result<HtmlOutput> {
        self.check_store_limits(&handle, STORE_LIMITS)?;
        let descriptor = self.next_descriptor;
        self.next_descriptor = self
            .next_descriptor
            .checked_add(1)
            .ok_or_else(|| anyhow::anyhow!("Aidoku HTML descriptor space exhausted"))?;
        self.resources.insert(descriptor, handle);
        Ok(HtmlOutput::Int(descriptor))
    }

    fn check_store_limits(&self, additional: &Handle, limits: StoreLimits) -> Result<()> {
        ensure!(
            self.resources.len() < limits.resources,
            "Aidoku HTML descriptor limit reached"
        );
        let usage = self.usage(Some(additional));
        ensure!(
            usage.input_bytes <= limits.input_bytes,
            "Aidoku HTML document storage exceeds its byte limit"
        );
        ensure!(
            usage.document_nodes <= limits.document_nodes,
            "Aidoku HTML document storage exceeds its DOM node limit"
        );
        ensure!(
            usage.node_references <= limits.node_references,
            "Aidoku HTML selected-node storage exceeds its reference limit"
        );
        Ok(())
    }

    /// Counts each retained document once even if many descriptors share it.
    /// Input bytes and DOM node counts are cached when parsing, so checking the
    /// bounded descriptor table never walks a document tree again.
    fn usage(&self, additional: Option<&Handle>) -> Usage {
        let mut usage = Usage::default();
        let mut documents = HashSet::new();
        for handle in self.resources.values().chain(additional) {
            usage.node_references = usage.node_references.saturating_add(handle.nodes.len());
            if documents.insert(Arc::as_ptr(&handle.document)) {
                usage.input_bytes = usage
                    .input_bytes
                    .saturating_add(handle.document.input_bytes);
                usage.document_nodes = usage
                    .document_nodes
                    .saturating_add(handle.document.node_count);
            }
        }
        usage
    }
}

fn node_kind(node: NodeRef<'_, Node>) -> i32 {
    match node.value() {
        Node::Document | Node::Fragment => 7,
        Node::Element(_) => 5,
        Node::Comment(_) => 4,
        Node::Text(_) => {
            if node
                .parent()
                .and_then(ElementRef::wrap)
                .is_some_and(|element| matches!(element.value().name(), "script" | "style"))
            {
                3
            } else {
                2
            }
        }
        _ => 1,
    }
}

fn node_text(node: NodeRef<'_, Node>, operation: &str) -> Result<String> {
    if operation == "own_text" {
        let mut output = String::new();
        for value in node.children().filter_map(|child| child.value().as_text()) {
            append_limited(&mut output, value, MAX_HTML_BYTES)?;
        }
        return Ok(output);
    }
    let mut output = String::new();
    for edge in node.traverse() {
        match edge {
            Edge::Open(current) => {
                if let Node::Text(value) = current.value() {
                    let data = current
                        .parent()
                        .and_then(ElementRef::wrap)
                        .is_some_and(|element| {
                            matches!(element.value().name(), "script" | "style")
                        });
                    if (operation == "data" && data) || (operation != "data" && !data) {
                        append_limited(&mut output, value, MAX_HTML_BYTES)?;
                    }
                } else if let Some(element) = current.value().as_element() {
                    if operation == "text"
                        && is_block(element.name())
                        && !output.ends_with(char::is_whitespace)
                    {
                        append_limited(&mut output, " ", MAX_HTML_BYTES)?;
                    }
                }
            }
            Edge::Close(current) => {
                if operation == "text"
                    && current
                        .value()
                        .as_element()
                        .is_some_and(|element| is_block(element.name()))
                    && !output.ends_with(char::is_whitespace)
                {
                    append_limited(&mut output, " ", MAX_HTML_BYTES)?;
                }
            }
        }
    }
    Ok(output)
}

fn is_block(name: &str) -> bool {
    matches!(
        name,
        "address"
            | "article"
            | "aside"
            | "blockquote"
            | "br"
            | "dd"
            | "div"
            | "dl"
            | "dt"
            | "fieldset"
            | "figcaption"
            | "figure"
            | "footer"
            | "form"
            | "h1"
            | "h2"
            | "h3"
            | "h4"
            | "h5"
            | "h6"
            | "header"
            | "hr"
            | "li"
            | "main"
            | "nav"
            | "ol"
            | "p"
            | "pre"
            | "section"
            | "table"
            | "td"
            | "th"
            | "tr"
            | "ul"
    )
}

fn absolute_url(base: &str, relative: &str) -> String {
    reqwest::Url::parse(relative)
        .or_else(|_| reqwest::Url::parse(base).and_then(|url| url.join(relative)))
        .map(|url| url.to_string())
        .unwrap_or_default()
}

fn escape_text(input: &str) -> Result<String> {
    ensure!(
        input.len() <= MAX_HTML_BYTES,
        "Aidoku HTML output exceeds its byte limit"
    );
    let mut output = String::with_capacity(input.len());
    for character in input.chars() {
        let mut utf8 = [0; 4];
        let text = match character {
            '&' => "&amp;",
            '<' => "&lt;",
            '>' => "&gt;",
            _ => character.encode_utf8(&mut utf8),
        };
        append_limited(&mut output, text, MAX_HTML_BYTES)?;
    }
    Ok(output)
}

/// Checks the combined byte length before growing the output. A selected list
/// may contain overlapping ancestor subtrees, so document size alone does not
/// bound the size of its combined text or serialized HTML.
fn append_limited(output: &mut String, addition: &str, limit: usize) -> Result<()> {
    ensure!(
        output.len() <= limit && addition.len() <= limit.saturating_sub(output.len()),
        "Aidoku HTML output exceeds its byte limit"
    );
    output.push_str(addition);
    Ok(())
}

fn unescape_text(input: &str) -> String {
    // RCDATA parsing decodes the complete HTML entity set without treating
    // markup in the supplied text as elements.
    let html = Html::parse_document(&format!(
        "<textarea>{}</textarea>",
        input.replace('<', "&lt;")
    ));
    let selector = Selector::parse("textarea").expect("The static textarea selector is valid");
    html.select(&selector)
        .next()
        .map(|element| element.text().collect::<String>())
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn descriptor(output: HtmlOutput) -> i32 {
        match output {
            HtmlOutput::Int(value) => value,
            HtmlOutput::Text(_) => panic!("Expected a descriptor"),
        }
    }

    fn text(output: HtmlOutput) -> String {
        match output {
            HtmlOutput::Text(value) => value,
            HtmlOutput::Int(_) => panic!("Expected a string"),
        }
    }

    #[test]
    fn selected_nodes_keep_their_document_and_resolve_base_urls() {
        let mut store = HtmlStore::default();
        let root = descriptor(
            store
                .dispatch(
                    "parse",
                    0,
                    0,
                    "<base href='/comics/'><a class='chapter' href='42?page=2'>Read</a>",
                    "https://example.test/library/",
                )
                .unwrap(),
        );
        let link = descriptor(
            store
                .dispatch("select_first", root, 0, "a.chapter", "")
                .unwrap(),
        );
        assert!(store.destroy(root));
        assert_eq!(
            text(store.dispatch("attr", link, 0, "abs:href", "").unwrap()),
            "https://example.test/comics/42?page=2"
        );
        assert_eq!(
            text(store.dispatch("text", link, 0, "", "").unwrap()),
            "Read"
        );
    }

    #[test]
    fn chapter_lists_preserve_order_and_distinguish_no_matches() {
        let mut store = HtmlStore::default();
        let root = descriptor(
            store
                .dispatch(
                    "parse",
                    0,
                    0,
                    "<ol><li>Chapter 1</li><li>Chapter 2</li></ol>",
                    "",
                )
                .unwrap(),
        );
        let list = descriptor(store.dispatch("select", root, 0, "li", "").unwrap());
        assert_eq!(
            descriptor(store.dispatch("size", list, 0, "", "").unwrap()),
            2
        );
        let second = descriptor(store.dispatch("get", list, 1, "", "").unwrap());
        assert_eq!(
            text(store.dispatch("text", second, 0, "", "").unwrap()),
            "Chapter 2"
        );
        assert_eq!(
            descriptor(store.dispatch("get", list, -1, "", "").unwrap()),
            -5
        );
        let empty = descriptor(store.dispatch("select", root, 0, "img", "").unwrap());
        assert_eq!(
            descriptor(store.dispatch("size", empty, 0, "", "").unwrap()),
            0
        );
        assert_eq!(
            descriptor(store.dispatch("select_first", root, 0, "img", "").unwrap()),
            -5
        );
    }

    #[test]
    fn text_separates_blocks_and_keeps_script_data_out_of_descriptions() {
        let mut store = HtmlStore::default();
        let root = descriptor(store.dispatch("parse", 0, 0,
            "<div><p>Hello <b>reader</b></p><p>Next</p><script>window.pages = [1];</script></div>", "").unwrap());
        let description = descriptor(store.dispatch("select_first", root, 0, "div", "").unwrap());
        assert_eq!(
            text(store.dispatch("text", description, 0, "", "").unwrap()),
            "Hello reader Next"
        );
        assert_eq!(
            text(store.dispatch("data", description, 0, "", "").unwrap()),
            "window.pages = [1];"
        );
        assert_eq!(
            text(
                store
                    .dispatch("unescape", 0, 0, "&copy; &#x4E66; <literal>", "")
                    .unwrap()
            ),
            "\u{00a9} \u{4e66} <literal>"
        );
    }

    #[test]
    fn unsupported_mutation_is_an_error_and_invalid_selectors_are_reported() {
        let mut store = HtmlStore::default();
        let root = descriptor(store.dispatch("parse", 0, 0, "<p>Text</p>", "").unwrap());
        assert_eq!(
            descriptor(store.dispatch("select", root, 0, "[", "").unwrap()),
            -4
        );
        assert!(
            store
                .dispatch("set_text", root, 0, "Replacement", "")
                .is_err()
        );
    }

    #[test]
    fn html_budget_counts_unique_documents_and_all_selected_references() {
        let mut store = HtmlStore::default();
        let root = descriptor(store.dispatch("parse", 0, 0, "<p>Text</p>", "").unwrap());
        let original = store.resources[&root].clone();
        let selected = Handle {
            document: original.document.clone(),
            nodes: original.nodes.clone(),
            list: true,
        };
        let limits = StoreLimits {
            resources: 2,
            input_bytes: original.document.input_bytes,
            document_nodes: original.document.node_count,
            node_references: 2,
        };
        assert!(store.check_store_limits(&selected, limits).is_ok());
        let excessive_references = Handle {
            nodes: vec![original.nodes[0]; 3],
            ..selected.clone()
        };
        assert!(
            store
                .check_store_limits(&excessive_references, limits)
                .is_err()
        );
        descriptor(store.store(selected).unwrap());
        assert!(store.check_store_limits(&original, limits).is_err());
        assert!(store.destroy(root));
        let second_document =
            descriptor(store.dispatch("parse", 0, 0, "<p>Another</p>", "").unwrap());
        let another = store.resources[&second_document].clone();
        assert!(
            store
                .check_store_limits(
                    &another,
                    StoreLimits {
                        resources: 4,
                        ..limits
                    }
                )
                .is_err()
        );
    }

    #[test]
    fn output_budget_checks_each_append_before_growing_the_string() {
        let mut output = String::new();
        append_limited(&mut output, "ab", 4).unwrap();
        append_limited(&mut output, "\u{00e9}", 4).unwrap();
        assert_eq!(output.len(), 4);
        assert!(append_limited(&mut output, "c", 4).is_err());
        assert_eq!(output, "ab\u{00e9}");
        assert!(append_limited(&mut output, "", 3).is_err());
        assert_eq!(output, "ab\u{00e9}");
    }
}
