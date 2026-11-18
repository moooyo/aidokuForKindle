"""Exercise the complete installed-entry script on synthetic Linux state."""

import hashlib
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest


HERE = pathlib.Path(__file__).resolve().parent
MOCK = r'''#!/usr/bin/env python3
import os, sys
from pathlib import Path
root = Path(os.environ['SMOKE_FIXTURE'])
lock = root/'lock'; proc = root/'proc'; log = root/'media/aidoku/data/logs/launcher.log'
app = root/'media/aidoku/app/aidoku'; mode = (root/'mode').read_text()
name = Path(sys.argv[0]).name; args = sys.argv[1:]
def write(p, val): p.write_text(str(val)+'\n')
def stat(pid, ppid, born):
    d=proc/str(pid); d.mkdir(exist_ok=True)
    (d/'stat').write_text(f'{pid} (a ) b) S {ppid} '+'0 '*17+str(born)+'\n')
def event(val):
    with (root/'actions').open('a') as f: print(val,file=f)
def append(val):
    with log.open('a') as f: print(val,file=f)
if name=='lipc-get-prop':
    print(('active' if not (lock/'phase').exists() or (lock/'phase').read_text().strip()!='parked' else 'screenSaver')
          if 'state' in args else ('script.shell_integration.launcher' if lock.exists() else 'native-home'))
elif name=='status': print('statusbar start/running')
elif name=='pidof':
    if args==['awesome']: print(700)
    elif lock.exists(): print(900)
    else: sys.exit(1)
elif name=='sqlite3':
    assert '-readonly' in args
    sql=args[-1]
    if 'p_lastAccess' in sql: print((root/'access').read_text().strip())
    elif 'count(*)' in sql: print(2 if mode=='duplicate-catalog' else 1)
    else: print('fixture-identity|text/x-shellscript|thumb|123|500')
elif name=='lipc-set-prop':
    action=args[-2]; event(action)
    if action=='start':
        assert args[-1]=='file://'+str(root/'media/documents/Aidoku.sh')
        lock.mkdir(); nonce='1'*32
        for n,v in [('application-pid',900),('pid',800),('runner-command',900),('nonce',nonce),('generation','1:'+nonce),('phase','active')]: write(lock/n,v)
        stat(800,1,1); stat(850,800,2); stat(900,850,3)
        e=proc/'900/exe'
        if not e.is_symlink(): e.symlink_to(app)
        write(root/'access',int((root/'access').read_text())+int(mode!='no-recent'))
        append('aidoku: Launcher worker started (pid=800).')
        append('aidoku: native window ready; profile=scribe, logical=930x1240, scale=2')
    elif action=='powerButton': write(lock/'phase','parked')
    elif action=='wakeUp':
        assert (lock/'phase').read_text().strip()=='parked'
        write(lock/'phase','active')
        if mode=='reused-pid': stat(850,800,4)
elif name=='smoke-signal':
    assert args==['TERM','900']; event('owned-term')
    append('aidoku: Catalog notification completed.')
    if mode=='duplicate-notice': append('aidoku: Catalog notification completed.')
    if mode=='fallback': append('aidoku: Catalog notification unavailable; using standard reindex recovery.')
    append('aidoku: Launcher worker exited with status '+('1' if mode=='bad-exit' else '0')+'.')
    for p in lock.iterdir(): p.unlink()
    lock.rmdir()
elif name=='sleep':
    if mode=='log-rotated' and args==['12']:
        log.rename(log.with_name('previous.log')); log.write_text('')
elif name=='usleep': pass
else: raise RuntimeError(name)
'''
CAPTURE = r'''#!/usr/bin/env python3
import os, sys
from pathlib import Path
root = Path(os.environ['SMOKE_FIXTURE']); mode = (root/'mode').read_text()
sys.stdout.buffer.write(b'P6\n# bpp=8 rotation=3\n1860 2480\n255\n')
if mode!='short-pixels':
    after_wake='wakeUp' in (root/'actions').read_text().split('start\n')[-1]
    left = b'S' if mode=='status-only' else b'X'
    right_change = (mode=='right-changed' or mode=='right-wake' and after_wake)
    for y in range(72):
        right=bytearray(bytes((245,251,253))*930)
        if mode not in ('flat-colored','flat-white') and y==71:
            right[-3 if not right_change else -6]=9
        if mode=='flat-white': right[:]=b'\xff'*len(right)
        sys.stdout.buffer.write(left*(930*3)+right)
    sys.stdout.buffer.write((b'X' if mode=='changed-pixels' else b'Y')*(13838400-401760))
'''


