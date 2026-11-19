"""Run the frozen catalog assertions with only mock Kindle services and fixture-owned processes."""
import argparse
import ctypes
import hashlib
import importlib.util
import json
import os
import traceback
import types
from pathlib import Path

HERE=Path(__file__).resolve().parent
FIXTURE='78323cfbe8fd397d8506affa5ff0ae0844bce63a4a2ff9eac3d660f4a6c1e47f'
CASES=('follows_native_restoration_once','keeps_nonzero_application_status','survives_library_waiter_retirement','failure_uses_bounded_standard_scan','overflow_is_not_accepted','timeout_preserves_exit_and_restoration','rejects_modified_helper')

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()

def load(name,path):
    s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('launcher',type=Path);parser.add_argument('output',type=Path);args=parser.parse_args()
    frozen=Path(os.environ.get('AIDOKU_R3_FROZEN_LAUNCHER_TEST','/root/aidoku-all-build-20261119/sources/final-v2/packaging/tests/launcher_test.py'))
    assert sha(frozen)==FIXTURE and not frozen.is_symlink()
    life=load('r3_lifecycle',Path(os.environ.get('AIDOKU_R3_LIFECYCLE',HERE.parent/'lifecycle-fixture/lifecycle.py')))
    tests=load('r3_frozen_fixture',frozen);tests.LAUNCHER=args.launcher.resolve()
    assert ctypes.CDLL(None,use_errno=True).prctl(36,1,0,0,0)==0 and not life.tree(os.getpid())
    cases={};diagnostics={}
    for suffix in CASES:
        name='test_catalog_notification_'+suffix;f=tests.LauncherTests(name)
        try:
            life.setup(f,'shell')
            def complete(self,timeout=20):
                result=self.launcher.wait(timeout=timeout)
                if self.log:self.log.close()
                return result
            f.complete=types.MethodType(complete,f)
            # This calls only the frozen test method; its global process-table complete() is replaced above.
            getattr(f,name)()
            f.assert_recovered()
            assert not (f.root/'forbidden-tools').exists()
            def no_owned_launcher_children():
                life.reap_owned({f.wm.pid});return life.tree(os.getpid())=={f.wm.pid}
            tests.wait_for(no_owned_launcher_children,seconds=4,description='owned catalog processes reaped')
            cases[name]=True
        except Exception:
            cases[name]=False;diagnostics[name]=traceback.format_exc()
        finally:
            life.stop_owned()
            if getattr(f,'log',None) is not None:f.log.close()
            if hasattr(f,'event_descriptor'):os.close(f.event_descriptor)
            if hasattr(f,'temporary'):f.temporary.cleanup()
            assert not life.tree(os.getpid())
    report={'passed':all(cases.values()),'cases':cases,'launcher_sha256':sha(args.launcher),'frozen_fixture_sha256':sha(frozen),'diagnostics':diagnostics}
    args.output.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    if diagnostics:print(json.dumps(diagnostics));raise SystemExit(1)
    print('Passed seven catalog ownership, ordering, and fallback cases.')
if __name__=='__main__':main()
