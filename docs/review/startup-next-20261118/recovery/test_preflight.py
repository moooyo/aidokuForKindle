"""Exercise the fixed script in a host-only synthetic directory on test-env."""

import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


module_path=Path(__file__).with_name('prepare_preflight.py')
spec=importlib.util.spec_from_file_location('preflight_test_target',module_path)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class TestPreflight(unittest.TestCase):
    def setUp(self):
        if sys.platform!='linux':self.fail('Run only on test-env')
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.bin=self.root/'bin';self.bin.mkdir()
        for name,value in [('status','printf "statusbar start/running\\n"'),('pidof',f'[ "$1" = awesome ] && echo {os.getpid()}'),
                           ('lipc-get-prop','case "$*" in *activeApp) echo com.lab126.KPPMainApp;; *powerd*) echo active;; *) exit 1;; esac')]:
            self.executable(self.bin/name,'#!/bin/sh\n'+value+'\n')
        self.files={}
        for relative in module.FILES:
            dest=self.root/relative.lstrip('/');dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(relative.encode())
            self.files[relative]=module.sha(dest.read_bytes())
        for directory in (self.root/'var/local/kindle-catalog-entry',self.root/module.RT.lstrip('/')):
            directory.chmod(0o755)
        self.client(True,'complete')
        self.catalog=self.root/'var/local/cc.db'; self.registry=self.root/'var/local/appreg.db';
        with sqlite3.connect(self.registry) as db:
            db.execute('create table properties(handlerId text,name text,value text)');db.execute('insert into properties values(?,?,?)',('tech.hackerdude.shell_integration.launcher','command',str(self.root/module.REG.lstrip('/'))))
        self.doc=self.root/module.DOC.lstrip('/')
        with sqlite3.connect(self.catalog) as db:
            db.execute('create table Entries(p_uuid text,p_location text,p_mimeType text,p_thumbnail text,p_modificationTime integer,p_contentSize integer,p_lastAccess integer,p_displayTags text)')
            db.execute('insert into Entries values(?,?,?,?,?,?,?,?)',('aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',str(self.doc),'text/x-shellscript','sensitive-fixture-thumb',int(self.doc.stat().st_mtime),self.doc.stat().st_size,20,'[]'))

    def executable(self,target,source):
        target.write_text(source);target.chmod(0o755)

    def client(self,success,stage,odd=''):
        target=self.root/module.CLIENT.lstrip('/')
        data=json.dumps(dict(success=success,operation='probe',stage=stage,elapsed_ms=20),separators=(',',':'))
        source="#!/bin/sh\n[ \"$#:$1\" = '1:--probe' ] || exit 10\n"+"printf '%s\\n' '"+data+"'\n"+odd+'\nexit '+str(int(not success))+'\n'
        self.executable(target,source);self.files[module.CLIENT]=module.sha(target.read_bytes())

    def observe(self):
        with patch.object(module,'FILES',self.files):program=module.script().decode()
        for prefix in ('/mnt/us','/var/local','/usr/lib/ccat','/tmp/aidoku-launcher.lock'):
            program=program.replace(prefix,str(self.root)+prefix)
        proc=subprocess.run(['sh','-s'],input=program.encode(),capture_output=True,env={**os.environ,'PATH':str(self.bin)+':'+os.environ['PATH']},timeout=5)
        self.assertNotIn(b'sensitive-fixture',proc.stdout);self.assertNotIn(b'bbbb-cccc',proc.stdout)
        return module.decision(proc.stdout,proc.returncode)

    def test_complete_read_only_and_no_files_created(self):
        before={p:(p.read_bytes(),p.stat().st_mtime_ns) for p in self.root.rglob('*') if p.is_file()}
        result=self.observe();self.assertTrue(result['passed'],result);self.assertEqual(result['fixed_probe'],{'success':True,'stage':'complete','elapsed_ms':20})
        self.assertTrue(result['observation']['stable_entry'] and result['observation']['stable_files']);self.assertFalse(result['framework_eligibility_proven'])
        self.assertEqual(before,{p:(p.read_bytes(),p.stat().st_mtime_ns) for p in self.root.rglob('*') if p.is_file()})

    def test_new_and_metadata_mismatch(self):
        with sqlite3.connect(self.catalog) as db: db.execute('update Entries set p_displayTags=\'["NEW"]\',p_contentSize=0')
        self.client(False,'new-item-fallback');obs=self.observe();self.assertFalse(obs['passed']);self.assertFalse(obs['observation']['file_metadata_matches']);self.assertEqual(obs['observation']['optional_catalog_new_tag'],'present');self.assertEqual(obs['fixed_probe']['stage'],'new-item-fallback')

    def test_missing_optional_tag_column_is_unknown_and_nonfatal(self):
        with sqlite3.connect(self.catalog) as db: db.execute('alter table Entries drop column p_displayTags')
        obs=self.observe();self.assertTrue(obs['passed'],obs);self.assertEqual(obs['observation']['optional_catalog_new_tag'],'unknown')

    def test_immutable_view_rejects_live_overlay(self):
        p=self.catalog.with_name('cc.db-wal');p.write_bytes(b'pending');obs=self.observe();self.assertEqual(obs['guard'],'live_overlay_unverified');self.assertEqual(p.read_bytes(),b'pending')

    def test_protocol_mismatch_is_not_forwarded(self):
        self.client(True,'complete',"printf '%s\\n' 'sensitive-fixture-private-extra'");obs=self.observe();self.assertEqual(obs['result'],'invalid_client_protocol');self.assertFalse(obs['passed'])

    def test_wrong_registered_command_blocks_probe(self):
        with sqlite3.connect(self.registry) as db: db.execute("update properties set value='other'")
        obs=self.observe();self.assertEqual(obs['guard'],'registration');self.assertNotIn('fixed_probe',obs)

    def test_protected_component_mismatch_blocks_probe(self):
        self.doc.write_text('mutated');obs=self.observe();self.assertEqual(obs['guard'],'files');self.assertFalse(obs['passed'])

    def test_json_duplicate_or_extra_stdout_does_not_escape(self):
        status,module_status=b'probe invalid\n',b'probe {"success":true,"success":true,"operation":"probe","stage":"complete","elapsed_ms":5}\n'
        base=b'context root\nrows one\nshape 1\nmetadata 1\nnew_tag absent\n'+status+b'rc 0\nstable_native 1\nstable_files 1\nstable_entry 1\n'
        for unexpected in (base.replace(status,module_status),base+b'pass secret\n',base.replace(b'absent',b'secret')):
            obs=module.decision(unexpected,0);self.assertFalse(obs['passed']);self.assertNotIn('secret',json.dumps(obs))


if __name__=='__main__':unittest.main()
