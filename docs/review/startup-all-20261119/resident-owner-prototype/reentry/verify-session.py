#!/usr/bin/env python3
"""Private positive integration; no native handler, hardware or catalog service is impersonated."""
import array, json, os, secrets, select, socket, subprocess, sys, tempfile, time
from pathlib import Path

HERE=Path(__file__).resolve()

def check(ok,msg):
    if not ok: raise RuntimeError(msg)

def tx(sock,word,fd=None):
    control=[] if fd is None else [(socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array('i',[fd]))]
    sock.sendmsg([word.encode()],control)

def rx(sock,timeout=8):
    check(select.select([sock],[],[],timeout)[0],'No private process response')
    data,aux,flags,_=sock.recvmsg(16384,socket.CMSG_SPACE(8))
    check(data and not flags & (socket.MSG_TRUNC|socket.MSG_CTRUNC),'Incomplete private message')
    descriptors=[]
    for lev,kind,buf in aux:
        check((lev,kind)==(socket.SOL_SOCKET,socket.SCM_RIGHTS),'Unexpected control data')
        a=array.array('i');a.frombytes(buf[:len(buf)//a.itemsize*a.itemsize]);descriptors.extend(a)
    check(len(descriptors)<=1,'Too many descriptors')
    return data.decode(),descriptors[0] if descriptors else None

def output(sock,obj): tx(sock,json.dumps(obj,separators=(',',':')))

def request(sock,command,fd=None):
    tx(sock,command,fd);text,extra=rx(sock);check(extra is None,'Unexpected response descriptor');return json.loads(text)

def marker(root,name,body):
    stage=root/(name+'.fixture');stage.write_text(str(body)+'\n');os.replace(stage,root/name)

def identity(pid):
    proc=Path('/proc',str(pid));fields=(proc/'stat').read_text().rsplit(') ',1)[1].split();st=(proc/'exe').stat()
    return (pid,int(fields[1]),int(fields[2]),int(fields[3]),int(fields[19]),os.readlink(proc/'exe'),st.st_dev,st.st_ino)

def usage(pid):
    p=Path('/proc',str(pid));fields=(p/'stat').read_text().rsplit(') ',1)[1].split()
    rollup=(p/'smaps_rollup').read_text().splitlines()
    values={tag:int(next(line for line in rollup if line.startswith(tag+':')).split()[1]) for tag in ('Rss','Pss')}
    return int(fields[11])+int(fields[12]),values['Rss'],values['Pss']

def reader(args):
    gate,exe,cmd=args;gate=int(gate)
    check(os.read(gate,1)==b'G','No native reader release');os.close(gate)
    seen=os.environ.get('FIX_ENTRY_FD'); fds=() if seen is None else (int(seen),)
    return subprocess.run([exe,cmd],pass_fds=fds).returncode

def runner(args):
    exe,fd=args;return subprocess.run([exe,'gui',fd],pass_fds=(int(fd),)).returncode

def waiter(args):
    addr,role,*rest=args
    with socket.socket(socket.AF_UNIX) as s:
        s.connect(addr)
        if role=='second':return 0 if s.recv(1)==b'H' else 1
        guardian,workerfd,exe=rest
        return subprocess.run([guardian,'--',sys.executable,str(HERE),'worker',workerfd,exe],).returncode

def worker(args):
    controlfd,exe=args;control=socket.socket(socket.AF_UNIX,socket.SOCK_SEQPACKET);control.connect(controlfd);root=Path(os.environ['AIDOKU_LAUNCHER_DIR']);nonce=os.environ['AIDOKU_LAUNCH_NONCE']
    marker(root,'pid',os.getpid());marker(root,'nonce',nonce);marker(root,'generation','1:'+nonce);marker(root,'epoch',0);marker(root,'phase','starting')
    worker_name=os.readlink('/proc/self/exe');seq=0;participant=None;appside=None;firstfd=os.environ.get('AIDOKU_INITIAL_WAITER_FD')
    output(control,{'worker':os.getpid(),'guardian':os.getppid(),'initial_settle_channel':firstfd is not None})
    def probe(cmd,entryfd=None):
        nonlocal seq
        seq+=1;read,write=os.pipe();fds=[read];env=dict(os.environ)
        if entryfd is not None:env['FIX_ENTRY_FD']=str(entryfd);fds.append(entryfd)
        with subprocess.Popen([sys.executable,str(HERE),'reader',str(read),exe,cmd],stdout=subprocess.PIPE,env=env,pass_fds=fds,start_new_session=True) as proc:
            os.close(read);marker(root,'native-read-go',f'{proc.pid}:{seq}:{nonce}');os.write(write,b'G');os.close(write);data,_=proc.communicate(timeout=6);check(proc.returncode==0,'Reader did not return normally');return json.loads(data)
    try:
        while True:
            cmd,passed=rx(control,20)
            try:
                if cmd in ('cold','home','next','take'):res=probe(cmd,passed)
                elif cmd=='newgui':
                    appside,childside=socket.socketpair(socket.AF_UNIX,socket.SOCK_DGRAM);c=childside.fileno()
                    participant=subprocess.Popen([sys.executable,str(HERE),'runner',exe,str(c)],pass_fds=(c,));childside.close()
                    g=json.loads(rx(appside)[0]);marker(root,'application-pid',g['pid']);res={'gui':g['pid'],'runner':participant.pid}
                elif cmd.startswith('phase:'):marker(root,'phase',cmd.split(':',1)[1]);res={'ok':True}
                elif cmd.startswith('gui:'):res=request(appside,cmd.split(':',1)[1],passed)
                elif cmd=='settle-first':
                    check(firstfd is not None,'Missing real first-waiter settlement channel')
                    with socket.socket(fileno=int(firstfd)) as settle:check(settle.send(b'S')==1,'Could not settle first visit')
                    firstfd=None;res={'ok':True}
                elif cmd=='exit':
                    if appside:tx(appside,'quit')
                    if participant:check(participant.wait(timeout=5)==0,'GUI did not exit normally')
                    res={'ok':True};output(control,res);break
                else:raise ValueError(cmd)
                output(control,res)
            finally:
                if passed is not None:os.close(passed)
    finally:
        if appside:appside.close()
        if participant and participant.poll() is None:participant.terminate();participant.wait(timeout=4)
        control.close()
    return 0

def main(args):
    if args[0] in ('reader','runner','worker','waiter'):return globals()[args[0]](args[1:])
    combined=args[0]=='--with-slint'
    if combined:args=args[1:]
    guardian,exe,reportfile=map(Path,args);guardian=guardian.resolve();exe=exe.resolve();os.umask(0o077)
    import ctypes
    require_prctl=ctypes.CDLL(None,use_errno=True).prctl(36,1,0,0,0);check(require_prctl==0,'Could not reap privately adopted fixture process')
    with tempfile.TemporaryDirectory(prefix='aidoku-reopen-') as name:
        root=Path(name);listener=socket.socket(socket.AF_UNIX);listener.settimeout(4);listener.bind(str(root/'visits'));listener.listen(2)
        ctl_listener=socket.socket(socket.AF_UNIX,socket.SOCK_SEQPACKET);ctl_listener.settimeout(4);eno=str(root/'control');ctl_listener.bind(eno);ctl_listener.listen(1);env=dict(os.environ,AIDOKU_LAUNCHER_DIR=name,AIDOKU_LAUNCH_NONCE=secrets.token_hex(16),AIDOKU_LAUNCH_GENERATION='1',AIDOKU_LIBRARY_SESSIONS='1')
        wait1=subprocess.Popen([sys.executable,str(HERE),'waiter',str(root/'visits'),'first',str(guardian),str(eno),str(exe)],env=env)
        w1,_=listener.accept();ctrl,_=ctl_listener.accept();ctl_listener.close();w2=wait2=workerpid=guardpid=None;out={};quitted=False
        def run(cmd,entry=None):return request(ctrl,cmd,entry.fileno() if entry else None)
        def expect(name,v,expected=True,reason=None):
            check(v['ok'] is expected,f'{name}: {v}');out[name]=v
            if reason:check(reason in v['error'],f'{name}: unexpected error {v}')
            return v
        def phase(x):check(run('phase:'+x)=={'ok':True},'phase')
        try:
            msg=json.loads(rx(ctrl)[0]);workerpid,guardpid=msg['worker'],msg['guardian'];check(msg['initial_settle_channel'],'Missing guarded first visit');original=identity(workerpid)
            required=['power','mode','getvar','sensor','direction','getvar','direction','mode','power','finish']
            cold=expect('cold_writer',run('cold'));check(cold['observations']==required,'Incomplete initial observation');old=(root/'native-portrait-calibration').read_bytes()
            ids=run('newgui');gui=identity(ids['gui']);runner_id=identity(ids['runner']);check(gui[1]==runner_id[0] and runner_id[1]==workerpid,'Broken live ancestry')
            phase('active');first=expect('cold_gui',run('gui:initial'));check(first['value']['scanout']=='Up','Unexpected initial display direction')
            expect('cold_writer_refuses_live_gui',run('cold'),False,'follows application initialization')
            phase('library-closing');expect('closed',run('gui:close',w1));phase('library-restored');expect('home',run('home',w1))
            old_context=expect('old_context_cannot_rebind',run('gui:try-original'),False,'was relinquished');check(not old_context['observations'],'Old anchor reached native I/O');run('settle-first');check(wait1.wait(timeout=4)==0,'First document waiter did not retire');check(identity(workerpid)==original and identity(ids['gui'])==gui and identity(ids['runner'])==runner_id,'A retained full identity changed')
            out['independent_waiter_return']={'first_returned':True,'worker_complete_identity_unchanged':True,'runner_complete_identity_unchanged':True,'gui_complete_identity_unchanged':True,'stable_parent_alive':identity(guardpid)[0]==guardpid}
            if combined:
                before=usage(gui[0]);started=time.monotonic();time.sleep(.4);after=usage(gui[0]);elapsed=time.monotonic()-started
                out['host_fixture_idle']={'interval_ms':round(elapsed*1000), 'cpu_tick_delta':after[0]-before[0],
                    'rss_kib_before':before[1], 'rss_kib_after':after[1], 'pss_kib_before':before[2], 'pss_kib_after':after[2],
                    'scope':'Software test GUI on Linux. Not a full Aidoku or physical Kindle measurement.'}
            phase('library-preparing');stale=expect('departed_peer_rejected',run('next',w1),False);check(not stale['observations'],'Departed peer reached native I/O')
            wait2=subprocess.Popen([sys.executable,str(HERE),'waiter',str(root/'visits'),'second'],env=env);w2,_=listener.accept()
            check(wait2.pid!=wait1.pid,'Duplicate waiter');marker(root,'sleep-request','power transition in progress')
            sleep=expect('pending_sleep_rejected',run('next',w2),False,'power transition');check(not sleep['observations'],'Power transition reached native I/O');(root/'sleep-request').unlink()
            second=expect('new_writer',run('next',w2));check(second['observations']==required,'Second presentation did not repeat the full stock observation')
            prepared=expect('prepared_only_refused',run('gui:prepared',w2),False);check(not prepared['observations'],'Prepared candidate acquired native I/O')
            phase('library-acquired');expect('owner_takeover',run('take',w2));expect('commit_visible',run('gui:prepared',w2))
            applied=expect('new_gui_observation',run('gui:apply',w2));expected={'scanout':'Down','rotation':2,'pid':gui[0]};expected.update({'same_slint_window_repainted':True} if combined else {});check(applied['value']==expected and applied['observations']==['getvar','sensor'],'Retained GUI did not see the distinct fresh calibration')
            duplicate=expect('duplicate_takeover_rejected',run('take',w2),False);check((root/'native-portrait-calibration').read_bytes()==old and (root/'application-pid').read_text()==str(gui[0])+'\n','Original cold record or GUI identity was damaged')
            marker(root,'ordinary-exit',f'{gui[0]} {env["AIDOKU_LAUNCH_NONCE"]} 0');expired=expect('ordinary_exit_revokes',run('gui:observe'),False,'power transition');check(not expired['observations'],'Ordinary exit read native I/O')
            run('exit');quitted=True;w2.send(b'H');check(wait2.wait(timeout=4)==0,'Second waiter remained');out['original_files_unchanged']=True
            from signal import SIGTERM
            deadline=time.monotonic()+3
            while time.monotonic()<deadline:
                try:child,status=os.waitpid(guardpid,os.WNOHANG)
                except ChildProcessError:break
                if child:check(os.waitstatus_to_exitcode(status)==0,'Guardian failed');break
                time.sleep(.01)
            else:raise RuntimeError('Guardian did not clean up')
        finally:
            if not quitted:
                try:run('exit')
                except (OSError,ValueError,RuntimeError):pass
            listener.close();w1.close();ctrl.close()
            if w2:w2.close()
            for p in (wait1,wait2):
                if p and p.poll() is None:p.terminate();p.wait(timeout=5)
        result={'schema':1,'scope':'unprivileged Linux; real application process ownership, Rust publication and kernel peers; explicit typed synthetic native devices and catalog authorization; no firmware, hardware or timing certification','findings':out}
        if combined:
            result['scope']='Unprivileged Linux: one real Slint GUI and two rendered software frames, real kernel FD/mmap disposal, Linux waiters and actual publication/consumption logic. Native catalog admission, property values, drivers and restoration are test adapters. No Kindle timings or real input/panel evidence.'
        # Runtime peer PIDs are private bookkeeping; the report records only whether equality held.
        result['findings']['new_gui_observation']['value'].pop('pid');result['findings']['new_gui_observation']['value']['same_pid']=True
        reportfile.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n');print('Stable guardian, two waiters, one GUI, two complete calibration transactions: passed.')
    return 0
if __name__=='__main__':raise SystemExit(main(sys.argv[1:]))