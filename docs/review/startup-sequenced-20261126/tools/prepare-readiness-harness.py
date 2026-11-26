#!/usr/bin/env python3
"""Exercise metadata readiness without bypassing the first full-frame gate."""

from pathlib import Path
import sys

work = Path(sys.argv[1]).resolve(strict=True)
assert work.parent == Path('/root') and work.name.startswith('aidoku-first-screen-sequenced-')
harness = work / 'source/scripts/verify-startup-fonts-qa.py'
text = harness.read_text()


def replace(before, after):
    global text
    assert text.count(before) == 1, before
    text = text.replace(before, after)


replace('                       f"exec sh {root}/launch.sh --foreground")', '''                       + ("export AIDOKU_TEST_EARLY_METADATA=1; " if os.getenv("AIDOKU_TEST_EARLY_METADATA") == "1"
                          else "unset AIDOKU_TEST_EARLY_METADATA; ")
                       + f"exec sh {root}/launch.sh --foreground")''')
replace('            result.checks["native_library_settings_round_trip"] = True\n', '''            result.checks["native_library_settings_round_trip"] = True
            events = {event["name"]: event for event in trace["events"]}
            early = os.getenv("AIDOKU_TEST_EARLY_METADATA") == "1"
            require(("startup_early_metadata_admitted" in events) == early,
                    "The guest did not run the requested metadata scheduling mode")
            initial = events["startup_initial_frame_submitted"]["boottime_us"]
            metadata = events["application_prepared"]["boottime_us"]
            entered = events["startup_metadata_worker_launched"]["boottime_us"]
            require((entered < initial) == early, "Metadata launch did not follow the requested frame order")
            result.checks["readiness_probe"] = {
                "early": early,
                "worker_launched_relative_to_loading_send_us": entered - initial,
                "metadata_ready_relative_to_loading_send_us": metadata - initial,
                "metadata_available_by_loading_send": metadata <= initial,
            }
''')
# Require the packaged helper in both comparisons. The earlier helperless
# calibration refusals stay in their own report; this probe does not test them.
replace('''            self.guest(f"test ! -e {root}/app/aidoku-native-query")
            baseline = self.run_startup_case(False)
            self.results.append(baseline)
            if self.arguments.native_query and baseline.checks.get("desktop_restored"):
                destination = f"{self.guest_root}/app/aidoku-native-query"
                self.put(self.arguments.native_query, destination, "755")
                self.guest(f"cmp -s - {shlex.quote(destination)}", payload=self.arguments.native_query.read_bytes())
                self.results.append(self.run_startup_case(True))''', '''            require(self.arguments.native_query is not None, "The packaged query helper is required")
            destination = f"{self.guest_root}/app/aidoku-native-query"
            self.put(self.arguments.native_query, destination, "755")
            self.guest(f"cmp -s - {shlex.quote(destination)}", payload=self.arguments.native_query.read_bytes())
            self.results.append(self.run_startup_case(True))''')
replace('        required = 2 if self.arguments.native_query else 1', '        required = 1')
replace('        deadline = time.monotonic() + 6\n', '        deadline = time.monotonic() + 10\n')
# Stage the binary once in each firmware guest and reuse those exact bytes
# for both arms. Every call to run_startup_case creates a fresh data root.
replace('''            self.results.append(self.run_startup_case(True))''','''            modes = ("baseline", "early") if self.arguments.profile == "scribe" else ("early", "baseline")
            for mode in modes:
                os.environ["AIDOKU_TEST_EARLY_METADATA"] = "1" if mode == "early" else "0"
                original_output = self.output
                self.output = original_output / mode
                self.output.mkdir()
                try:
                    case = self.run_startup_case(True)
                    case.name = mode
                    self.results.append(case)
                finally:
                    self.output = original_output''')
replace('        required = 1\n', '        required = 2\n')
target = harness.with_name('verify-early-metadata-qa.py')
compile(text, str(target), 'exec')
with target.open('x') as out:
    out.write(text)

runner = (work / 'verify-firmware.sh').read_text()
anchor = '\nstatus=0\n'
assert runner.count(anchor) == 1
runner = runner[:runner.index(anchor) + 1] + '''status=0
python3 -I -B scripts/verify-early-metadata-qa.py --cases startup-library --profile "$profile" \\
    --firmware 5.20.1 --machine "$machine" --emulator-root "$root/eink-emulator" \\
    --guest-ssh "$root/$guest" --binary "$w/bin/direct-screen-probe" --native-query "$w/bin/aidoku-native-query" \\
    --output "$w/metadata-readiness-$profile-$suffix" --duration-ms 22000 || status=1
finish_guest || exit 1
exit "$status"
'''
with (work / 'verify-readiness.sh').open('x') as out:
    out.write(runner)
print('Prepared same-ELF firmware readiness comparisons in both launch orders.')
