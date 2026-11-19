#!/usr/bin/env python3
"""Materialize separate warm and cold device runners from reviewed sources."""

import argparse
import hashlib
from pathlib import Path
import subprocess
import sys

ROOT=Path('/root/aidoku-all-bench-20261119')
OLD_APP='62de2771891b5bf8f720572c4544a3a732d619fa95131111cb00bccb6e0bc612'
APP='9d92dc4cd01005a6ba2f48fe61786e5df5570c43feb5709fe3159bb164807d0f'
INPUTS={
    'warm': (Path('/root/aidoku-next-bench-20261118/tools'),'85d60c15b871375477cd14ef2020f0cc6adb7119ef618a010bec6ed934d97f7c','3126a4c5ceffb3836218f9f9ccb9dab2b111c67c56a0d380c98a8167379da14d'),
    'cold': (Path('/root/aidoku-next-coldbench-20261118/tools'),'0d56c4d2ad634a165f6184b12aff9a3fc27dc5f5662293052270f4c910561f26','49d597c9aae6348244b12e2b9a3345c2e60cb227fbab58ee3b7a3fe07f87c057'),
}
HERE=Path(__file__).resolve().parent
POLICY='''
OPTION_VALUES = {
    'prewarm': {'0','2'}, 'last_cover_grace': {'off','on'},
    'first_layout': {'baseline','optimized'}, 'import_thumbnail': {'off','idle'}, 'resident': {'off','45'},
}
def case_options(ns, side):
    values = {}
    for pair in getattr(ns,side+'_option'):
        key, sep, value = pair.partition('=')
        if sep!='=' or key in values or value not in OPTION_VALUES.get(key,set()):
            raise ValueError('Unexpected or duplicate experimental setting')
        values[key]=value
    return values, ''.join(f'{k}={v}\\n' for k,v in sorted(values.items())).encode('ascii')
'''


def sha(b):return hashlib.sha256(b).hexdigest()


def one(src,old,new):
    if src.count(old)!=1:raise ValueError('Pinned source anchor changed: '+old[:70])
    return src.replace(old,new,1)


def frozen(kind):
    d,r,s=INPUTS[kind];raw=(d/'run_bench.py').read_bytes(),(d/'bench_device.sh').read_bytes()
    if (sha(raw[0]),sha(raw[1]))!=(r,s):raise ValueError('Historical tool identity changed')
    return tuple(v.decode() for v in raw)


