#!/bin/sh
# Development-only replacement for app/aidoku in a disposable firmware guest.
# Never package this wrapper as the product executable.
set -eu

AIDOKU_HOME=${AIDOKU_HOME:-/mnt/us/aidoku}
LOG_DIR=$AIDOKU_HOME/data/logs
NATIVE=$AIDOKU_HOME/app/aidoku.native
SCREEN=library
DURATION=15000
mkdir -p "$LOG_DIR"
if [ -r "$LOG_DIR/test-screen" ]; then
    SCREEN=$(sed -n '1{s/\r$//;p;}' "$LOG_DIR/test-screen")
fi
case "$SCREEN" in
    library|catalog|browse|search|history|settings|detail|reader|sources|downloads) ;;
    *) printf '%s\n' 'Unknown native test screen.' >&2; exit 1 ;;
esac
if [ -r "$LOG_DIR/test-duration-ms" ]; then
    DURATION=$(sed -n '1{s/\r$//;p;}' "$LOG_DIR/test-duration-ms")
fi
case "$DURATION" in
    ''|*[!0-9]*) printf '%s\n' 'A numeric native test duration is required.' >&2; exit 1 ;;
esac
[ "$DURATION" -ge 1200 ] && [ "$DURATION" -le 180000 ] || exit 1
[ -x "$NATIVE" ] || { printf '%s\n' 'The immutable native test executable is missing.' >&2; exit 1; }
exec "$NATIVE" "$@" --demo --offline --screen "$SCREEN" \
    --capture "$LOG_DIR/native-$SCREEN.png" --exit-after-ms "$DURATION"
