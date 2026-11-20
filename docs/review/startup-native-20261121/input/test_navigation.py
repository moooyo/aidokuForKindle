import gzip
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFont
from public_fixture import FixtureDisplay


ROOT = Path("/work")
os.environ.update(LC_ALL="C", OMP_THREAD_LIMIT="1")
assert os.environ.get("AIDOKU_XVFB_FIXTURE") == "1"
display = FixtureDisplay()
source = (ROOT / "navigation.sh").read_text()
source = source.replace("stage=/mnt/us/.aidoku-native-$label", "stage=$FIXTURE/stage")
source = source.replace("state=/var/tmp/aidoku-native-$label", "state=$FIXTURE/state")
source = source.replace('kit=$stage/input', 'kit=/work/host-ocr')
source = source.replace(' DISPLAY=:0', '')
source = source.replace('"$kit/lib/ld-linux-armhf.so.3"', 'qemu-arm -cpu cortex-a9 "$kit/lib/ld-linux-armhf.so.3"')
start, end = source.index('native() {'), source.index('probe() {')
source = source[:start] + 'native() { same "$state/owner" "$label" && same "$stage/owner" "$label"; }\n' + source[end:]
label = "bbbbbbbbbbbbbbbb"
font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 33)


def paint(active):
    img = Image.new("L", (1860, 2480), "white")
    draw = ImageDraw.Draw(img)
    if active != "unknown":
        draw.text((186, 2398), "Home", fill=0, font=font)
        draw.text((572, 2398), "Library", fill=0, font=font)
        left, right = (186, 275) if active == "home" else (572, 679)
        draw.rectangle((left, 2440, right, 2442), fill=0)
    display.paint(img)


def run(mode, accepted=True):
    result = subprocess.run(["sh", script, mode, label], capture_output=True, timeout=100)
    if (result.returncode == 0) != accepted:
        raise ValueError((mode, result.stdout, result.stderr))
    return result.stdout.decode().strip()


def transition(mode, source_tab, expected_tab):
    paint(source_tab)
    watcher = subprocess.Popen(["xev", "-root", "-event", "button"], stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, text=True)
    try:
        process = subprocess.Popen(["sh", script, mode, label], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        import select
        import time
        deadline = time.monotonic() + 55
        seen = b""
        while time.monotonic() < deadline:
            if not select.select([watcher.stdout], [], [], 1)[0]:
                if process.poll() is not None:
                    break
                continue
            seen += os.read(watcher.stdout.fileno(), 4096)
            if b"ButtonRelease" in seen:
                paint(expected_tab)
                break
        out, err = process.communicate(timeout=50)
        if b"ButtonRelease" not in seen or process.returncode:
            raise ValueError((mode, out, err))
    finally:
        watcher.terminate()
        watcher.wait(timeout=5)


try:
    with tempfile.TemporaryDirectory() as temp:
        base = Path(temp)
        os.environ["FIXTURE"] = str(base)
        for folder in ("stage", "state"):
            (base / folder).mkdir()
            (base / folder / "owner").write_text(label + "\n")
        script = base / "navigation.sh"
        script.write_text(source)
        raw = gzip.decompress(sys.stdin.buffer.read())
        assert len(raw) == 1872 * 2480
        img = Image.frombytes("L", (1860, 2480), raw, "raw", "L", 1872, 1)
        for name, view in (("normal", img), ("reversed", img.transpose(Image.Transpose.ROTATE_180))):
            blank = Image.new("L", img.size, 255)
            box = (0, 2340, 1860, 2480)
            blank.paste(view.crop(box), box)
            display.paint(blank)
            if name == "normal":
                assert run("inspect").split()[0] == "home"
            else:
                run("inspect", False)
        print("PASS offline captured bottom strip: upright Home only", flush=True)
        subprocess.run(["xdotool", "mousemove", "93", "122"], check=True)
        paint("unknown")
        run("library", False)
        assert not (base / "state/input-original").exists()
        print("PASS no navigation or memo from an unrecognized stock view", flush=True)
        transition("library", "home", "library")
        assert (base / "state/input-original").read_text().strip() == "home 93 122"
        assert run("inspect").startswith("library ")
        transition("restore", "library", "home")
        assert run("inspect") == "home 93 122"
        print("PASS ARM/QEMU stock tab pointer round trip and checked restoration", flush=True)
finally:
    display.close()
