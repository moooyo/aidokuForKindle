"""Optional stock-Library pointer transport for the isolated indexed A/B runner."""

import ast
import gzip
from hashlib import sha256
import io
import json
from pathlib import Path
import re
import shlex
import subprocess

import numpy as np
from PIL import Image


STUDY = Path('/root/aidoku-entry-study-20261119')
FROZEN = STUDY/'frozen-low'
GUARD_SOURCE='9b237727763b87b3301076c8f7871d6a636d1163d3d7ac4dc0cb212b96ac56bf'
TARGET='13fa7f18827f4abb2002f91bd9ba2a4864693fd9ef667c80bd5efad9b0e0caec'
CLICK='2df118aad6346f8835224cabe50e27d56ddfe97a65a60ac5ff99ea707198f3c8'
COUNT=1872*2480
HOME_HEADER=(130,267,576,332)
HOME_NAV=(175,2398,295,2455)
LIB_NAV=(550,2398,710,2455)
GUARDS='''owner; stable_entry; native_wait
[ "$(lipc-get-prop com.lab126.appmgrd activeApp)" = com.lab126.KPPMainApp ]
[ "$(lipc-get-prop -s com.lab126.powerd state)" = active ]
if pidof aidoku >/dev/null; then exit 1; fi
'''
PANEL='''[ "$(cat /sys/class/graphics/fb0/bits_per_pixel)" = 8 ]
[ "$(cat /sys/class/graphics/fb0/stride)" = 1872 ]
[ "$(cat /sys/class/graphics/fb0/pan)" = 0,0 ]
[ "$(cat /sys/class/graphics/fb0/rotate)" = 3 ]
dd if=/dev/fb0 bs=1872 count=2480 2>/dev/null | gzip -1 -c
'''
TARGET_ROWS=((233,142),(798,142))

def sha(b): return sha256(b).hexdigest()

def need(condition, why):
    if not condition: raise ValueError(why)

def bounded(path, limit, digest=None):
    path=Path(path); need(path.is_file() and not path.is_symlink() and 0<path.stat().st_size<=limit,'Unbound native UI reference')
    data=path.read_bytes(); need(not digest or sha(data)==digest,'Native UI reference changed'); return data

def helper_scope():
    source=bounded(FROZEN/'study.py',65536,GUARD_SOURCE)
    defs=('grid','crop','same','words','nav_label','locate')
    body=[n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name in defs]
    need(len(body)==len(defs),'Native UI guard helper missing')
    scope={'np':np,'Image':Image,'io':io,'subprocess':subprocess,'re':re,'need':need,'HOME_NAV':HOME_NAV,'LIB_NAV':LIB_NAV}
    exec(compile(ast.Module(body=body,type_ignores=[]),'<frozen-ui-guards>','exec'),scope)
    return scope

def open_panel(data):
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as gz: raw=gz.read(COUNT+1)
    need(len(raw)==COUNT,'Unexpected native framebuffer size'); return raw

def row_guard(raw):
    return sha(b''.join(raw[y*1872:(y+h)*1872] for y,h in TARGET_ROWS))

def original_inputs():
    plan=bounded(FROZEN/'target-plan.json',4096,TARGET)
    ref=Path(json.loads(plan)['reference']); home=ref.parent/'03-home-before-private.stdout'
    return {k:{'path':str(v),'sha256':sha(bounded(v,COUNT))} for k,v in {'home':home,'library':ref}.items()}