def generate(kind):
    root=ROOT/('base-'+kind); r,dev=frozen(kind);r=one(r,OLD_APP,APP);dev=one(dev,OLD_APP,APP)
    r=one(r,"ROOT = Path('"+str(INPUTS[kind][0].parent)+"')", "ROOT = Path('"+str(root)+"')")
    r=r.replace('aidoku-next-{ns.name}', 'aidoku-all-{ns.name}').replace('aidoku-next-[a-z]', 'aidoku-all-[a-z]')
    dev=one(dev,'/mnt/us/.aidoku-next-*','/mnt/us/.aidoku-all-*')
    # A bridge routes only this document's normal, qualified Library handoff.
    r=one(r,'stage={stage}\n[ "$(cat', '''[ "${{AIDOKU_CATALOG_NOTIFY_HELPER:-}}" = /var/local/kindle-catalog-entry/v1-2db100d0bcb60a76571cd8de96bed7ea6c016e6ab73e388a885f692955cacd86/aidoku-catalog-client ] && [ "${{AIDOKU_CATALOG_NOTIFY_SHA256:-}}" = f46a0225d5a265f5f9749029fc115060576508ee5caadf25becb661738b88161 ] || exit 69
stage={stage}
[ "$(cat''')
    r=one(r,'def arm(binary):',POLICY+'\n\ndef arm(binary):')
    r=one(r,"    for v in TOKENS:\n        q.add_argument", "    q.add_argument('--trial',choices=('supervisor','prewarm','grace','layout','combined','import','reopen'),required=True)\n    for v in TOKENS:\n        q.add_argument('--'+v+'-option',action='append',default=[])\n        q.add_argument")
    r=one(r,'        args, env = cli_args(ns, v)', "        options,option_bytes=case_options(ns,v)\n        add(v+'/bench.options',option_bytes);add(v+'/bench.options.sha256',(sha(option_bytes)+'\\n').encode())\n        args, env = cli_args(ns, v)")
    r=one(r,"'args':args,'env':env,'extra_sha256':extras", "'args':args,'env':env,'extra_sha256':extras,'startup_options':options,'options_sha256':sha(option_bytes)")
    extra="'trial':ns.trial,'baseline_reference_sha256':APP_SHA,"
    if kind=='cold':
        left=r.index("    if (ns.primary != 'covers'");right=r.index('    variants = {};',left)
        r=r[:left]+r[right:]
        r=one(r,"'cache_regime':'empty-cache-v1','fixture_policy':'per-case-empty-cache-v1'",extra+"'cache_regime':'empty-cache-v2','fixture_policy':'per-case-empty-cache-v2'")
    else:
        r=one(r,"'cases':cases,'variants':variants",extra+"'cache_regime':'warm-cache-v2','fixture_policy':'sealed-private-cache-v2','cases':cases,'variants':variants")
        r=one(r,'AIDOKU_RESEARCH_DATA="$stage/data"','AIDOKU_RESEARCH_DATA="$stage/cases/$case_id/data"')
    r=one(r,"'original_launcher_sha256':LAUNCH_SHA", "'original_launcher_sha256':LAUNCH_SHA,'fixture_case_seed':'independent'")
    r=one(r,"    add('bridge.sh',bridge(run,cases,flags),0o755)", "    if ns.trial=='combined' and (variants['baseline']['application_sha256']!=APP_SHA or any(v['args'] or v['env'] or v['startup_options'] for v in variants.values())): raise ValueError('Combined requires installed control and both production defaults')\n    add('bridge.sh',bridge(run,cases,flags),0o755)")
    # New trial files, not anonymous process environment, bind Rust options.
    r=one(r,"    if parts and (len(parts) != 2 or parts[0] != '--startup-cached-covers' or parts[1] not in {'0', '1', '2'}):", "    if parts:")
    dev=one(dev,'prepare_device() {\n', (HERE/'fixture.sh').read_text()+'\nprepare_device() {\n')
    if kind=='warm':
        dev=one(dev,'    cp -rp "$f" "$STAGE/data"', '    mkdir "$STAGE/cases"')
        dev=one(dev, '    savedlog=$STAGE/data/logs/launcher.log;', '    prepare_case_warm "$C"\n    apply_case_options "$STAGE/cases/$C/data" "$variant"\n    savedlog=$STAGE/cases/$C/data/logs/launcher.log;')
        dev=one(dev, 'is_app "$p" "$variant"; "$STAGE/capture"','is_app "$p" "$variant"; mark native_capture_started; "$STAGE/capture"')
        # Snapshot the real index bytes plus artifact roster even in the warm regime.
        _,cd=frozen('cold');realcache=cd[cd.index('real_cache_snapshot() {'):cd.index('check_cold_case() {')]
        dev=one(dev,'fixture() {\n',realcache+'fixture() {\n');dev=one(dev,'    cat /proc/sys/kernel/random/boot_id','    real_cache_snapshot || return 1\n    cat /proc/sys/kernel/random/boot_id')
    else:
        dev=one(dev,'    prepare_case_cold "$C"\n', '    prepare_case_cold "$C"\n    apply_case_options "$STAGE/cases/$C/data" "$variant"\n')
    dev=one(dev,'    stable_entry; printf \'ok\\n\'', '    verify_case_options "$STAGE/cases/$C/data" "$variant"\n    stable_entry; printf \'ok\\n\'')
    return {'run_bench.py':r,'bench_device.sh':dev}


def main():
    if sys.platform!='linux':raise SystemExit('Run only on test-env')
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--output',type=Path,default=ROOT);args=ap.parse_args()
    if args.output.resolve()!=ROOT:raise SystemExit('Use the fixed research root')
    for regime in ('warm','cold'):
        target=ROOT/('base-'+regime);target.mkdir(mode=0o700,parents=True,exist_ok=True)
        for name,value in generate(regime).items():
            path=target/name
            if path.exists() and path.read_text()!=value:
                raise ValueError('Different existing runner; preserve the released copy and use a new version')
            if not path.exists():path.write_text(value)
            print(regime,name,sha(path.read_bytes()))
        subprocess.run(['sh','-n',target/'bench_device.sh'],check=True)
        subprocess.run([sys.executable,'-I','-m','py_compile',target/'run_bench.py'],check=True)


if __name__=='__main__':main()
