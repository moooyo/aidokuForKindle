"""Synthetic host fixtures for the 9d92 deployment boundary and private physical pixels."""

import copy
import gzip
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import capture_private as cap
import release_common as c
import run_all as run


def reference_initial_frame():
    raw=c.raw(Path(os.environ['AIDOKU_RELEASE_REFERENCE_FRAME']),15*1024*1024)
    assert len(raw)==cap.SIZE and raw.startswith(cap.HEADER)
    return raw


class ImageChecks(unittest.TestCase):
    def setUp(self):
        scratch=tempfile.TemporaryDirectory();self.addCleanup(scratch.cleanup);self.tar=Path(scratch.name)/'synthetic.tar'
    def make(self,pictures,substitute=None,extra=None):
        with tarfile.open(self.tar,'w') as archive:
            for label,data in pictures.items():
                body,right=cap.physical(data)
                items={label+'.native.ppm.gz':gzip.compress(data,compresslevel=1),label+'.sha':body,label+'.right.tsv':right}
                if substitute:
                    for name,blob in substitute.items():
                        if name in items:items[name]=blob
                for name,blob in items.items():
                    item=tarfile.TarInfo(name);item.size=len(blob);archive.addfile(item,io.BytesIO(blob))
            if extra:
                item=tarfile.TarInfo(extra);item.type=tarfile.SYMTYPE;item.linkname='/not-a-capture';archive.addfile(item)
    def test_native_bytes_are_checked_without_exporting_pixels_or_digests(self):
        frame=reference_initial_frame();later=bytearray(frame);later[len(cap.HEADER)+25]^=1
        self.make({'before':frame,'after':later,'wake':later})
        summary=cap.check_capture_archive(self.tar);self.assertEqual(summary['native_p6_count'],3)
        self.assertTrue(summary['body_pixels_match'] and summary['right_toolbar_observable'])
        self.assertNotIn('sha',json.dumps(summary))
    def test_wrong_right_toolbar_or_covered_book_is_rejected(self):
        frame=reference_initial_frame()
        for offset in (len(cap.HEADER)+3*1000,len(cap.HEADER)+3*1860*80):
            candidate=bytearray(frame);candidate[offset]^=1
            self.make({'before':frame,'after':candidate,'wake':candidate})
            with self.assertRaises(ValueError):cap.check_capture_archive(self.tar)
    def test_archived_evidence_cannot_add_links_or_override_device_hashes(self):
        frame=reference_initial_frame();p={s:frame for s in ('before','after','wake')}
        for changes in ({'extra':'unexpected'},{'substitute':{'wake.sha':b'0'*64+b'\n'}},
                        {'substitute':{'before.native.ppm.gz':gzip.compress(b'x'*(cap.SIZE+2),compresslevel=1)}}):
            self.make(p,**changes)
            with self.assertRaises(ValueError):cap.check_capture_archive(self.tar)

    def test_device_capture_stays_off_the_userstore_and_is_removed_after_collection(self):
        root=self.tar.parent; cwd=root/'media'; cwd.mkdir();(cwd/'lock').mkdir();(cwd/'lock/phase').write_text('active\n')
        (cwd/'raster').mkdir();(cwd/'raster/owner').write_text('synthetic-0\n');(cwd/'capture').write_text('#!/bin/sh\ncat native-fixture\n');(cwd/'capture').chmod(0o700)
        data=reference_initial_frame();(cwd/'native-fixture').write_bytes(data)
        native=str(root/'native-private-')
        def rebase(text):return text.replace('/var/tmp/aidoku-all-private-pixels-',native)
        code=cap.materialize(c.historical('smoke.sh')).split(b'# Entry point.')[0].decode('ascii')
        script=rebase(code)+'''\nRUN=synthetic-0; LOCK=$PWD/lock; PIXTMP=$PWD/raster
same_app() { return 0; }
snapshot before.right.tsv >before.sha
test ! -e before.native.ppm.gz
'''+rebase(cap.collection_shell({'before':'before'}))
        result=subprocess.run(['sh','-s'],cwd=cwd,input=script.encode(),capture_output=True,check=True)
        self.tar.write_bytes(result.stdout);self.assertEqual(cap.check_capture_archive(self.tar,('before',))['native_p6_count'],1)
        vault=root/'native-private-synthetic-0';self.assertEqual(vault.stat().st_mode & 0o777,0o700)
        foreign=subprocess.run(['sh','-s'],cwd=cwd,input=('set -eu\nRUN=synthetic-0\n'+rebase(cap.cleanup_shell()).replace(' = 0:700',' = 0:755')).encode(),capture_output=True)
        self.assertNotEqual(foreign.returncode,0);self.assertTrue(vault.exists())
        subprocess.run(['sh','-s'],cwd=cwd,input=('set -eu\nRUN=synthetic-0\n'+rebase(cap.cleanup_shell())).encode(),capture_output=True,check=True)
        self.assertFalse(vault.exists())