class Route:
    def __init__(self,batch,info):
        self.batch=batch; self.h=helper_scope()
        self.plan=json.loads(bounded(FROZEN/'target-plan.json',4096,TARGET))
        self.home,self.lib=[self.h['grid'](open_panel(bounded(info[k]['path'],COUNT,info[k]['sha256']))) for k in ('home','library')]
        need(self.at_home(self.home) and not self.at_home(self.lib) and self.at_lib(self.lib) and not self.at_lib(self.home),'Original stock views not distinct')
        x,y=self.h['locate'](self.lib,self.plan,self.lib); need((x,y)==(714,866),'Original Aidoku cover not unique')
        self.pointerfile=batch/'stock-original-pointer.json'

    def at_home(self,a): return self.h['same'](a,self.home,HOME_HEADER) and self.h['nav_label'](a,self.home,'library')
    def at_lib(self,a): return self.h['same'](a,self.lib,tuple(self.plan['library_header'])) and not self.h['same'](a,self.home,HOME_HEADER) and self.h['nav_label'](a,self.lib,'home')

    def panel(self,remote,label):
        raw=open_panel(bounded(remote(label,GUARDS+PANEL,timeout=22),COUNT)); return raw,self.h['grid'](raw)

    def pointer(self,remote,label,cli):
        return json.loads(bounded(remote(label,GUARDS+'same_hash "$STAGE/guitap" '+CLICK+'\nexport DISPLAY=:0\n"$STAGE/guitap" '+cli,timeout=18),4096))

    def admit(self,remote):
        _,img=self.panel(remote,'stock-original-home')
        need(self.at_home(img),'Start in the original stock Home tab, not another application or view')
        p=self.pointer(remote,'stock-initial-pointer','--inspect')
        need(p['buttons_clear'] is True and (p['width'],p['height'])==(1860,2480) and 0<=p['pointer_x']<1860 and 0<=p['pointer_y']<2480,'Unsafe original pointer')
        with self.pointerfile.open('x') as out: json.dump([p['pointer_x'],p['pointer_y']],out)

    def to_library(self,remote,case):
        _,img=self.panel(remote,'stock-origin-'+case)
        if self.at_lib(img): return
        need(self.at_home(img),'Cannot positively identify Home/Library tab before launch')
        r=self.pointer(remote,'stock-tab-'+case,'--tap 630 2424'); need(r.get('ok') is True,'Stock Library tab navigation uncertain')
        remote('stock-tab-settle-'+case,GUARDS+'sleep 2')
        _,img=self.panel(remote,'stock-library-'+case); need(self.at_lib(img),'Stock Library did not appear')

    def target(self,remote,case):
        raw,img=self.panel(remote,'stock-target-'+case)
        need(self.at_lib(img),'Not on the verified stock Library grid immediately before tap')
        x,y=self.h['locate'](img,self.plan,self.lib)
        return x,y,row_guard(raw)

    def restore(self,remote):
        if not self.pointerfile.exists(): return  # Preflight owned no native UI changes.
        pos=json.loads(bounded(self.pointerfile,128)); need(len(pos)==2 and all(type(v) is int for v in pos) and 0<=pos[0]<1860 and 0<=pos[1]<2480,'Bad original pointer')
        _,img=self.panel(remote,'stock-return-view')
        if not self.at_home(img):
            need(self.at_lib(img),'Unrecognised native view; no blind recovery tap')
            r=self.pointer(remote,'stock-return-home','--tap 230 2424'); need(r.get('ok') is True,'Home navigation uncertain')
            remote('stock-home-settle',GUARDS+'sleep 2')
        _,img=self.panel(remote,'stock-restored-home'); need(self.at_home(img),'Original Home tab not restored')
        self.pointer(remote,'stock-restore-pointer','--restore-pointer %d %d'%tuple(pos))
        r=self.pointer(remote,'stock-final-pointer','--inspect'); need([r['pointer_x'],r['pointer_y']]==pos and r['buttons_clear'],'Original pointer not restored')


