"""Host-only audit of a frozen stock-Library XTEST benchmark; never contacts a device."""

import functools
import hashlib
import json
from pathlib import Path
import re
import subprocess
import types


CONTROLLER = '1553cdbdb5a410b8594e8f1d86e495de54494c598db8c5c9f3c7bbd7e5f969cd'
TARGET = '13fa7f18827f4abb2002f91bd9ba2a4864693fd9ef667c80bd5efad9b0e0caec'
GUARD = '9b237727763b87b3301076c8f7871d6a636d1163d3d7ac4dc0cb212b96ac56bf'
CLICK = '2df118aad6346f8835224cabe50e27d56ddfe97a65a60ac5ff99ea707198f3c8'
FIELDS = {'native_input', 'tile_controller_sha256', 'tile_targets_sha256'}
HEX = re.compile(r'[a-f0-9]{64}\Z')
CASE = re.compile(r'(baseline|candidate)-(warmup|qualify|[0-9]{2})\Z')
PANEL_BYTES = 1872 * 2480
TAP_KEYS = {'mode','clock','x','y','hold_requested_ms','down_before_us','down_after_us',
            'up_before_us','up_after_us','down_confirmed','up_sent','buttons_clear_after','ok'}


class Rejected(ValueError):
    pass


def need(condition, reason):
    if not condition:
        raise Rejected(reason)


def safe(fn):
    @functools.wraps(fn)
    def run(*a, **k):
        try:
            return fn(*a, **k)
        except Rejected:
            raise
        except (ValueError, KeyError, TypeError, IndexError, OSError, EOFError, subprocess.SubprocessError) as err:
            # Never include paths, recognition text, pixels or raw input records in public results.
            raise Rejected('malformed_tile_evidence') from None
    return run


def digest(blob):
    return hashlib.sha256(blob).hexdigest()


def raw(path, limit):
    need(path.is_file() and not path.is_symlink() and 0 <= path.stat().st_size <= limit, 'invalid_tile_file')
    data = path.read_bytes()
    need(len(data) <= limit, 'invalid_tile_file')
    return data


def loads(data):
    def unique(pairs):
        d = dict(pairs)
        need(len(d) == len(pairs), 'duplicate_tile_json_key')
        return d
    return json.loads(data, object_pairs_hook=unique)


def tile_module(root):
    # Only the independently reviewed controller can run its original host-side
    # ROI/OCR functions. Do not execute code authorized by the supplied plan alone.
    blob = raw(root/'tile_mode.py', 65536)
    need(digest(blob) == CONTROLLER, 'tile_controller_identity')
    mod = types.ModuleType('_pinned_r4_tile_controller')
    exec(compile(blob, '<reviewed-host-tile-controller>', 'exec'), mod.__dict__)
    need((mod.TARGET, mod.GUARD_SOURCE, mod.CLICK) == (TARGET, GUARD, CLICK), 'tile_controller_dependencies')
    for leaf, expected, limit in [('study.py', GUARD, 65536), ('target-plan.json', TARGET, 4096), ('guitap', CLICK, 65536)]:
        need(digest(raw(mod.FROZEN/leaf, limit)) == expected, 'tile_dependency_identity')
    return mod


def evidence_target(root, plan):
    mod = tile_module(root)
    provenance = raw(root/'tile-targets.json', 8192)
    need(plan['tile_controller_sha256'] == CONTROLLER and digest(provenance) == plan['tile_targets_sha256'],
         'tile_frozen_identity')
    original = mod.original_inputs()
    need(loads(provenance) == original and set(original) == {'library','home'}, 'wrong_stock_reference')
    # Route.__init__ reads the two private known-good captures and touches no device.
    # It checks the reference is an actual Library, distinct from Home, and a single
    # known Aidoku logo matches the expected reference tile.
    return mod, mod.Route(root, original)


@safe
def plan_audit(root, plan, payload_names):
    present = FIELDS & plan.keys()
    if not present:
        need('guitap' not in payload_names and not any((root/k).exists() or (root/k).is_symlink()
             for k in ('tile_mode.py','tile-targets.json')), 'uri_plan_with_tile_artifacts')
        return False
    need(present == FIELDS and plan['native_input'] == 'stock-library-XTEST' and
         plan['tile_controller_sha256'] == CONTROLLER and HEX.fullmatch(plan['tile_targets_sha256'])
         and 'guitap' in payload_names, 'invalid_stock_entry_plan')
    if isinstance(payload_names, dict):
        need(payload_names['guitap'] == CLICK, 'wrong_guitap_payload')
    # The outer checker verifies the caller-frozen plan + each authenticated
    # payload member digest. Enforce the stock branch of the on-worker sources.
    for leaf, key, required in (
            ('runner.py','runner_sha256',(b'ui.admit(remote)', b'ui.to_library(remote,case)',
             b'x,y,guard=ui.target(remote,case)', b"'run_case '+shlex.quote(case)+f' {x} {y} {guard}'",
             b'ui.restore(remote)', b"report['stock_home_pointer_restored']=True")),
            ('device.sh','device_script_sha256',(b'prepare_tile_case() {', b'run_case() {',
             b'[ "${current%% *}" = "$4" ]', b'export DISPLAY=:0; mark native_open_requested',
             b'"$STAGE/guitap" --tap "$2" "$3" >"$OUT/tap.json"'))):
        data = raw(root/leaf, 2 * 1024**2)
        need(digest(data) == plan[key] and all(data.count(s) == 1 for s in required), 'missing_frozen_tile_operation')
        if leaf == 'device.sh':
            need(b'lipc-set-prop com.lab126.appmgrd start file:///mnt/us/documents/Aidoku.sh' not in data,
                 'uri_dispatch_in_tile_device_program')
    evidence_target(root,plan)
    return True


