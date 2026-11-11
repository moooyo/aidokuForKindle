#!/bin/sh
# Name: Aidoku
# DontUseFBInk
set -eu
umask 077
LOG_DIR=/mnt/us/aidoku/data/logs
mkdir -p "$LOG_DIR"
exec </dev/null >>"$LOG_DIR/kpm-entry.log" 2>&1
exec /var/local/kmc/bin/kpm launch aidoku --asap