def enhance(runner,dev,once):
    runner=once(runner,'    for name,(data,_) in files.items():', "    tile_src=Path(__file__).with_name('tile_mode.py').read_bytes()\n    import types\n    tile=types.ModuleType('frozen_tile'); exec(compile(tile_src,'<frozen-tile>','exec'),tile.__dict__)\n    add('guitap',tile.bounded(tile.FROZEN/'guitap',65536,tile.CLICK),0o755)\n    (batch/'tile_mode.py').write_bytes(tile_src)\n    private_targets=tile.original_inputs(); tile.Route(batch,private_targets)\n    (batch/'tile-targets.json').write_text(json.dumps(private_targets,sort_keys=True))\n    for name,(data,_) in files.items():")
    runner=once(runner,"    save(batch/'plan.json',plan);", "    plan.update(native_input='stock-library-XTEST',tile_controller_sha256=sha(tile_src),tile_targets_sha256=sha((batch/'tile-targets.json').read_bytes()))\n    save(batch/'plan.json',plan);")
    runner=once(runner,"    stage='/mnt/us/.'+run", "    import types\n    tile_src=(batch/'tile_mode.py').read_bytes(); private_targets=(batch/'tile-targets.json').read_bytes()\n    if sha(tile_src)!=plan['tile_controller_sha256'] or sha(private_targets)!=plan['tile_targets_sha256']: raise ValueError('Frozen stock Library inputs changed')\n    tile=types.ModuleType('frozen_tile'); exec(compile(tile_src,'<frozen-tile>','exec'),tile.__dict__)\n    ui=tile.Route(batch,json.loads(private_targets))\n    stage='/mnt/us/.'+run")
    runner=once(runner,"                remote('activate-bridge','swap_launcher')","                ui.admit(remote)\n                remote('activate-bridge','swap_launcher')")
    runner=once(runner,"                    remote('case-'+case,'run_case '+shlex.quote(case),timeout=80)", "                    ui.to_library(remote,case)\n                    remote('stage-case-'+case,'prepare_tile_case '+shlex.quote(case),timeout=60)\n                    x,y,guard=ui.target(remote,case)\n                    remote('case-'+case,'run_case '+shlex.quote(case)+f' {x} {y} {guard}',timeout=80)")
    runner=once(runner,"            report['entry_restored']=True", "            ui.restore(remote)\n            report['stock_home_pointer_restored']=True\n            report['entry_restored']=True")
    dev=once(dev, 'run_case() {','prepare_tile_case() {')
    where='''    mark native_open_requested
    lipc-set-prop com.lab126.appmgrd start file:///mnt/us/documents/Aidoku.sh'''
    repl='''    printf '%s\\n' "$offset" >"$OUT/tile-log-offset"
}
run_case() {
    C=$1; variant=${C%%-*}; role=${C#*-}; OUT=$STAGE/out/$C
    owner; stable_entry; native_wait; same_hash "$INSTALLED" "$BRIDGE_SHA"
    [ "$(cat "$STAGE/case")" = "$C" ] && nonlink "$OUT/tile-log-offset"
    [ "$(lipc-get-prop com.lab126.appmgrd activeApp)" = com.lab126.KPPMainApp ]
    savedlog=$STAGE/cases/$C/data/logs/launcher.log; offset=$(cat "$OUT/tile-log-offset")
    current=$({ dd if=/dev/fb0 bs=1872 skip=233 count=142 2>/dev/null && dd if=/dev/fb0 bs=1872 skip=798 count=142 2>/dev/null; } | sha256sum)
    [ "${current%% *}" = "$4" ]; same_hash "$STAGE/guitap" '''+CLICK+'''
    export DISPLAY=:0; mark native_open_requested
    "$STAGE/guitap" --tap "$2" "$3" >"$OUT/tap.json"'''
    dev=once(dev,where,repl); return runner,dev


if __name__=='__main__':
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        route=Route(Path(d),original_inputs()); seen=0
        samples=STUDY/'private/entry-time-aac5ee953d4a'
        for i in range(1,7):
            paths=list(samples.glob('*-library-before-'+str(i)+'.stdout')) or list(samples.glob('*-view-before-'+str(i)+'.stdout'))
            if i==1: paths=list(samples.glob('*-library-view-private.stdout'))
            need(len(paths)==1,'Expected museum visit'); raw=open_panel(bounded(paths[0],COUNT)); img=route.h['grid'](raw)
            need(route.at_lib(img) and len(row_guard(raw))==64,'Original stock Library not identified'); route.h['locate'](img,route.plan,route.lib); seen+=1
        print('Host-only replay of native tile target guards:',seen,'previous Library views, distinct Home and Library, unique matched title, row guard.')
