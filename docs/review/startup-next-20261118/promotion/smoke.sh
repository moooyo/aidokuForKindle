#!/bin/sh
# Qualify the unmodified installed Library entry under the operator's lease.
set -eu
set -f
umask 077
export LC_ALL=C
APP=/mnt/us/aidoku/app/aidoku
DOC=/mnt/us/documents/Aidoku.sh
LOCK=/tmp/aidoku-launcher.lock
LOG=/mnt/us/aidoku/data/logs/launcher.log
DB=/var/local/cc.db
CAPTURE_SHA=d728222e1363405564ae55f6ee32a850ac918cf6401cc013e0a84b50adf44c90
fail() { printf 'Installed entry: %s\n' "$1" >&2; exit 1; }
regular() { [ -f "$1" ] && [ ! -L "$1" ]; }
absent() { [ ! -e "$1" ] && [ ! -L "$1" ]; }
number() { case "$1" in ''|*[!0-9]*) return 1;; esac; }
pid() { number "$1" && [ "$1" -gt 1 ]; }
same_line() { regular "$2" && printf '%s\n' "$1" | cmp -s - "$2"; }
new_line() { (set -C; printf '%s\n' "$1" > "$2"); }
file_sha() { out=$(sha256sum "$1") || return 1; printf '%s\n' "${out%% *}"; }

native_now() (
    absent "$LOCK" || exit 1
    if ids=$(pidof aidoku 2>/dev/null); then exit 1; else rc=$?; fi
    [ "$rc" -eq 1 ] && [ -z "$ids" ] || exit 1
    active=$(lipc-get-prop com.lab126.appmgrd activeApp) || exit 1
    case "$active" in ''|*shell_integration.launcher*) exit 1;; esac
    if regular smoke.native; then same_line "$active" smoke.native || exit 1; fi
    [ "$(lipc-get-prop -s com.lab126.powerd state)" = active ] || exit 1
    case "$(status statusbar)" in *start/running*) ;; *) exit 1;; esac
    wms=$(pidof awesome) && [ -n "$wms" ] || exit 1
    for wm in $wms; do
        pid "$wm" || exit 1
        state=$(sed -n 's/^State:[[:space:]]*//p' "/proc/$wm/status") || exit 1
        case "$state" in R*|S*) ;; *) exit 1;; esac
    done
)
wait_native() (
    tries=$1
    while ! native_now; do
        [ "$tries" -gt 0 ] || exit 1
        tries=$((tries-1)); usleep 100000 || exit 1
    done
)
proc_identity() (
    who=$1
    pid "$who" && IFS= read -r fields < "/proc/$who/stat" || exit 1
    case "$fields" in "$who ("*') '*) ;; *) exit 1;; esac
    set -- ${fields##*) }
    [ "$#" -ge 20 ] || exit 1
    case "$1" in Z|X|x) exit 1;; esac
    ppid=$2; number "$ppid" || exit 1; shift 19; number "$1" || exit 1
    printf '%s %s\n' "$ppid" "$1"
)
identity() (
    [ -d "$LOCK" ] && [ ! -L "$LOCK" ] || exit 1
    for name in pid nonce generation runner-command application-pid; do regular "$LOCK/$name" || exit 1; done
    a=$(cat "$LOCK/application-pid"); w=$(cat "$LOCK/pid"); n=$(cat "$LOCK/nonce")
    pid "$a" && pid "$w" && same_line "$a" "$LOCK/runner-command" || exit 1
    case "$n" in ''|*[!0-9a-f]*) exit 1;; esac
    [ "${#n}" -eq 32 ] && same_line "1:$n" "$LOCK/generation" || exit 1
    [ "$(readlink "/proc/$a/exe")" = "$APP" ] || exit 1
    ai=$(proc_identity "$a") || exit 1
    set -- $ai; r=$1; ab=$2; pid "$r" || exit 1
    ri=$(proc_identity "$r") || exit 1
    set -- $ri; [ "$1" = "$w" ] || exit 1; rb=$2
    wi=$(proc_identity "$w") || exit 1
    # The Library waiter can retire; its independent worker can be reparented.
    set -- $wi; printf '%s %s %s %s %s %s %s\n' "$a" "$ab" "$r" "$rb" "$w" "$2" "$n"
)
same_app() ( row=$(identity) && same_line "$row" owned-app; )
bind_log() (
    regular "$LOG" || exit 1
    readout=$(stat -c '%d:%i %s' "$LOG") || exit 1
    new_line "$readout" smoke.log.bound
)
log_delta() (
    regular "$LOG" && regular smoke.log.bound || exit 1
    IFS=' ' read -r inode offset < smoke.log.bound || exit 1
    number "$offset" && [ "$(stat -c '%d:%i' "$LOG")" = "$inode" ] || exit 1
    size=$(stat -c %s "$LOG") || exit 1
    [ "$size" -ge "$offset" ] && [ "$((size-offset))" -le 1048576 ] || exit 1
    tail -c "+$((offset+1))" "$LOG"
)
bind_app() (
    same_line "$OP" smoke.request || exit 1
    row=$(identity) || exit 1; set -- $row
    delta=$(log_delta) || exit 1
    [ "$(printf '%s\n' "$delta" | grep -c '^aidoku: Launcher worker started (pid=[0-9][0-9]*)\.$' || :)" -eq 1 ] || exit 1
    printf '%s\n' "$delta" | grep -Fx "aidoku: Launcher worker started (pid=$5)." >/dev/null || exit 1
    if regular owned-app; then same_line "$row" owned-app; else new_line "$row" owned-app.pending && mv owned-app.pending owned-app; fi
)

