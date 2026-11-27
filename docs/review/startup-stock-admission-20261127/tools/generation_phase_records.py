#!/usr/bin/env python3
"""Adapt the stock sampler's typed evidence, without staging a device runner."""

import re
from pathlib import Path
import runpy


HERE = Path(__file__).resolve().parent


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def stock_export_awk(tools):
    """Filter an already bound log delta; no general log or catalog data escapes."""
    outer = ' '.join(tools['OUTER']).encode('ascii')
    generation = ' '.join(tools['PARENT']+tools['CHILD']).encode('ascii')
    return br'''
BEGIN {
    split("@OUTER@", outer); count=split("@GENERATION@", gen);
    for(i=1;i<=count;i++) allowed[gen[i]]=1;
}
/^aidoku: launcher-diag/ {
    if (++marks>10 || length($5)>17 || $4!=outer[marks] ||
        $0 !~ /^aidoku: launcher-diag v1 [a-z_]+ [0-9]+\.[0-9][0-9]$/) {bad=1; exit 1}
    out[++records]="LAUNCH_PHASE " $4 " " $5;
}
/^aidoku: generation-diag/ {
    if (++genmarks>count || !($4 in allowed) || ++used[$4]!=1 || length($5)>17 ||
        length($6)>10 || length($7)>10 ||
        $0 !~ /^aidoku: generation-diag v1 [a-z_]+ [0-9]+\.[0-9][0-9] [0-9]+ [0-9]+$/) {bad=1; exit 1}
    out[++records]="GENERATION_PHASE " $4 " " $5 " " $6 " " $7;
}
/^aidoku: Launcher worker started/ {
    if (++starts!=1 || $0!="aidoku: Launcher worker started (pid=" worker ").") {bad=1; exit 1}
    out[++records]="LAUNCH_BIND " worker;
}
/^aidoku: Launcher worker exited/ {
    if (++ends!=1 || $0!="aidoku: Launcher worker exited with status 0.") {bad=1; exit 1}
    out[++records]="LAUNCH_EXIT 0";
}
END {
    if (bad || marks!=10 || genmarks!=count || starts!=1 || ends!=1) exit 1;
    for(i=1;i<=records;i++) print out[i];
}
'''.replace(b'@OUTER@', outer).replace(b'@GENERATION@', generation)


OUTER = re.compile(rb'LAUNCH_PHASE ([a-z_]{1,36}) ([0-9]{1,14}\.[0-9]{2})')
GEN = re.compile(rb'GENERATION_PHASE ([a-z_]{1,36}) ([0-9]{1,14}\.[0-9]{2}) ([0-9]{1,10}) ([0-9]{1,10})')
BIND = re.compile(rb'LAUNCH_BIND ([1-9][0-9]{0,9})')
WORKER = re.compile(rb'STOCK_OBSERVER_BIND control-01 ([1-9][0-9]{0,9})')
EXIT = b'LAUNCH_EXIT 0'


def attach_generation(raw, rows, historical_phase_parser):
    """Require both the original birth/outer-phase join and the new partition."""
    require(isinstance(raw, bytes) and len(raw) <= 7*1024**2 and raw.endswith(b'\n'), 'Invalid private stock reply')
    relevant = [line for line in raw.splitlines()
                if line.startswith((b'STOCK_SAMPLE', b'STOCK_BIRTH', b'STOCK_OBSERVER_', b'LAUNCH_', b'GENERATION_'))]
    generation = [line for line in relevant if line.startswith(b'GENERATION_PHASE')]
    if not rows:
        require(not relevant, 'Unexpected timing in a no-visit response')
        return historical_phase_parser(raw, [])
    tools = runpy.run_path(str(HERE/'generation_probe.py'))
    require(len(rows) == 1 and rows[0]['name'] == 'control-01'
            and len(generation) == len(tools['PARENT'])+len(tools['CHILD'])
            and len(relevant) == 15+len(generation), 'Wrong number of bound stage rows')
    r = rows[0]
    sample = ('STOCK_SAMPLE control-01 '+' '.join(str(r[k]) for k in ('release_before_us','release_after_us',
                  'library_send_us','sequence','marker','library','grid'))).encode('ascii')
    birth = ('STOCK_BIRTH control-01 '+' '.join(str(r[k]) for k in ('clock_ticks_per_second','worker_birth_tick',
                  'gui_birth_tick'))).encode('ascii')
    known_worker = WORKER.fullmatch(relevant[2])
    require(relevant[0] == sample and relevant[1] == birth and known_worker is not None
            and relevant[-1] == EXIT, 'Stock visit boundary is not unique')
    cooked = []
    worker = None
    for item in relevant[3:]:
        if match := OUTER.fullmatch(item):
            cooked.append(b'aidoku: launcher-diag v1 '+b' '.join(match.groups()))
        elif match := GEN.fullmatch(item):
            cooked.append(b'aidoku: generation-diag v1 '+b' '.join(match.groups()))
        elif match := BIND.fullmatch(item):
            require(worker is None and match[1] == known_worker[1], 'Wrong worker in typed phase stream')
            worker = int(match[1]); cooked.append(b'aidoku: Launcher worker started (pid='+match[1]+b').')
        elif item == EXIT:
            cooked.append(b'aidoku: Launcher worker exited with status 0.')
        else:
            raise ValueError('Malformed private phase row')
    stage = tools['summarize'](b'\n'.join(cooked)+b'\n', worker)
    bound = historical_phase_parser(raw, [dict(rows[0])])
    require(len(bound) == 1 and 'launcher_breakdown' in bound[0], 'Missing original worker-to-GUI gate')
    previous = bound[0]['launcher_breakdown']['adjacent_stages_us']
    # The old parser also bounds outer shell marks with real birth ticks. The
    # two conservative intervals must be compatible, not necessarily equal.
    for old, new in (('generation_setup_with_observer_in_flight', 'observer_start_to_wait_begin_us'),
                     ('remaining_observer_admission', 'observer_wait_begin_to_owned_us')):
        lo, hi = previous[old]
        lower, upper = stage[new]
        require(max(lo, lower) <= min(hi, upper), 'Inconsistent clocks across validated phase streams')
    bound[0]['generation_breakdown'] = stage
    return bound
