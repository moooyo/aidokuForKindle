"""Trace complete launchers to a deliberately preowned private lock; isolate selector counters separately."""
import collections
import hashlib
import json
import os
import platform
import re
import shlex
import statistics
import subprocess
import tempfile
import time
from pathlib import Path

HERE=Path(__file__).resolve().parent
PAIR=HERE.parent/'pair'
ARTIFACTS={s:(PAIR/f'{s}.sh').read_bytes() for s in ('baseline','candidate')}
TRACE=HERE/'raw'
TRACE.mkdir(exist_ok=True,mode=0o700)
ROUTES={'library':['--library'],'kual':[],'worker':['--foreground'],'relative':['--library'],'custom':['--library'],'first-library':['--library'],'fresh-worker':['--foreground'],'crlf-library':['--library'],'absent-profile-library':['--library']}
SCENES={'missing':(None,None,'shell'),'empty':(b'',None,'shell'),'recommended':(b'portrait=shell\n',None,'shell'),'file-native':(b'portrait=native\n',None,'native'),'native-override':(None,'native','native'),'legacy-override':(b'portrait=native\n','legacy','shell'),'invalid-override':(b'portrait=native\n','unknown','shell'),'duplicate':(b'portrait=native\nportrait=native\n',None,'shell'),'nul':(b'portrait=native\x00\n',None,'shell'),'overlong':(b'portrait=native\n'+b'a'*1009,None,'shell'),'symlink-native':(b'portrait=native\n',None,'shell'),'missing-helper':(b'portrait=native\n',None,'shell'),'noncapture':(b'portrait=native\n','native','generic')}

def sha(data): return hashlib.sha256(data).hexdigest()

def trace(run, command, **kwargs):
    p=TRACE/(run+'.strace')
    cp=subprocess.run(['strace','-qq','-f','-yy','-s','400','-e','trace=%process,%file','-o',str(p),*command],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=12,**kwargs)
    spawns=[];executables=[];pending={};writes=set()
    root=str(kwargs['cwd'])
    for raw in p.read_text().splitlines():
        pid,line=raw.split(None,1)
        if line.endswith('<unfinished ...>'):
            assert pid not in pending;pending[pid]=line[:line.rindex('<unfinished ...>')];continue
        resuming=re.match(r'<\.\.\. \w+ resumed>(.*)',line)
        if resuming: line=pending.pop(pid)+resuming[1]
        if re.match(r'(?:fork|vfork|clone|clone3)\(',line) and re.search(r'= [1-9][0-9]*$',line): spawns.append(line)
        prog=re.match(r'(?:execve|execveat)\("([^\"]+)"',line)
        if prog and re.search(r'= 0$',line): executables.append(Path(prog[1]).name)
        if '-selector-' in run: continue
        mutation=re.match(r'(mkdir(?:at)?|open(?:at)?|creat|rename(?:at2?)?|unlink(?:at)?|rmdir|link(?:at)?|symlink(?:at)?|(?:f)?chmod(?:at)?|chown(?:at)?|utime(?:nsat)?|truncate)\(',line)
        if not mutation or not re.search(r'= [0-9]+(?:<.*>)?$',line): continue
        op=mutation[1]
        if op in ('open','openat'):
            if not re.search(r'O_(?:WRONLY|RDWR|CREAT|TRUNC|APPEND)',line):continue
            target=re.search(r'= [0-9]+<([^<>]+)(?:<.*>)?>$',line)
            assert target,(run,line)
            path=target[1]
            assert path in ('/dev/null',root+'/Aidoku home/data/logs/launcher.log'),(run,line)
            writes.add('open:'+path.replace(root,'{fixture}'))
        elif op=='mkdir':
            target=re.match(r'mkdir\("([^\"]+)"',line)
            assert target and target[1] in ('logs','imports'),(run,line)
            writes.add('mkdir:'+target[1])
        else: raise AssertionError((run,'unexpected shared write',line))
    assert not pending and executables and ('dash' in executables), (run,executables,pending)
    result={'forks':len(spawns),'execs':len(executables),'programs':dict(sorted(collections.Counter(executables).items())),'status':cp.returncode,'trace_sha256':sha(p.read_bytes())}
    if '-selector-' not in run: result['successful_write_targets']=sorted(writes)
    return cp,result

