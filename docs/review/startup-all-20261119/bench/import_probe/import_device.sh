# Used only by the owner-checked, leased base driver against generated comic data.
fixture() {
    owner; stable_entry
    mkdir "$STAGE/cases" "$STAGE/out"
    nonlink "$STAGE/comics"; same_hash "$STAGE/fixture.json" "$IMPORT_FIXTURE_SHA"
    (cd "$STAGE/comics" && sha256sum -c "$STAGE/archives.sha256" >/dev/null)
}

import_case() {
    c=$1
    case "$c" in baseline-*|candidate-*) ;; *) return 1;; esac
    iv=${c%%-*}; suffix=${c#*-}
    case "$suffix" in warmup-ingest|qualify-ingest|[0-9][0-9]-ingest) phase=ingest;;
      warmup-reopen|qualify-reopen|[0-9][0-9]-reopen) phase=reopen;; *) return 1;; esac
    trial=${c%-*}; case_root="$STAGE/cases/$trial"; data="$case_root/data"
    case "$iv" in baseline) imode=off;; candidate) imode=idle;; *) return 1;; esac
    if [ "$phase" = ingest ]; then
        [ ! -e "$case_root" ] && [ ! -L "$case_root" ]; mkdir "$case_root"; mkdir "$data"
        cp -p "$APP/data/device-profile" "$data/device-profile"; test "$(cat "$data/device-profile")" = scribe
        apply_case_options "$data" "$iv"; grep -Fx "import_thumbnail=$imode" "$data/startup-options" >/dev/null
        [ ! -e "$data/library.sqlite3" ] && [ ! -e "$data/cache" ] && [ ! -e "$data/local" ]
        printf 'fresh-empty-state\n' >"$OUT/import-start-state"
    else
        nonlink "$STAGE/cases"; nonlink "$case_root"; nonlink "$data"; nonlink "$data/library.sqlite3"
        [ "$(cat "$STAGE/out/$trial-ingest/case-ok")" = ok ]; [ ! -e "$case_root/sort-changed" ]
        cp "$STAGE/out/$trial-ingest/startup-options.sha256" "$OUT/startup-options.sha256"
        cp "$STAGE/out/$trial-ingest/startup-options-presence" "$OUT/startup-options-presence"
        verify_case_options "$data" "$iv"
        test "$(sqlite3 -readonly "$data/library.sqlite3" 'SELECT count(*) FROM books;')" = 20
        # The two arms use identical synthetic UI preferences. Only the next screen's sort is selected here.
        sqlite3 "$data/library.sqlite3" "INSERT INTO preferences(key,document) VALUES('ui:interaction-v1','{\"library_sort\":\"title\",\"library_ascending\":true}') ON CONFLICT(key) DO UPDATE SET document=json_set(preferences.document,'$.library_sort','title','$.library_ascending',json('true'));"
        test "$(sqlite3 -readonly "$data/library.sqlite3" "SELECT json_extract(document,'$.library_sort')||':'||json_extract(document,'$.library_ascending') FROM preferences WHERE key='ui:interaction-v1';")" = title:1
        # Use the same synthetic reading order in both arms. Never write actual user metadata.
        test "$(sqlite3 -readonly "$data/library.sqlite3" 'SELECT count(*) FROM progress;')" = 0
        sqlite3 "$data/library.sqlite3" "INSERT INTO progress(chapter_id,book_id,page,total,completed,updated_at) SELECT c.id,b.id,1,2,0,1600000000 FROM books b JOIN chapters c ON c.book_id=b.id WHERE title='Fixture S00';"
        test "$(sqlite3 -readonly "$data/library.sqlite3" 'SELECT count(*) FROM progress;')" = 1
        printf 'title-ascending-synthetic-hero-s00\n' >"$case_root/sort-changed"
    fi
    nonlink "$data"; same_hash "$data/startup-options" "$(cat "$STAGE/$iv/bench.options.sha256")"
}

audit_import() {
    owner; native_wait; stable_entry; nonlink "$data"; nonlink "$data/library.sqlite3"
    (cd "$STAGE/comics" && sha256sum -c "$STAGE/archives.sha256" >/dev/null)
    db="$data/library.sqlite3"
    test "$(sqlite3 -readonly "$db" "SELECT count(*) FROM books WHERE source_id='local' AND in_library=1;")" = 20
    test "$(sqlite3 -readonly "$db" 'SELECT count(*) FROM chapters;')" = 20
    test "$(sqlite3 -readonly "$db" 'SELECT count(*) FROM pages;')" = 40
    sqlite3 -readonly -separator '|' "$db" "SELECT b.title,b.id,p.position,json_extract(p.document,'$.local_path'),json_extract(b.document,'$.cover') FROM books b JOIN chapters c ON c.book_id=b.id JOIN pages p ON p.chapter_id=c.id ORDER BY b.title,p.position;" >"$OUT/imported-rows.tsv"
    : >"$OUT/page-hashes.tsv"
    while IFS='|' read -r name id pos path cover; do
        case "$name" in 'Fixture S'[0-1][0-9]) ;; *) return 1;; esac
        case "$pos" in 0|1) ;; *) return 1;; esac
        case "$path" in "$data"/local/content/*/*/00000[12].png) ;; *) return 1;; esac
        nonlink "$path"; case "$pos" in 0) [ "$cover" = "$path" ];; esac
        h=$(sha256sum "$path"); printf '%s|%s|%s\n' "$name" "$pos" "${h%% *}" >>"$OUT/page-hashes.tsv"
    done <"$OUT/imported-rows.tsv"
    root="$data/cache/shared-v1"; nonlink "$root"; nonlink "$root/index.sqlite3"
    # Archive only processed artifacts in this newly created synthetic run. Never archive a database or user file.
    sqlite3 -readonly "$root/index.sqlite3" "SELECT cache_key FROM artifacts WHERE valid=1 AND kind='processed' ORDER BY cache_key;" >"$OUT/processed-keys"
    count=$(wc -l <"$OUT/processed-keys"); [ "$count" -gt 0 ] && [ "$count" -le 24 ]; mkdir "$OUT/derivatives"
    while IFS= read -r k; do
        [ "${#k}" -eq 64 ]; case "$k" in *[!0-9a-f]*) return 1;; esac
        prefix=$(printf '%s' "$k" | cut -c1-2); f="$root/processed/$prefix/$k.png"; nonlink "$f"
        bytes=$(wc -c <"$f"); [ "$bytes" -gt 0 ] && [ "$bytes" -le 3145728 ]; cp -p "$f" "$OUT/derivatives/$k.png"
    done <"$OUT/processed-keys"
    cp "$STAGE/fixture.json" "$OUT/fixture.json"; cp "$data/startup-options" "$OUT/observed-options"
    case "$phase" in ingest) grep -F '"name": "import_library_published"' "$OUT/application.json" >/dev/null; grep -F '"name": "import_visible_covers_settled"' "$OUT/application.json" >/dev/null;; esac
}
