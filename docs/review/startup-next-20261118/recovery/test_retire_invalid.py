"""Host-only original-owner retirement gates; never imports live transport."""

import copy
import io
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('retire',HERE/'retire_invalid.py');r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
HISTORY=Path('/root/aidoku-next-coldbench-20261118/private/aidoku-next-cold-confirm-576fdfaf')


class ReindexFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root,cls.plan,cls.report,cls.script,cls.case=r.batch_input(HISTORY);c=cls.root/'outputs/out'/cls.case
        cls.args=[(HISTORY/'before.stdout').read_bytes(),(HISTORY/'after-functional-recovery.stdout').read_bytes(),(c/'catalog-before.identity').read_bytes(),(c/'catalog-after.identity').read_bytes(),(c/'catalog-before.access').read_bytes(),(c/'catalog-after.access').read_bytes(),json.loads((c/'application.json').read_text()),(c/'shell.tsv').read_bytes(),*r.times(HISTORY,cls.report)]

    def test_completed_incident_is_only_a_retirement(self):
        record=r.audit(self.root,self.report,self.case)
        self.assertFalse(record['batch_eligible']);self.assertTrue(record['lease_released']);self.assertEqual(record['protected_snapshot_rows'],38)
        for sensitive in [self.args[2].split(b'|')[0],self.args[2].split(b'|')[2]]:self.assertNotIn(sensitive,json.dumps(record).encode())

    def test_historical_released_token_is_rejected_before_connection(self):
        with patch.object(r,'isolated_remote',side_effect=AssertionError('unexpected transport')):
            with self.assertRaisesRegex(r.Rejected,'already_released_token'):r.retire(self.root,self.plan,self.report,self.script,self.case)

    def test_unchanged_or_extra_snapshot_deltas_reject(self):
        before,after=self.args[:2]
        with self.assertRaises(r.Rejected):r.snap(before,before)
        for i in [0,2,7,9,16,36,37]:
            lines=after.splitlines();lines[i]+=b' ';modified=b'\n'.join(lines)+b'\n'
            with self.assertRaises(r.Rejected):r.snap(before,modified)

    def test_catalog_binding_rejects_duplicate_wrong_mime_cursor_or_clock(self):
        old=self.args[2];new=self.args[3];fields=new.rstrip(b'\n').split(b'|');m=[]
        m.append((3,new+new));m.append((3,old));m.append((3,b'|'.join(fields[:1]+[b'other']+fields[2:])+b'\n'))
        for j,s in [(0,b'bad-id'),(2,b'other-thumbnail'),(3,b'1234567890'),(4,b'308')]:
            v=list(fields);v[j]=s;m.append((3,b'|'.join(v)+b'\n'))
        m.append((5,self.args[4]));m.append((9,self.args[8]));m.append((7,b'native_home_active\n'))
        for i,v in m:
            args=copy.deepcopy(self.args);args[i]=v
            with self.assertRaises(r.Rejected):r.classify(*args)

    def test_both_firmware_probes_remain_queries_only(self):
        for cold in (False,True):
            sh=r.retirement_shell('baseline-warmup',cold)
            subprocess.run(['sh','-n'],input=sh.encode(),check=True)
            self.assertNotRegex(sh,r'\b(touch|chmod|mv|cp|kill|rm|UPDATE|INSERT|DELETE)\b')
            for line in sh.splitlines():
                if 'sqlite3 ' in line:self.assertIn('sqlite3 -readonly ',line)

    def test_failed_release_preserves_lease_and_nonqualifying_status(self):
        # Feed the exact failing stage release would see into a local mocked
        # transport and mock the lease module. No real lease or network access.
        for fault in ('','snapshot_changed','final_row_changed','not_owned'):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as d:
                base=Path(d)/self.root.name;base.mkdir();(base/'before.stdout').write_bytes(self.args[0]);(base/'lease-acquired.json').write_text(json.dumps({'run_id':base.name,'ok':True}))
                seed=Path(d)/'seed';seed.write_text(json.dumps({'lease_tool_directory':str(r.SHARED),'lease_dir':'unused'}));cfg=json.loads(seed.read_text());cfg.update(owner_thread=base.name,lease_run_id=base.name,lease_token_file=str(base/'lease-token'));(base/'transport.json').write_text(json.dumps(cfg))
                state=dict(self.report);state.update(success=False,lease_released=False)
                closed=[];calls=[]
                def status(_):return {'ownership_verified':fault!='not_owned'}
                def release(_,*,native_restored_and_connections_closed):closed.append(native_restored_and_connections_closed);return {'ok':True,'run_id':base.name}
                fake=SimpleNamespace(LeaseRequest=lambda *args:args,status=status,release=release)
                archive=io.BytesIO()
                with tarfile.open(fileobj=archive,mode='w:gz') as tar:tar.add(self.root/'outputs/out',arcname='out')
                def remote(cmd,data,timeout,errors):
                    calls.append(errors.stem)
                    if errors.stem=='check-original-state':return 0,self.args[1] + (b'bad\n' if fault=='snapshot_changed' else b'')
                    if errors.stem=='preserve-failed-samples':return 0,archive.getvalue()
                    if errors.stem=='verify-before-stage-removal':return 0,self.args[1]
                    if errors.stem=='remove-owned-stage':return 0,b''
                    if errors.stem=='verify-closed-state':return (1,b'') if fault=='final_row_changed' else (0,self.args[1])
                    raise AssertionError('unrecognized host fixture command')
                with patch.object(r,'SEED',seed),patch.dict(sys.modules,{'device_lease':fake}),patch.object(r,'isolated_remote',remote):
                    if fault:
                        with self.assertRaises(r.Rejected):r.retire(base,self.plan,state,self.script,self.case)
                        self.assertEqual(closed,[]);self.assertFalse((base/'lease-released.json').exists())
                    else:
                        result=r.retire(base,self.plan,state,self.script,self.case);self.assertEqual(closed,[True]);self.assertFalse(result['batch_eligible']);self.assertFalse(state['success']);self.assertFalse(state['installed_and_user_unchanged']);self.assertTrue(state['lease_released'])

    def test_archive_rejects_outside_outputs(self):
        data=io.BytesIO()
        with tarfile.open(fileobj=data,mode='w:gz') as t:
            i=tarfile.TarInfo('out/../other');i.size=3;t.addfile(i,io.BytesIO(b'abc'))
        with tempfile.TemporaryDirectory() as d,self.assertRaisesRegex(r.Rejected,'unsafe_private_output_archive'):r.extract_evidence(data.getvalue(),Path(d)/'output')


if __name__=='__main__':unittest.main()
