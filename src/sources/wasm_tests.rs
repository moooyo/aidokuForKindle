use std::{
    collections::BTreeMap,
    fs,
    io::{Cursor, Read, Write},
    net::TcpListener,
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    thread,
    time::{Duration, Instant},
};

use reqwest::blocking::Client;
use serde::Serialize;

use crate::domain::{SourceConfig, compound_id};

use super::super::abi::{Chapter, Listing, ListingKind, Manga, MangaPageResult, Page, PageContent};
use super::{WasmSource, zip_request};

const SEARCH_RESULT: i32 = 1_024;
const MANGA_RESULT: i32 = 4_096;
const PAGES_RESULT: i32 = 8_192;

fn header_value<'a>(headers: &'a BTreeMap<String, String>, name: &str) -> Option<&'a str> {
    headers
        .iter()
        .find_map(|(key, value)| key.eq_ignore_ascii_case(name).then_some(value.as_str()))
}

#[test]
fn sparse_catalog_entries_preserve_previously_loaded_chapter_context() {
    let fixture = fixture(&search_result());
    let full = manga();
    fixture.source.cache_manga(&full).unwrap();
    let sparse = Manga {
        key: full.key.clone(),
        title: full.title.clone(),
        ..Manga::default()
    };
    let merged = fixture.source.cache_manga(&sparse).unwrap();
    assert_eq!(merged.chapters.as_ref().unwrap()[0].key, "chapter-7");
    assert_eq!(
        merged.chapters.as_ref().unwrap()[0].language.as_deref(),
        Some("en")
    );
    assert_eq!(merged.authors.as_ref().unwrap()[0], "Example author");
}

#[test]
fn unchanged_merged_metadata_keeps_the_existing_file() {
    let fixture = fixture(&search_result());
    let full = manga();
    fixture.source.cache_manga(&full).unwrap();
    let path = fixture.source.manga_path(&full.key);
    let original = fs::File::open(&path).unwrap();
    let before = original.metadata().unwrap();
    let original_bytes = fs::read(&path).unwrap();
    for _ in 0..4 {
        fixture.source.cache_manga(&full).unwrap();
        fixture
            .source
            .cache_manga(&Manga {
                key: full.key.clone(),
                title: full.title.clone(),
                ..Manga::default()
            })
            .unwrap();
    }
    let after = fs::metadata(&path).unwrap();
    assert_eq!(after.modified().unwrap(), before.modified().unwrap());
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        assert_eq!(after.ino(), before.ino());
    }
    assert_eq!(fs::read(&path).unwrap(), original_bytes);
    let mut changed = full;
    changed.title = "Updated metadata title".into();
    fixture.source.cache_manga(&changed).unwrap();
    let updated: Manga = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
    assert_eq!(updated.title, "Updated metadata title");
    assert_eq!(updated.chapters.unwrap()[0].key, "chapter-7");
}

#[test]
fn the_global_permit_serializes_complete_wasm_instance_lifetimes() {
    use std::sync::mpsc::{self, RecvTimeoutError};

    let active = fixture(&search_result());
    assert!(matches!(
        super::EXECUTION.try_lock(),
        Err(std::sync::TryLockError::WouldBlock)
    ));
    let (attempting_sender, attempting_receiver) = mpsc::channel();
    let (entered_sender, entered_receiver) = mpsc::channel();
    let second = thread::spawn(move || {
        attempting_sender.send(()).unwrap();
        let next = fixture(&search_result());
        entered_sender.send(()).unwrap();
        drop(next);
    });
    attempting_receiver
        .recv_timeout(Duration::from_secs(5))
        .unwrap();
    assert!(matches!(
        entered_receiver.recv_timeout(Duration::from_millis(100)),
        Err(RecvTimeoutError::Timeout)
    ));
    drop(active);
    entered_receiver
        .recv_timeout(Duration::from_secs(10))
        .unwrap();
    second.join().unwrap();
}

#[test]
fn a_waiting_constructor_cannot_start_a_guest_from_an_old_network_epoch() {
    use std::sync::mpsc;

    let active = fixture(&search_result());
    let directory = tempfile::tempdir().unwrap();
    let control = super::super::NetworkControl::open(directory.path()).unwrap();
    let old_epoch = control.epoch();
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    listener.set_nonblocking(true).unwrap();
    let url = format!("http://{}/must-not-start", listener.local_addr().unwrap());
    let payload = directory.path().join("Payload");
    fs::create_dir(&payload).unwrap();
    fs::write(payload.join("source.json"), br#"{"info":{"id":"en.test"}}"#).unwrap();
    let wat = format!(
        r#"(module
        (import "net" "init" (func $init (param i32) (result i32)))
        (import "net" "set_url" (func $set_url (param i32 i32 i32) (result i32)))
        (import "net" "send" (func $send (param i32) (result i32)))
        (memory (export "memory") 1)
        (func (export "start") (local $request i32)
            i32.const 0 call $init local.set $request
            local.get $request i32.const 1024 i32.const {} call $set_url drop
            local.get $request call $send drop)
        (func (export "free_result") (param i32))
        (func (export "get_search_manga_list") (param i32 i32 i32) (result i32) i32.const 0)
        (func (export "get_manga_update") (param i32 i32 i32) (result i32) i32.const 0)
        (func (export "get_page_list") (param i32 i32) (result i32) i32.const 0)
        {})"#,
        url.len(),
        data_segment(1024, url.as_bytes())
    );
    fs::write(payload.join("main.wasm"), wat::parse_str(wat).unwrap()).unwrap();
    let mut config = active.config.clone();
    config
        .options
        .insert("package_dir".into(), serde_json::json!(payload));
    let data_dir = directory.path().to_path_buf();
    let worker_control = control.clone();
    let (waiting, wait) = mpsc::channel();
    let worker = thread::spawn(move || {
        super::super::network::with_session(&worker_control, old_epoch, || {
            super::EXECUTION_WAIT_SIGNAL.with(|signal| *signal.borrow_mut() = Some(waiting));
            WasmSource::new(&data_dir, &config, client()).map(drop)
        })
    });
    wait.recv_timeout(Duration::from_secs(5)).unwrap();
    let resumed_epoch = control.suspend();
    assert_eq!(control.resume(), resumed_epoch);
    drop(active);
    let error = worker.join().unwrap().unwrap_err();
    assert!(super::super::is_network_interrupted(&error), "{error:#}");
    assert_eq!(
        error
            .downcast_ref::<super::super::NetworkInterrupted>()
            .unwrap()
            .epoch,
        resumed_epoch
    );
    assert!(
        !directory.path().join("sources/state").exists(),
        "A stale constructor must not initialize source preferences"
    );
    assert!(
        matches!(listener.accept(), Err(error) if error.kind() == std::io::ErrorKind::WouldBlock),
        "A stale constructor must not run the guest's start HTTP request"
    );
}

