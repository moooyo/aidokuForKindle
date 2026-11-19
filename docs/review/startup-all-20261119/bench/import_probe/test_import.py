"""Host-only qualification, including a real Xvfb desktop import and fresh reopen."""

import io
import json
from pathlib import Path
import shlex
import tarfile
import subprocess
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parent))
import analyze_import as analysis
import attest
import fixture
import materialize


def trace(stage,n,name,**extra):return {'name':name,'boottime_us':stage+n*1000,**extra}


class ImportModel(unittest.TestCase):
    def test_fixed_inputs_and_natural_order(self):
        parts=fixture.bundle();self.assertEqual(fixture.sha(parts['fixture.json']),materialize.FIXTURE_SHA)
        for i,b in enumerate(json.loads(parts['fixture.json'])['books']):
            self.assertEqual(b['cover_size'],list(fixture.LARGE if i in fixture.TARGETS else fixture.SMALL))
            with zipfile.ZipFile(io.BytesIO(parts['comics/'+b['file']])) as z:
                self.assertEqual(z.namelist()[:2],['002.png','001.png'])
                self.assertEqual([fixture.sha(z.read(f'{j:03}.png')) for j in (1,2)],b['page_sha256'])

    def test_work_is_charged_at_its_later_completion(self):
        base=1000000;clocks={'native_open_requested':base,'native_home_active':base+1200000}
        seq=[trace(base,n,k) for n,k in [(10,'startup_library_frame_submitted'),(12,'startup_library_input_ready'),
             (14,'import_started'),(17,'import_library_published'),(90,'import_thumbnail_started')]]
        for i in range(4):
            if i:seq.append(trace(base,90+80*i,'import_thumbnail_started'))
            seq.append(trace(base,155+80*i,'import_thumbnail_persisted'))
        done=trace(base,160,'import_visible_covers_settled',marker=2,cover_slots=16,cover_images=16,cover_pending=False)
        seq += [done,trace(base,400,'import_thumbnail_queue_drained'),trace(base,940,'shutdown_started')]
        seq.sort(key=lambda e:e['boottime_us']);cost=analysis.measure(seq,clocks,'ingest','candidate')
        self.assertEqual(cost['worker_elapsed_sum_ms'],260);self.assertEqual(cost['request_to_all_work_ms'],400)
        self.assertEqual(cost['extra_tail_after_visible_ms'],240)
        with self.assertRaises(ValueError): analysis.measure(seq,clocks,'ingest','baseline')
        done['cover_images']=0
        with self.assertRaises(ValueError): analysis.measure(seq,clocks,'ingest','candidate')

    def test_order_and_private_guard(self):
        plan=analysis.names('screen',[1,2]);self.assertEqual(plan[4:8],['baseline-01-ingest','baseline-01-reopen','candidate-01-ingest','candidate-01-reopen'])
        self.assertEqual(plan[8:12],['candidate-02-ingest','candidate-02-reopen','baseline-02-ingest','baseline-02-reopen'])
        src=materialize.generate(); self.assertNotIn('.backup $f/library.sqlite3',src['bench_device.sh'])
        self.assertNotIn('sleep 6\n',src['bench_device.sh']);self.assertIn('native_restored_and_connections_closed=True',src['run_bench.py'])
        self.assertIn('restore_launcher',src['bench_device.sh']);self.assertIn('apply_case_options "$data"',src['bench_device.sh'])
        with self.assertRaises(ValueError):materialize.once('two two','two','one')

    def test_freeze_binds_both_phases_and_one_uninstrumented_binary(self):
        # Freeze only. The older genuine ARM input proves packaging, not feature support or speed.
        exemplar='/root/aidoku-startup-delivery-20261118/artifacts/package-stage/aidoku/app/aidoku'
        with tempfile.TemporaryDirectory(prefix='aidoku-import-freeze-') as name:
            path=Path(name); file=path/'run_bench.py';src=materialize.generate()
            args=[sys.executable,'-I',str(file),'prepare','--trial','import','--mode','pilot','--name','dry','--primary','covers',
                  '--baseline-app',exemplar,'--candidate-app',exemplar,'--baseline-option','import_thumbnail=off',
                  '--candidate-option','import_thumbnail=idle']
            file.write_text(materialize.once(src['run_bench.py'],str(materialize.DEST),str(path)))
            (path/'bench_device.sh').write_text(src['bench_device.sh'])
            for k,v in fixture.bundle().items():f=path/'fixture_payload'/k; f.parent.mkdir(parents=True,exist_ok=True);f.write_bytes(v)
            tryplan=subprocess.run(args,check=True,capture_output=True,timeout=25)
            loc=Path(tryplan.stdout.decode().strip());plan=json.loads(loc.read_text());self.assertEqual(len(plan['cases']),8)
            with tarfile.open(loc.parent/'payload.tar.gz','r:gz') as arc:
                self.assertEqual(len([s for s in arc.getnames() if s.startswith('bin-')]),1)
                self.assertEqual(arc.extractfile('fixture.json').read(),attest.reference())
                self.assertEqual(arc.extractfile('candidate/launch.sh').read(),arc.extractfile('baseline/launch.sh').read())
                self.assertIn(b'--import $stage/comics',arc.extractfile('bridge.sh').read())
            self.assertFalse((loc.parent/'lease-token').exists());self.assertFalse((loc.parent/'transport.json').exists())
            self.assertNotEqual(subprocess.run(args+['--candidate-option','first_layout=optimized'],capture_output=True,timeout=25).returncode,0)

    def test_real_desktop_import_and_reopen(self):
        # This build predates idle preparation. It validates the control workload and page visibility, not its performance.
        desktop=Path('/root/aidoku-next-status-20261118/artifacts/candidate-desktop')
        if not desktop.is_file():self.fail('Qualified Scribe desktop build is absent')
        with tempfile.TemporaryDirectory(prefix='aidoku-import-fixture-') as name:
            stage=Path(name);profile=stage/'fake-home'/'data';profile.mkdir(parents=True);(profile/'device-profile').write_text('scribe\n')
            payload=fixture.bundle()
            for k,v in payload.items():f=stage/k;f.parent.mkdir(exist_ok=True,parents=True);f.write_bytes(v)
            for arm,opt in [('baseline','off'),('candidate','idle')]:
                folder=stage/arm;folder.mkdir();flags=f'import_thumbnail={opt}\n'.encode();(folder/'bench.options').write_bytes(flags)
                (folder/'bench.options.sha256').write_text(fixture.sha(flags)+'\n')
            base=(materialize.BASE/'bench_device.sh').read_text();options=base[base.index('apply_case_options() {'):base.index('prepare_device() {')]
            support='''set -eu
owner() { :; }; native_wait() { :; }; stable_entry() { :; }
digest() { sha256sum "$1" | cut -d' ' -f1; }
same_hash() { [ -f "$1" ] && [ ! -L "$1" ] && [ "$(digest "$1")" = "$2" ]; }
nonlink() { [ ! -L "$1" ] && [ "$(readlink -f "$1")" = "$1" ]; }
'''+f'STAGE={shlex.quote(str(stage))}\nAPP={shlex.quote(str(profile.parent))}\nIMPORT_FIXTURE_SHA={materialize.FIXTURE_SHA}\n'+options+Path(__file__).with_name('import_device.sh').read_text()
            def shell(s):subprocess.run(['/bin/sh','-s'],input=(support+'\n'+s).encode(),check=True,timeout=18)
            shell('fixture');data=stage/'cases'/'baseline-01'/'data'; all_edges={}
            for phase in ('ingest','reopen'):
                cid='baseline-01-'+phase;out=stage/'out'/cid;out.mkdir()
                shell('OUT='+shlex.quote(str(out))+'\nimport_case '+cid)
                cmd=['xvfb-run','-a',str(desktop),'--device','scribe','--offline','--data-dir',str(data),'--state-output',str(out/'state.json'),'--performance-trace',str(out/'application.json'),'--exit-after-ms','5500']
                if phase=='ingest':cmd+=['--import',str(stage/'comics')]
                subprocess.run(cmd,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=25)
                dev=Path(__file__).with_name('import_device.sh').read_text()
                # The earlier desktop ELF lacks the candidate's new trace markers. All artifact, path, source and pixel checks still run.
                audit='\n'.join(l for l in dev[dev.index('audit_import() {'):].splitlines() if 'grep -F' not in l)
                shell(audit+'\nOUT='+shlex.quote(str(out))+'\ndata='+shlex.quote(str(data))+'\nphase='+phase+'\naudit_import')
                state=attest.verify_case(out,phase,'baseline',host_fixture_data=data)
                self.assertTrue(state['source_images_verified']); self.assertEqual(state['target_derivative_count'],0 if phase=='ingest' else 4)
                all_edges[phase]=state;(out/'case-ok').write_text('ok\n')
                if phase=='reopen':
                    events=json.loads((out/'application.json').read_text())['events']
                    self.assertEqual(sum(e['name']=='cover_local_derivative_prepared' for e in events),4)
                    self.assertEqual(sum(e['name']=='cover_local_derivative_reused' for e in events),11)
            self.assertTrue(all(all_edges['reopen']['image_sha256_by_title'][k]==v for k,v in all_edges['ingest']['image_sha256_by_title'].items()))
            original=(out/'page-hashes.tsv').read_text();(out/'page-hashes.tsv').write_text(original[:-5]+'ffff\n')
            with self.assertRaises(ValueError):attest.verify_case(out,'reopen','baseline',host_fixture_data=data)


if __name__=='__main__':
    if sys.platform!='linux':raise SystemExit('Run only on test-env')
    unittest.main()
