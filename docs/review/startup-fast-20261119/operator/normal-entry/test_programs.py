import io
from pathlib import Path
import tarfile
from tempfile import TemporaryDirectory
import unittest

from check_programs import checked_archive, instrument


LAUNCH = b'''DATA_DIR=$AIDOKU_HOME/data
ROOT_PID=$$
"$APP_DIR/aidoku" --kindle --device "$PROFILE" --data-dir "$DATA_DIR" --resume 3>&- &
"$APP_DIR/aidoku" --kindle --device "$PROFILE" --data-dir "$DATA_DIR" 3>&- &
'''


class ProductionInputs(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.original = b"old=1\n" + LAUNCH
        self.members = [("candidate/app/aidoku", b"measured"),
                        ("candidate/app/aidoku-startup-portrait", b"portrait"),
                        ("candidate/launch.sh", instrument(LAUNCH)),
                        ("baseline/launch.sh", instrument(self.original)),
                        ("control-launcher", self.original)]
        for name, value in (("aidoku-arm", b"measured"), ("aidoku-startup-portrait", b"portrait"), ("launch.sh", LAUNCH)):
            (self.root / name).write_bytes(value)

    def run_check(self, members):
        path = self.root / "payload.tar.gz"
        with tarfile.open(path, "w:gz") as archive:
            for name, value in members:
                info = tarfile.TarInfo(name)
                info.size, info.mode = len(value), 0o755
                archive.addfile(info, io.BytesIO(value))
        checked_archive(path, self.root, self.original, LAUNCH)

    def test_exact_programs_and_recomposed_launchers(self):
        self.run_check(self.members)

    def test_changed_elf_stale_launcher_or_ambiguous_input(self):
        for changed in ([("candidate/app/aidoku", b"altered!"), *self.members[1:]],
                        [*self.members, self.members[0]], self.members[:-1],
                        [*self.members[:2], ("candidate/launch.sh", LAUNCH), *self.members[3:]]):
            with self.subTest(changed=changed[0][0]), self.assertRaises(ValueError):
                self.run_check(changed)


if __name__ == "__main__":
    unittest.main()
