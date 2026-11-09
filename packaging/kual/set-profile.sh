#!/bin/sh
set -eu
case "${1:-}" in
    pw5|pw6|scribe|scribe2|scribe3|scribe3-nofrontlight) ;;
    *) printf '%s\n' 'Unknown Aidoku device profile.' >&2; exit 1 ;;
esac
mkdir -p /mnt/us/aidoku/data
printf '%s\n' "$1" >/mnt/us/aidoku/data/device-profile.tmp
mv /mnt/us/aidoku/data/device-profile.tmp /mnt/us/aidoku/data/device-profile
