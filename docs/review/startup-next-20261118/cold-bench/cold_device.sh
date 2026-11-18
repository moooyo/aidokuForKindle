# The seed is immutable. Every operation uses a new private data directory.
fixture() {
    owner
    mkdir "$STAGE/fixture" "$STAGE/out" "$STAGE/cases"
    cp -p "$APP/data/device-profile" "$STAGE/fixture/device-profile"
    sqlite3 -readonly -cmd '.timeout 1500' "$APP/data/library.sqlite3" ".backup $STAGE/fixture/library.sqlite3"
    (cd "$STAGE/fixture" && sha256sum library.sqlite3 device-profile) >"$STAGE/seed.sha256"
    [ ! -e "$STAGE/fixture/cache" ] && [ ! -L "$STAGE/fixture/cache" ]
}

prepare_case_cold() {
    owner
    case "$1" in baseline-*|candidate-*) ;; *) return 1;; esac
    number=${1#*-}; case "$number" in warmup|qualify|[0-9][0-9]) ;; *) return 1;; esac
    cold_root=$STAGE/cases/$1
    nonlink "$STAGE/cases"; nonlink "$STAGE/fixture"; nonlink "$OUT"
    [ ! -e "$cold_root" ]; [ ! -L "$cold_root" ]; mkdir -m 700 "$cold_root" "$cold_root/data"
    for item in library.sqlite3 device-profile; do cp -p "$STAGE/fixture/$item" "$cold_root/data/$item"; done
    (cd "$cold_root/data" && sha256sum library.sqlite3 device-profile) >"$OUT/seed.sha256"
    cmp "$STAGE/seed.sha256" "$OUT/seed.sha256"
    nonlink "$cold_root/data"; [ ! -e "$cold_root/data/cache" ]; [ ! -L "$cold_root/data/cache" ]
    digest "$OUT/seed.sha256" >"$OUT/cold-fixture.sha256"
    printf 'empty-cache-v1\n' >"$OUT/cold-fixture"
}

real_cache_snapshot() {
    # All user paths and hashes stay in the private operator records. Run this
    # outside timed samples; never touch, clean or open the user's cache DB.
    cache_root=$APP/data/cache/shared-v1
    nonlink "$cache_root" || return 1
    (cd "$cache_root" && find . -print | LC_ALL=C sort | sha256sum) || return 1
    for name in index.sqlite3 index.sqlite3-wal; do
        if [ -e "$cache_root/$name" ]; then
            nonlink "$cache_root/$name" || return 1
            sha256sum "$cache_root/$name" || return 1
        else printf 'absent %s\n' "$name"; fi
    done
    # BusyBox find on the supported image supplies -exec and stat -c. Record
    # all artifact names, sizes and mtimes plus index bytes; sources are read-only.
    (cd "$cache_root" && find raw processed -type f -exec stat -c '%n:%s:%Y' '{}' ';' | LC_ALL=C sort | sha256sum)
}

check_cold_case() {
    cold_miss=$(grep -cF '"name": "cover_local_derivative_prepared"' "$OUT/application.json" || :)
    cold_hit=$(grep -cF '"name": "cover_local_derivative_reused"' "$OUT/application.json" || :)
    cold_jobs=$(grep -cF '"name": "cover_preparation_finished"' "$OUT/application.json" || :)
    printf '%s %s\n' "$cold_miss" "$cold_hit" >"$OUT/cold-counts"
    [ "$cold_miss" = 6 ] && [ "$cold_hit" = 0 ] && [ "$cold_jobs" = 6 ]
}