def setup(home, data, route):
    home.mkdir(); (home/'app').mkdir(); (home/'app'/'aidoku').write_text('#!/bin/sh\nexit 95\n'); (home/'app'/'aidoku').chmod(0o700)
    data.mkdir();
    if route!='absent-profile-library': (data/'device-profile').write_bytes(b'scribe\r\n' if route=='crlf-library' else b'scribe\n')
    if route not in ('first-library','fresh-worker'):
        for p in (data/'logs',data/'imports'): p.mkdir()

def launch(env_base, t, artifact, route):
    home=t/'Aidoku home';data=home/'data';setup(home,data,route)
    lock=t/'already-owned';lock.mkdir();(lock/'unchanged').write_text('preserved\n')
    launch=home/'launch.sh';launch.write_bytes(artifact); launch.chmod(0o700)
    env={**env_base,'AIDOKU_HOME':str(home),'AIDOKU_FRAMEBUFFER':'/dev/null','AIDOKU_LOCK_DIR':str(lock)}
    if route=='relative': arg='./'+home.name+'/launch.sh'
    elif route=='custom':
        arg=str(t/'custom entry.sh');Path(arg).symlink_to(launch)
    else: arg=str(launch)
    return home,lock,env,['/bin/dash',arg,*ROUTES[route]]

def original_selector_artifact(artifact):
    shell=artifact.decode();start=shell.index('startup_portrait_native() (\n');end=shell.index('\nstart_legacy_native_read() {',start)
    return shell[start:end]

