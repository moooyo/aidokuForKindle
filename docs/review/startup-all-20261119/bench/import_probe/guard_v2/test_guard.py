"""Qualify the inventory only on disposable host files; never connect to the Kindle."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parent))
import inventory_records as ir
import materialize_guard as mg


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.p=Path(self.tmp.name)
        self.root=self.p/'cache';self.raw=self.root/'raw'/'01';self.proc=self.root/'processed'/'ab'
        self.raw.mkdir(parents=True);self.proc.mkdir(parents=True)
        self.a=self.raw/'original.raw';self.b=self.proc/'render.png'
        self.a.write_bytes(b'opened read-only');self.b.write_bytes(b'abc');self.bin=self.p/'observer'
        subprocess.run(['cc','-std=c11','-O2','-Wall','-Wextra','-Werror','-I'+str(mg.CBASE),'-DINVENTORY_ROOT="'+str(self.root)+'"',
                        str(mg.HERE/'cache_inventory.c'),str(mg.CBASE/'sha256.c'),'-o',str(self.bin)],check=True)

    def observe(self):
        out=self.p/'inventory';out.write_bytes(subprocess.check_output([self.bin]));return ir.inventory_read(out)

    def test_exact_hashes_and_no_atime_modification(self):
        paths=[self.a,self.b,self.raw,self.proc,self.root/'raw',self.root/'processed'];atime=1_000_000_000_123_456_789
        for path in paths:os.utime(path,ns=(atime,1_600_000_000_987_654_321))
        r=self.observe();self.assertEqual({k:v[3] for k,v in r.items()},
            {'raw/01/original.raw':hashlib.sha256(b'opened read-only').hexdigest(),'processed/ab/render.png':hashlib.sha256(b'abc').hexdigest()})
        self.assertEqual(len(r),2);self.assertEqual([x.stat().st_atime_ns for x in paths],[atime]*6)
        digest=subprocess.check_output(['sh','-c',"find raw processed -type f -exec stat -c '%n:%s:%Y' '{}' ';' | LC_ALL=C sort | sha256sum"],cwd=self.root).decode().split()[0]
        self.assertEqual(digest,ir.inventory_legacy(r))

    def test_distinguishes_bytes_same_length_time_and_metadata_only(self):
        original=self.observe();st=self.b.stat();self.b.write_bytes(b'xyz');os.utime(self.b,ns=(st.st_atime_ns,st.st_mtime_ns))
        changed=self.observe();self.assertEqual(ir.inventory_legacy(original),ir.inventory_legacy(changed));d=ir.inventory_compare(original,changed,self.p)
        self.assertEqual(d['changes'],{'added':0,'removed':0,'mtime':0,'size':0,'content':1});self.assertFalse(d['exactly_equal'])
        self.assertNotIn('render.png',json.dumps(d));self.assertIn('render.png',(self.p/'cache-private-differences.json').read_text())
        os.utime(self.b,ns=(st.st_atime_ns,st.st_mtime_ns+2_000_000_000));again=self.observe()
        self.assertNotEqual(ir.inventory_legacy(changed),ir.inventory_legacy(again));self.assertEqual(ir.inventory_compare(changed,again)['changes']['mtime'],1)

    def test_rejects_symlink_truncation_or_extra_arguments(self):
        self.a.unlink();self.a.symlink_to(self.b)
        self.assertNotEqual(subprocess.run([self.bin],capture_output=True).returncode,0)
        self.assertEqual(subprocess.run([self.bin,str(self.root)],capture_output=True).returncode,64)
        v=self.p/'invalid';v.write_text('F\traw/01/x\t3\t123\t000000000\t'+'0'*64+'\n')
        with self.assertRaises(ValueError):ir.inventory_read(v)

    def test_original_failure_cannot_be_attributed_or_qualified(self):
        old=mg.OLD.parent/'private/aidoku-all-import-screen-362b98e8'
        a=(old/'before.stdout').read_bytes();b=(old/'after.stdout').read_bytes();self.assertTrue(ir.only_legacy_file_stat_changed(a,b));self.assertEqual(len(a.splitlines()),38)
        self.assertFalse(json.loads((old/'report.json').read_bytes())['success']);self.assertEqual(b,(old/'after-7a19ee.stdout').read_bytes())
        bad=b.replace(b'absent ',b'not-absent ',1);self.assertFalse(ir.only_legacy_file_stat_changed(a,bad))

    def test_only_predeclared_short_screen_and_independent_qualification_can_freeze(self):
        files=mg.generate();namespace={'__name__':'fixture_runner','__file__':str(mg.DEST/'tools/run_bench.py')};exec(compile(files['run_bench.py'],'frozen','exec'),namespace)
        order=namespace['case_order'];self.assertEqual(order('screen',1,3)[2:],['baseline-01','candidate-01','candidate-02','baseline-02','baseline-03','candidate-03'])
        self.assertEqual(order('screen',4,3)[2:],['candidate-04','baseline-04','baseline-05','candidate-05','candidate-06','baseline-06'])
        with self.assertRaises(ValueError):order('screen',1,6)
        self.assertEqual(order('qualify',1,0)[-2:],['baseline-qualify','candidate-qualify'])
        self.assertIn("and report.get('private_cache_verified')",files['run_bench.py']);self.assertIn('real_cache_snapshot || return 1',files['bench_device.sh'])


if __name__=='__main__':
    if sys.platform!='linux':raise SystemExit('Run only on test-env')
    unittest.main()
