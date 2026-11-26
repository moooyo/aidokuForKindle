#!/usr/bin/env python3
"""Add live QMP touch checks to a private copy of the stock startup harness."""

from pathlib import Path
import sys

source = Path(sys.argv[1]).resolve(strict=True)
original = source / "scripts/verify-firmware-emulator.py"
target = source / "scripts/verify-startup-fonts-qa.py"
text = original.read_text()


def insert_once(anchor, extra):
    global text
    assert text.count(anchor) == 1, anchor
    text = text.replace(anchor, extra + anchor)


insert_once('    def run_startup_case(self, helper: bool) -> Result:\n', '''    def require_owned_startup_app(self) -> None:
        launcher = shlex.quote(f"{self.guest_root}/launch.sh")
        binary = shlex.quote(f"{self.guest_root}/app/aidoku.native")
        self.guest(
            "set -eu; lock=/tmp/aidoku-launcher.lock; "
            'owner=$(cat "$lock/pid"); app=$(cat "$lock/application-pid"); '
            'case "$owner:$app" in :*|*:|*[!0-9:]*) exit 1 ;; esac; '
            f"tr '\\000' '\\n' </proc/$owner/cmdline | grep -Fx {launcher} >/dev/null; "
            f'test "$(readlink /proc/$app/exe)" = {binary}; '
            'test "$(cat "$lock/phase")" = active; '
            'case "$(cat "$lock/generation")" in 1:*) ;; *) exit 1 ;; esac'
        )

    def wait_dock(self, path: Path, tab: int, process: subprocess.Popen) -> None:
        width, height = self.native_size
        dock = 160 if self.arguments.profile == "scribe" else 144
        y = height - dock + 5
        active_x = width * (2 * tab + 1) // 10
        inactive_x = width * (9 if tab == 0 else 1) // 10
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline and process.poll() is None:
            self.qmp("screendump", str(path))
            with Image.open(path) as capture:
                image = capture.convert("L")
                active = image.crop((active_x - 8, y, active_x + 8, y + 2))
                inactive = image.crop((inactive_x - 8, y, inactive_x + 8, y + 2))
                if active.getextrema()[1] < 85 and inactive.getextrema()[0] > 170:
                    return
            time.sleep(0.15)
        raise AssertionError(f"QMP did not show active dock tab {tab} in the owned process")

''')
# Preserve escaped NUL and newline in the guest shell source.
text = text.replace("tr '\\000' '\\n' </proc/$owner/cmdline", "tr '\\\\000' '\\\\n' </proc/$owner/cmdline")
insert_once('                require(process.wait(timeout=self.arguments.duration_ms / 1000 + 15) == 0,\n', '''                self.require_owned_startup_app()
                self.wait_dock(frame, 0, process)
                changed = difference(stock, frame)
                settings_frame = self.output / f"{name}.settings-framebuffer.png"
                returned_frame = self.output / f"{name}.returned-framebuffer.png"
                width, height = self.logical_size
                self.touch("settings_nav", (width * .9, height - 33))
                self.wait_dock(settings_frame, 4, process)
                require(difference(frame, settings_frame) > .003, "Settings did not replace the Library scene")
                self.require_owned_startup_app()
                self.touch("library_nav", (width * .1, height - 33))
                self.wait_dock(returned_frame, 0, process)
                result.artifacts.extend(map(str, (settings_frame, returned_frame)))
''')
insert_once('            if helper and self.arguments.require_toolbar_snapshot:\n', '''            result.checks["native_library_settings_round_trip"] = True
''')
with target.open("x", encoding="utf-8") as file:
    compile(text, str(target), "exec")
    file.write(text)
print("Prepared non-demo Library + QMP Settings round-trip verifier")
