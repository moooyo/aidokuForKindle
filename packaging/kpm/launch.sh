#!/bin/sh
# Name: Aidoku
# DontUseFBInk
set -eu
umask 077
LOG_DIR=/mnt/us/aidoku/data/logs
mkdir -p "$LOG_DIR"
exec </dev/null >>"$LOG_DIR/kpm-launch.log" 2>&1
printf '%s\n' 'aidoku-kpm: Launch request received; the production worker records its final result in launcher.log.'
exec sh /mnt/us/aidoku/launch.sh
