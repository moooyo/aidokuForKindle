#!/bin/sh
set -eu
export LC_ALL=C
umask 077
guard_fail() { printf 'refused %s\n' "$1" >&2; exit 1; }
[ "$#" = 2 ] || guard_fail guard_arguments
operation=$1
label=$2
case "$label" in *[!0-9a-f]*|'') guard_fail guard_label;; esac
[ "${#label}" = 16 ] || guard_fail guard_label
media=/mnt/us
stage=$media/.aidoku-native-$label
state=/var/tmp/aidoku-native-$label
guard=$state/guard
runtime=/var/local/kindle-catalog-entry/v1-2db100d0bcb60a76571cd8de96bed7ea6c016e6ab73e388a885f692955cacd86
registry=/var/local/appreg.db
catalog=/var/local/cc.db

absent() { [ ! -e "$1" ] && [ ! -L "$1" ]; }
leaf() { [ -f "$1" ] && [ ! -L "$1" ]; }
plain() { leaf "$1" && [ "$(readlink -f "$1")" = "$1" ]; }
directory() { [ -d "$1" ] && [ ! -L "$1" ] && [ "$(readlink -f "$1")" = "$1" ]; }
marker() { plain "$1" && printf '%s\n' "$label" | cmp -s - "$1"; }
number() { case "$1" in ''|*[!0-9]*) return 1;; *) return 0;; esac; }
owned() { directory "$stage" && directory "$state" && marker "$stage/owner" && marker "$state/owner"; }
sqlite_read() { sqlite3 -batch -readonly -noheader -cmd '.timeout 1500' -cmd 'PRAGMA query_only=ON' -cmd '.mode quote' "$1" "$2"; }
file_state() {
    plain "$1" && [ -s "$1" ] || return 1
    attributes=$(stat -c '%d:%i:%s:%a:%y:%z' "$1") || return 1
    printf 'file %s %s\n' "$1" "$attributes"
}
native_state() {
    [ "$(uname -m)" = armv7l ] &&
        [ "$(lipc-get-prop -s com.lab126.powerd state)" = active ] &&
        [ "$(lipc-get-prop -s com.lab126.appmgrd activeApp)" = com.lab126.KPPMainApp ] || return 1
    case "$(status statusbar)" in *start/running*) ;; *) return 1;; esac
    for reader in aidoku weread bilicomics; do
        absent "/tmp/$reader-launcher.lock" || return 1
        if pidof "$reader" >/dev/null; then return 1; else [ "$?" = 1 ] || return 1; fi
    done
    boot=$(cat /proc/sys/kernel/random/boot_id) && [ -n "$boot" ] || return 1
    printf 'boot %s\n' "$boot"
    managers=$(pidof awesome) && [ -n "$managers" ] || return 1
    for manager in $managers; do
        number "$manager" || return 1
        case "$(sed -n 's/^State:[[:space:]]*//p' "/proc/$manager/status")" in R*|S*) ;; *) return 1;; esac
        birth=$(awk '{print $22}' "/proc/$manager/stat") && number "$birth" || return 1
        printf 'manager %s %s\n' "$manager" "$birth"
    done
    [ "$(cat /sys/class/graphics/fb0/stride)" = 1872 ] &&
        [ "$(cat /sys/class/graphics/fb0/bits_per_pixel)" = 8 ] || return 1
    rotation=$(cat /sys/class/graphics/fb0/rotate) && geometry=$(cat /sys/class/graphics/fb0/virtual_size) || return 1
    case "$rotation:$geometry" in [13]:1872,4960|[13]:1872,2480|[13]:1860,2480) ;; *) return 1;; esac
    printf 'panel %s %s\n' "$rotation" "$geometry"
    for node in /sys/class/input/event*; do
        [ -e "$node" ] || return 1
        printf 'input %s\n' "${node##*/}"
    done
}
lights() {
    for light in flIntensity currentAmberLevel; do
        value=$(lipc-get-prop -i com.lab126.powerd "$light") && number "$value" && [ "$value" -le 24 ] || return 1
        printf '%s %s\n' "$light" "$value"
    done
}
fixed_files() {
    for path in "$media"/documents/Aidoku.sh "$media"/aidoku/app/aidoku-startup-portrait \
        "$media"/aidoku/app/aidoku-native-query "$media"/aidoku/app/kindle-jpeg-worker \
        "$media"/aidoku/assets/aidoku-cover-v2.png "$media"/aidoku/assets/aidoku-cover.png \
        "$media"/documents/weread.sh "$media"/weread/launch.sh "$media"/weread/app/weread \
        "$media"/weread/app/weread-frame-guard "$media"/weread/app/weread-native-query \
        "$runtime"/aidoku-library-dispatch "$runtime"/aidoku-entry-hooks.so "$runtime"/aidoku-catalog-client \
        "$runtime"/protocol /usr/lib/ccat/change.lua; do
        file_state "$path" || return 1
    done
    [ "$(stat -c %s "$media/weread/app/weread")" = 95137176 ] || return 1
    handler_file=/var/local/kmc/bin/sh_integration_launcher
    leaf "$handler_file" && [ -s "$handler_file" ] || return 1
    target=$(readlink -f "$handler_file") && [ -n "$target" ] || return 1
    observation=$(stat -c '%d:%i:%s:%a:%y:%z' "$handler_file") || return 1
    printf 'handler %s %s\n' "$target" "$observation"
    for path in "$media"/bilicomics "$media"/documents/bilicomics.sh "$media"/documents/BiliComics.sh \
        "$media"/documents/bilicomics.sdr "$media"/documents/BiliComics.sdr \
        "$media"/documents/bilicomics.sh.sdr "$media"/documents/BiliComics.sh.sdr; do
        absent "$path" || return 1
    done
}
selection() {
    plain "$registry" && plain "$catalog" || return 1
    handler=tech.hackerdude.shell_integration.launcher
    mime='MT:text/x-shellscript'
    settings=$(sqlite_read "$registry" "BEGIN;
        SELECT (count(*)=1 AND max(value='$runtime/aidoku-library-dispatch')) FROM properties WHERE handlerId='$handler' AND name='command';
        SELECT count(*)=1 FROM associations WHERE handlerId='$handler' AND contentId='$mime';
        SELECT * FROM properties WHERE handlerId='$handler' ORDER BY name,value;
        SELECT * FROM associations WHERE handlerId='$handler' OR contentId='$mime' ORDER BY contentId,handlerId;
        COMMIT;") || return 1
    [ "$(printf '%s\n' "$settings" | sed -n '1,2p')" = "$(printf '1\n1')" ] || return 1
    printf '%s\n' "$settings"
    rows=$(sqlite_read "$catalog" "BEGIN;
        SELECT count(*) FROM Entries WHERE p_location COLLATE NOCASE='/mnt/us/documents/bilicomics.sh';
        SELECT count(*)=1 AND max(p_mimeType='text/x-shellscript') FROM Entries WHERE p_location COLLATE BINARY='/mnt/us/documents/Aidoku.sh';
        SELECT count(*)=1 AND max(p_mimeType='text/x-shellscript') FROM Entries WHERE p_location COLLATE BINARY='/mnt/us/documents/weread.sh';
        SELECT p_uuid,p_location,p_mimeType,p_thumbnail,p_modificationTime,p_contentSize FROM Entries
          WHERE p_location COLLATE BINARY IN ('/mnt/us/documents/Aidoku.sh','/mnt/us/documents/weread.sh') ORDER BY p_location;
        COMMIT;") || return 1
    [ "$(printf '%s\n' "$rows" | sed -n '1,3p')" = "$(printf '0\n1\n1')" ] || return 1
    printf '%s\n' "$rows"
}
projection() { native_state && fixed_files && selection; }
project_same() {
    observed=$(projection) && plain "$guard/projection" && printf '%s\n' "$observed" | cmp -s - "$guard/projection"
}
light_same() {
    observed=$(lights) && plain "$guard/lights" && printf '%s\n' "$observed" | cmp -s - "$guard/lights"
}

case "$operation" in
    admit)
        projection >/dev/null 2>&1 || guard_fail guard_admission
        lights >/dev/null 2>&1 || guard_fail guard_lights
        printf 'guard_admitted\n';;
    capture)
        owned && absent "$guard" || guard_fail guard_owner
        original=$(projection 2>/dev/null) || guard_fail guard_projection
        light_values=$(lights 2>/dev/null) || guard_fail guard_lights
        mkdir "$guard" || guard_fail guard_exists
        (set -C; printf '%s\n' "$original" >"$guard/projection" &&
            printf '%s\n' "$light_values" >"$guard/lights" && printf '%s\n' "$label" >"$guard/ready") || guard_fail guard_save
        printf 'guard_captured\n';;
    verify|restore)
        owned && directory "$guard" && marker "$guard/ready" || guard_fail guard_owner
        project_same 2>/dev/null || guard_fail guard_state_changed
        if [ "$operation" = restore ] && ! light_same 2>/dev/null; then
            plain "$guard/lights" && [ "$(wc -l <"$guard/lights")" -eq 2 ] || guard_fail guard_lights
            for light in flIntensity currentAmberLevel; do
                saved=$(awk -v light="$light" '$1==light && NF==2 {print $2}' "$guard/lights")
                number "$saved" && [ "$saved" -le 24 ] || guard_fail guard_lights
                now=$(lipc-get-prop -i com.lab126.powerd "$light") && number "$now" && [ "$now" -le 24 ] || guard_fail guard_lights
                if [ "$now" != "$saved" ]; then
                    lipc-set-prop -i com.lab126.powerd "$light" "$saved" >/dev/null 2>&1 || guard_fail guard_lights
                fi
            done
        fi
        light_same 2>/dev/null || guard_fail guard_lights_changed
        printf 'guard_verified\n';;
    *) guard_fail guard_operation;;
esac
