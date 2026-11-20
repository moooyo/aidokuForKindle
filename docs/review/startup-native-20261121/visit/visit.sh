#!/bin/sh
set -efu
export LC_ALL=C
umask 077

fail() { printf 'refused visit_%s\n' "$1" >&2; exit 1; }
[ "$#" -ge 3 ] && [ "$#" -le 4 ] || fail arguments
step=$1; label=$2; name=$3; origin=${4:-}
case "$label" in ''|*[!0-9a-f]*) fail label;; esac
[ "${#label}" -eq 16 ] || fail label
case "$name" in [a-z]*) ;; *) fail case_name;; esac
case "$name" in *[!a-z0-9-]*) fail case_name;; esac
[ "${#name}" -le 48 ] || fail case_name
case "$step:$origin" in
    arm:stock-tap|arm:indexed-uri|request:indexed-uri) [ "$#" = 4 ] || fail arguments;;
    prepare:|bind:|check:|exit:|recover:|recover-dispatched:|inspect:) [ "$#" = 3 ] || fail arguments;;
    *) fail operation;;
esac
stage=/mnt/us/.aidoku-native-$label
state=/var/tmp/aidoku-native-$label
out=$state/visit-$name
live=$state/visit-live
[ "$out" != "$live" ] || fail case_name
home=/mnt/us/aidoku
app=$home/app/aidoku
document=/mnt/us/documents/Aidoku.sh
log=$home/data/logs/launcher.log
lock=/tmp/aidoku-launcher.lock
database=/var/local/cc.db
detail=0
case "$step" in prepare|inspect) detail=1;; esac
why() { [ "$detail" != 1 ] || printf 'refused visit_%s\n' "$1" >&2; return 1; }

absent() { [ ! -e "$1" ] && [ ! -L "$1" ]; }
regular() { [ -f "$1" ] && [ ! -L "$1" ]; }
directory() { [ -d "$1" ] && [ ! -L "$1" ] && [ "$(readlink -f "$1")" = "$1" ]; }
line() { regular "$1" && printf '%s\n' "$2" | cmp -s - "$1"; }
put() { (set -C; printf '%s\n' "$2" >"$1"); }
number() { case "$1" in ''|*[!0-9]*) return 1;; esac; }
pid() { number "$1" && [ "$1" -gt 1 ]; }
context() {
    directory "$stage" || { why stage_directory; return 1; }
    directory "$state" || { why state_directory; return 1; }
    line "$stage/owner" "$label" || { why stage_owner_marker; return 1; }
    line "$state/owner" "$label" || { why state_owner_marker; return 1; }
    [ "$(stat -c '%u:%a' "$state" 2>/dev/null)" = 0:700 ] || { why state_mode; return 1; }
}
case_owner() { directory "$out" && line "$out/owner" "$label $name"; }
current() { case_owner && directory "$live" && line "$live/case" "$label $name"; }
close_live() { current && rm "$live/case" && rmdir "$live"; }
same_boot() { regular "$out/boot" && cmp -s "$out/boot" /proc/sys/kernel/random/boot_id; }
dispatched_owner() (
    current && same_boot && line "$state/current" "$name" && line "$state/ready" "$label" &&
        line "$state/guard/ready" "$label" && directory "$state/out" && directory "$state/out/$name" &&
        absent "$state/out/$name/qualified" && absent "$out/complete" && regular "$out/request" || exit 1
    requested=$(cat "$out/request") || exit 1
    case "$requested" in indexed-uri|stock-tap) ;; *) exit 1;; esac
    line "$out/request" "$requested" && line "$state/out/$name/origin" "$requested" &&
        line "$state/out/$name/dispatched" "$requested"
)