fn interrupt_after_next_guest_call(control: &super::super::NetworkControl) {
    let control = control.clone();
    super::AFTER_GUEST_CALL.with(|hook| {
        *hook.borrow_mut() = Some(Box::new(move || {
            control.suspend();
            control.resume();
        }));
    });
}

#[test]
fn an_interrupted_guest_result_is_freed_before_its_inputs_are_released() {
    let mut fixture = fixture(&search_result());
    let payload = std::path::PathBuf::from(fixture.config.options["package_dir"].as_str().unwrap());
    let wat = module_with(&search_result(), "", "").replace(
        r#"(func (export "free_result") (param i32))"#,
        r#"(func (export "free_result") (param i32) i32.const 64 i32.const 1 i32.store)"#,
    );
    fs::write(payload.join("main.wasm"), wat::parse_str(wat).unwrap()).unwrap();
    fixture = fixture.reload();
    let control = super::super::NetworkControl::open(fixture._directory.path()).unwrap();
    let epoch = control.epoch();
    interrupt_after_next_guest_call(&control);
    let error =
        super::super::network::with_session(&control, epoch, || fixture.source.search("needle", 1))
            .unwrap_err();
    assert!(super::super::is_network_interrupted(&error), "{error:#}");
    let memory = fixture
        .source
        .instance
        .get_memory(&fixture.source.store, "memory")
        .unwrap();
    let mut released = [0; 4];
    memory
        .read(&fixture.source.store, 64, &mut released)
        .unwrap();
    assert_eq!(
        i32::from_le_bytes(released),
        1,
        "The returned guest buffer must be freed even when its epoch is closed"
    );
    assert_eq!(fixture.source.store.data().resource_count(), 0);
    assert!(
        !fixture.source.manga_path("series-1").exists(),
        "An interrupted result must not publish metadata"
    );
}

#[test]
fn a_guest_trap_from_a_closed_epoch_reports_a_typed_interruption() {
    let mut fixture = fixture("unreachable");
    let control = super::super::NetworkControl::open(fixture._directory.path()).unwrap();
    let epoch = control.epoch();
    interrupt_after_next_guest_call(&control);
    let error =
        super::super::network::with_session(&control, epoch, || fixture.source.search("needle", 1))
            .unwrap_err();
    assert!(super::super::is_network_interrupted(&error), "{error:#}");
    assert_eq!(fixture.source.store.data().resource_count(), 0);
}

struct Fixture {
    _directory: tempfile::TempDir,
    source: WasmSource,
    config: SourceConfig,
}

fn fixture(search_body: &str) -> Fixture {
    fixture_with_module(search_body, "", "", None)
}

fn fixture_with_module(
    search_body: &str,
    imports: &str,
    declarations: &str,
    settings: Option<serde_json::Value>,
) -> Fixture {
    let directory = tempfile::tempdir().unwrap();
    let payload = directory.path().join("Payload");
    fs::create_dir(&payload).unwrap();
    fs::write(
        payload.join("source.json"),
        br#"{"info":{"id":"en.test","name":"Fixture source","version":1,"languages":["en"],"url":"https://example.invalid"}}"#,
    )
    .unwrap();
    if let Some(settings) = settings {
        fs::write(
            payload.join("settings.json"),
            serde_json::to_vec(&settings).unwrap(),
        )
        .unwrap();
    }
    fs::write(
        payload.join("main.wasm"),
        wat::parse_str(module_with(search_body, imports, declarations)).unwrap(),
    )
    .unwrap();
    let config = SourceConfig {
        id: "en.test".into(),
        name: "Fixture source".into(),
        kind: "wasm".into(),
        base_url: "https://example.invalid".into(),
        version: 1,
        languages: vec!["en".into()],
        enabled: true,
        options: BTreeMap::from([("package_dir".into(), serde_json::json!(payload))]),
        ..Default::default()
    };
    let source = WasmSource::new(directory.path(), &config, client()).unwrap();
    Fixture {
        _directory: directory,
        source,
        config,
    }
}

fn client() -> Client {
    Client::builder()
        .no_proxy()
        .timeout(Duration::from_secs(3))
        .build()
        .unwrap()
}