class InstalledSmoke(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.media = self.root/'media'
        self.stage = self.media/'.aidoku-next-example'
        self.stage.mkdir(parents=True)
        (self.stage/'owner').write_text('example\n')
        self.temp = self.root/'temp'; self.temp.mkdir()
        (self.media/'aidoku/data/logs').mkdir(parents=True)
        (self.media/'aidoku/data/logs/launcher.log').write_text('previous session\n')
        (self.media/'aidoku/app').mkdir(); (self.media/'aidoku/app/aidoku').touch()
        proc = self.root/'proc'; (proc/'700').mkdir(parents=True)
        (proc/'700/status').write_text('State:\tS (sleeping)\n')
        (self.root/'access').write_text('125'); (self.root/'actions').touch()
        self.mode('ok')
        bindir = self.root/'bin'; bindir.mkdir()
        mock = bindir/'stub'; mock.write_text(MOCK); mock.chmod(0o755)
        for name in ['lipc-get-prop','lipc-set-prop','status','pidof','sqlite3','smoke-signal']:
            (bindir/name).symlink_to(mock)
        for name in ['usleep','sleep']:
            if name=='usleep': (bindir/name).symlink_to('/bin/true')
            else: (bindir/name).symlink_to(mock)
        self.env = os.environ | {'SMOKE_FIXTURE':str(self.root),'PATH':str(bindir)+':'+os.environ['PATH']}
        cap=self.stage/'capture'; cap.write_text(CAPTURE); cap.chmod(0o755)
        src=(HERE/'smoke.sh').read_text()
        for old,new in {
            '/mnt/us':str(self.media), '/var/tmp/aidoku-next-smoke-':str(self.temp/'smoke-'),
            '/tmp/aidoku-launcher.lock':str(self.root/'lock'), '/proc/':str(proc)+'/',
            'kill "-$1" "$a"':'smoke-signal "$1" "$a"',
            'd728222e1363405564ae55f6ee32a850ac918cf6401cc013e0a84b50adf44c90':hashlib.sha256(cap.read_bytes()).hexdigest(),
            'wait_native 200':'wait_native 3', 'tries=180':'tries=3',
        }.items():
            self.assertIn(old,src); src=src.replace(old,new)
        self.script=self.stage/'smoke.sh'; self.script.write_text(src)

    def mode(self,value): (self.root/'mode').write_text(value)

    def run_case(self, op, ok, run='example'):
        result=subprocess.run(['sh',str(self.script),op,run],cwd=self.stage,env=self.env,
                              capture_output=True,text=True,timeout=12)
        self.assertEqual(result.returncode==0,ok,(op,result.stdout,result.stderr))
        if ok: self.assertEqual(result.stdout.strip(),{
            'before':'BASELINE_SMOKE_OK','after':'CANDIDATE_SMOKE_OK','recover':'RECOVERED'}[op])
        return result

    def test_exact_status_mask_and_same_pid_wake(self):
        self.run_case('before',True); self.mode('status-only'); self.run_case('after',True)
        self.assertEqual((self.stage/'before.sha').read_text(),(self.stage/'wake.sha').read_text())
        region=(self.stage/'before.right.tsv').read_text().strip().split('\t')
        self.assertEqual(region[:-1],['930','0','930','72','200880','9','245','251','251','253','253'])
        roi=bytearray(bytes((245,251,253))*930*72);roi[-3]=9
        self.assertEqual(region[-1],hashlib.sha256(roi.hex().encode('ascii')).hexdigest())
        self.assertEqual((self.stage/'before.right.tsv').read_bytes(),(self.stage/'wake.right.tsv').read_bytes())
        self.assertFalse(any(self.temp.iterdir()))
        self.assertEqual((self.root/'actions').read_text().count('owned-term'),2)

    def test_constant_rgb_triplet_is_not_an_observable_toolbar(self):
        self.mode('flat-colored'); self.run_case('before',False)
        region=(self.stage/'before.right.tsv').read_text().strip().split('\t')
        self.assertEqual(region[5:11],['245','245','251','251','253','253'])
        self.assertFalse((self.stage/'before.sha').exists());self.run_case('recover',True)

    def test_all_white_is_not_an_observable_toolbar(self):
        self.mode('flat-white'); self.run_case('before',False);self.run_case('recover',True)

    def test_single_different_toolbar_pixel_fails_despite_same_extrema(self):
        self.run_case('before',True);self.mode('right-changed');self.run_case('after',False)
        old=(self.stage/'before.right.tsv').read_text().strip().split('\t')
        new=(self.stage/'after.right.tsv').read_text().strip().split('\t')
        self.assertEqual(old[:-1],new[:-1]);self.assertNotEqual(old[-1],new[-1]);self.run_case('recover',True)

    def test_changed_toolbar_only_on_wake_rejects_installed_candidate(self):
        self.run_case('before',True);self.mode('right-wake');self.run_case('after',False)
        self.assertFalse((self.stage/'after.sha').exists());self.run_case('recover',True)

    def test_existing_raw_samples_match_independent_pillow_crop(self):
        # Compare on test-env against the previous unchanged-device recordings.
        # Never copy images, private fingerprints or book metadata to reports.
        from PIL import Image
        prior=pathlib.Path('/root/aidoku-next-coldbench-20261118/private/aidoku-next-cold-pixels-141a0865/outputs/out')
        installed=pathlib.Path('/root/aidoku-startup-delivery-20261118/private/installed-smoke-5d30361d/outputs/first-open')
        samples=[prior/(side+'-qualify')/'native-settled.ppm' for side in ('baseline','candidate')]
        samples.extend(installed/n for n in ('installed-library.ppm','installed-after-wake.ppm'))
        if not all(p.is_file() for p in samples): self.skipTest('Historical private device evidence not present')
        library=self.script.read_text().split('# Entry point.')[0]
        audit=self.stage/'toolbar-audit';audit.write_text(library+'''\nRUN=example; PIXTMP=$1; right_toolbar "$2" "$3"\n''')
        tmp=self.root/'reading';tmp.mkdir();(tmp/'owner').write_text('example\n')
        for i,path in enumerate(samples):
            sidecar=self.stage/('sample-'+str(i)+'.tsv')
            subprocess.run(['sh',str(audit),str(tmp),str(path),str(sidecar)],cwd=self.stage,env=self.env,check=True,timeout=10)
            with Image.open(path) as image:
                self.assertEqual((image.format,image.size),('PPM',(1860,2480)))
                ref=image.crop((930,0,1860,72)).convert('RGB')
                ranges=[str(value) for pair in ref.getextrema() for value in pair]
                observed=any(lo!=hi for lo,hi in ref.getextrema())
                fingerprint=hashlib.sha256(ref.tobytes().hex().encode('ascii')).hexdigest()
            self.assertTrue(observed)
            fields=sidecar.read_text().strip().split('\t')
            self.assertEqual(fields[:5],['930','0','930','72','200880'])
            self.assertTrue(fields[5:11]==ranges and fields[11]==fingerprint,'Private crop differs')

    def test_changed_picture_rejects_and_owned_recovery_restores(self):
        self.run_case('before',True); self.mode('changed-pixels'); self.run_case('after',False)
        self.assertFalse((self.stage/'after.sha').exists()); self.run_case('recover',True)
        self.assertFalse((self.root/'lock').exists()); self.assertFalse(any(self.temp.iterdir()))

    def test_reused_runner_identity_is_never_signalled_or_woken(self):
        self.run_case('before',True); self.mode('reused-pid'); self.run_case('after',False)
        actions=(self.root/'actions').read_text(); self.run_case('recover',False)
        self.assertEqual(actions,(self.root/'actions').read_text())
        self.assertTrue((self.root/'lock').exists())

    def test_short_pixel_buffer_rejects(self):
        self.mode('short-pixels'); self.run_case('before',False); self.run_case('recover',True)

    def test_rotated_launcher_log_rejects(self):
        self.mode('log-rotated'); self.run_case('before',False); self.run_case('recover',True)

    def test_zero_exit_and_single_notification_mandatory(self):
        self.mode('duplicate-notice'); self.run_case('before',False)
        self.assertFalse((self.stage/'before.sha').exists()); self.run_case('recover',True)

    def test_catalog_fallback_is_not_counted_as_success(self):
        self.mode('fallback'); self.run_case('before',False); self.run_case('recover',True)

    def test_recent_access_must_advance(self):
        self.mode('no-recent'); self.run_case('before',False); self.run_case('recover',True)

    def test_preflight_has_no_side_effects_for_foreign_stage_or_catalog(self):
        self.run_case('before',False,run='other'); self.mode('duplicate-catalog'); self.run_case('before',False)
        self.assertEqual((self.root/'actions').read_text(),'')


if __name__=='__main__':
    if not sys.platform.startswith('linux'): raise SystemExit('Run only on test-env')
    unittest.main()