proc_row() (
    who=$1; pid "$who" && IFS= read -r raw <"/proc/$who/stat" || exit 1
    case "$raw" in "$who ("*') '*) ;; *) exit 1;; esac
    set -- ${raw##*) }
    [ "$#" -ge 20 ] || exit 1
    status=$1; parent=$2; shift 19
    case "$status" in Z|X|x) exit 1;; esac
    number "$parent" && number "$1" || exit 1
    printf '%s %s %s\n' "$parent" "$1" "$status"
)
no_reader() (
    for application in "$@"; do
        if ids=$(pidof "$application" 2>/dev/null); then why reader_present; exit 1; else result=$?; fi
        [ "$result" = 1 ] && [ -z "$ids" ] || { why reader_query; exit 1; }
        absent "/tmp/$application-launcher.lock" || { why reader_lock; exit 1; }
    done
)
native_state() (
    no_reader aidoku weread bilicomics || exit 1
    [ "$(lipc-get-prop -s com.lab126.powerd state 2>/dev/null)" = active ] || { why power_state; exit 1; }
    [ "$(lipc-get-prop -s com.lab126.appmgrd activeApp 2>/dev/null)" = com.lab126.KPPMainApp ] || { why stock_foreground; exit 1; }
    case "$(status statusbar 2>/dev/null)" in *start/running*) ;; *) why statusbar; exit 1;; esac
    managers=$(pidof awesome 2>/dev/null) && [ -n "$managers" ] || { why wm_absent; exit 1; }
    roster=''
    for manager in $managers; do
        row=$(proc_row "$manager" 2>/dev/null) || { why wm_identity; exit 1; }
        set -- $row; case "$3" in R|S) ;; *) why wm_state; exit 1;; esac
        roster="$roster $manager:$2"
    done
    printf '%s\n' $roster | sort
)
stock_original() (
    same_boot || exit 1
    now=$(native_state) && line "$out/native" "$now"
)
wait_stock() (
    count=160
    until stock_original; do count=$((count-1)); [ "$count" -gt 0 ] || exit 1; usleep 100000 || exit 1; done
)
get_light() (
    value=$(lipc-get-prop -i com.lab126.powerd "$1") && number "$value" && [ "$value" -le 24 ] || exit 1
    printf '%s\n' "$value"
)
lights() (
    for key in flIntensity currentAmberLevel; do
        old=$(cat "$out/$key") && line "$out/$key" "$old" && number "$old" && [ "$old" -le 24 ] || exit 1
        now=$(get_light "$key") || exit 1
        if [ "$now" != "$old" ]; then
            [ "$1" = restore ] && regular "$out/bound" && stock_original &&
                lipc-set-prop -i com.lab126.powerd "$key" "$old" || exit 1
        fi
        tries=8
        until [ "$(get_light "$key")" = "$old" ]; do
            tries=$((tries-1)); [ "$tries" -gt 0 ] || exit 1; usleep 100000 || exit 1
        done
    done
)
catalog() (
    regular "$document" || { why document_type; exit 1; }
    [ "$(readlink -f "$document" 2>/dev/null)" = "$document" ] || { why document_path; exit 1; }
    regular "$database" || { why catalog_type; exit 1; }
    row=$(sqlite3 -readonly -cmd '.timeout 1500' -separator '|' "$database" \
        "SELECT count(*),hex(p_uuid),hex(p_mimeType),hex(p_thumbnail),p_modificationTime,p_contentSize,p_lastAccess FROM Entries WHERE p_location='$document';" 2>/dev/null) || { why catalog_query; exit 1; }
    case "$row" in *'
'*) why catalog_lines; exit 1;; esac
    IFS='|' read -r count uuid mime thumb modified size access extra <<EOF
$row
EOF
    [ "$count" = 1 ] || { why catalog_count; exit 1; }
    [ -z "$extra" ] || { why catalog_fields; exit 1; }
    [ "${#uuid}" = 72 ] || { why catalog_uuid; exit 1; }
    [ "$mime" = 746578742F782D7368656C6C736372697074 ] || { why catalog_mime; exit 1; }
    number "$modified" || { why catalog_mtime_format; exit 1; }
    number "$size" || { why catalog_size_format; exit 1; }
    number "$access" || { why catalog_access_format; exit 1; }
    [ "$modified" = "$(stat -c %Y "$document" 2>/dev/null)" ] || { why catalog_file_mtime; exit 1; }
    [ "$size" = "$(stat -c %s "$document" 2>/dev/null)" ] || { why catalog_file_size; exit 1; }
    case "$uuid$thumb" in *[!0-9A-F]*) why catalog_identity_shape; exit 1;; esac
    printf '%s %s\n' "$uuid:$mime:$thumb:$modified:$size" "$access"
)
log_mark() {
    regular "$log" || { why log_type; return 1; }
    stat -c '%d:%i %s' "$log" 2>/dev/null || { why log_stat; return 1; }
}
log_delta() (
    IFS=' ' read -r inode offset <"$out/log-mark" && number "$offset" && regular "$log" || exit 1
    now=$(log_mark) || exit 1; set -- $now
    [ "$1" = "$inode" ] && [ "$2" -ge "$offset" ] && [ "$(($2-offset))" -le 1048576 ] || exit 1
    tail -c "+$((offset+1))" "$log"
)
argv() { subject=$1; shift; printf '%s\0' "$@" | cmp -s - "/proc/$subject/cmdline"; }
environment() (
    subject=$1; role=$2; nonce=$3
    tr '\000' '\n' <"/proc/$subject/environ" | awk -v role="$role" -v nonce="$nonce" -v home="$home" -v lock="$lock" '
      /^AIDOKU_HOME=/ { if ($0!="AIDOKU_HOME=" home) bad=1; home_ok++; next }
      /^AIDOKU_MANAGED_LIBRARY=/ { if ($0!="AIDOKU_MANAGED_LIBRARY=1") bad=1; managed++; next }
      /^AIDOKU_LAUNCHER_DIR=/ { if (role!="app" || $0!="AIDOKU_LAUNCHER_DIR=" lock) bad=1; dir++; next }
      /^AIDOKU_LAUNCH_NONCE=/ { if(role!="app" || $0!="AIDOKU_LAUNCH_NONCE=" nonce) bad=1; token++; next }
      /^AIDOKU_LAUNCH_GENERATION=/ { if(role!="app" || $0!="AIDOKU_LAUNCH_GENERATION=1") bad=1; gen++; next }
      /^AIDOKU_CATALOG_NOTIFY_(SHA256|HELPER)=/ { if(role!="worker") bad=1; next }
      /^AIDOKU_/ {bad=1}
      END {exit !(home_ok==1 && managed==1 && !bad && (role=="worker" || (dir==1 && token==1 && gen==1)))}'
)
production_lock() (
    [ -d "$lock" ] && [ ! -L "$lock" ] || exit 1
    parent=$(readlink -f "${lock%/*}") && [ -n "$parent" ] || exit 1
    target=$parent/aidoku-launcher.lock
    [ "$(readlink -f "$lock")" = "$target" ] && [ ! -L "$target" ] || exit 1
    identity=$(stat -c '%d:%i:%u:%a' "$lock") && [ "$identity" = "$(stat -c '%d:%i:%u:%a' "$target")" ] || exit 1
    case "$identity" in *:0:700|*:0:2700) ;; *) exit 1;; esac
    printf '%s %s\n' "$target" "$identity"
)
identity() (
    lock_at_start=$(production_lock) || exit 1
    for key in pid nonce application-pid runner-command generation; do regular "$lock/$key" || exit 1; done
    gui=$(cat "$lock/application-pid"); worker=$(cat "$lock/pid"); nonce=$(cat "$lock/nonce")
    pid "$gui" && pid "$worker" && line "$lock/application-pid" "$gui" && line "$lock/pid" "$worker" &&
        line "$lock/runner-command" "$gui" && line "$lock/nonce" "$nonce" && line "$lock/generation" "1:$nonce" || exit 1
    case "$nonce" in ''|*[!0-9a-f]*) exit 1;; esac
    [ "${#nonce}" = 32 ] && [ "$(readlink "/proc/$gui/exe")" = "$app" ] &&
        [ "$(stat -Lc '%d:%i' "/proc/$gui/exe")" = "$(stat -c '%d:%i' "$app")" ] &&
        [ "$(readlink "/proc/$worker/exe")" = "$(readlink -f /bin/sh)" ] &&
        argv "$gui" "$app" --kindle --device scribe --data-dir "$home/data" &&
        argv "$worker" sh "$home/launch.sh" --foreground || exit 1
    a=$(proc_row "$gui") || exit 1; set -- $a; runner=$1; ab=$2
    r=$(proc_row "$runner") || exit 1; set -- $r; [ "$1" = "$worker" ] || exit 1; rb=$2
    w=$(proc_row "$worker") || exit 1; set -- $w
    [ "$(production_lock)" = "$lock_at_start" ] || exit 1
    printf '%s %s %s %s %s %s %s\n' "$gui" "$ab" "$runner" "$rb" "$worker" "$2" "$nonce"
)
same_app() (
    current && same_boot && row=$(identity) && line "$out/bound" "$row"
)
bind_now() (
    current && same_boot && regular "$out/request" && row=$(identity) || exit 1
    set -- $row; worker=$5
    environment "$1" app "$7" && environment "$worker" worker "$7" || exit 1
    delta=$(log_delta) || exit 1
    printf '%s\n' "$delta" | awk -v wanted="aidoku: Launcher worker started (pid=$worker)." '
      /^aidoku: Launcher worker started \(pid=[0-9]+\)\.$/ {total++; if ($0==wanted) correct++}
      END {exit !(total==1 && correct==1)}' || exit 1
    [ "$(identity)" = "$row" ] || exit 1
    if regular "$out/bound"; then line "$out/bound" "$row"; else put "$out/bound" "$row"; fi
)
wait_bound() (
    tries=$1
    until bind_now; do tries=$((tries-1)); [ "$tries" -gt 0 ] || exit 1; usleep 150000 || exit 1; done
)
io_ready() (
    set +f
    panel=0; input=0
    for desc in /proc/"$1"/fd/*; do
        dest=$(readlink "$desc" 2>/dev/null) || continue
        case "$dest" in /dev/fb0) panel=1;; /dev/input/event*) number "${dest#/dev/input/event}" || exit 1; input=1;; esac
    done
    [ "$panel:$input" = 1:1 ]
)
active_app() (
    same_app && line "$lock/phase" active && line "$lock/epoch" 0 &&
        absent "$lock/sleep-request" && absent "$lock/ordinary-exit" && no_reader weread bilicomics || exit 1
    IFS=' ' read -r gui ab runner rb worker wb nonce <"$out/bound" || exit 1
    environment "$gui" app "$nonce" && environment "$worker" worker "$nonce" && io_ready "$gui" || exit 1
    delta=$(log_delta) || exit 1
    printf '%s\n' "$delta" | grep -Fxq 'aidoku: native window ready; profile=scribe, logical=930x1240, scale=2' || exit 1
    same_app
)
stop_owned() (
    same_app && line "$lock/epoch" 0 && line "$lock/phase" active && absent "$lock/sleep-request" || exit 1
    IFS=' ' read -r gui ab runner rb worker wb nonce <"$out/bound" || exit 1
    if line "$lock/ordinary-exit" "$gui $nonce 0"; then
        regular "$out/term" || exit 1
    else
        absent "$lock/ordinary-exit" || exit 1
        if ! regular "$out/term"; then put "$out/term" requested || exit 1; fi
        if [ "$step" = recover-dispatched ]; then dispatched_owner || exit 1; fi
        same_app && kill -TERM "$gui" || exit 1
    fi
    tries=350
    until line "$lock/ordinary-exit" "$gui $nonce 0"; do
        [ -d "$lock" ] || exit 1
        tries=$((tries-1)); [ "$tries" -gt 0 ] || exit 1; usleep 10000 || exit 1
    done
    if regular "$out/ack"; then line "$out/ack" observed; else put "$out/ack" observed; fi
)
complete_log() (
    delta=$(log_delta) || exit 1
    printf '%s\n' "$delta" | awk '
      /^aidoku: Launcher worker started \(pid=[0-9]+\)\.$/ {s++}
      /^aidoku: native window ready;/ {r++}
      /^aidoku: Launcher worker exited with status / {x++; if ($0=="aidoku: Launcher worker exited with status 0.") good++}
      /^aidoku: Catalog notification completed\.$/ {n++}
      /Catalog notification unavailable|standard reindex recovery/ {bad=1}
      END {exit !(s==1 && r==1 && x==1 && good==1 && n==1 && !bad)}'
)
final_checks() (
    current && stock_original && lights compare && line "$out/ack" observed || exit 1
    row=$(catalog) || exit 1; set -- $row
    IFS=' ' read -r identity_before access <"$out/catalog" || exit 1
    [ "$identity_before" = "$1" ] && [ "$2" -gt "$access" ] && complete_log
)

if [ "$step" = inspect ]; then
    if absent "$stage" && absent "$state"; then
        scope=no_stage
    else
        context || exit 1
        absent "$live" || fail existing_live
        absent "$out" || fail existing_case
        scope=staged
    fi
    native_state >/dev/null && catalog >/dev/null && log_mark >/dev/null || exit 1
    for key in flIntensity currentAmberLevel; do get_light "$key" >/dev/null 2>&1 || fail lighting; done
    [ -r /proc/sys/kernel/random/boot_id ] || fail boot_unreadable
    printf 'visit_inputs_admitted %s\n' "$scope"
    exit 0
fi
if ! context; then [ "$detail" = 1 ] && exit 1; fail owner; fi
if [ "$step" = recover-dispatched ]; then
    exec 7<"$stage"; flock -n 7 || fail outer_operation_running
    dispatched_owner || fail dispatch_ownership
fi
if [ "$step" = prepare ]; then
    absent "$live" || fail existing_live
    absent "$out" || fail existing_case
    mkdir -m 700 "$out" 2>/dev/null || fail case_create
    put "$out/owner" "$label $name" || fail case_marker
fi
case_owner || fail case_owner
exec 8<"$out"; flock -n 8 || fail operation_running
case "$step" in
    prepare)
        stock=$(native_state) && record=$(catalog) && boundary=$(log_mark) || exit 1
        put "$out/native" "$stock" && put "$out/catalog" "$record" && put "$out/log-mark" "$boundary" &&
            cp /proc/sys/kernel/random/boot_id "$out/boot" || fail baseline
        for key in flIntensity currentAmberLevel; do value=$(get_light "$key") && put "$out/$key" "$value" || fail lighting; done
        mkdir -m 700 "$live" && put "$live/case" "$label $name" || fail live_case
        printf 'prepared\n';;
    arm)
        current && stock_original && lights compare && record=$(catalog) && line "$out/catalog" "$record" &&
            boundary=$(log_mark) && line "$out/log-mark" "$boundary" && put "$out/request" "$origin" || fail dispatch_admission
        printf 'armed %s\n' "$origin";;
    request)
        current && same_boot && line "$out/request" indexed-uri && put "$out/uri-sent" once &&
            stock_original && lipc-set-prop com.lab126.appmgrd start file:///mnt/us/documents/Aidoku.sh || fail uri
        printf 'requested indexed-uri\n';;
    bind)
        current && wait_bound 100 || fail ownership
        tries=70
        until active_app; do tries=$((tries-1)); [ "$tries" -gt 0 ] && same_app || fail normal_gui; usleep 100000; done
        put "$out/ready" observed || fail readiness
        printf 'bound normal_gui display_and_input\n';;
    check)
        line "$out/ready" observed && active_app || fail normal_gui
        printf 'active normal_gui display_and_input\n';;
    exit)
        line "$out/ready" observed && active_app && stop_owned || fail owned_exit
        wait_stock && lights restore || fail native_return
        tries=12
        until final_checks; do tries=$((tries-1)); [ "$tries" -gt 0 ] || fail exit_qualification; usleep 200000; done
        put "$out/complete" qualified && close_live || fail close
        printf 'qualified ordinary_exit native_restored lights_restored catalog_advanced notified\n';;
    recover|recover-dispatched)
        if ! current; then
            case_owner && absent "$live" && { line "$out/complete" qualified || line "$out/recovered" safe || absent "$out/request"; } || fail recovery_owner
            if regular "$out/boot" && regular "$out/native"; then stock_original || fail native_return; else native_state >/dev/null || fail native_return; fi
            printf 'no_live_visit\n'; exit 0
        fi
        same_boot || fail changed_boot
        if regular "$out/request"; then
            if absent "$out/bound"; then wait_bound 100 || absent "$lock" || fail unknown_session; fi
            if ! absent "$lock"; then
                if same_app; then
                    if absent "$out/ack"; then stop_owned || { regular "$out/term" && wait_stock || fail owned_exit; }; fi
                elif regular "$out/term"; then
                    wait_stock || fail ambiguous_exit
                else
                    fail unowned_session
                fi
            fi
        fi
        wait_stock && lights restore || fail native_return
        put "$out/recovered" safe && close_live || fail recovery_close
        printf 'restored_only\n';;
esac