catalog_identity() (
    sql="SELECT p_uuid,p_mimeType,p_thumbnail,p_modificationTime,p_contentSize FROM Entries WHERE p_location='$DOC' AND p_mimeType='text/x-shellscript' AND p_uuid IS NOT NULL;"
    line=$(sqlite3 -readonly -cmd '.timeout 1500' -separator '|' "$DB" "$sql") || exit 1
    [ -n "$line" ] && [ "$(printf '%s\n' "$line" | wc -l)" -eq 1 ] || exit 1
    count=$(sqlite3 -readonly -cmd '.timeout 1500' "$DB" "SELECT count(*) FROM Entries WHERE p_location='$DOC';") || exit 1
    [ "$count" = 1 ] || exit 1
    result=$(printf '%s\n' "$line" | sha256sum) || exit 1; printf '%s\n' "${result%% *}"
)
access_time() (
    a=$(sqlite3 -readonly -cmd '.timeout 1500' "$DB" "SELECT p_lastAccess FROM Entries WHERE p_location='$DOC';") || exit 1
    number "$a" || exit 1; printf '%s\n' "$a"
)
temp_guard() {
    [ -d "$PIXTMP" ] && [ ! -L "$PIXTMP" ] && [ "$(readlink -f "$PIXTMP")" = "$PIXTMP" ] && same_line "$RUN" "$PIXTMP/owner"
}
remove_pixels() {
    absent "$PIXTMP" && return 0
    temp_guard || return 1
    rm -f "$PIXTMP/frame" "$PIXTMP/read-status" "$PIXTMP/right-read" "$PIXTMP/right-od" "$PIXTMP/right-awk" "$PIXTMP/right-ranges" "$PIXTMP/owner" && rmdir "$PIXTMP"
}
right_toolbar() (
    # Each Scribe row is 5,580 RGB bytes. Read the header and top 72 rows
    # once. Only the right half of each row enters the lowercase hex stream.
    image=$1; report=$2
    temp_guard && regular "$image" && absent "$report" || exit 1
    for leaf in right-read right-od right-awk right-ranges; do absent "$PIXTMP/$leaf" || exit 1; done
    trap 'temp_guard && rm -f "$PIXTMP/right-read" "$PIXTMP/right-od" "$PIXTMP/right-awk" "$PIXTMP/right-ranges"' 0
    signature=$(
        (head -c 401796 "$image" && new_line done "$PIXTMP/right-read") |
        (od -An -v -tu1 && new_line done "$PIXTMP/right-od") |
        (awk -v ranges="$PIXTMP/right-ranges" '
          { for (i=1;i<=NF;i++) {
              n++; if (n<=36 || ((n-37)%5580)<2790) continue;
              ch=kept%3; v=$i+0; if (kept<3 || v<lo[ch]) lo[ch]=v; if(kept<3 || v>hi[ch]) hi[ch]=v;
              printf "%02x",v; kept++;
          }}
          END {if(n!=401796 || kept!=200880) exit 1;
               printf "%d %d %d %d %d %d\n",lo[0],hi[0],lo[1],hi[1],lo[2],hi[2] >ranges;
               if(close(ranges)!=0) exit 1}' && new_line done "$PIXTMP/right-awk") | sha256sum
    ) || exit 1
    for leaf in right-read right-od right-awk; do same_line done "$PIXTMP/$leaf" || exit 1; done
    digest=${signature%% *}; case "$digest" in ''|*[!0-9a-f]*) exit 1;; esac; [ "${#digest}" = 64 ] || exit 1
    regular "$PIXTMP/right-ranges" && read -r rl rh gl gh bl bh < "$PIXTMP/right-ranges" || exit 1
    # Preserve the measured extrema even on an invisible (flat) toolbar. A
    # constant colored area is unobservable, just like a constant white one.
    (set -C; printf '930\t0\t930\t72\t200880\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$rl" "$rh" "$gl" "$gh" "$bl" "$bh" "$digest" > "$report") || exit 1
    [ "$rl" -lt "$rh" ] || [ "$gl" -lt "$gh" ] || [ "$bl" -lt "$bh" ]
)
snapshot() (
    temp_guard && same_app && same_line active "$LOCK/phase" || exit 1
    # The qualified P6 header is 36 bytes. Exclude 72 physical status-bar rows.
    absent "$PIXTMP/frame" && absent "$PIXTMP/read-status" || exit 1
    trap 'temp_guard && rm -f "$PIXTMP/frame" "$PIXTMP/read-status"' 0
    (set -C; ./capture > "$PIXTMP/frame") || exit 1
    regular "$PIXTMP/frame" && [ "$(wc -c < "$PIXTMP/frame")" -eq 13838436 ] || exit 1
    header=$(head -c 36 "$PIXTMP/frame" | od -An -tx1 | tr -d ' \n') || exit 1
    [ "$header" = 50360a23206270703d3820726f746174696f6e3d330a3138363020323438300a3235350a ] || exit 1
    right_toolbar "$PIXTMP/frame" "$1" || exit 1
    digest=$( (tail -c +401797 "$PIXTMP/frame" && new_line done "$PIXTMP/read-status") | sha256sum) || exit 1
    same_line done "$PIXTMP/read-status" || exit 1
    same_app || exit 1; printf '%s\n' "${digest%% *}"
)
wait_phase() (
    want=$1; tries=$2
    while [ "$tries" -gt 0 ]; do
        same_app || exit 1
        same_line "$want" "$LOCK/phase" && exit 0
        usleep 100000 || exit 1; tries=$((tries-1))
    done
    exit 1
)
signal_owned() (
    same_app || exit 1
    read -r a rest < owned-app || exit 1
    kill "-$1" "$a"
)
recover() (
    # A transport failure after dispatch can precede both appmgr delivery and
    # lock creation. Observe the normal startup budget before declaring no work.
    if regular smoke.request && absent owned-app && absent "$LOCK"; then
        delay=180
        while [ "$delay" -gt 0 ] && absent "$LOCK"; do usleep 100000; delay=$((delay-1)); done
    fi
    if ! absent "$LOCK"; then
        if ! regular owned-app; then
            regular smoke.request || exit 1
            OP=$(cat smoke.request); case "$OP" in before|after) ;; *) exit 1;; esac
            tries=80
            while ! absent "$LOCK" && ! regular owned-app && [ "$tries" -gt 0 ]; do
                bind_app && break
                usleep 100000; tries=$((tries-1))
            done
        fi
        if ! absent "$LOCK" && same_app; then
            if same_line "$RUN" smoke.sleep; then
                current=$(lipc-get-prop -s com.lab126.powerd state) || exit 1
                if [ "$current" != active ]; then
                    same_app && lipc-set-prop -i com.lab126.powerd wakeUp 1 || exit 1
                fi
                wait_phase active 100 || exit 1
            fi
            signal_owned TERM || exit 1
        fi
    fi
    wait_native 200 && remove_pixels
)
exercise() {
    for f in smoke.request smoke.log.bound owned-app owned-app.pending smoke.sleep; do absent "$f" || fail 'Unfinished prior case.'; done
    regular ./capture && [ -x ./capture ] && [ "$(file_sha ./capture)" = "$CAPTURE_SHA" ] || fail 'Capture helper changed.'
    absent "$OP.sha" || fail 'A qualification result for this case already exists.'
    if [ "$OP" = after ]; then regular before.sha && absent wake.sha || fail 'Missing baseline or existing wake result.'; fi
    wait_native 80 || fail 'Native control unavailable at admission.'
    if ! regular smoke.native; then
        absent smoke.native || fail 'Invalid native record.'
        active=$(lipc-get-prop com.lab126.appmgrd activeApp) || fail 'Cannot observe native application.'
        new_line "$active" smoke.native || fail 'Cannot bind native application.'
    fi
    observed=$(catalog_identity) && access=$(access_time) || fail 'Unqualified catalog entry.'
    if [ "$OP" = before ]; then new_line "$observed" smoke.catalog; else same_line "$observed" smoke.catalog || fail 'Installed catalog identity differs.'; fi
    bind_log || fail 'Cannot bind the existing launcher log.'
    absent "$PIXTMP" && mkdir -m 700 "$PIXTMP" && new_line "$RUN" "$PIXTMP/owner" || fail 'Cannot create private raster directory.'
    lipc-set-prop -i com.lab126.powerd touchScreenSaverTimeout 1 || fail 'Cannot keep native Home active.'
    sleep 2
    native_now || fail 'Native state changed before dispatch.'
    new_line "$OP" smoke.request
    lipc-set-prop com.lab126.appmgrd start file:///mnt/us/documents/Aidoku.sh || fail 'Library request failed.'
    tries=180; ready=0
    while [ "$tries" -gt 0 ]; do
        if bind_app; then
            delta=$(log_delta) || fail 'Launcher log was replaced.'
            case "$delta" in *'aidoku: native window ready; profile=scribe, logical=930x1240, scale=2'*)
                if same_line active "$LOCK/phase"; then ready=1; break; fi;; esac
        fi
        usleep 100000; tries=$((tries-1))
    done
    [ "$ready" -eq 1 ] || fail 'No qualified normal-data Scribe application.'
    sleep 12
    frame=$(snapshot "$OP.right.tsv") || fail 'Library frame or right toolbar could not be observed.'
    if [ "$OP" = after ]; then
        same_line "$frame" before.sha || fail 'Installed Library differs below the status bar.'
        cmp -s before.right.tsv after.right.tsv || fail 'Installed Library right toolbar differs.'
        new_line "$RUN" smoke.sleep
        same_app && lipc-set-prop -i com.lab126.powerd powerButton 1 || fail 'Cannot request owned screensaver.'
        # No delayed wake process should survive cancellation or touch another test.
        wait_phase parked 45 || fail 'The application did not park.'
        same_app && lipc-set-prop -i com.lab126.powerd wakeUp 1 || fail 'Cannot wake the parked launch.'
        wait_phase active 100 || fail 'The same application did not resume.'
        rm smoke.sleep
        wake_frame=$(snapshot wake.right.tsv) || fail 'Restored frame or right toolbar could not be observed.'
        [ "$wake_frame" = "$frame" ] || fail 'Restored Library pixels differ.'
        cmp -s after.right.tsv wake.right.tsv || fail 'Restored Library right toolbar differs.'
    fi
    signal_owned TERM || fail 'Ownership changed before shutdown.'
    wait_native 200 || fail 'Native control was not restored.'
    delta=$(log_delta) || fail 'Cannot read the complete launcher interval.'
    starts=$(printf '%s\n' "$delta" | grep -c '^aidoku: Launcher worker started (pid=[0-9][0-9]*)\.$' || :)
    ready_lines=$(printf '%s\n' "$delta" | grep -c '^aidoku: native window ready;' || :)
    exits=$(printf '%s\n' "$delta" | grep -c '^aidoku: Launcher worker exited with status' || :)
    notices=$(printf '%s\n' "$delta" | grep -Fxc 'aidoku: Catalog notification completed.' || :)
    [ "$starts:$ready_lines:$exits:$notices" = '1:1:1:1' ] || fail 'Ambiguous launch, notification or exit.'
    printf '%s\n' "$delta" | grep -Fx 'aidoku: Launcher worker exited with status 0.' >/dev/null || fail 'Launch exited unsuccessfully.'
    case "$delta" in *'Catalog notification unavailable'*|*'standard reindex recovery'*) fail 'Catalog notification used recovery.';; esac
    final=$(catalog_identity) && final_access=$(access_time) && [ "$observed" = "$final" ] && [ "$final_access" -gt "$access" ] || fail 'Catalog identity or recent access was not preserved.'
    remove_pixels || fail 'Cannot remove the private raster.'
    rm smoke.request smoke.log.bound owned-app
    new_line "$frame" "$OP.sha"
    if [ "$OP" = after ]; then new_line "$wake_frame" wake.sha; printf '%s\n' CANDIDATE_SMOKE_OK; else printf '%s\n' BASELINE_SMOKE_OK; fi
}

# Entry point. The outer coordinator owns final restoration and lease release.
[ "$#" -eq 2 ] || fail 'Expected before, after or recover and a run ID.'
OP=$1; RUN=$2
case "$RUN" in ''|-*|*[!A-Za-z0-9_-]*) fail 'Invalid run ID.';; esac
[ "${#RUN}" -le 80 ] && [ "$(pwd -P)" = "/mnt/us/.aidoku-next-$RUN" ] && same_line "$RUN" owner || fail 'Stage ownership differs.'
PIXTMP=/var/tmp/aidoku-next-smoke-$RUN
case "$OP" in
    before|after) exercise;;
    recover) recover || fail 'Recovery cannot prove owned native control.'; printf '%s\n' RECOVERED;;
    *) fail 'Unknown operation.';;
esac
