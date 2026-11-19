#!/bin/sh
# Read named public reports only. Supply --report NAME=PATH for other published filenames.
set -eu
tools=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
out=${1:?Provide an evidence output directory}
shift
bench=/root/aidoku-all-bench-20261119/reports
life=/root/aidoku-all-release-20261119/reports/final-defaults-20126222.json
set -- "$@" --report "defaults-lifecycle=$life"
v2life=/root/aidoku-all-release-20261119/reports/final-v2-defaults-70b9688b.json
if [ -f "$v2life" ]; then set -- "$@" --report "v2-defaults-lifecycle=$v2life"; fi
if [ -f "$tools/native-entry-live-results.json" ]; then
    set -- "$@" --report "installed-gui-entry-observation=$tools/native-entry-live-results.json"
fi
for spec in "invalid-default-pilot=$bench/joint-pilot.json" "defaults-warm=$bench/default-warm.json" "defaults-cold=$bench/default-cold.json" "all-options-functional=$bench/all-options-functional.json"; do
    if [ -f "${spec#*=}" ]; then set -- "$@" --report "$spec"; fi
done
for trial in supervisor prewarm grace layout import; do
    for phase in pilot screen confirm; do
        file=$bench/$trial-$phase.json
        if [ -f "$file" ]; then set -- "$@" --report "$trial-$phase=$file"; fi
    done
done
for name in v2-default-w v2-default-c v2-layout v2-prewarm v2-grace v2-layout-screen v2-prewarm-screen v2-grace-screen v2-layout-confirm v2-prewarm-confirm v2-grace-confirm v2-import-pilot v2-import-screen v2-import-confirm v2-all-options-functional v2-reopen-pilot v2-reopen-screen v2-reopen-confirm v2-reopen-qualify v2-keeper-pilot v2-keeper-screen v2-keeper-confirm v2-keeper-qualify; do
    file=$bench/$name.json
    if [ -f "$file" ]; then set -- "$@" --report "$name=$file"; fi
done
exec python3 -I "$tools/export.py" --out "$out" "$@"
