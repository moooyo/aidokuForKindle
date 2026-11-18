#!/bin/sh
# Read public analyzers on test-env. Additional --report arguments may name
# outputs saved under different public filenames; never read sample directories.
set -eu
tools_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
out=${1:?Pass the evidence output directory}
shift
warm=/root/aidoku-next-bench-20261118/reports
cold=/root/aidoku-next-coldbench-20261118/reports
frame=/root/aidoku-next-framebench-20261118/reports
toolbar=/root/aidoku-next-statusbench-20261118/reports
for spec in \
    "supervisor-pilot=$warm/native-pilot.json" \
    "supervisor-screen=$warm/native-screen.json" \
    "prewarm-screen=$warm/covers-screen.json" \
    "cold-screen=$cold/cold-screen.json" \
    "cold-confirm=$cold/cold-confirm.json" \
    "cold-pixels=$cold/cold-pixels.json" \
    "last-frame-screen=$frame/frame-screen.json" \
    "cold-warm-guard=$warm/cold-warm-guard.json" \
    "status-warm-guard=$toolbar/status-warm-guard.json" \
    "status-cold-screen=$toolbar/status-cold-screen.json" \
    "status-cold-confirm=$toolbar/status-cold-confirm.json" \
    "status-pixels=$toolbar/status-cold-pixels.json"
do
    case "$spec" in *=*) file=${spec#*=} ;; *) exit 2 ;; esac
    if [ -f "$file" ]; then set -- "$@" --report "$spec"; fi
done
exec python3 -I "$tools_dir/export.py" --out "$out" "$@" \
    --stopped supervisor-pilot=aidoku-next-native-pilot-689dcde0:device_unavailable \
    --stopped cold-confirm=aidoku-next-cold-confirm-576fdfaf:guard_failed
