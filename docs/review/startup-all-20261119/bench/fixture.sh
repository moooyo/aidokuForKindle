# Each timed launch receives its own library and cache, prepared before request.
seal_warm_seed() {
    owner; nonlink "$STAGE/cases"; nonlink "$STAGE/out/baseline-warmup/case-ok"
    [ "$(cat "$STAGE/out/baseline-warmup/case-ok")" = ok ]
    local_cache=$STAGE/cases/baseline-warmup/data/cache
    nonlink "$local_cache"; [ ! -e "$STAGE/sealed" ]; [ ! -L "$STAGE/sealed" ]
    mkdir "$STAGE/sealed"; cp -p "$STAGE/fixture/library.sqlite3" "$STAGE/fixture/device-profile" "$STAGE/sealed/"
    cp -rp "$local_cache" "$STAGE/sealed/cache"
    warm_seed_hash "$STAGE/sealed" >"$STAGE/sealed.sha256"
}
warm_seed_hash() {
    # Hash privately in a canonical relative namespace; never export artifact
    # paths or catalog/seed identities from the host-side public analyzer.
    w=$1
    nonlink "$w"; nonlink "$w/cache/shared-v1"; nonlink "$w/library.sqlite3"; nonlink "$w/device-profile"
    test -z "$(find "$w" -type l -print | head -n 1)"
    (cd "$w" && find . -type f | LC_ALL=C sort | while IFS= read -r p; do
        case "$p" in ./library.sqlite3|./device-profile|./cache/shared-v1/*) ;; *) exit 1;; esac
        sha256sum "$p" || exit 1
    done) >"$STAGE/.warm-calculation"
    digest "$STAGE/.warm-calculation"
}
prepare_case_warm() {
    owner
    case "$1" in baseline-*|candidate-*) ;; *) return 1;; esac
    suffix=${1#*-}; case "$suffix" in warmup|qualify|[0-9][0-9]) ;; *) return 1;; esac
    nonlink "$STAGE/cases"; nonlink "$STAGE/fixture"; nonlink "$OUT"
    data=$STAGE/cases/$1/data
    [ ! -e "${data%/*}" ]; [ ! -L "${data%/*}" ]; mkdir -m 700 "${data%/*}"
    if [ "$1" = baseline-warmup ]; then
        src=$STAGE/fixture
        warm_seed_hash "$src" >"$STAGE/prewarm.sha256"; expected=$STAGE/prewarm.sha256
    else
        if [ "$1" = candidate-warmup ]; then seal_warm_seed; fi
        nonlink "$STAGE/sealed"; src=$STAGE/sealed; expected=$STAGE/sealed.sha256
    fi
    cp -rp "$src" "$data"; warm_seed_hash "$data" >"$OUT/fixture.sha256"; cmp "$OUT/fixture.sha256" "$expected"
    printf 'warm-cache-v2\n' >"$OUT/fixture-name"
}
apply_case_options() {
    data=$1; variant=$2; nonlink "$OUT"; nonlink "$data"
    [ ! -e "$data/startup-options" ]; [ ! -L "$data/startup-options" ]
    nonlink "$STAGE/$variant/bench.options"; nonlink "$STAGE/$variant/bench.options.sha256"
    expected=$(cat "$STAGE/$variant/bench.options.sha256"); same_hash "$STAGE/$variant/bench.options" "$expected"
    if [ -s "$STAGE/$variant/bench.options" ]; then
        cp "$STAGE/$variant/bench.options" "$data/startup-options"; chmod 600 "$data/startup-options"
        same_hash "$data/startup-options" "$expected"; printf 'present\n' >"$OUT/startup-options-presence"
    else
        printf 'absent\n' >"$OUT/startup-options-presence"
    fi
    printf '%s\n' "$expected" >"$OUT/startup-options.sha256"
}
verify_case_options() {
    data=$1; variant=$2; expected=$(cat "$OUT/startup-options.sha256")
    case "$(cat "$OUT/startup-options-presence")" in
        present) same_hash "$data/startup-options" "$expected";;
        absent) [ ! -e "$data/startup-options" ] && [ ! -L "$data/startup-options" ];;
        *) return 1;;
    esac
}