impl Fixture {
    fn reload(self) -> Self {
        let Self {
            _directory,
            source,
            config,
        } = self;
        drop(source);
        let source = WasmSource::new(_directory.path(), &config, client()).unwrap();
        Self {
            _directory,
            source,
            config,
        }
    }
}

fn chapter() -> Chapter {
    Chapter {
        key: "chapter-7".into(),
        title: Some("A real guest result".into()),
        chapter_number: Some(7.0),
        volume_number: Some(2.0),
        language: Some("en".into()),
        ..Default::default()
    }
}

fn manga() -> Manga {
    Manga {
        key: "series-1".into(),
        title: "Fixture manga".into(),
        cover: Some("https://example.invalid/cover.png".into()),
        authors: Some(vec!["Example author".into()]),
        chapters: Some(vec![chapter()]),
        ..Default::default()
    }
}

fn result_data(value: &impl Serialize) -> String {
    let value = postcard::to_allocvec(value).unwrap();
    let length = u32::try_from(value.len() + 8).unwrap().to_le_bytes();
    length
        .iter()
        .chain(length.iter())
        .chain(value.iter())
        .map(|byte| format!("\\{byte:02x}"))
        .collect()
}

fn wat_bytes(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("\\{byte:02x}")).collect()
}

fn data_segment(pointer: i32, bytes: &[u8]) -> String {
    format!("(data (i32.const {pointer}) \"{}\")", wat_bytes(bytes))
}

fn assert_memory(pointer: i32, bytes: &[u8]) -> String {
    bytes
        .iter()
        .enumerate()
        .map(|(offset, byte)| {
            format!(
                "i32.const {} i32.load8_u i32.const {byte} i32.ne if unreachable end\n",
                pointer + offset as i32
            )
        })
        .collect()
}

fn assert_buffer(bytes: &[u8]) -> String {
    let mut body = format!(
        "local.get $buffer call $buffer_len i32.const {} i32.ne if unreachable end\n\
         local.get $buffer i32.const 32768 i32.const {} call $read_buffer\n\
         i32.const 0 i32.ne if unreachable end\n",
        bytes.len(),
        bytes.len()
    );
    body.push_str(&assert_memory(32_768, bytes));
    body
}

fn module_with(search_body: &str, imports: &str, declarations: &str) -> String {
    let search_data = result_data(&MangaPageResult {
        entries: vec![manga()],
        has_next_page: false,
    });
    let manga_data = result_data(&manga());
    let pages_data = result_data(&vec![Page {
        content: PageContent::Url("https://example.invalid/chapter-7/page-1.png".into(), None),
        thumbnail: None,
        has_description: false,
        description: None,
    }]);
    format!(
        r#"(module
            (import "std" "buffer_len" (func $buffer_len (param i32) (result i32)))
            (import "std" "read_buffer" (func $read_buffer (param i32 i32 i32) (result i32)))
            (import "js" "context_create" (func $context_create (result i32)))
            {imports}
            (memory (export "memory") 1)
            (global $started (mut i32) (i32.const 0))
            (func (export "start")
                i32.const 1
                global.set $started)
            (func (export "free_result") (param i32))
            (func (export "get_search_manga_list") (param i32 i32 i32) (result i32)
                (local $resource i32)
                (local $other i32)
                (local $buffer i32)
                global.get $started
                i32.const 1
                i32.ne
                if unreachable end
                {search_body})
            (func (export "get_manga_update") (param i32 i32 i32) (result i32)
                local.get 0
                call $buffer_len
                i32.const 0
                i32.le_s
                if unreachable end
                i32.const {MANGA_RESULT})
            (func (export "get_page_list") (param i32 i32) (result i32)
                local.get 0
                call $buffer_len
                i32.const 0
                i32.le_s
                if unreachable end
                local.get 1
                call $buffer_len
                i32.const 0
                i32.le_s
                if unreachable end
                i32.const {PAGES_RESULT})
            (data (i32.const {SEARCH_RESULT}) "{search_data}")
            (data (i32.const {MANGA_RESULT}) "{manga_data}")
            (data (i32.const {PAGES_RESULT}) "{pages_data}")
            {declarations})"#
    )
}

fn search_result() -> String {
    format!("i32.const {SEARCH_RESULT}")
}

#[test]
fn executes_guest_search_and_decodes_the_postcard_result() {
    let mut fixture = fixture(&search_result());
    let books = fixture.source.search("needle", 1).unwrap();
    assert_eq!(books.len(), 1);
    assert_eq!(books[0].id, compound_id("en.test", "series-1"));
    assert_eq!(books[0].source_id, "en.test");
    assert_eq!(books[0].remote_id, "series-1");
    assert_eq!(books[0].title, "Fixture manga");
    assert_eq!(books[0].cover, "https://example.invalid/cover.png");
    assert_eq!(fixture.source.store.data().resource_count(), 0);
}

#[test]
fn page_results_preserve_the_guest_continuation_flag_and_the_terminal_page() {
    let mut fixture = fixture(&search_result());
    let memory = fixture
        .source
        .instance
        .get_memory(&fixture.source.store, "memory")
        .unwrap();
    let write_result = |source: &mut WasmSource, result: MangaPageResult| {
        let payload = postcard::to_allocvec(&result).unwrap();
        let length = (payload.len() as i32 + 8).to_le_bytes();
        let bytes = [length.as_slice(), length.as_slice(), payload.as_slice()].concat();
        memory
            .write(&mut source.store, SEARCH_RESULT as usize, &bytes)
            .unwrap();
    };
    write_result(
        &mut fixture.source,
        MangaPageResult {
            entries: vec![manga()],
            has_next_page: true,
        },
    );
    let first = fixture.source.search_page("needle", 1).unwrap();
    assert_eq!(first.books.len(), 1);
    assert!(
        first.has_next,
        "The source continuation flag is authoritative even for a short page"
    );
    let catalog = fixture.source.browse_page(1, "popular").unwrap();
    assert_eq!(catalog.books.len(), 1);
    assert!(catalog.has_next);
    write_result(
        &mut fixture.source,
        MangaPageResult {
            entries: Vec::new(),
            has_next_page: false,
        },
    );
    let last = fixture.source.search_page("needle", 2).unwrap();
    assert!(last.books.is_empty());
    assert!(!last.has_next);
}