class InstalledGuards(unittest.TestCase):
    def test_program_swap_storage_checks_hash_db_bytes_and_cache_inventory_without_exporting_its_names(self):
        with tempfile.TemporaryDirectory() as name:
            data=Path(name)/'data';cache=data/'cache/shared-v1';(cache/'raw').mkdir(parents=True);(cache/'processed').mkdir()
            db=data/'library.sqlite3';db.write_bytes(b'database-v0');(data/'device-profile').write_text('scribe\n')
            idx=cache/'index.sqlite3';idx.write_bytes(b'index-a');private=cache/'raw'/'private-synthetic-title';private.write_bytes(b'book')
            shell='set -eu\n'+run.TRANSACTION_DATA.replace('/mnt/us/aidoku/data',str(data))
            get=lambda:subprocess.run(['sh','-s'],input=shell.encode(),capture_output=True,check=True).stdout
            old=get();self.assertIn(b'cache-artifact-roster ',old);self.assertNotIn(private.name.encode(),old);self.assertEqual(get(),old)
            idx.write_bytes(b'index-b');self.assertNotEqual(get(),old)
            prior=get();private.write_bytes(b'book with a changed size');self.assertNotEqual(get(),prior)
            prior=get();(data/'library.sqlite3-wal').write_bytes(b'sidecar');self.assertNotEqual(get(),prior)

    def test_collected_rollback_payload_is_checked_again_after_release(self):
        tx='promote-000000000001';app=b'old installed application';launcher=b'old installed launcher';changes=b'fixture change log\n'
        body={name:(tx+'\n').encode() for name in ('owner','sealed','installed','aidoku.intent','launch.sh.intent','aidoku-startup-portrait.intent')}
        body.update({'changes.tsv':changes,'aidoku.mode':b'777\n','launch.sh.mode':b'755\n','aidoku.original':app,'launch.sh.original':launcher})
        with tempfile.TemporaryDirectory() as name,patch.object(c,'BASE',c.sha(app)),patch.object(c,'LAUNCH_SHA',c.sha(launcher)):
            archive=Path(name)/'archive.tgz'
            def put():
                with tarfile.open(archive,'w:gz') as t:
                    for n,v in body.items():
                        item=tarfile.TarInfo(n);item.size=len(v);t.addfile(item,io.BytesIO(v))
            put();self.assertTrue(run.verify_backup(archive,tx,changes))
            body['launch.sh.original']=b'wrong source version';put()
            with self.assertRaises(ValueError):run.verify_backup(archive,tx,changes)
            body['launch.sh.original']=launcher;body['library.sqlite3']=b'not an allowed program payload';put()
            with self.assertRaises(ValueError):run.verify_backup(archive,tx,changes)

    def test_user_database_is_query_only_and_preferences_and_progress_are_protected(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);db=root/'library.sqlite3'
            schema={'books':'id,source_id,remote_id,title,in_library,category,updated_at,document','chapters':'id,book_id,source_id,remote_id,number,document',
                    'pages':'chapter_id,position,document','progress':'chapter_id,book_id,page,total,completed,updated_at',
                    'bookmarks':'chapter_id,page,book_id,created_at','preferences':'key,document','sources':'id,document',
                    'downloads':'chapter_id,book_id,state,document'}
            conn=sqlite3.connect(db)
            for table,cols in schema.items(): conn.execute(f'CREATE TABLE {table} ({cols})')
            conn.execute("INSERT INTO preferences VALUES('navigation-session','old')");conn.execute("INSERT INTO preferences VALUES('application','private app preference')");conn.commit()
            script='set -eu\n'+c.DATA_SUFFIX.replace('/mnt/us/aidoku/data/',str(root)+'/')
            call=lambda:subprocess.run(['sh','-s'],input=script.encode(),capture_output=True,check=False)
            before=call();self.assertEqual(before.returncode,0,before.stderr);self.assertRegex(before.stdout.decode(),r'^user-library-projection [0-9a-f]{64}\n$')
            conn.execute("UPDATE preferences SET document='new' WHERE key='navigation-session'");conn.commit();self.assertEqual(call().stdout,before.stdout)
            conn.execute("INSERT INTO progress VALUES('a','b',3,45,0,0)");conn.commit();self.assertNotEqual(call().stdout,before.stdout)
            conn.execute("INSERT INTO downloads VALUES('a','b','queued','{}')");conn.commit();self.assertNotEqual(call().returncode,0)
            conn.execute("DELETE FROM downloads");conn.execute('DROP TABLE bookmarks');conn.commit();self.assertNotEqual(call().returncode,0)
            conn.close()
    def test_historical_package_and_runtime_cannot_select_retired_rollback_files(self):
        c.installed_reference();engine=run.coordinator();self.assertEqual(engine.DEPLOYED[c.APP],c.BASE)
        self.assertNotEqual(engine.DEPLOYED[c.APP],c.PREVIOUS);self.assertEqual(engine.ROOT,c.ROOT)
        self.assertEqual(engine.REGISTRATION,c.REG);self.assertIn('-readonly',engine.REGISTRY)
        self.assertNotIn('UPDATE',engine.REGISTRY);subprocess.run(['sh','-n'],input=engine.STATE.encode(),check=True)
        _,files=run.stage_archive({'changes.tsv':(b'',0o600)},'promote-synthetic',True,engine)
        self.assertEqual(files['transaction.sh'][0],c.historical('transaction.sh'))
        self.assertIn(b'.native.ppm.gz',files['smoke.sh'][0]);self.assertNotIn(b'/mnt/us/.aidoku-next-',files['smoke.sh'][0])
        rollback=run.coordinator(False);self.assertEqual(rollback.DEPLOYED[c.APP],c.BASE)
        self.assertIn("personal-option %s %s",rollback.STATE);self.assertNotIn('test "$queued" = 0',rollback.STATE)
        self.assertIn('test "$queued" = 0',engine.STATE);self.assertNotIn('personal-option %s %s',engine.STATE)
        subprocess.run(['sh','-n'],input=rollback.STATE.encode(),check=True)
    def test_coordinator_never_accepts_old_controls_or_extra_paths(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name); files={k:k.encode() for k in c.DEPLOYED};states={k:'default_off_optional' for k in c.FEATURES}
            z=root/'aidoku-startup-all.zip'
            with zipfile.ZipFile(z,'w') as arc:
                for k,v in files.items():arc.writestr(k,v);f=root/'delta'/k;f.parent.mkdir(parents=True,exist_ok=True);f.write_bytes(v)
            kp=root/'aidoku-startup-all.kpkg';kp.write_bytes(b'host-fixture');p=root/'plan.json'
            plan={'schema':2,'baseline_sha256':c.BASE,'catalog_registry':c.REG,'features':states,'feature_qualifications':{k:{} for k in states},
                  'policy_sha256':c.validate_policy(states),'source_manifest_sha256':'5'*64,'evidence_sha256':{k:'6'*64 for k in ('build','production_defaults','default_warm','default_cold')},
                  'archive_sha256':c.sha(z.read_bytes()),'kpm_sha256':c.sha(kp.read_bytes()),
                  'changes':[{'path':k,'old':c.DEPLOYED[k],'new':c.sha(files[k]),'file':'delta/'+k} for k in (c.EXTRA,c.APP,c.LAUNCH)]}
            prog={k:c.sha(v) for k,v in files.items()}; plan.update(baseline_archive_sha256=c.PACKAGED_ZIP,baseline_kpm_sha256=c.PACKAGED_KPM,program_sha256=prog,
                              default_nonregression={name:{'paired':{'n':6,'regression_guard_passed':True}} for name in ('warm','cold')})
            for f in states:
                plan['evidence_sha256'][f+'_screen']='7'*64
                plan['feature_qualifications'][f]={'availability':'qualified','program_sha256':prog,'source_manifest_sha256':'5'*64,
                        'default_policy':'default_off_optional','conclusion':'available_default_off'}
            def bind(obj):
                p.write_text(json.dumps(obj));return run.freeze(p,c.sha(p.read_bytes()))
            self.assertEqual(bind(plan)[2][c.APP],c.sha(files[c.APP]))
            for mutation in ('old','rejected-v2','foreign','missing','policy','unlisted','reordered','warm','fake-speedup'):
                test=copy.deepcopy(plan)
                if mutation=='old':test['changes'][1]['old']=c.PREVIOUS
                if mutation=='rejected-v2':test['changes'][1]['new']='1b1c508e1ee84a4be3bd0c15bcf6d42bdd492d688f3fa81b065fb256e00dd3af'
                if mutation=='foreign':test['catalog_registry']='/not/the/managed/handler'
                if mutation=='missing':test['changes'].pop(0)
                if mutation=='policy':test['features']['resident']='unknown'
                if mutation=='unlisted':test['changes'][2]['path']='documents/Aidoku.sh'
                if mutation=='reordered':test['changes'].reverse()
                if mutation=='warm':test['default_nonregression']['warm']['paired']['regression_guard_passed']=False
                if mutation=='fake-speedup':test['feature_qualifications']['resident']['conclusion']='confirmed_benefit'
                with self.subTest(mutation),self.assertRaises(ValueError):bind(test)


if __name__=='__main__':unittest.main()
