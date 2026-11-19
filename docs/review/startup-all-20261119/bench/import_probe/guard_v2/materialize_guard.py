"""Produce a new guarded run family without editing or migrating any version-one batch."""

import argparse
import ast
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

OLD=Path('/root/aidoku-all-bench-20261119/import_probe/tools')
DEST=OLD.parent.parent/'import_probe_r2'
FIXTURE_SHA='4bce2ff13a0fab6e04bceb73b1071b86681f0d36fb4ee6eb2d58c91c9039a8a0'
PIN={'run_bench.py':'628bbc4c0c7ef8e122d6ca1d6c406168e79e387c1f4d8c245f41c0dc0c154d27','bench_device.sh':'deb5858e8ce9355e002724393d12713ab5732e2f18dd5770346e029671209932',
     'analyze_import.py':'99cd66e6c64f2c14aa220c581fbb16182db3a351d67db696b044f3f6c916d1ba','attest.py':'41cbed24497802875621cf5f6662b8eeda9eab10e8e1fb4f04ad41241498e001',
     'fixture.py':'01414496efd2b5fd0aa929b3bdaeb4fefafccfc9437aab67dea2e07f42856fb1'}
CBASE=Path('/root/aidoku-startup-delivery-20261118/source/src/native_catalog_entry')
CSHA={'sha256.c':'ac1db5c5f958f9b441c162ad00d2def76ea0bd7137e15df852420ba1843ea72e','sha256.h':'177aa348df771e92dc22a03b0a78d0d3926bfb96cbfd304b42ac58844e56a27f'}
ZIG='/root/weread-build/tools/zig-x86_64-linux-0.15.2/zig'
HERE=Path(__file__).resolve().parent
def sha(b):return hashlib.sha256(b).hexdigest()


def once(src,old,new):
    if src.count(old)!=1:raise ValueError('Pinned source anchor changed: '+old[:60])
    return src.replace(old,new,1)


def pinned(name):
    b=(OLD/name).read_bytes()
    if sha(b)!=PIN[name]:raise ValueError('Original source changed: '+name)
    return b.decode()


def generate():
    r,d=pinned('run_bench.py'),pinned('bench_device.sh');r=once(r,str(OLD.parent),str(DEST))
    r=once(r,"(1,6),(1,3),(4,3)","(1,3),(4,3)")
    r=once(r,"{'screen':6,'confirm':10,'pilot':1,'qualify':0}","{'screen':3,'confirm':5,'pilot':1,'qualify':0}")
    c=(DEST/'tools/cache-inventory').read_bytes(); ch=sha(c)
    needle="    add('owner', (run+'\\n').encode()); default="
    r=once(r,needle,"    cache_helper=(Path(__file__).resolve().parent/'cache-inventory').read_bytes()\n    if sha(cache_helper)!='"+ch+"':raise ValueError('Read-only cache observer changed')\n    add('cache-inventory',cache_helper,0o755)\n"+needle)
    r=once(r,"'fixture_policy':'fresh-empty-state-same-arm-reopen'","'cache_guard_version':2,'cache_inventory_sha256':'"+ch+"','fixture_policy':'fresh-empty-state-same-arm-reopen'")
    r=once(r,'def sha(content):', (HERE/'inventory_records.py').read_text()+'\n\ndef sha(content):')
    r=once(r,"                remote('activate-bridge','swap_launcher')",'''                report['inventory_attempted']=True;save(report_path,report)
                original_inventory=remote('cache-inventory-before','capture_private_cache_inventory',timeout=75)
                first=inventory_read(original_inventory)
                if inventory_legacy(first)!=legacy_member(before.read_bytes()):raise RuntimeError('Initial private inventory and original guard disagree')
                report['inventory_before_valid']=True;save(report_path,report)
                remote('activate-bridge','swap_launcher')''')
    r=once(r,"        if before!=after:raise RuntimeError('Protected snapshot changed; see private before/after')\n        report.update(entry_restored=True,installed_and_user_unchanged=True,input_roster_unchanged=True,same_boot=True,native_restored=True)",'''        equal=before==after
        safe_to_release=equal or only_legacy_file_stat_changed(before,after)
        complete=False
        if probe=='owned' and report.get('inventory_attempted'):
            end=remote('cache-inventory-after','capture_private_cache_inventory',timeout=75,required=False)
            try:
                if not report.get('inventory_before_valid') or end is None:raise ValueError('Private inventory unavailable')
                a=inventory_read(batch/'cache-inventory-before.stdout');b=inventory_read(end)
                delta=inventory_compare(a,b,batch);report['cache_inventory_result']=delta
                report['cache_inventory_after_file']=end.name
                complete=delta['exactly_equal'] and inventory_legacy(a)==legacy_member(before) and inventory_legacy(b)==legacy_member(after)
            except (OSError,ValueError):report['cache_inventory_failed']=True
        report['private_cache_verified']=equal and complete
        if not safe_to_release:raise RuntimeError('A protected state other than the artifact-stat digest changed; keep the original recovery guard')
        if not report['private_cache_verified']:
            report['case_exception']=report.get('case_exception') or 'Real cache integrity was not established; batch cannot qualify'
        report.update(entry_restored=True,installed_and_user_unchanged=equal and complete,input_roster_unchanged=True,same_boot=True,native_restored=True)''')
    r=once(r,"and report.get('native_restored'))","and report.get('native_restored') and report.get('private_cache_verified'))")
    d+='\ncapture_private_cache_inventory() {\n    owner; stable_entry; native_wait\n    same_hash "$STAGE/cache-inventory" '+ch+'\n    "$STAGE/cache-inventory"\n}\n'
    return {'run_bench.py':r,'bench_device.sh':d}


