#!/bin/sh
# Run one profile at a time; never use a real-device connection.
set -eu
umask 077
w=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
root=/root/aidoku-emulator
profile=${1:?Pass scribe or pw6}
suffix=${2:-initial}
case "$profile" in
    scribe) machine=aidoku-scribe1-5201; guest=guest-ssh-scribe1 ;;
    pw6) machine=aidoku-pw6-5201; guest=guest-ssh ;;
    *) exit 2 ;;
esac
case "$suffix" in ''|*[!a-z0-9-]*) exit 2 ;; esac
cd "$w/source"
pidfile=$root/run/$machine.pid
qmp=$root/eink-emulator/machines/$machine/$machine.qmp.sock
if [ -S "$qmp" ] || { [ -r "$pidfile" ] && IFS= read -r pid <"$pidfile" && kill -0 "$pid" 2>/dev/null; }; then
    printf '%s\n' "Guest already running; refusing to take ownership: $machine" >&2
    exit 1
fi
"$root/start-guest" "$profile" >"$w/logs/guest-$profile-$suffix-start.log" 2>&1 || {
    grep -E '.' "$w/logs/guest-$profile-$suffix-start.log" >&2
    exit 1
}
IFS= read -r started <"$pidfile"
previous=
finish_guest() {
    trap - EXIT HUP INT TERM
    [ -r "$pidfile" ] && IFS= read -r current <"$pidfile" && [ "$current" = "$started" ] &&
        tr '\000' '\n' <"/proc/$started/cmdline" | grep -Fxq "$machine" || return 1
    [ "$(timeout 12 "$root/$guest" 'cat /var/local/aidoku-emulator-id' 2>/dev/null)" = "$machine" ] || return 1
    if [ -n "$previous" ]; then
        timeout 15 "$root/$guest" "test ! -e /tmp/aidoku-launcher.lock && lipc-set-prop -i com.lab126.powerd preventScreenSaver $previous && test \"\$(lipc-get-prop -i com.lab126.powerd preventScreenSaver)\" = $previous && sync" || return 1
    else
        timeout 15 "$root/$guest" 'test ! -e /tmp/aidoku-launcher.lock && sync' || return 1
    fi
    (cd "$root/eink-emulator" && python3 -B scripts/eink-qmp.py --machine "$machine" quit) || return 1
    printf '%s\n' "$profile guest native state restored and stopped"
}
trap 'finish_guest || printf "%s\n" "Guest cleanup not verified: $machine" >&2' EXIT
trap 'exit 1' HUP INT TERM
ready=no
log_base=$w/logs/guest-$profile-$suffix
for n in $(seq 1 80); do
    if timeout 8 "$root/$guest" 'cat /var/local/aidoku-emulator-id; status statusbar; pidof awesome' >"$log_base-ready.log" 2>&1 &&
        grep -Fxq "$machine" "$log_base-ready.log" && grep -q 'start/running' "$log_base-ready.log"; then
        ready=yes
        break
    fi
    sleep 2
done
[ "$ready" = yes ] || { grep -E '.' "$log_base-ready.log" >&2 || :; exit 1; }
power=$(timeout 12 "$root/$guest" 'lipc-get-prop -s com.lab126.powerd state')
if [ "$power" = screenSaver ]; then
    timeout 12 "$root/$guest" 'lipc-set-prop -i com.lab126.powerd wakeUp 1'
    for n in $(seq 1 30); do
        power=$(timeout 12 "$root/$guest" 'lipc-get-prop -s com.lab126.powerd state')
        [ "$power" != active ] || break
        sleep 1
    done
fi
[ "$power" = active ] || { printf 'Guest not active: %s (%s)\n' "$profile" "$power" >&2; exit 1; }
previous=$(timeout 12 "$root/$guest" 'lipc-get-prop -i com.lab126.powerd preventScreenSaver')
case "$previous" in 0|1) ;; *) previous=; exit 1 ;; esac
timeout 15 "$root/$guest" 'test ! -e /tmp/aidoku-launcher.lock && lipc-set-prop -i com.lab126.powerd touchScreenSaverTimeout 1 && lipc-set-prop -i com.lab126.powerd preventScreenSaver 1 && test "$(lipc-get-prop -i com.lab126.powerd preventScreenSaver)" = 1'
status=0
for arm in control candidate; do
    printf '%s\n' "$profile $arm qualification"
    python3 -I -B scripts/verify-startup-fonts-qa.py --cases startup-library --profile "$profile" \
        --firmware 5.20.1 --machine "$machine" --emulator-root "$root/eink-emulator" \
        --guest-ssh "$root/$guest" --binary "$w/bin/$arm" --native-query "$w/bin/aidoku-native-query" \
        --output "$w/firmware-$profile-$arm-$suffix" --duration-ms 12000 || status=1
done
finish_guest || exit 1
exit "$status"