def positive(value):
    return type(value) is int and value > 0


def point(x, y):
    need(type(x) is int and type(y) is int and 0 <= x < 1860 and 0 <= y < 2480, 'invalid_native_pointer')
    return x,y


def tap(d):
    need(isinstance(d, dict) and set(d) == TAP_KEYS and d['mode'] == 'tap' and d['clock'] == 'CLOCK_BOOTTIME'
         and type(d['hold_requested_ms']) is int and d['hold_requested_ms'] == 50 and
         all(d[k] is True for k in ('down_confirmed','up_sent','buttons_clear_after','ok')), 'stock_tap_not_verified')
    xy = point(d['x'], d['y'])
    ts = [d[k+'_us'] for k in ('down_before','down_after','up_before','up_after')]
    need(all(positive(t) for t in ts) and ts == sorted(ts) and ts[2]-ts[1] >= 50000, 'bad_stock_tap_clock')
    return xy, {'down': tuple(ts[:2]), 'up': tuple(ts[2:])}


@safe
def origin(d, shell_marks):
    # The retained shell markers floor /proc/uptime to a centisecond. The native
    # button timestamps are microsecond BOOTTIME bounds from the X server roundtrips.
    _, bounds = tap(loads(raw(d/'tap.json', 2048)))
    a, b = [shell_marks[k] for k in ('native_open_requested','launcher_bridge')]
    need(positive(a) and positive(b) and a <= bounds['down'][0] and bounds['up'][1] < b+10000,
         'stock_tap_not_bound_to_own_dispatch')
    return bounds


def inspect(d):
    need(isinstance(d,dict) and d['mode'] == 'inspect' and (d['width'],d['height']) == (1860,2480)
         and type(d['xtest_major']) is int and d['xtest_major'] >= 2 and type(d['xtest_minor']) is int
         and d['xtest_minor'] >= 0 and d['buttons_clear'] is True and d['target_has_child'] is None,
         'stock_inspection_unverified')
    return point(d['pointer_x'], d['pointer_y'])


@safe
def operations_audit(root, plan, cmd_names):
    need(plan.get('native_input') == 'stock-library-XTEST', 'not_a_tile_batch')
    mod, route = evidence_target(root, plan)
    cases = plan['cases']
    need(cases and len(cases) == len(set(cases)) and all(CASE.fullmatch(n) for n in cases), 'invalid_tile_cases')
    need(len(cmd_names) == len(set(cmd_names)), 'duplicate_tile_command')
    expected = []

    def take(name):
        i = len(expected)
        need(i < len(cmd_names) and cmd_names[i] == name, 'unexpected_tile_command_sequence')
        expected.append(name)
        return root/(name+'.stdout')

    def tab(name):
        image = route.h['grid'](mod.open_panel(raw(take(name),PANEL_BYTES)))
        home, library = bool(route.at_home(image)), bool(route.at_lib(image))
        need(home != library,'unrecognized_stock_tab')
        return ('home' if home else 'library'), image

    def navigation(name, xy):
        reached, _ = tap(loads(raw(take(name), 2048)))
        need(reached == xy, 'wrong_stock_tab_target')

    for k in ('before','create-stage','upload','prepare-device','original-program-backup'):
        take(k)
    need(tab('stock-original-home')[0] == 'home','not_original_home')
    old_pos = inspect(loads(raw(take('stock-initial-pointer'),2048)))
    saved = loads(raw(root/'stock-original-pointer.json',128))
    need(isinstance(saved,list) and len(saved)==2 and point(*saved)==old_pos, 'original_pointer_binding_mismatch')
    take('activate-bridge')
    last_up = 0
    for name in cases:
        start, _ = tab('stock-origin-'+name)
        if start == 'home':
            navigation('stock-tab-'+name, (630,2424))
            take('stock-tab-settle-'+name)
            need(tab('stock-library-'+name)[0] == 'library', 'stock_library_not_admitted')
        take('stage-case-'+name)
        origin_tab, frame = tab('stock-target-'+name)
        need(origin_tab == 'library', 'not_library_before_tap')
        target = route.h['locate'](frame, route.plan, route.lib)
        reached, times = tap(loads(raw(root/'outputs'/'out'/name/'tap.json', 2048)))
        need(reached == target and times['down'][0] > last_up, 'wrong_or_reused_tile_tap')
        last_up = times['up'][1]
        take('case-'+name)
    take('owned-stage-status')
    take('repair-launcher')
    final_tab, _ = tab('stock-return-view')
    if final_tab != 'home':
        navigation('stock-return-home',(230,2424))
        take('stock-home-settle')
    need(tab('stock-restored-home')[0] == 'home','stock_home_not_restored')
    restore = loads(raw(take('stock-restore-pointer'),2048))
    need(restore['mode'] == 'restore-pointer' and restore['buttons_clear'] is True and
         point(restore['x'],restore['y']) == old_pos, 'stock_pointer_not_restored')
    need(inspect(loads(raw(take('stock-final-pointer'),2048))) == old_pos, 'stock_pointer_final_mismatch')
    for k in ('collect','restore-light','after','remove-owned-stage'):
        take(k)
    need(len(cmd_names) == len(expected), 'unexpected_trailing_tile_call')
    return True
