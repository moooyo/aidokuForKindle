#!/bin/sh
set -fu
export LC_ALL=C
exec 2>/dev/null

emit() { printf 'STOCK_DIAG1 %s\n' "$1"; exit 0; }
[ "$#" -eq 1 ] && [ "${#1}" -eq 16 ] || exit 64
case "$1" in *[!0-9a-f]*) exit 64 ;; esac

reply=$(sh -s -- admit "$1" 2>&1; printf ':%s' "$?")
case "$reply" in
    'guard_admitted
:0') emit guard_passed ;;
    'refused guard_lights
:1') emit guard_lights ;;
    'refused guard_admission
:1') ;;
    *) emit guard_protocol_unknown ;;
esac

power=$(lipc-get-prop -s com.lab126.powerd state) && [ -n "$power" ] || emit power_unobserved
[ "$power" = active ] || emit power_not_active
foreground=$(lipc-get-prop -s com.lab126.appmgrd activeApp) && [ -n "$foreground" ] || emit foreground_unobserved
[ "$foreground" = com.lab126.KPPMainApp ] || emit foreground_not_stock
bar=$(status statusbar) && [ -n "$bar" ] || emit statusbar_unobserved
case "$bar" in *start/running*) emit other_native_or_protection_or_state_changed ;; *) emit statusbar_not_running ;; esac
