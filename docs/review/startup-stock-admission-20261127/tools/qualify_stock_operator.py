#!/usr/bin/env python3
"""Publish the strictly off-device qualification for this exact stock plan."""

import json
from pathlib import Path
import re
import runpy


HERE = Path(__file__).resolve().parent


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def qualify():
    tools = runpy.run_path(str(HERE/'prepare_stock_generation.py'))
    host = runpy.run_path(str(HERE/'run_stock_generation.py'))
    require(HERE.parent == Path('/root') and HERE.name.startswith('aidoku-stock-generation-20261127.') and
            not HERE.stat().st_mode & 0o077, 'wrong_private_workspace')
    counts = {'phase':4, 'recovery':46, 'sealing':3, 'transport':4, 'native':3, 'firmware-timeout':3, 'admission':7}
    for name, count in counts.items():
        log = (HERE/f'logs/{name}.log').read_text()
        require(log.splitlines()[-1] == 'OK' and re.search(rf'^Ran {count} tests? in ', log, re.M),
                'fixture_suite_not_complete')
    require(not any(HERE.glob('attempt-*')) and not any((HERE/p).exists() or (HERE/p).is_symlink() for p in
                ('measure','fresh-artwork.guard','locator-qualified.json','locator-sent.json','measure-sent.json')), 'live_plan_already_consumed')
    for kind, source in (('locator','locator/device.sh'),('measure','measure-template.sh')):
        original = tools['plain'](tools['private_path']('stock_templates')/source, 65536)
        fresh = tools['make_device'](original,kind)
        require(fresh == (HERE/f'{kind}-template.sh').read_bytes(), 'generated_template_changed_after_validation')
    _, wire, space = host['check_plan'](HERE,'locator')
    preflight = json.loads((HERE/'logs/preflight.log').read_bytes())
    require(preflight['source'] == tools['SOURCE'] and preflight['operator'] == tools['OPERATOR']
            and preflight['kind'] == 'locator' and preflight['host_preflight'] is True and preflight['physical_access'] is False
            and preflight['lease_acquired'] is False and preflight['shared_operation_lock_module_available'] is True
            and preflight['framed_bytes'] == wire.stat().st_size and preflight['space_kib'] == space, 'locator_not_preflighted')
    firmware = json.loads((tools['private_path']('generation_lab')/'firmware-qualified.json').read_bytes())
    require(firmware['source'] == tools['SOURCE'] and firmware['diagnostic'] == tools['DIAGNOSTIC'] and
            {(row['profile'],row['guest_stopped']) for row in firmware['profiles']} == {('scribe',True),('pw6',True)},
            'wrong_prerequisite_firmware_qualification')
    require(host['OPERATOR'] == tools['OPERATOR'], 'wrong_native_restoration_protocol')
    result = {'source': tools['SOURCE'], 'diagnostic': tools['DIAGNOSTIC'], 'operator': tools['OPERATOR'],
              'physical_access': False, 'physical_lease_acquired': False, 'locator_sealed_and_preflighted': True,
              'locator_requires_original_library_tab': True, 'locator_has_no_input_or_mount': True,
              'both_integrated_device_variants_verified': True, 'linux_disconnection_fixtures_passed': True,
              'guarded_arm_home_to_library_recovery_verified': True,
              'firmware_timeout_abi_profiles': ['scribe-5.19.5', 'scribe-5.20.1', 'pw6-5.20.1'],
              'scribe_5195_direct_kill_verified': True,
              'hard_timeout_closeout_fixture_passed': True,
              'firmware_watchdog_journal_fd_scope_verified': True,
              'bounded_busy_finalization_fixture_passed': True,
              'shared_operation_lock_scope_verified': True,
              'host_close_timeout_exact_readback_and_verified_release': True,
              'host_close_timeout_without_exact_readback_does_not_release': True,
              'read_only_admission_fixtures_passed': True,
              'bound_reserve_refusal_before_journal_releases_untouched': True,
              'unknown_reserve_disposition_keeps_ownership': True,
              'timed_out_admission_reply_never_authorizes_reserve': True,
              'ready_dispatch_uses_existing_atomic_lease': True,
              'busy_dispatch_opens_no_device_connection': True,
              'minimum_dispatch_seconds': {kind:host['minimum_visit_seconds'](kind) for kind in host['KINDS']},
              'shared_queue_registration_written': False,
              'historical_physical_admission_failure_predicate_known': False,
              'accepted_physical_timing_samples': 0,
              'targeted_checks': counts, 'total_checks': sum(counts.values()), 'locator_bytes': wire.stat().st_size,
              'locator_space_kib': space, 'real_measure_payload_staged': False, 'historical_frame_used_as_live_guard': False,
              'prior_firmware_scope': 'Exact launcher probe and functional managed entry, not this physical operator'}
    host['save'](HERE/host['QUALIFICATION'], result)
    return result


if __name__ == '__main__':
    print(json.dumps(qualify(), indent=2))
