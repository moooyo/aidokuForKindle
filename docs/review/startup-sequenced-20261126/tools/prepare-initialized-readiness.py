#!/usr/bin/env python3
"""Probe two launches from identical, already initialized synthetic guest data."""

from pathlib import Path
import sys

work = Path(sys.argv[1]).resolve(strict=True)
assert work.parent == Path('/root') and work.name.startswith('aidoku-first-screen-sequenced-')
harness = work / 'source/scripts/verify-early-metadata-qa.py'
text = harness.read_text(encoding='utf-8')


def replace(before, after):
    global text
    assert text.count(before) == 1, before
    text = text.replace(before, after)


replace('            self.guest(f"cat > {root}/data/library.sqlite3", payload=startup_library_fixture())', '''            if self.readiness_mode == "seed":
                self.guest(f"cat > {root}/data/library.sqlite3", payload=startup_library_fixture())
            else:
                self.guest(f"set -eu; test -f {root}/initialized/library.sqlite3; "
                           f"test ! -L {root}/initialized; test ! -e {root}/initialized/logs; "
                           f"cp -a {root}/initialized/. {root}/data/; "
                           f"cmp -s {root}/initialized/library.sqlite3 {root}/data/library.sqlite3")''')
replace('''                "metadata_available_by_loading_send": metadata <= initial,
            }
''', '''                "metadata_available_by_loading_send": metadata <= initial,
                "initialization_seed": self.readiness_mode == "seed",
            }
            if self.readiness_mode == "seed":
                # Preserve the fixture only after the actual app has finished.
                # The fresh log and trace files for each visit are never copied.
                self.guest(f"set -eu; test ! -e /tmp/aidoku-launcher.lock; "
                           f"test ! -e {root}/data/library.sqlite3-journal; "
                           f"test ! -e {root}/data/library.sqlite3-wal; "
                           f"mkdir {root}/initialized; cp -a {root}/data/. {root}/initialized/; "
                           f"test -d {root}/initialized/logs && rm -rf {root}/initialized/logs; "
                           f"test ! -e {root}/initialized/logs; "
                           f"test -f {root}/initialized/cache/shared-v1/index.sqlite3")
                db_bytes = self.guest(f"cat {root}/initialized/library.sqlite3")
                require(len(db_bytes) <= 1024 * 1024, "Seed is not a bounded two-book fixture")
                with closing(sqlite3.connect(":memory:")) as seed:
                    seed.deserialize(db_bytes)
                    require(seed.execute("PRAGMA quick_check").fetchall() == [("ok",)]
                            and seed.execute("PRAGMA user_version").fetchone() == (1,)
                            and seed.execute("SELECT count(*) FROM books").fetchone() == (2,)
                            and seed.execute("SELECT count(*) FROM sources").fetchone()[0] > 0,
                            "Seed is not a healthy initialized synthetic Library")
''')
replace('''            for mode in modes:
                os.environ["AIDOKU_TEST_EARLY_METADATA"]''', '''            for mode in ("seed", *modes):
                self.readiness_mode = mode
                os.environ["AIDOKU_TEST_EARLY_METADATA"]''')
replace('''                finally:
                    self.output = original_output
        except Exception as failure:''', '''                finally:
                    self.output = original_output
                if not case.checks.get("desktop_restored") or (mode == "seed" and not case.passed):
                    break
        except Exception as failure:''')
# A stock compositor can still be repainting after a previous launch. The
# generic before/after pixel difference is not a unique stock identifier;
# rely instead on the owned app, verified Library projection, native Settings
# and return touches, accepted update, and restored services.
replace('            require(changed > 0.0005, "QMP did not observe a visible change from the stock framebuffer")\n', '')
replace('''                self.output = original_output / mode
''', '''                if mode != "seed":
                    # Wait for the previous return-to-stock repaint before
                    # capturing a new stock reference in this disposable guest.
                    time.sleep(3)
                self.output = original_output / mode
''')
replace('        required = 2\n', '        required = 3\n')
output = harness.with_name('verify-seeded-admission-qa.py')
compile(text, str(output), 'exec')
with output.open('x', encoding='utf-8') as out:
    out.write(text)
runner = (work / 'verify-readiness.sh').read_text(encoding='utf-8')
assert runner.count('scripts/verify-early-metadata-qa.py') == runner.count('metadata-readiness-$profile-$suffix') == 1
runner = runner.replace('scripts/verify-early-metadata-qa.py', 'scripts/verify-seeded-admission-qa.py')
runner = runner.replace('metadata-readiness-$profile-$suffix', 'metadata-initialized-$profile-$suffix')
with (work / 'verify-seeded-admission.sh').open('x', encoding='utf-8') as out:
    out.write(runner)
print('Prepared initialized Library readiness comparison; both arms start from the same owned synthetic seed.')
