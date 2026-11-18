"""Linux host fixtures for the launch bridge, order, and consistency guards."""

import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('runner',HERE/'run_bench.py')
runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
sha=lambda b: hashlib.sha256(b).hexdigest()


class Contract(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup); self.base=Path(tmp.name)
        self.runid='aidoku-next-fixture-1234abcd'; self.stage=self.base/('.'+self.runid); self.stage.mkdir()
        self.app=self.base/'aidoku'; self.app.mkdir(); self.inst=self.app/'launch.sh'
        self.inst.write_text('original script\n'); self.inst.chmod(0o751);os.utime(self.inst,(1620000000,1620000000))
        self.orig=self.inst.read_bytes();self.bridge=b'temporary bridge\n'; (self.stage/'bridge.sh').write_bytes(self.bridge)
        (self.stage/'owner').write_text(self.runid+'\n');subprocess.run(['cp','-p',self.inst,self.stage/'original-launcher'],check=True)
        (self.stage/'original-launcher.stat').write_bytes(subprocess.check_output(['stat','-c','%s:%Y:%a',self.inst]))

    def shell(self,cmd):
        dev=(HERE/'bench_device.sh').read_text().replace('/mnt/us',str(self.base)).replace('/tmp/aidoku-launcher.lock',str(self.base/'lock'))
        pro=f'RUN={self.runid}\nSTAGE={self.stage}\nOWNER_HASH={sha((self.runid+chr(10)).encode())}\nBRIDGE_SHA={sha(self.bridge)}\nORIGINAL_SHA={sha(self.orig)}\n'
        return subprocess.run(['sh','-s'],input=pro+dev+'\nnative_wait() { :; }; stable_entry() { :; }\n'+cmd+'\n',capture_output=True,text=True)

    def test_restore_exact_bytes_mode_time(self):
        r=self.shell('swap_launcher; restore_launcher');self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(self.inst.read_bytes(),self.orig);self.assertEqual(self.inst.stat().st_mode&0o777,0o751);self.assertEqual(int(self.inst.stat().st_mtime),1620000000)

    def test_foreign_mutation_left_untouched(self):
        self.assertEqual(self.shell('swap_launcher').returncode,0)
        self.inst.write_text('other owner\n');self.assertNotEqual(self.shell('restore_launcher').returncode,0);self.assertEqual(self.inst.read_text(),'other owner\n')

    def test_ownership_and_symlink_rejected(self):
        (self.stage/'owner').write_text('other-run\n');self.assertNotEqual(self.shell('swap_launcher').returncode,0);self.assertEqual(self.inst.read_bytes(),self.orig)
        (self.stage/'owner').write_text(self.runid+'\n');p=self.inst.with_name('other');p.write_bytes(self.orig);self.inst.unlink();self.inst.symlink_to(p)
        self.assertNotEqual(self.shell('swap_launcher').returncode,0);self.assertTrue(self.inst.is_symlink())

    def test_failed_upload_leaves_original_launcher(self):
        (self.stage/'original-launcher.stat').unlink();(self.stage/'original-launcher').unlink()
        self.assertEqual(self.shell('restore_launcher').returncode,0)
        self.assertEqual(self.inst.read_bytes(),self.orig)

    def test_all_phases_predetermined(self):
        screen=runner.case_order('screen',1,6); self.assertEqual(len(screen),14);self.assertEqual(screen[2:6],['baseline-01','candidate-01','candidate-02','baseline-02']);self.assertNotIn('baseline-qualify',screen)
        confirm=runner.case_order('confirm',1,10)[2:];self.assertEqual(confirm,runner.case_order('confirm',1,5)[2:]+runner.case_order('confirm',6,5)[2:]);self.assertEqual(len(set(confirm)),20)
        for param in [('screen',1,1),('confirm',1,6),('pilot',3,2)]:
            with self.assertRaises(ValueError):runner.case_order(*param)

    def test_preserve_formal_entry_and_registry(self):
        source=(HERE/'bench_device.sh').read_text()
        for line in source.splitlines():
            if 'sqlite3 ' in line:self.assertIn('sqlite3 -readonly ',line)
        self.assertIn('start file:///mnt/us/documents/Aidoku.sh',source); self.assertNotIn('doFullScan',source)
        origin=(runner.DELIVERY/'artifacts/package-stage/aidoku/launch.sh').read_bytes()
        self.assertEqual(sha(origin),runner.LAUNCH_SHA)
        instrumented=runner.instrument(origin.decode(), ['--startup-cached-covers','2'])
        self.assertEqual(instrumented.count(b'--startup-cached-covers 2'),2)
        self.assertEqual(instrumented.count(b'--capture-native-portrait --performance-trace'),0)
        subprocess.run(['sh','-n'],input=instrumented,check=True)

    def test_bridge_only_for_owned_library_calls(self):
        cases=runner.case_order('screen',1,6);(self.stage/'case').write_text('candidate-01\n')
        out=self.stage/'out/candidate-01';out.mkdir(parents=True);c=self.stage/'candidate';c.mkdir()
        (c/'launch.sh').write_text('printf "%s|%s|%s\\n" "$1" "$AIDOKU_STARTUP_PORTRAIT" "$AIDOKU_RESEARCH_DWELL"\n')
        src=runner.bridge(self.runid,cases,{'baseline':':','candidate':'export AIDOKU_STARTUP_PORTRAIT=native'}).decode().replace('/mnt/us',str(self.base))
        b=self.stage/'bridge';b.write_text(src)
        ok=subprocess.run(['sh',b,'--library'],capture_output=True,text=True);self.assertEqual(ok.returncode,0,ok.stderr);self.assertEqual(ok.stdout.strip(),'--library|native|8000')
        self.assertNotEqual(subprocess.run(['sh',b,'--foreground']).returncode,0)
        (self.stage/'owner').write_text('other\n');self.assertNotEqual(subprocess.run(['sh',b,'--library']).returncode,0)


if __name__=='__main__':
    unittest.main()
