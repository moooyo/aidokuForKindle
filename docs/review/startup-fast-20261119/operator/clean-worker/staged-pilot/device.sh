#!/bin/sh
set -eu
export LC_ALL=C
umask 077
mode=$1; label=$2; name=$3; stock_check=$4
[ "${#label}" = 16 ] || exit 64
case "$label" in *[!0-9a-f]*) exit 64;; esac
stage=/mnt/us/.aidoku-pilot-$label
installed=/mnt/us/aidoku
lock=/tmp/aidoku-launcher.lock
fail() { printf 'refused %s\n' "$1" >&2; exit 1; }
stock() {
    result=$(sh -c "$stock_check" stock check "$label") || { printf '%s\n' "$result" >&2; return 1; }
}
owned() {
    [ -d "$stage" ] && [ ! -L "$stage" ] && [ -f "$stage/owner" ] &&
        [ ! -L "$stage/owner" ] && [ "$(cat "$stage/owner")" = "$label" ]
}
mark() {
    IFS=' ' read -r tick unused < /proc/uptime
    printf '%s %s\n' "$tick" "$1" >>"$out/shell.tsv"
}
protected() {
    cat /proc/sys/kernel/random/boot_id
    pidof awesome
    panel_geometry || return 1
    for path in "$installed/launch.sh" "$installed/app/aidoku" "$installed/app/aidoku-startup-portrait" \
        "$installed/data/library.sqlite3" /mnt/us/documents/Aidoku.sh /var/local/appreg.db; do
        [ -f "$path" ] && [ ! -L "$path" ] || return 1
        stat -c '%d:%i:%s:%Y:%a' "$path" || return 1
    done
    for key in flIntensity currentAmberLevel; do
        value=$(lipc-get-prop -i com.lab126.powerd "$key") || return 1
        case "$value" in ''|*[!0-9]*) return 1;; esac
        [ "$value" -le 24 ] || return 1
        printf '%s %s\n' "$key" "$value"
    done
}
panel_geometry() {
    [ "$(cat /sys/class/graphics/fb0/stride)" = 1872 ] && [ "$(cat /sys/class/graphics/fb0/bits_per_pixel)" = 8 ] || return 1
    rotation=$(cat /sys/class/graphics/fb0/rotate); pan=$(cat /sys/class/graphics/fb0/pan); size=$(cat /sys/class/graphics/fb0/virtual_size)
    case "$rotation" in 1|3) ;; *) return 1;; esac
    case "$size:$pan" in 1872,4960:0,0|1872,4960:0,2480|1872,2480:0,0|1860,2480:0,0) ;; *) return 1;; esac
    printf '%s %s %s\n' "$rotation" "${pan#0,}" "$size"
}
panel_rows() {
    path=$1; first=$2; rows=$3
    before=$(panel_geometry) || return 1
    set -- $before
    dd if=/dev/fb0 of="$path" bs=1872 skip=$(($2 + first)) count="$rows" 2>/dev/null &&
        [ "$(wc -c <"$path")" -eq "$((1872 * rows))" ] && [ "$before" = "$(panel_geometry)" ] || return 1
    printf '%s\n' "$before"
}
stock_body() { panel_rows "$1" 72 2336 >/dev/null; }
listed_case() {
    case "$1" in prime|baseline-warmup|candidate-warmup|baseline-0[1-9]|candidate-0[1-9]|baseline-10|candidate-10|baseline-picture|candidate-picture) ;; *) return 1;; esac
    case " $(cat "$stage/cases") " in *" $1 "*) return 0;; *) return 1;; esac
}
free_entry() {
    stock || return 1
    for item in /mnt/us/.aidoku-pilot-*; do [ ! -e "$item" ] && [ ! -L "$item" ] || fail existing_stage; done
    panel_geometry >/dev/null || fail panel_geometry
    [ "$(readlink -f "$installed")" = "$installed" ] &&
        df -Pk /mnt/us | awk 'END {exit !(NF>=6 && $4+0>=327680)}'
}
launch_is_owned() {
    [ -f "$stage/worker" ] || return 1
    read -r worker side birth <"$stage/worker" || return 1
    case "$worker:$birth" in *[!0-9:]*) return 1;; esac
    case "$side" in candidate|baseline) ;; *) return 1;; esac
    [ -r "/proc/$worker/stat" ] && [ "$(awk '{print $22}' "/proc/$worker/stat")" = "$birth" ] &&
        tr '\000' '\n' <"/proc/$worker/cmdline" | grep -Fx "$stage/$side/launch.sh" >/dev/null
}
settled_stock() {
    remaining=50
    while ! stock; do remaining=$((remaining - 1)); [ "$remaining" -gt 0 ] || return 1; usleep 200000; done
}
case "$mode" in
    admit) free_entry || fail native_admission; printf 'admitted\n';;
    prepare)
        free_entry || fail native_admission
        mkdir "$stage"; printf '%s\n' "$label" >"$stage/owner"
        exec 8<"$stage"; flock -n 8 || fail live_stage
        protected >"$stage/before" || fail protection
        stock_body "$stage/stock-before" || fail stock_capture
        tar -xzf - -C "$stage" || fail upload
        cmp -s "$installed/launch.sh" "$stage/control-launcher" || fail control_launcher
        mkdir -p "$stage/baseline/app" "$stage/out"
        cp -p "$installed/app/aidoku" "$installed/app/aidoku-startup-portrait" "$stage/baseline/app/"
        for side in baseline candidate; do
            cp -p "$installed/app/aidoku-native-query" "$installed/app/kindle-jpeg-worker" "$stage/$side/app/"
        done
        printf 'prepared\n';;
    case)
        owned && stock && cmp -s "$installed/launch.sh" "$stage/control-launcher" || fail case_admission
        exec 8<"$stage"; flock -n 8 || fail live_stage
        listed_case "$name" || exit 64
        side=${name%%-*}; [ "$side" != prime ] || side=baseline
        regime=$(cat "$stage/regime")
        out=$stage/out/$name
        data=$stage/data-$name
        mkdir "$out" "$data"
        cp -p "$stage/seed/library.sqlite3" "$data/library.sqlite3"
        printf 'scribe\n' >"$data/device-profile"
        case "$regime" in
            warm) if [ "$name" != prime ]; then cp -rp "$stage/warm-cache" "$data/cache"; fi; dwell=6500;;
            cold) [ "$name" != prime ] || exit 64; dwell=12500;;
            *) exit 64;;
        esac
        [ ! -e "$data/startup-options" ] || fail options
        capture=''
        case "$name" in *-picture) capture="--capture $out/final.png --capture-settle-covers"; dwell=19000;; esac
        unset AIDOKU_CATALOG_NOTIFY_HELPER AIDOKU_CATALOG_NOTIFY_SHA256 AIDOKU_STARTUP_PORTRAIT
        export AIDOKU_HOME="$stage/$side" AIDOKU_RESEARCH_DATA="$data" AIDOKU_RESEARCH_OUT="$out" \
            AIDOKU_MANAGED_LIBRARY=1 AIDOKU_RESEARCH_DWELL="$dwell" AIDOKU_RESEARCH_CAPTURE_ARGS="$capture"
        : >"$stage/started"
        lipc-set-prop -i com.lab126.powerd touchScreenSaverTimeout 1
        mark staged_foreground_requested
        sh "$stage/$side/launch.sh" --foreground </dev/null >"$out/launcher.log" 2>&1 8>&- &
        worker=$!
        printf '%s %s %s\n' "$worker" "$side" "$(awk '{print $22}' "/proc/$worker/stat")" >"$stage/worker"
        case "$name" in *-picture)
            remaining=100
            until grep -F 'aidoku: native window ready;' "$out/native.log" >/dev/null 2>&1; do
                kill -0 "$worker" 2>/dev/null || fail early_gui_exit
                remaining=$((remaining - 1)); [ "$remaining" -gt 0 ] || fail gui_timeout; usleep 100000
            done
            if [ "$regime" = warm ]; then sleep 7; else sleep 12; fi
            gui=$(cat "$lock/application-pid"); [ "$(readlink "/proc/$gui/exe")" = "$stage/$side/app/aidoku" ] || fail display_owner
            mark capture_started
            panel_rows "$out/panel.gray" 0 2480 >"$out/panel.geometry" &&
                [ "$(readlink "/proc/$gui/exe")" = "$stage/$side/app/aidoku" ] || fail panel_capture
        ;; esac
        wait "$worker" || fail launcher_exit
        rm "$stage/worker"
        settled_stock || fail native_return
        mark stock_returned
        [ -s "$out/application.json" ] && [ -s "$out/state.json" ] || fail missing_trace
        if [ "$name" = prime ]; then mv "$data/cache" "$stage/warm-cache"; fi
        printf 'case_completed\n';;
    collect)
        owned && [ -d "$stage/out" ] || exit 1
        tar -czf - -C "$stage" out;;
    restore|close)
        if [ ! -e "$stage" ] && [ ! -L "$stage" ]; then printf 'stage_absent\n'; exit 0; fi
        owned || fail unowned_stage
        if [ -f "$stage/restored" ] && [ "$mode" = close ]; then
            stock && owned || fail native_recovery
            rm -rf "$stage"; [ ! -e "$stage" ] && [ ! -L "$stage" ] || fail cleanup
            printf 'stage_absent\n'; exit 0
        fi
        if launch_is_owned; then kill -TERM "$worker" 2>/dev/null || :; fi
        exec 8<"$stage"
        remaining=50
        until flock -n 8; do remaining=$((remaining - 1)); [ "$remaining" -gt 0 ] || fail live_stage; usleep 200000; done
        settled_stock || fail native_recovery
        if [ -f "$stage/before" ] && [ -f "$stage/worker" ] && launch_is_owned; then fail still_running; fi
        if [ -s "$stage/before" ] && [ -f "$stage/started" ]; then
            [ "$(head -n 1 "$stage/before")" = "$(cat /proc/sys/kernel/random/boot_id)" ] || fail changed_boot
            for key in flIntensity currentAmberLevel; do
                value=$(awk -v key="$key" '$1==key {print $2}' "$stage/before")
                case "$value" in ''|*[!0-9]*) fail missing_light;; esac
                [ "$value" -le 24 ] || fail bad_light
                if [ "$(lipc-get-prop -i com.lab126.powerd "$key")" != "$value" ]; then
                    lipc-set-prop -i com.lab126.powerd "$key" "$value"
                fi
            done
            protected >"$stage/after" || fail protection_check
            if cmp -s "$stage/before" "$stage/after" && cmp -s "$installed/launch.sh" "$stage/control-launcher"; then
                printf 'protected_state_equal\n'
            else
                printf 'protected_state_changed\n'
            fi
            retry=6
            until stock_body "$stage/stock-after" && cmp -s "$stage/stock-before" "$stage/stock-after"; do
                retry=$((retry - 1)); [ "$retry" -gt 0 ] || break; usleep 300000
            done
            if [ "$retry" -gt 0 ]; then printf 'stock_body_equal\n'; else printf 'stock_body_changed\n'; fi
        fi
        if [ "$mode" = restore ]; then printf '%s\n' "$label" >"$stage/restored"; printf 'native_restored\n'; exit 0; fi
        owned || fail unowned_stage
        rm -rf "$stage"; [ ! -e "$stage" ] && [ ! -L "$stage" ] || fail cleanup
        printf 'stage_absent\n';;
    *) exit 64;;
esac
