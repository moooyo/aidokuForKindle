# Appended to the pinned installed-entry smoke library in a guarded sh -s call.

guard_visit_stage() {
    [ -d "$STAGE" ] && [ ! -L "$STAGE" ] && [ "$(readlink -f "$STAGE")" = "$STAGE" ] &&
        [ "$(stat -c '%u:%a' "$STAGE")" = 0:700 ] && same_line "$RUN" "$STAGE/owner"
}

same_argv() {
    subject=$1; shift
    printf '%s\0' "$@" | cmp -s - "/proc/$subject/cmdline"
}

project_chain() (
    same_app && read -r app ab runner rb worker wb nonce < owned-app || exit 1
    shell_exe=$(readlink -f /bin/sh) || exit 1
    case "$shell_exe" in /bin/sh|/bin/busybox|/bin/dash|/bin/bash|/usr/bin/busybox) ;; *) exit 1;; esac
    su_exe=$(readlink -f /var/local/mkk/su 2>/dev/null || :)
    case "$su_exe" in /var/local/mkk/su|/var/local/mkk/gandalf) ;; *) su_exe='';; esac
    handler_exe=/var/local/kmc/bin/sh_integration_launcher
    canonical='sh -l "/mnt/us/documents/Aidoku.sh"'
    launch=/mnt/us/aidoku/launch.sh
    front=$(lipc-get-prop com.lab126.appmgrd activeApp) || exit 1
    case "$front" in tech.hackerdude.shell_integration.launcher|app://tech.hackerdude.shell_integration.launcher*) scoped=1;; *) scoped=0;; esac
    worker_ok=0
    if [ "$(readlink "/proc/$worker/exe")" = "$shell_exe" ] && same_argv "$worker" sh "$launch" --foreground; then
        worker_ok=1
    fi
    printf 'summary %s %s\n' "$worker_ok" "$scoped"
    row=$(proc_identity "$worker") || exit 1; set -- $row
    [ "$2" = "$wb" ] || exit 1
    current=$1; depth=0
    while pid "$current" && [ "$depth" -lt 8 ]; do
        before=$(proc_identity "$current") || exit 1; set -- $before
        parent=$1; birth=$2
        executable=$(readlink "/proc/$current/exe") || exit 1
        role=unexpected
        if [ "$executable" = "$shell_exe" ]; then
            if [ "$depth" = 0 ] && same_argv "$current" sh "$launch" --library; then role=waiter
            elif same_argv "$current" sh -c "$canonical" || same_argv "$current" /bin/sh -c "$canonical"; then role=command_shell
            elif same_argv "$current" sh -l "$DOC" || same_argv "$current" /bin/sh -l "$DOC"; then role=document_shell
            else role=other_shell; fi
        elif [ -n "$su_exe" ] && [ "$executable" = "$su_exe" ]; then
            role=su_other
            case "$(stat -L -c '%u:%a' "/proc/$current/exe")" in
                0:755|0:4755) if same_argv "$current" su -c "$canonical"; then role=canonical_su; fi;;
            esac
        elif [ "$executable" = "$handler_exe" ]; then
            role=handler_other
            if same_argv "$current" "$handler_exe"; then role=handler; fi
        else
            case "$executable" in
                /usr/sbin/appmgrd|/usr/bin/appmgrd|/sbin/appmgrd|/bin/appmgrd)
                    case "$(stat -L -c '%u:%a' "/proc/$current/exe")" in 0:755|0:750|0:700) role=manager;; esac;;
            esac
        fi
        [ "$(proc_identity "$current")" = "$before" ] && [ "$(readlink "/proc/$current/exe")" = "$executable" ] || exit 1
        printf '%s %s %s %s\n' "$role" "$current" "$birth" "$parent"
        [ "$role" != manager ] || break
        current=$parent; depth=$((depth+1))
    done
    same_app
)

