# Measurement closeout only. The caller holds the current journal lock and
# has checked the bound card visit, restored launcher and original installation.
stock_navigation_coordinates() (
    [ "$#" = 2 ] && number "$1" && number "$2" &&
        [ "${#1}" -le 4 ] && [ "${#2}" -le 4 ] && [ "$1" -lt 1860 ] && [ "$2" -lt 2480 ]
)

stock_saved_navigation() (
    baseline=$state/input-original
    regular "$baseline" && IFS=' ' read -r tab x y extra <"$baseline" &&
        [ "$tab" = library ] && [ -z "$extra" ] && stock_navigation_coordinates "$x" "$y" &&
        line "$baseline" "library $x $y" || exit 1
    printf 'library %s %s\n' "$x" "$y"
)

stock_navigation_view() (
    regular "$stage/input/navigation.sh" || exit 1
    # The read-only navigator needs native foreground, a unique footer and
    # clear X buttons. Its blocking tools are bounded directly, not via the shell.
    framed=$(sh "$stage/input/navigation.sh" inspect "$label" && printf '.') || exit 1
    set -- ${framed%?}
    [ "$#" = 3 ] && [ "$framed" = "$(printf '%s %s %s\n.' "$1" "$2" "$3")" ] &&
        stock_navigation_coordinates "$2" "$3" || exit 1
    case "$1" in home|library) printf '%s %s %s\n' "$1" "$2" "$3";; *) exit 1;; esac
)

stock_navigation_intent_valid() {
    absent "$state/input-restore-intent" ||
        { issued_owned_tap && { line "$state/input-restore-intent" "home $1" ||
            line "$state/input-restore-intent" "library $1"; }; }
}

stock_navigation_exact() (
    saved=$(stock_saved_navigation) && stock_navigation_intent_valid "$saved" &&
        [ "$(stock_navigation_view)" = "$saved" ]
)

stock_restore_input_ready() {
    close_scope && line "$state/armed" "$label" && stage_owned && stock_original && no_overlay
}

stock_restore_input() (
    kit=$stage/input
    stock_restore_input_ready && regular "$kit/card-input" && regular "$kit/lib/ld-linux-armhf.so.3" &&
        DISPLAY=:0 OMP_THREAD_LIMIT=1 timeout -k 1 12 "$kit/lib/ld-linux-armhf.so.3" --inhibit-cache --library-path "$kit/lib" \
            "$kit/card-input" "$@" >/dev/null
)

restore_owned_navigation() (
    saved=$(stock_saved_navigation) && stock_navigation_intent_valid "$saved" &&
        here=$(stock_navigation_view) || exit 1
    [ "$here" != "$saved" ] || exit 0
    # No card stimulus means no repair input. Once written, this intent is
    # spent even if the helper never starts or its result is lost. A later
    # owner-checked close may observe the state, but may not issue any input.
    intent="${here%% *} $saved"
    issued_owned_tap && absent "$state/input-restore-intent" && stock_restore_input_ready &&
        put "$state/input-restore-intent" "$intent" && line "$state/input-restore-intent" "$intent" || exit 1
    if [ "${here%% *}" = home ]; then
        # Recheck the live footer and use the existing guarded button pair,
        # never the open-card mode or the general-purpose navigation command.
        stock_restore_input nav-library "$stage/input/models" || exit 1
        for check in 1 2; do
            here=$(stock_navigation_view) || here=''
            [ "${here%% *}" != library ] || break
            [ "$check" != 1 ] || sleep 1
        done
        [ "${here%% *}" = library ] || exit 1
    fi
    [ "$here" != "$saved" ] || exit 0
    set -- $saved
    stock_restore_input restore-pointer "$2" "$3" || exit 1
    [ "$(stock_navigation_view)" = "$saved" ]
)