#[test]
fn sends_query_bytes_to_guest_through_std_buffers() {
    let mut body = String::from(
        "local.get 0 call $buffer_len i32.const 6 i32.ne if unreachable end\n\
         local.get 1 i32.const 1 i32.ne if unreachable end\n\
         local.get 0 i32.const 32768 i32.const 6 call $read_buffer\n\
         i32.const 0 i32.ne if unreachable end\n",
    );
    for (offset, byte) in b"needle".iter().enumerate() {
        body.push_str(&format!(
            "i32.const {} i32.load8_u i32.const {byte} i32.ne if unreachable end\n",
            32_768 + offset
        ));
    }
    body.push_str(&search_result());
    let mut fixture = fixture(&body);
    assert_eq!(
        fixture.source.search("needle", 1).unwrap()[0].title,
        "Fixture manga"
    );
    assert!(fixture.source.search("wrong", 1).is_err());
    assert_eq!(fixture.source.store.data().resource_count(), 0);
}

#[test]
fn executes_chapter_and_page_exports_with_real_wire_arguments() {
    let mut fixture = fixture(&search_result());
    let book = fixture.source.search("needle", 1).unwrap().remove(0);
    let chapters = fixture.source.chapters(&book).unwrap();
    assert_eq!(fixture.source.store.data().resource_count(), 0);
    assert_eq!(chapters.len(), 1);
    assert_eq!(chapters[0].book_id, book.id);
    assert_eq!(chapters[0].source_id, "en.test");
    assert_eq!(chapters[0].remote_id, "chapter-7");
    assert_eq!(chapters[0].number, 7.0);
    assert_eq!(chapters[0].title, "A real guest result");
    let pages = fixture.source.pages(&book, &chapters[0]).unwrap();
    assert_eq!(pages.len(), 1);
    assert_eq!(pages[0].url, "https://example.invalid/chapter-7/page-1.png");
    assert!(pages[0].local_path.is_none());
    assert_eq!(fixture.source.store.data().resource_count(), 0);
}

#[test]
fn reports_unsupported_javascript_when_the_guest_calls_it() {
    let body = format!("call $context_create drop {}", search_result());
    let mut fixture = fixture(&body);
    let error = fixture.source.search("needle", 1).unwrap_err();
    assert!(format!("{error:#}").contains("JavaScript"));
}

#[test]
fn fuel_budget_stops_an_untrusted_infinite_loop() {
    let body = format!("(loop $spin br $spin) {}", search_result());
    let mut fixture = fixture(&body);
    let error = fixture.source.search("needle", 1).unwrap_err();
    let message = format!("{error:#}").to_ascii_lowercase();
    assert!(
        message.contains("fuel"),
        "Unexpected execution error: {message}"
    );
}

#[test]
fn rejects_a_guest_result_pointer_outside_linear_memory() {
    let mut fixture = fixture("i32.const 65532");
    assert!(fixture.source.search("needle", 1).is_err());
    assert_eq!(fixture.source.store.data().resource_count(), 0);
}

#[test]
fn argument_cleanup_preserves_guest_resources_with_the_same_integer_as_flags() {
    let mut fixture = fixture(&search_result());
    let retained = fixture
        .source
        .store
        .data_mut()
        .put_buffer(b"retained".to_vec())
        .unwrap();
    assert_eq!(retained, 1);
    let book = fixture
        .source
        .search("needle", retained as u32)
        .unwrap()
        .remove(0);
    fixture.source.chapters(&book).unwrap();
    assert_eq!(fixture.source.store.data().resource_count(), 1);
    fixture.source.store.data_mut().release_resource(retained);
    assert_eq!(fixture.source.store.data().resource_count(), 0);
}

#[test]
fn failed_image_exports_release_both_input_descriptors() {
    let declarations =
        r#"(func (export "get_image_request") (param i32 i32) (result i32) unreachable)"#;
    let mut fixture = fixture_with_module(&search_result(), "", declarations, None);
    let context = BTreeMap::from([("Referer".into(), "https://example.invalid/title".into())]);
    for _ in 0..8 {
        assert!(
            fixture
                .source
                .image_request("https://example.invalid/page.png", Some(&context))
                .is_err()
        );
        assert_eq!(fixture.source.store.data().resource_count(), 0);
    }
}

