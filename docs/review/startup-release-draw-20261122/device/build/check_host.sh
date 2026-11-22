#!/bin/sh
set -eu
work=${1:?Supply the private runner project}
scratch=$work/probe/host
mkdir -p "$scratch"
libc_source=$(find "$CARGO_HOME/registry/src" -maxdepth 3 -path '*/libc-0.2.191/src/lib.rs' -print)
if [ -z "$libc_source" ]; then
    libc_source=$(find "$CARGO_HOME/registry/src" -maxdepth 4 -path '*/libc-0.2.191/src/lib.rs' -print)
fi
test -f "$libc_source"
rustc --edition=2021 --crate-name libc --crate-type rlib -Awarnings -C opt-level=1 "$libc_source" -o "$scratch/liblibc.rlib"
rustc --edition=2024 --test --extern libc="$scratch/liblibc.rlib" -C opt-level=2 \
    "$work/source/vendor/slint-backend-kindle/src/draw_diagnostics.rs" -o "$scratch/thread-tests"
"$scratch/thread-tests" --test-threads=1