def analysis_source():
    a=once(pinned('analyze_import.py'),'import materialize','import materialize_guard as materialize\nimport inventory_records as ir')
    a=once(a,"    generated=materialize.generate();",'''    need(report.get('cache_guard_version')==2 and report.get('inventory_before_valid') is True and report.get('private_cache_verified') is True,'detailed_cache_guard_failed')
    af=report.get('cache_inventory_after_file','');need(isinstance(af,str) and re.fullmatch('cache-inventory-after(?:-[a-f0-9]{6})?[.]stdout',af),'unknown_private_inventory')
    inventory_a=ir.inventory_read(directory/'cache-inventory-before.stdout');inventory_b=ir.inventory_read(directory/af)
    need(ir.inventory_compare(inventory_a,inventory_b)['exactly_equal'] and ir.inventory_legacy(inventory_a)==ir.legacy_member((directory/'before.stdout').read_bytes()) and ir.inventory_legacy(inventory_b)==ir.legacy_member((directory/'after.stdout').read_bytes()),'private_inventory_or_legacy_disagrees')
    generated=materialize.generate();''')
    a=once(a,"        generated_fixture=attest.fixture.bundle()", "        need(sha(artifact('cache-inventory'))==plan['cache_inventory_sha256']==sha((materialize.DEST/'tools/cache-inventory').read_bytes()),'wrong_private_observer')\n        generated_fixture=attest.fixture.bundle()")
    return a


def main():
    if sys.platform!='linux':raise SystemExit('Run only on test-env')
    o=DEST/'tools';o.mkdir(parents=True,exist_ok=True)
    if not all(sha((CBASE/k).read_bytes())==v for k,v in CSHA.items()):raise ValueError('Pinned SHA-256 support changed')
    arm=o/'cache-inventory'; source=HERE/'cache_inventory.c'
    # Static musl; no firmware API, secret or private data leaves the guarded operator transport.
    with tempfile.TemporaryDirectory(prefix='private-inventory-build-') as tmp:
        candidate=Path(tmp)/'cache-inventory'
        subprocess.run([ZIG,'cc','-target','arm-linux-musleabihf','-mcpu=cortex_a8','-static','-s','-O2','-fstack-protector-strong','-std=c11','-Wall','-Wextra','-Werror','-I'+str(CBASE),str(source),str(CBASE/'sha256.c'),'-o',str(candidate)],check=True)
        if arm.exists() and sha(arm.read_bytes())!=sha(candidate.read_bytes()):raise ValueError('Existing observer differs; freeze another tool version')
        if not arm.exists():shutil.copyfile(candidate,arm);arm.chmod(0o755)
    files=generate();files['analyze_import.py']=analysis_source()
    for f in ('fixture.py','attest.py'):files[f]=pinned(f)
    for n,text in files.items():
        if n.endswith('.py'):ast.parse(text)
        else:subprocess.run(['sh','-n'],input=text.encode(),check=True)
        dest=o/n
        if dest.exists() and dest.read_text()!=text:raise ValueError('Use a new tools version; existing derivative differs: '+n)
        dest.write_text(text);print(n,sha(text.encode()))
    for n in ('inventory_records.py','materialize_guard.py','cache_inventory.c'):
        if HERE!=o:shutil.copyfile(HERE/n,o/n)
    sys.path.insert(0,str(o));import fixture
    for n,raw in fixture.bundle().items():f=o/'fixture_payload'/n;f.parent.mkdir(exist_ok=True,parents=True);f.write_bytes(raw)
    print('observer',sha(arm.read_bytes()))


if __name__=='__main__':main()