#[test]
fn long_chapter_image_requests_use_postcard_arguments_and_bounded_host_resources() {
    let url = "https://example.invalid/page.png";
    let context = BTreeMap::from([("Referer".into(), "https://example.invalid/title".into())]);
    let encoded_url = postcard::to_allocvec(&url).unwrap();
    let encoded_context = postcard::to_allocvec(&context).unwrap();
    let prefix_length = encoded_url.len() - url.len();
    let imports = r#"
        (import "net" "init" (func $request_init (param i32) (result i32)))
        (import "net" "set_url" (func $set_url (param i32 i32 i32) (result i32)))
        (import "net" "set_header" (func $set_header (param i32 i32 i32 i32 i32) (result i32)))"#;
    let declarations = format!(
        r#"(func (export "get_image_request") (param i32 i32) (result i32)
               (local $buffer i32)
               (local $request i32)
               (local $remaining i32)
               (local $cursor i32)
               (local $byte i32)
               local.get 0 local.set $buffer
               {}
               i32.const 0 call $request_init local.set $request
               local.get $request i32.const {} i32.const {} call $set_url
               i32.const 0 i32.ne if unreachable end
               local.get 1 i32.const 0 i32.ge_s
               if
                   local.get 1 local.set $buffer
                   {}
               else
                   local.get 1 i32.const -1 i32.ne if unreachable end
               end
               local.get $request i32.const 40000 i32.const 9 i32.const 41000 i32.const 12 call $set_header
               i32.const 0 i32.ne if unreachable end
               local.get $request i32.const 1 i32.shl local.set $remaining
               i32.const 42008 local.set $cursor
               (loop $encode
                   local.get $remaining i32.const 127 i32.and local.set $byte
                   local.get $remaining i32.const 7 i32.shr_u local.set $remaining
                   local.get $cursor local.get $byte
                   local.get $remaining i32.eqz
                   if (result i32) i32.const 0 else i32.const 128 end
                   i32.or i32.store8
                   local.get $cursor i32.const 1 i32.add local.set $cursor
                   local.get $remaining i32.const 0 i32.ne br_if $encode)
               i32.const 42000 local.get $cursor i32.const 42000 i32.sub i32.store
               i32.const 42004 local.get $cursor i32.const 42000 i32.sub i32.store
               i32.const 42000)
           {}
           {}"#,
        assert_buffer(&encoded_url),
        32_768 + prefix_length,
        url.len(),
        assert_buffer(&encoded_context),
        data_segment(40_000, b"X-Fixture"),
        data_segment(41_000, b"postcard-url")
    );
    let mut fixture = fixture_with_module(&search_result(), imports, &declarations, None);
    for _ in 0..1_500 {
        let request = fixture.source.image_request(url, Some(&context)).unwrap();
        assert_eq!(request.url, url);
        assert_eq!(
            header_value(&request.headers, "X-Fixture"),
            Some("postcard-url")
        );
        assert_eq!(fixture.source.store.data().resource_count(), 0);
    }
    for _ in 0..1_500 {
        let request = fixture.source.image_request(url, None).unwrap();
        assert_eq!(request.url, url);
        assert_eq!(
            header_value(&request.headers, "X-Fixture"),
            Some("postcard-url")
        );
        assert_eq!(fixture.source.store.data().resource_count(), 0);
    }
    let cover = fixture.source.cover_request(url).unwrap();
    assert_eq!(cover.url, url);
    assert_eq!(fixture.source.store.data().resource_count(), 0);
}

#[test]
fn guest_network_imports_send_a_real_request_and_read_the_response() {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let address = listener.local_addr().unwrap();
    listener.set_nonblocking(true).unwrap();
    let serve = move || -> Result<String, String> {
        let deadline = Instant::now() + Duration::from_secs(5);
        let mut connection = loop {
            match listener.accept() {
                Ok((connection, _)) => break connection,
                Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                    if Instant::now() >= deadline {
                        return Err("The guest did not connect to the mock server".into());
                    }
                    thread::sleep(Duration::from_millis(5));
                }
                Err(error) => return Err(error.to_string()),
            }
        };
        connection
            .set_read_timeout(Some(Duration::from_secs(3)))
            .map_err(|error| error.to_string())?;
        let mut request = Vec::new();
        while !request.windows(4).any(|bytes| bytes == b"\r\n\r\n") {
            let mut chunk = [0; 1_024];
            let count = connection
                .read(&mut chunk)
                .map_err(|error| error.to_string())?;
            if count == 0 {
                return Err("The guest closed its request before sending the headers".into());
            }
            request.extend_from_slice(&chunk[..count]);
            if request.len() > 8_192 {
                return Err("The guest request headers exceeded the fixture limit".into());
            }
        }
        connection.write_all(
            b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 8\r\nConnection: close\r\n\r\nguest-ok",
        ).map_err(|error| error.to_string())?;
        String::from_utf8(request).map_err(|error| error.to_string())
    };
    let url = format!("http://{address}/fixture/search?q=needle");
    let imports = r#"
        (import "net" "init" (func $request_init (param i32) (result i32)))
        (import "net" "set_url" (func $set_url (param i32 i32 i32) (result i32)))
        (import "net" "set_header" (func $set_header (param i32 i32 i32 i32 i32) (result i32)))
        (import "net" "send" (func $send (param i32) (result i32)))
        (import "net" "get_status_code" (func $status (param i32) (result i32)))
        (import "net" "data_len" (func $data_len (param i32) (result i32)))
        (import "net" "read_data" (func $read_data (param i32 i32 i32) (result i32)))"#;
    let declarations = [
        data_segment(40_000, url.as_bytes()),
        data_segment(41_000, b"X-Fixture"),
        data_segment(42_000, b"wasm-host-test"),
    ]
    .join("\n");
    let body = format!(
        "i32.const 0 call $request_init local.set $resource\n\
         local.get $resource i32.const 40000 i32.const {} call $set_url\n\
         i32.const 0 i32.ne if unreachable end\n\
         local.get $resource i32.const 41000 i32.const 9 i32.const 42000 i32.const 14 call $set_header\n\
         i32.const 0 i32.ne if unreachable end\n\
         local.get $resource call $send i32.const 0 i32.ne if unreachable end\n\
         local.get $resource call $status i32.const 200 i32.ne if unreachable end\n\
         local.get $resource call $data_len i32.const 8 i32.ne if unreachable end\n\
         local.get $resource i32.const 32768 i32.const 8 call $read_data\n\
         i32.const 0 i32.ne if unreachable end\n\
         {}\n{}",
        url.len(),
        assert_memory(32_768, b"guest-ok"),
        search_result()
    );
    let mut fixture = fixture_with_module(&body, imports, &declarations, None);
    let server = thread::spawn(serve);
    let result = fixture.source.search("needle", 1);
    let request = server.join().unwrap().unwrap();
    assert!(
        request.starts_with("GET /fixture/search?q=needle HTTP/1.1\r\n"),
        "{request}"
    );
    assert!(
        request
            .to_ascii_lowercase()
            .contains("\r\nx-fixture: wasm-host-test\r\n"),
        "{request}"
    );
    assert_eq!(result.unwrap()[0].title, "Fixture manga");
}

