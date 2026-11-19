# Used in the signed standalone benchmark, never in the installed launcher.
reopen_proc() {
    read -r rs <"/proc/$1/stat" || return 1
    case "$rs" in "$1 ("*) ;; *) return 1;; esac
    rs=${rs##*) }; set -- $rs
    [ "$#" -ge 20 ] || return 1
    case "$1" in Z|X) return 1;; esac
    r_session=$4; r_ticks=$((${12}+${13})); shift 19; r_birth=$1
    case "$r_birth:$r_session" in *[!0-9:]*|:*|*:) return 1;; esac
}
keeper_pid() {
    kpid=0; kbirth=0; ksid=0; kticks=0
    for cf in /proc/[0-9]*/comm; do
        [ -r "$cf" ] || continue; { read -r comm <"$cf"; } 2>/dev/null || continue
        case "$comm" in exe|aidoku|aidoku-resident*|memfd:aidoku*) ;; *) continue;; esac
        kd=${cf%/comm}; kp=${kd##*/}
        [ "$(readlink "$kd/exe" 2>/dev/null || :)" = "$STAGE/$variant/app/aidoku" ] || continue
        [ "$(sha256sum "$kd/cmdline" 2>/dev/null | cut -d ' ' -f 1)" = "$ROLE_SHA" ] || continue
        reopen_proc "$kp" || continue
        [ "$kp" = "$r_session" ] || return 1
        [ "$kpid" = 0 ] || return 1
        kpid=$kp;kbirth=$r_birth;ksid=$r_session;kticks=$r_ticks
    done
}
keeper_fds() {
    kd=/proc/$kpid; tries=5
    while [ "$tries" -gt 0 ]; do
        zeros=0;socks=0;temporary=0
        for fd in "$kd"/fd/*; do
            to=$(readlink "$fd" 2>/dev/null || :);nfd=${fd##*/}
            case "$nfd:$to" in 0:/dev/null|1:/dev/null|2:/dev/null) zeros=$((zeros+1));;
                *:socket:\[*) inode=${to#socket:\[};inode=${inode%\]};case "$inode" in ''|*[!0-9]*) return 1;; esac
                    awk -v id="$inode" '$7==id && $4=="00010000" && $5=="0001" {found++} END {exit found!=1}' "$kd/net/unix" || return 1
                    socks=$((socks+1));;
                *:/proc/meminfo|*:) temporary=1;; *) return 1;; esac
        done
        if [ "$zeros:$socks:$temporary" = 3:1:0 ]; then return 0; fi
        usleep 5000; tries=$((tries-1))
    done
    return 1
}
reopen_resource() {
    point=$1;read -r before unused </proc/uptime
    state=absent;rss=0;pss=0;keeper_pid
    if [ "$kpid" -gt 0 ]; then
        keeper_fds; state=held
        rss=$(awk '$1=="VmRSS:" {print $2}' /proc/$kpid/status)
        if [ -r /proc/$kpid/smaps_rollup ]; then mp=/proc/$kpid/smaps_rollup; else mp=/proc/$kpid/smaps; fi
        pss=$(awk '$1=="Pss:" {v+=$2;found=1} END{if(found)print v+0;else print -1}' "$mp" 2>/dev/null || printf '%s\n' -1)
        case "$rss" in ''|*[!0-9]*) return 1;; esac
        if [ "$point" = between ]; then printf 'unix-listener-only\n' >"$GROUP/holder-fds"; fi
        reopen_proc "$kpid"; [ "$r_birth:$r_session" = "$kbirth:$ksid" ]
    fi
    read -r after unused </proc/uptime
    printf '%s %s %s %s %s %s %s %s %s\n' "$point" "$state" "$kpid" "$kbirth" "$ksid" "$rss" "$pss" "$kticks" "$after" >>"$GROUP/keeper.tsv"
    printf '%s %s %s\n' "$point" "$before" "$after" >>"$GROUP/resource-intervals.tsv"
    awk -v p="$point" '$1=="MemAvailable:" {print p,$2}' /proc/meminfo >>"$GROUP/headroom.tsv"
}
gui_identity() {
    n=75
    while [ "$n" -gt 0 ]; do
        p=$(cat "$LOCK/application-pid" 2>/dev/null || :)
        if is_app "$p" "$variant" 2>/dev/null && reopen_proc "$p"; then
            printf '%s %s %s\n' "$p" "$r_birth" "$r_session" >"$OUT/gui-owner"; return 0
        fi
        usleep 100000; n=$((n-1))
    done
    return 1
}
# Failures never leave a helper alive beyond its own finite lifetime. Avoid
# signalling any unowned, shared or ambiguously identified process.
wait_reopen_helpers() {
    owner
    for variant in baseline candidate; do
        n=205; keeper_pid
        while [ "$kpid" -gt 0 ] && [ "$n" -gt 0 ]; do usleep 250000; n=$((n-1));keeper_pid;done
        [ "$kpid" = 0 ] || return 1
    done
}
run_case() {
    pair=$1;variant=${pair%%-*};GROUP=$STAGE/out/$pair
    case "$pair" in baseline-warmup|candidate-warmup|baseline-qualify|candidate-qualify|baseline-[0-9][0-9]|candidate-[0-9][0-9]) ;; *) return 1;; esac
    owner;stable_entry;native_wait
    [ ! -e "$GROUP" ]; [ ! -L "$GROUP" ];mkdir -m 700 "$GROUP"
    role_sha_init;keeper_pid;[ "$kpid" = 0 ]
    single_visit "$pair-prime"
    reopen_resource between
    if [ "$scenario" = ttl ] && [ "$pair" = candidate-qualify ]; then
        [ "$kpid" -gt 0 ];read -r ttl_start unused </proc/uptime
        # Observe natural eviction; never hold while waiting for an external action.
        i=205;while [ "$kpid" -gt 0 ] && [ "$i" -gt 0 ]; do usleep 250000;i=$((i-1));keeper_pid;done
        [ "$kpid" = 0 ];read -r ttl_end unused </proc/uptime
        printf '%s %s\n' "$ttl_start" "$ttl_end" >"$GROUP/ttl.tsv"
    fi
    single_visit "$pair-second"
    reopen_resource after_second
    [ "$kpid" = 0 ];stable_entry;native_wait;printf 'ok\n' >"$GROUP/case-ok"
}
role_sha_init() {
    ROLE_SHA=$(printf 'aidoku-resident-keeper\000--resident-keeper\000' | sha256sum | cut -d ' ' -f 1)
}
