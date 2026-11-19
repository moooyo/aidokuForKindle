#!/usr/bin/env python3
"""Build the disposable research-only Slint studio on the receipt backend."""

import argparse
from pathlib import Path
import runpy
import shutil
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    prepare = here / "prepare-backend.py"
    replace = runpy.run_path(str(prepare))["replace"]
    subprocess.run([sys.executable, str(prepare), str(args.source), str(args.destination)], check=True)
    out = args.destination.resolve()

    def update(name, original, edited):
        path = out / name
        path.write_text(replace(path.read_text(), original, edited))

    update("Cargo.toml", "\n[dependencies]\n", """
[features]
resident-studio = []

[[example]]
name = "resident-studio"
required-features = ["resident-studio"]

[dependencies]
""")
    (out / "examples").mkdir(exist_ok=True)
    shutil.copyfile(here / "studio-smoke.rs", out / "examples/resident-studio.rs")
    shutil.copyfile(here / "studio-session.rs", out / "src/resident_studio.rs")
    shutil.copyfile(here / "studio-observation.rs", out / "src/framebuffer/studio_observation.rs")

    optional = '#[cfg(feature = "resident-studio")]'
    fixture = '#[cfg(any(test, feature = "resident-studio"))]'
    update("src/lib.rs", "pub use resident_session::LibraryIoClosed;", f"""pub use resident_session::LibraryIoClosed;
{optional}
pub use crate::framebuffer::studio_observation::studio_observation;
{optional}
pub use crate::platform::resident_studio::{{StudioControl, start_studio}};""")
    update("src/platform.rs", 'mod resident_session_tests;', f"""mod resident_session_tests;

{optional}
#[path = "resident_studio.rs"]
pub(crate) mod resident_studio;""")
    platform = out / "src/platform.rs"
    text = platform.read_text()
    for locator in ["\ntype FixtureSession", "\n    fixture_session:", "\n            fixture_session:",
                    "\n            let end = match self.fixture_session"]:
        text = replace(text, "#[cfg(test)]" + locator, fixture + locator)
    text = replace(text, "#[cfg(not(test))]\n            let end", '#[cfg(not(any(test, feature = "resident-studio")))]\n            let end')
    platform.write_text(text)
    update("src/framebuffer/mod.rs", "#[cfg(test)]\nmod test_support;", f"""{fixture}
mod test_support;
{optional}
pub(crate) mod studio_observation;""")
    update("src/touch.rs", "#[cfg(test)]\n    pub(crate) fn idle_socket_for_session_test", fixture + "\n    pub(crate) fn idle_socket_for_session_test")


if __name__ == "__main__":
    main()