#[test]
fn guest_defaults_use_f32_schema_defaults_and_survive_a_new_source_instance() {
    let imports = r#"
        (import "defaults" "get" (func $defaults_get (param i32 i32) (result i32)))
        (import "defaults" "set" (func $defaults_set (param i32 i32 i32 i32) (result i32)))"#;
    let declarations = format!(
        "{}\n(data (i32.const 41000) \"{}\")",
        data_segment(40_000, b"quality"),
        result_data(&7.0_f32)
    );
    let check_seven = format!(
        "i32.const 40000 i32.const 7 call $defaults_get local.set $buffer\n{}",
        assert_buffer(&postcard::to_allocvec(&7.0_f32).unwrap())
    );
    let body = format!(
        "i32.const 40000 i32.const 7 call $defaults_get local.set $buffer\n\
         local.get $buffer call $buffer_len i32.const 4 i32.ne if unreachable end\n\
         local.get $buffer i32.const 32768 i32.const 4 call $read_buffer\n\
         i32.const 0 i32.ne if unreachable end\n\
         local.get 0 call $buffer_len i32.const 5 i32.eq\n\
         if\n\
             i32.const 32768 f32.load f32.const 3 f32.ne if unreachable end\n\
             i32.const 40000 i32.const 7 i32.const 3 i32.const 41000 call $defaults_set\n\
             i32.const 0 i32.ne if unreachable end\n\
         else\n\
             i32.const 32768 f32.load f32.const 7 f32.ne if unreachable end\n\
         end\n\
         {check_seven}\n{}",
        search_result()
    );
    let settings = serde_json::json!([{
        "type": "stepper", "key": "quality", "title": "Quality", "default": 3,
        "minimumValue": 1, "maximumValue": 10, "stepValue": 1
    }]);
    let mut fixture = fixture_with_module(&body, imports, &declarations, Some(settings));
    assert_eq!(
        fixture.source.search("write", 1).unwrap()[0].title,
        "Fixture manga"
    );
    fixture = fixture.reload();
    assert_eq!(
        fixture.source.search("read", 1).unwrap()[0].title,
        "Fixture manga"
    );
}

#[test]
fn guest_html_imports_parse_select_and_read_attribute_and_text_buffers() {
    let html =
        br#"<html><body><a class="chapter" href="/chapter/7">Chapter seven</a></body></html>"#;
    let base = b"https://example.invalid/title/";
    let imports = r#"
        (import "html" "parse" (func $html_parse (param i32 i32 i32 i32) (result i32)))
        (import "html" "select_first" (func $html_select (param i32 i32 i32) (result i32)))
        (import "html" "attr" (func $html_attr (param i32 i32 i32) (result i32)))
        (import "html" "text" (func $html_text (param i32) (result i32)))"#;
    let declarations = [
        data_segment(40_000, html),
        data_segment(42_000, base),
        data_segment(43_000, b"a.chapter"),
        data_segment(44_000, b"abs:href"),
    ]
    .join("\n");
    let body = format!(
        "i32.const 40000 i32.const {} i32.const 42000 i32.const {}\n\
         call $html_parse local.set $resource\n\
         local.get $resource i32.const 43000 i32.const 9 call $html_select local.set $other\n\
         local.get $other i32.const 44000 i32.const 8 call $html_attr local.set $buffer\n\
         {}\n\
         local.get $other call $html_text local.set $buffer\n\
         {}\n{}",
        html.len(),
        base.len(),
        assert_buffer(b"https://example.invalid/chapter/7"),
        assert_buffer(b"Chapter seven"),
        search_result()
    );
    let mut fixture = fixture_with_module(&body, imports, &declarations, None);
    assert_eq!(
        fixture.source.search("needle", 1).unwrap()[0].title,
        "Fixture manga"
    );
}

#[test]
fn dynamic_listings_drive_the_listing_export_instead_of_search() {
    let listings = vec![
        Listing {
            id: "recent".into(),
            name: "Recent updates".into(),
            kind: ListingKind::Default,
        },
        Listing {
            id: "popular".into(),
            name: "Popular titles".into(),
            kind: ListingKind::List,
        },
    ];
    let selected_bytes = postcard::to_allocvec(&listings[1]).unwrap();
    let mut listing_manga = manga();
    listing_manga.key = "popular-series".into();
    listing_manga.title = "Popular listing guest".into();
    let listing_result = MangaPageResult {
        entries: vec![listing_manga],
        has_next_page: false,
    };
    let declarations = format!(
        r#"(func (export "get_listings") (result i32) i32.const 12000)
           (func (export "get_manga_list") (param i32 i32) (result i32)
               (local $buffer i32)
               local.get 1 i32.const 1 i32.ne if unreachable end
               local.get 0 local.set $buffer
               {}
               i32.const 16000)
           (data (i32.const 12000) "{}")
           (data (i32.const 16000) "{}")"#,
        assert_buffer(&selected_bytes),
        result_data(&listings),
        result_data(&listing_result)
    );
    let mut fixture = fixture_with_module("unreachable", "", &declarations, None);
    assert_eq!(
        fixture.source.listings().unwrap(),
        vec![
            ("recent".into(), "Recent updates".into()),
            ("popular".into(), "Popular titles".into())
        ]
    );
    let books = fixture.source.browse(1, "popular").unwrap();
    assert_eq!(books.len(), 1);
    assert_eq!(books[0].remote_id, "popular-series");
    assert_eq!(books[0].title, "Popular listing guest");
}

