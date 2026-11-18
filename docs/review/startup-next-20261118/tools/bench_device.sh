#!/bin/sh
# Executed on the device only while the operator holds its lease.
set -eu
umask 077
APP=/mnt/us/aidoku
DOC=/mnt/us/documents/Aidoku.sh
INSTALLED=$APP/launch.sh
LOCK=/tmp/aidoku-launcher.lock
REG=/var/local/appreg.db
DB=/var/local/cc.db
RT=/var/local/kindle-catalog-entry/v1-2db100d0bcb60a76571cd8de96bed7ea6c016e6ab73e388a885f692955cacd86
REG_CMD=$RT/aidoku-library-dispatch
digest() { sha256sum "$1" | cut -d ' ' -f 1; }
same_hash() { [ ! -L "$1" ] && [ -f "$1" ] && [ "$(digest "$1")" = "$2" ]; }
nonlink() { [ -e "$1" ] && [ ! -L "$1" ] && [ "$(readlink -f "$1")" = "$1" ]; }
owner() {
    case "$STAGE" in /mnt/us/.aidoku-next-*) ;; *) return 1;; esac
    nonlink "$STAGE" && same_hash "$STAGE/owner" "$OWNER_HASH" && [ "$(cat "$STAGE/owner")" = "$RUN" ]
}
mark() { IFS=' ' read -r clock unused </proc/uptime; printf '%s %s\n' "$clock" "$1" >>"$OUT/shell.tsv"; }
registry() { sqlite3 -readonly -cmd '.timeout 1500' "$REG" "SELECT value FROM properties WHERE handlerId='tech.hackerdude.shell_integration.launcher' AND name='command';"; }
stable_entry() {
    [ "$(registry)" = "$REG_CMD" ] &&
    same_hash "$DOC" 709cefdf00318d40a3ee7cb8a5cc8b47e4b8c7865677040779a61264ae086960 &&
    same_hash "$REG_CMD" 2db100d0bcb60a76571cd8de96bed7ea6c016e6ab73e388a885f692955cacd86
}
native_wait() {
    [ ! -e "$LOCK" ] && [ ! -L "$LOCK" ] || return 1
    n=100
    while [ "$n" -gt 0 ]; do
        ok=1
        case "$(status statusbar 2>/dev/null || :)" in *start/running*) ;; *) ok=0;; esac
        wms=$(pidof awesome || :); [ -n "$wms" ] || ok=0
        for p in $wms; do
            s=$(sed -n 's/^State:[[:space:]]*//p' "/proc/$p/status" 2>/dev/null || :)
            case "$s" in S*|R*) ;; *) ok=0;; esac
        done
        active=$(lipc-get-prop com.lab126.appmgrd activeApp) || return 1
        case "$active" in ''|*shell_integration.launcher*) ok=0;; esac
        [ "$ok" -ne 1 ] || return 0
        usleep 100000; n=$((n - 1))
    done
    return 1
}
catalog() {
    # Identity and thumbnail values remain private, never in the public results.
    sqlite3 -readonly -cmd '.timeout 1500' -separator '|' "$DB" "SELECT p_uuid,p_mimeType,p_thumbnail,p_modificationTime,p_contentSize FROM Entries WHERE p_location='/mnt/us/documents/Aidoku.sh';" >"$1.identity"
    [ "$(wc -l <"$1.identity")" -eq 1 ] && [ -s "$1.identity" ] || return 1
    sqlite3 -readonly -cmd '.timeout 1500' "$DB" "SELECT COALESCE(p_lastAccess,-1) FROM Entries WHERE p_location='/mnt/us/documents/Aidoku.sh';" >"$1.access"
    record=$(cat "$1.access"); case "$record" in ''|*[!0-9]*) return 1;; esac
}
state_snapshot() {
    nonlink "$APP" && nonlink "$APP/app" && nonlink "$INSTALLED" && nonlink "$DOC" || return 1
    stable_entry || return 1
    native_wait || return 1
    [ "$(lipc-get-prop -s com.lab126.powerd state)" = active ] || return 1
    if pidof aidoku >/dev/null; then return 1; fi
    same_hash "$INSTALLED" "$ORIGINAL_SHA" || return 1
    same_hash "$APP/app/aidoku" 62de2771891b5bf8f720572c4544a3a732d619fa95131111cb00bccb6e0bc612 || return 1
    same_hash "$APP/app/aidoku-native-query" 84c2f27d8233d8695001aa86d880e29454fe188deb9884aca1c6694c82a5887d || return 1
    same_hash "$APP/app/kindle-jpeg-worker" 1b41abcab8083ef70d3cb76ad16303d8b7934c382c4e49927f9789d11a7abf41 || return 1
    same_hash "$APP/assets/aidoku-cover-v2.png" dde92cb1ae5f1dbf37db61e179a55522edea3759567fba74714f4954955dbf31 || return 1
    same_hash "$APP/assets/aidoku-cover.png" b3b65e42af19906ecc5f1c99a5b761b4bf4686bbc5a150a98bb4751f760090d9 || return 1
    same_hash "$RT/aidoku-entry-hooks.so" 448a68e9da0a80eb968f49c6d18ca93e99f1cae1a371866deffed9445d074667 || return 1
    same_hash "$RT/aidoku-catalog-client" f46a0225d5a265f5f9749029fc115060576508ee5caadf25becb661738b88161 || return 1
    same_hash /var/local/kmc/bin/sh_integration_launcher 2b3db59ed7856b3f5026e3837f7ef07fc9bb6bc1c042274b48b3cd3fc2e04abc || return 1
    LC_ALL=C sha256sum "$APP/data/library.sqlite3" "$APP/data/device-profile" "$DOC" "$INSTALLED" "$RT/protocol"
    for f in "$APP/data/library.sqlite3-wal"; do if [ -f "$f" ]; then sha256sum "$f"; else printf 'absent %s\n' "$f"; fi; done
    stat -c 'document %s:%Y:%a' "$DOC"; stat -c 'launcher %s:%Y:%a' "$INSTALLED"
    cat /proc/sys/kernel/random/boot_id
    for node in /sys/class/input/event*; do printf '%s ' "${node##*/}"; cat "$node/dev" "$node/device/name" "$node/device/capabilities/abs"; done
    for f in scaling_governor scaling_min_freq scaling_max_freq; do cat "/sys/devices/system/cpu/cpu0/cpufreq/$f"; done
    lipc-get-prop com.lab126.appmgrd activeApp
    lipc-get-prop -s com.lab126.powerd state
    lipc-get-prop -i com.lab126.powerd flIntensity
    lipc-get-prop -i com.lab126.powerd currentAmberLevel
}
fixture() {
    mkdir "$STAGE/fixture" "$STAGE/out"
    f=$STAGE/fixture
    cp -p "$APP/data/device-profile" "$f/device-profile"
    sqlite3 -readonly -cmd '.timeout 1500' "$APP/data/library.sqlite3" ".backup $f/library.sqlite3"
    src=$APP/data/cache/shared-v1; dst=$f/cache/shared-v1
    mkdir -p "$dst/raw" "$dst/processed" "$dst/temp"
    sqlite3 -readonly -cmd '.timeout 1500' "$src/index.sqlite3" ".backup $dst/index.sqlite3"
    sqlite3 -readonly -cmd '.timeout 1500' "$src/index.sqlite3" "SELECT DISTINCT a.kind||'/'||substr(a.cache_key,1,2)||'/'||a.cache_key||CASE WHEN a.kind='raw' THEN '.raw' ELSE '.png' END FROM artifacts a JOIN aliases b ON a.id=b.artifact_id WHERE a.valid=1 AND (b.cover=1 OR b.cover IS NULL) AND length(a.cache_key)=64 AND a.cache_key NOT GLOB '*[^0-9a-f]*' AND a.kind IN ('raw','processed');" >"$STAGE/fixture.paths"
    while IFS= read -r rel; do
        case "$rel" in raw/*/*.raw|processed/*/*.png) ;; *) return 1;; esac
        [ ! -L "$src/$rel" ] || return 1
        if [ -f "$src/$rel" ]; then mkdir -p "$dst/${rel%/*}"; cp -p "$src/$rel" "$dst/$rel"; fi
    done <"$STAGE/fixture.paths"
    cp -rp "$f" "$STAGE/data"
}
prepare_device() {
    owner; stable_entry
    (cd "$STAGE" && sha256sum -c inputs.sha256 >/dev/null)
    for v in baseline candidate; do
        app_src=$(cat "$STAGE/$v/binary-ref"); case "$app_src" in installed) app_src=$APP/app/aidoku;; *) case "$app_src" in *[!0-9a-f]*|'') return 1;; esac; [ "${#app_src}" -eq 64 ]; app_src=$STAGE/bin-$app_src;; esac
        mkdir -p "$STAGE/$v/app"; cp -p "$app_src" "$STAGE/$v/app/aidoku"
        for name in aidoku-native-query kindle-jpeg-worker; do cp -p "$APP/app/$name" "$STAGE/$v/app/$name"; done
        same_hash "$STAGE/$v/app/aidoku" "$(cat "$STAGE/$v/binary-hash")"
    done
    fixture
    catalog "$STAGE/original-catalog"
    cp -p "$INSTALLED" "$STAGE/original-launcher"
    same_hash "$STAGE/original-launcher" "$ORIGINAL_SHA"
    stat -c '%s:%Y:%a' "$INSTALLED" >"$STAGE/original-launcher.stat"
    [ "$(cat "$STAGE/original-launcher.stat")" = "$(stat -c '%s:%Y:%a' "$STAGE/original-launcher")" ]
}
swap_launcher() {
    owner; native_wait; stable_entry
    same_hash "$INSTALLED" "$ORIGINAL_SHA"; same_hash "$STAGE/original-launcher" "$ORIGINAL_SHA"
    [ ! -e "$INSTALLED.$RUN.pending" ]; [ ! -L "$INSTALLED.$RUN.pending" ]
    cp "$STAGE/bridge.sh" "$INSTALLED.$RUN.pending"; chmod 755 "$INSTALLED.$RUN.pending"
    same_hash "$INSTALLED.$RUN.pending" "$BRIDGE_SHA"; same_hash "$INSTALLED" "$ORIGINAL_SHA"
    mv -f "$INSTALLED.$RUN.pending" "$INSTALLED"; same_hash "$INSTALLED" "$BRIDGE_SHA"
}
run_case() {
    C=$1; variant=${C%%-*}; role=${C#*-}; OUT=$STAGE/out/$C
    owner; stable_entry; native_wait; same_hash "$INSTALLED" "$BRIDGE_SHA"
    [ ! -e "$OUT" ]; [ ! -L "$OUT" ]; mkdir "$OUT"
    printf '%s\n' "$C" >"$STAGE/case.new"; mv "$STAGE/case.new" "$STAGE/case"
    catalog "$OUT/catalog-before"; cmp "$STAGE/original-catalog.identity" "$OUT/catalog-before.identity"
    savedlog=$STAGE/data/logs/launcher.log; offset=0; if [ -f "$savedlog" ]; then offset=$(wc -c <"$savedlog"); fi
    lipc-set-prop -i com.lab126.powerd touchScreenSaverTimeout 1
    sleep 8
    mark native_open_requested
    lipc-set-prop com.lab126.appmgrd start file:///mnt/us/documents/Aidoku.sh
    if [ "$role" = qualify ]; then
        n=200
        while [ "$n" -gt 0 ]; do
            if [ -s "$OUT/native.log" ] && grep -F 'aidoku: native window ready;' "$OUT/native.log" >/dev/null; then break; fi
            usleep 100000; n=$((n - 1))
        done
        [ "$n" -gt 0 ]; p=$(cat "$LOCK/application-pid"); is_app "$p" "$variant"; sleep 6
        is_app "$p" "$variant"; "$STAGE/capture" >"$OUT/native-settled.ppm"
    fi
    n=420
    while [ "$n" -gt 0 ]; do
        if [ -s "$OUT/application.json" ] && [ ! -e "$LOCK" ]; then break; fi
        usleep 100000; n=$((n - 1))
    done
    [ "$n" -gt 0 ]; mark native_restored; native_wait; mark native_home_active
    grep -F '"success": true' "$OUT/application.json" >/dev/null
    grep -F '"name": "startup_library_input_ready"' "$OUT/application.json" >/dev/null
    grep -F '"name": "startup_visible_covers_settled"' "$OUT/application.json" >/dev/null
    [ -s "$OUT/state.json" ]; [ ! -f "$OUT/final.png" ] || [ "$role" = qualify ]
    catalog "$OUT/catalog-after"; cmp "$OUT/catalog-before.identity" "$OUT/catalog-after.identity"
    [ "$(cat "$OUT/catalog-after.access")" -gt "$(cat "$OUT/catalog-before.access")" ]
    tail -c +$((offset+1)) "$savedlog" >"$OUT/launcher.log"
    [ "$(grep -cF 'Catalog notification completed.' "$OUT/launcher.log" || :)" = 1 ]
    if grep -F 'Catalog notification unavailable;' "$OUT/launcher.log" >/dev/null; then return 1; fi
    engines=$(sed -n 's/^aidoku: Startup portrait supervisor: \(native\|shell\)\.$/\1/p' "$OUT/launcher.log")
    case "$engines" in '') printf 'unmarked\n';; shell|native) printf '%s\n' "$engines";; *) return 1;; esac >"$OUT/portrait-engine"
    stable_entry; printf 'ok\n' >"$OUT/case-ok"
}
is_app() {
    case "$1" in ''|*[!0-9]*) return 1;; esac
    [ "$(cat "$LOCK/application-pid")" = "$1" ] && [ "$(readlink "/proc/$1/exe")" = "$STAGE/$2/app/aidoku" ]
}
restore_launcher() {
    owner
    if [ -e "$LOCK" ]; then
        n=240
        while [ "$n" -gt 0 ] && [ -e "$LOCK" ]; do
            if p=$(cat "$LOCK/application-pid" 2>/dev/null); then
                for v in baseline candidate; do
                    if is_app "$p" "$v"; then kill -CONT "$p" 2>/dev/null || :; if is_app "$p" "$v"; then kill -TERM "$p" 2>/dev/null || :; fi; break 2; fi
                done
            fi
            usleep 100000; n=$((n - 1))
        done
        n=240; while [ "$n" -gt 0 ] && [ -e "$LOCK" ]; do usleep 100000; n=$((n - 1)); done
    fi
    native_wait
    if same_hash "$INSTALLED" "$BRIDGE_SHA"; then
        same_hash "$STAGE/original-launcher" "$ORIGINAL_SHA"
        [ ! -e "$INSTALLED.$RUN.restore" ]; [ ! -L "$INSTALLED.$RUN.restore" ]
        cp -p "$STAGE/original-launcher" "$INSTALLED.$RUN.restore"
        same_hash "$INSTALLED.$RUN.restore" "$ORIGINAL_SHA"; same_hash "$INSTALLED" "$BRIDGE_SHA"
        mv -f "$INSTALLED.$RUN.restore" "$INSTALLED"
    fi
    same_hash "$INSTALLED" "$ORIGINAL_SHA"
    if [ -f "$STAGE/original-launcher.stat" ]; then [ "$(cat "$STAGE/original-launcher.stat")" = "$(stat -c '%s:%Y:%a' "$INSTALLED")" ]; fi
    if [ -e "$INSTALLED.$RUN.pending" ]; then same_hash "$INSTALLED.$RUN.pending" "$BRIDGE_SHA"; rm "$INSTALLED.$RUN.pending"; fi
    if [ -e "$INSTALLED.$RUN.restore" ]; then same_hash "$INSTALLED.$RUN.restore" "$ORIGINAL_SHA"; rm "$INSTALLED.$RUN.restore"; fi
    stable_entry
    if [ -f "$STAGE/original-catalog.identity" ]; then
        catalog "$STAGE/restore-catalog"; cmp "$STAGE/original-catalog.identity" "$STAGE/restore-catalog.identity"
    fi
}
