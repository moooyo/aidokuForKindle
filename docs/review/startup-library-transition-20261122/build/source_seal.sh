#!/bin/sh
set -eu
compiler=$1
shift
case " $* " in
    *' --crate-name aidoku_kindle '*|*' --crate-name aidoku '*)
        if [ ! -f /work/reports/transition-source-ready ]; then
            printf '%s\n' 'Transition dependencies are ready; measurement sources are not sealed.' >&2
            exit 75
        fi
        ;;
esac
exec "$compiler" "$@"