#[test]
fn zip_pages_are_lazy_and_extract_two_pngs_through_the_shared_cache() {
    let mut first_png = Cursor::new(Vec::new());
    image::DynamicImage::ImageRgba8(image::RgbaImage::from_pixel(
        2,
        3,
        image::Rgba([40, 40, 40, 255]),
    ))
    .write_to(&mut first_png, image::ImageFormat::Png)
    .unwrap();
    let first_png = first_png.into_inner();
    let mut second_png = Cursor::new(Vec::new());
    image::DynamicImage::ImageRgba8(image::RgbaImage::from_pixel(
        3,
        2,
        image::Rgba([220, 220, 220, 255]),
    ))
    .write_to(&mut second_png, image::ImageFormat::Png)
    .unwrap();
    let second_png = second_png.into_inner();
    let mut archive = zip::ZipWriter::new(Cursor::new(Vec::new()));
    let options = zip::write::SimpleFileOptions::default()
        .compression_method(zip::CompressionMethod::Deflated);
    archive.start_file("chapter/001.png", options).unwrap();
    archive.write_all(&first_png).unwrap();
    archive.start_file("chapter/002.png", options).unwrap();
    archive.write_all(&second_png).unwrap();
    let archive_bytes = archive.finish().unwrap().into_inner();
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let address = listener.local_addr().unwrap();
    listener.set_nonblocking(true).unwrap();
    let archive_url = format!("http://{address}/chapter.zip");
    let guest_pages = vec![
        Page {
            content: PageContent::Zip(archive_url.clone(), "chapter/001.png".into()),
            thumbnail: None,
            has_description: false,
            description: None,
        },
        Page {
            content: PageContent::Zip(archive_url, "chapter/002.png".into()),
            thumbnail: None,
            has_description: false,
            description: None,
        },
    ];
    let mut fixture = fixture(&search_result());
    let book = fixture.source.search("needle", 1).unwrap().remove(0);
    let chapter = fixture.source.chapters(&book).unwrap().remove(0);
    let encoded = postcard::to_allocvec(&guest_pages).unwrap();
    let length = u32::try_from(encoded.len() + 8).unwrap().to_le_bytes();
    let mut result = Vec::new();
    result.extend_from_slice(&length);
    result.extend_from_slice(&length);
    result.extend_from_slice(&encoded);
    let memory = fixture
        .source
        .instance
        .get_memory(&fixture.source.store, "memory")
        .unwrap();
    memory
        .write(&mut fixture.source.store, PAGES_RESULT as usize, &result)
        .unwrap();
    let pages = fixture.source.pages(&book, &chapter).unwrap();
    assert_eq!(pages.len(), 2);
    for (index, page) in pages.iter().enumerate() {
        assert!(page.local_path.is_none());
        let request = zip_request(page).unwrap().unwrap();
        assert_eq!(request.archive.url, format!("http://{address}/chapter.zip"));
        assert_eq!(request.entry, format!("chapter/{:03}.png", index + 1));
    }
    assert!(
        matches!(listener.accept(), Err(error) if error.kind() == std::io::ErrorKind::WouldBlock),
        "Reading the chapter page list must not request the archive"
    );
    let source_state = fixture.source.directory.clone();
    let Fixture {
        _directory,
        source,
        config,
    } = fixture;
    drop(source);
    let manager = super::super::SourceManager::new(_directory.path(), &[config]).unwrap();
    let stopped = Arc::new(AtomicBool::new(false));
    let server_stopped = stopped.clone();
    let server = thread::spawn(move || -> Result<Vec<String>, String> {
        let deadline = Instant::now() + Duration::from_secs(8);
        let mut requests = Vec::new();
        while !server_stopped.load(Ordering::Acquire) {
            let mut connection = match listener.accept() {
                Ok((connection, _)) => connection,
                Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                    if Instant::now() >= deadline {
                        return Err("The archive fixture exceeded its server deadline".into());
                    }
                    thread::sleep(Duration::from_millis(5));
                    continue;
                }
                Err(error) => return Err(error.to_string()),
            };
            connection
                .set_read_timeout(Some(Duration::from_secs(3)))
                .map_err(|error| error.to_string())?;
            let mut request = Vec::new();
            while !request.windows(4).any(|bytes| bytes == b"\r\n\r\n") {
                let mut chunk = [0; 1_024];
                let count = connection
                    .read(&mut chunk)
                    .map_err(|error| error.to_string())?;
                if count == 0 || request.len() + count > 8_192 {
                    return Err("The archive request headers were incomplete or too large".into());
                }
                request.extend_from_slice(&chunk[..count]);
            }
            requests.push(String::from_utf8(request).map_err(|error| error.to_string())?);
            let headers = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: application/zip\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                archive_bytes.len()
            );
            connection
                .write_all(headers.as_bytes())
                .map_err(|error| error.to_string())?;
            connection
                .write_all(&archive_bytes)
                .map_err(|error| error.to_string())?;
        }
        Ok(requests)
    });
    let fetched = pages
        .iter()
        .map(|page| manager.fetch_page(page, super::super::FetchPriority::Interactive, None))
        .collect::<anyhow::Result<Vec<_>>>();
    stopped.store(true, Ordering::Release);
    let requests = server.join().unwrap().unwrap();
    let fetched = fetched.unwrap();
    assert_eq!(
        requests.len(),
        1,
        "The same chapter archive should be requested once"
    );
    assert!(requests[0].starts_with("GET /chapter.zip HTTP/1.1\r\n"));
    assert_eq!(fetched.len(), 2);
    for (page, path) in pages.iter().zip(&fetched) {
        assert_eq!(path, &path.canonicalize().unwrap());
        assert_eq!(
            manager.cache().lookup_raw(page).unwrap().as_ref(),
            Some(path),
            "The extracted page must use the shared cache"
        );
        assert!(
            !path.starts_with(&source_state),
            "Source state must not duplicate page images"
        );
    }
    assert!(fs::read_dir(&source_state).unwrap().all(|entry| {
        !entry
            .unwrap()
            .file_name()
            .to_string_lossy()
            .starts_with("page-")
    }));
    let first_bytes = fs::read(&fetched[0]).unwrap();
    let second_bytes = fs::read(&fetched[1]).unwrap();
    assert_eq!(first_bytes, first_png);
    assert_eq!(second_bytes, second_png);
    let first = image::load_from_memory(&first_bytes).unwrap().to_rgba8();
    let second = image::load_from_memory(&second_bytes).unwrap().to_rgba8();
    assert_eq!(first.dimensions(), (2, 3));
    assert_eq!(second.dimensions(), (3, 2));
    assert_eq!(first.get_pixel(0, 0).0, [40, 40, 40, 255]);
    assert_eq!(second.get_pixel(0, 0).0, [220, 220, 220, 255]);
}