projection_flags() {
    awk '
        NR == 1 {worker=$2; front=$3; next}
        { n++; kind[n]=$1; id[n]=$2; parent[n]=$4; seen[$1]++ }
        END {
          for (i=1;i<=n;i++) {
            if (kind[i]=="canonical_su" && kind[i+1]=="handler" && kind[i+2]=="manager" &&
                parent[i]==id[i+1] && parent[i+1]==id[i+2]) scoped_su=1;
          }
          printf "%d %d %d %d %d %d %d %d %d %d %d %d\n", worker, front,
              (kind[1]=="waiter"), (seen["canonical_su"]==1), (seen["handler"]==1), (seen["manager"]==1),
              scoped_su, (seen["command_shell"]>0), (seen["document_shell"]>0),
              (seen["unexpected"]>0 || seen["other_shell"]>0), (seen["su_other"]>0), (seen["handler_other"]>0);
        }
    ' "$1"
}

distinct_role() (
    previous=$(awk -v key="$1" '$1==key { print $2, $3 }' first-admitted)
    current=$(awk -v key="$1" '$1==key { print $2, $3 }' second-admitted)
    if [ -n "$previous" ] && [ -n "$current" ] && [ "$previous" != "$current" ]; then printf 1; else printf 0; fi
)

visit() {
    OP=$1; label=$2
    for leaf in smoke.request smoke.log.bound owned-app owned-app.pending; do absent "$leaf" || return 1; done
    wait_native 80 || return 1
    bind_log && new_line "$OP" smoke.request || return 1
    lipc-set-prop com.lab126.appmgrd start file:///mnt/us/documents/Aidoku.sh >/dev/null 2>&1 || return 1
    tries=180; ready=0
    while [ "$tries" -gt 0 ]; do
        if bind_app; then
            delta=$(log_delta) || return 1
            case "$delta" in *'aidoku: native window ready; profile=scribe, logical=930x1240, scale=2'*)
                if same_line active "$LOCK/phase"; then ready=1; break; fi;; esac
        fi
        usleep 100000 || return 1; tries=$((tries-1))
    done
    [ "$ready" -eq 1 ] || return 1
    (set -C; project_chain >"$label-admitted") || return 1
    sleep 1
    (set -C; project_chain >"$label-exiting") || return 1
    stable=0
    if cmp -s "$label-admitted" "$label-exiting"; then stable=1; fi
    signal_owned TERM || return 1
    wait_native 200 || return 1
    delta=$(log_delta) || return 1
    [ "$(printf '%s\n' "$delta" | grep -Fxc 'aidoku: Launcher worker exited with status 0.' || :)" -eq 1 ] || return 1
    [ "$(printf '%s\n' "$delta" | grep -Fxc 'aidoku: Catalog notification completed.' || :)" -eq 1 ] || return 1
    printf '%s-admitted %s\n' "$label" "$(projection_flags "$label-admitted")"
    printf '%s-exiting %s\n' "$label" "$(projection_flags "$label-exiting")"
    printf '%s-stable %s\n' "$label" "$stable"
    rm -f smoke.request smoke.log.bound owned-app
}

observe_visits() {
    wait_native 80 || return 1
    active=$(lipc-get-prop com.lab126.appmgrd activeApp) || return 1
    new_line "$active" smoke.native || return 1
    original=$(catalog_identity) && access=$(access_time) || return 1
    visit before first && visit after second || return 1
    after=$(catalog_identity) && access_after=$(access_time) || return 1
    [ "$original" = "$after" ] && [ "$access_after" -gt "$access" ] || return 1
    printf 'distinct %s %s %s\n' "$(distinct_role waiter)" "$(distinct_role canonical_su)" "$(distinct_role handler)"
}

visit_recover() {
    if regular smoke.request; then recover || return 1; else wait_native 200 || return 1; fi
}

remove_visit_stage() {
    guard_visit_stage || return 1
    for leaf in smoke.native smoke.request smoke.log.bound owned-app owned-app.pending \
        first-admitted first-exiting second-admitted second-exiting; do
        if ! absent "$leaf"; then regular "$leaf" && rm -f "$leaf" || return 1; fi
    done
    [ "$(ls -A .)" = owner ] && rm -f owner && cd / && rmdir "$STAGE"
}