def run():
    env={'PATH':'/usr/bin:/bin','LC_ALL':'C'}
    runs={};selector={};warm={'paired_ms':[],'warmup_pairs_excluded':2,'end':'Library waiter returns its worker error from the deliberately preowned lock; no display transaction occurs'}
    for route in ROUTES:
        outcomes={}
        for label,artifact in ARTIFACTS.items():
            with tempfile.TemporaryDirectory() as d:
                root=Path(d); home,lock,en,command=launch(env,root,artifact,route)
                cp,result=trace(label+'-'+route,command,env=en,cwd=root)
                why='Another Aidoku launcher owns the display lock.'
                assert cp.returncode==(0 if route=='kual' else 1), (label,route,cp.returncode,cp.stderr)
                assert why in cp.stderr.decode() or why in (home/'data/logs/launcher.log').read_text(),(label,route)
                assert [(p.name,p.read_text()) for p in lock.iterdir()]==[('unchanged','preserved\n')]
                assert all((home/'data'/name).is_dir() for name in ['logs','imports'])
                outcomes[label]=result
        runs[route]=outcomes
    for scene,(file,override,mode) in SCENES.items():
        pair={}
        for label,artifact in ARTIFACTS.items():
            with tempfile.TemporaryDirectory() as d:
                root=Path(d); app=root/'app'; data=root/'data'; lock=root/'lock'
                for p in [app,data,lock]: p.mkdir()
                if file is not None:
                    options=data/'startup-options'
                    if scene=='symlink-native': options.symlink_to(data/'target');options=data/'target'
                    options.write_bytes(file)
                helper=app/'aidoku-startup-portrait'
                if scene!='missing-helper': helper.write_text('#!/bin/sh\nprintf "native-helper\\n"\n'); helper.chmod(0o700)
                en={**env,'APP_DIR':str(app),'DATA_DIR':str(data),'LOCK_DIR':str(lock),'PROFILE':'scribe','ROOT_PID':'973','NATIVE_READ_SEQUENCE':'0'}
                if override is not None:en['AIDOKU_STARTUP_PORTRAIT']=override
                entry=root/'selector.sh'
                entry.write_text('set -eu\n'+original_selector_artifact(artifact)+'''\nstart_legacy_native_read() { printf 'shell-helper\\n' >"$LOCK_DIR/native-read-output" & }
finish_native_read() { wait "$NATIVE_READ_PID"; }
native_read_command "$APP_DIR/aidoku" --kindle --device "$PROFILE" "$1" --capture-sleep-capability
''')
                arg='--capture-native-portrait' if scene!='noncapture' else '--authorize-native-portrait-resume'
                cp,result=trace(label+'-selector-'+scene,['/bin/dash',str(entry),arg],env=en,cwd=root)
                assert cp.returncode==0,(scene,label,cp.stderr)
                assert (('supervisor: '+mode+'.').encode() in cp.stderr if mode!='generic' else b'Startup portrait supervisor:' not in cp.stderr), (scene,cp.stderr)
                assert (lock/'native-read-output').read_text()==('native-helper\n' if mode=='native' else 'shell-helper\n')
                result['mode']=mode;pair[label]=result
        selector[scene]=pair
    with tempfile.TemporaryDirectory() as d:
        root=Path(d);home,lock,en,cmd=launch(env,root,ARTIFACTS['baseline'],'library');entry=home/'launch.sh'
        with open(os.devnull,'wb') as sink:
            for i in range(32):
                pair={}
                labels=('baseline','candidate') if i%2==0 else ('candidate','baseline')
                for label in labels:
                    entry.write_bytes(ARTIFACTS[label]);(home/'data/logs/launcher.log').write_text('')
                    before=time.perf_counter_ns();cp=subprocess.run(cmd,cwd=root,env=en,stdout=sink,stderr=sink);elapsed=(time.perf_counter_ns()-before)/1e6
                    assert cp.returncode==1 and 'Another Aidoku launcher owns' in (home/'data/logs/launcher.log').read_text()
                    pair[label]=round(elapsed,4)
                pair['baseline_minus_candidate_ms']=round(pair['baseline']-pair['candidate'],4)
                if i>=2:warm['paired_ms'].append(pair)
    for label in ['baseline','candidate','baseline_minus_candidate_ms']: warm[label+'_median_ms']=round(statistics.median(p[label] for p in warm['paired_ms']),4)
    warm['candidate_faster']=sum(p['baseline_minus_candidate_ms']>0 for p in warm['paired_ms'])
    def delta(result,field): return result['baseline'][field]-result['candidate'][field]
    for key in ['library','kual']:
        assert delta(runs[key],'execs')==8 and delta(runs[key],'forks')>=8,(key,runs[key])
    assert delta(runs['worker'],'execs')==4 and delta(runs['first-library'],'execs')==7
    assert delta(selector['missing'],'forks')==1 and delta(selector['empty'],'forks')>=1 and delta(selector['recommended'],'forks')==0
    report={'schema':1,'result':'pass','environment':{'host_arch':platform.machine(),'shell':os.path.realpath('/bin/dash'), 'strace_version':subprocess.check_output(['strace','--version'],text=True).splitlines()[0]},'source_sha256':{k:sha(v) for k,v in ARTIFACTS.items()},'scope':'Admission uses unmodified, complete launcher scripts and a preowned private lock. The selector uses verbatim functions up to the dispatch boundary with both leaf runners fixed, independently of admission. Counts include the initial dash exec. The timing endpoint is not Kindle readiness.','admission':runs,'selector':selector,'untraced_timing':warm,'driver_sha256':sha(Path(__file__).read_bytes())}
    out=HERE/'results.json';out.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    for label in ['library','worker','relative','first-library']:print(label,[(k,v['forks'],v['execs'],v['programs']) for k,v in runs[label].items()])
    print('selector missing',selector['missing'],'empty',selector['empty']);print('untraced', {k:v for k,v in warm.items() if k!='paired_ms'});print(out,sha(out.read_bytes()))
if __name__=='__main__':run()