fn zip_descriptor(archive: &str, path: &str, source: &str, version: &str) -> crate::domain::Page {
    let mut url = reqwest::Url::parse("aidoku-zip://page/").unwrap();
    url.query_pairs_mut()
        .append_pair("archive", archive)
        .append_pair("path", path)
        .append_pair("source", source)
        .append_pair("version", version);
    crate::domain::Page {
        url: url.to_string(),
        ..Default::default()
    }
}

#[test]
fn lazy_zip_descriptors_preserve_encoded_paths_and_archive_headers() {
    let mut page = zip_descriptor(
        "https://example.invalid/chapter.zip?token=a%2Bb&part=1",
        "chapter/\u{4e2d}\u{6587} 01.png",
        "en.test",
        "1",
    );
    page.headers
        .insert("Authorization".into(), "Bearer fixture-token".into());
    let request = zip_request(&page).unwrap().unwrap();
    assert_eq!(
        request.archive.url,
        "https://example.invalid/chapter.zip?token=a%2Bb&part=1"
    );
    assert_eq!(request.archive.headers, page.headers);
    assert!(request.archive.local_path.is_none());
    assert_eq!(request.entry, "chapter/\u{4e2d}\u{6587} 01.png");
    assert!(
        zip_request(&crate::domain::Page::default())
            .unwrap()
            .is_none()
    );
    let ordinary = crate::domain::Page {
        url: "https://example.invalid/page.png".into(),
        ..Default::default()
    };
    assert!(zip_request(&ordinary).unwrap().is_none());
}

#[test]
fn lazy_zip_descriptors_reject_unsafe_paths_and_ambiguous_protocol_fields() {
    for entry in [
        "",
        "../page.png",
        "/page.png",
        "\\page.png",
        "a\\page.png",
        "a/../page.png",
        "C:/page.png",
        "a\0.png",
        "a//page.png",
    ] {
        assert!(
            zip_request(&zip_descriptor(
                "https://example.invalid/chapter.zip",
                entry,
                "en.test",
                "1"
            ))
            .is_err(),
            "{entry:?}"
        );
    }
    for archive in [
        "file:///chapter.zip",
        "ftp://example.invalid/chapter.zip",
        "https://user:password@example.invalid/chapter.zip",
        "https://example.invalid/chapter.zip#part",
    ] {
        assert!(
            zip_request(&zip_descriptor(archive, "page.png", "en.test", "1")).is_err(),
            "{archive}"
        );
    }
    let valid = zip_descriptor(
        "https://example.invalid/chapter.zip",
        "page.png",
        "en.test",
        "1",
    );
    for bad_url in [
        valid.url.replacen("://page/", "://other/", 1),
        valid.url.replacen("://page/", "://user@page/", 1),
        valid.url.replacen("://page/", "://page:80/", 1),
        valid.url.replacen("://page/", "://page/extra", 1),
        format!("{}&path=another.png", valid.url),
        format!("{}&other=value", valid.url),
        format!("{}#fragment", valid.url),
    ] {
        let page = crate::domain::Page {
            url: bad_url,
            ..Default::default()
        };
        assert!(zip_request(&page).is_err(), "{}", page.url);
    }
    assert!(
        zip_request(&zip_descriptor(
            "https://example.invalid/chapter.zip",
            "page.png",
            "../source",
            "1"
        ))
        .is_err()
    );
    assert!(
        zip_request(&zip_descriptor(
            "https://example.invalid/chapter.zip",
            "page.png",
            "en.test",
            "0"
        ))
        .is_err()
    );
}
