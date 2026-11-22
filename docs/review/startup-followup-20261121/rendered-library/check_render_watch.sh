#!/bin/sh
set -eu

if [ "$#" -ne 1 ] || [ -e "$1" ] || [ -L "$1" ]; then
    echo 'Usage: check_render_watch.sh NEW_ARM_OUTPUT' >&2
    exit 2
fi

directory=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
scratch=$(mktemp -d)
trap 'rm -rf -- "$scratch"' EXIT HUP INT TERM
cd "$directory"

cc -O2 -Wall -Wextra -Werror -o "$scratch/unit" test_render_watch.c
cc -O2 -Wall -Wextra -Werror -o "$scratch/replay" replay.c
zig cc -target arm-linux-musleabihf -O2 -Wall -Wextra -Werror -s -static -o "$scratch/watch" render_watch.c
zig cc -target arm-linux-musleabihf -O2 -Wall -Wextra -Werror -static -o "$scratch/unit-arm" test_render_watch.c
"$scratch/unit"
qemu-arm -cpu cortex-a7 "$scratch/unit-arm"
RENDER_REPLAY="$scratch/replay" RENDER_UNIT="$scratch/unit" python3 -B test_render_watch.py -v
cp -- "$scratch/watch" "$1"
