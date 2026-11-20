import os
from pathlib import Path
import subprocess
import tempfile

from PIL import Image, ImageDraw, ImageFont
import artwork


ROOT = Path("/work")
assert os.environ.get("AIDOKU_XVFB_FIXTURE") == "1"
official = Image.open(ROOT / "aidoku-cover-v2.png").convert("L")
libs = ROOT / "host-ocr/lib"
qemu = ["qemu-arm", "-cpu", "cortex-a9", libs/"ld-linux-armhf.so.3", "--inhibit-cache", "--library-path", libs]
tile = official.resize((240, 320), Image.Resampling.LANCZOS)


def fixture(positions, image=tile):
    screenshot = Image.new("L", (1860,2480), "white")
    draw = ImageDraw.Draw(screenshot)
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 33)
    draw.text((186,2398),"Home",fill=0,font=font)
    draw.text((572,2398),"Library",fill=0,font=font)
    draw.rectangle((572,2440,679,2442),fill=0)
    for point in positions:
        screenshot.paste(image, point)
    return screenshot


def invoke(path, mode, accepted):
    result = subprocess.run([*qemu,ROOT/"fixture-input",mode,"artwork.guard"],cwd=path,
                            capture_output=True,timeout=10)
    if (result.returncode == 0) != accepted:
        raise ValueError((mode,result.stdout,result.stderr))
    return result.stdout.strip()


with tempfile.TemporaryDirectory() as name:
    directory = Path(name)
    one = fixture([(270,445)])
    matches = artwork.artwork(one,official)
    assert len(matches) == 1
    artwork.write_guard(one,matches[0][1],directory/"artwork.guard")
    two = fixture([(270,445),(1071,1185)])
    assert len(artwork.artwork(two,official)) == 2
    foreign = Image.new("L",official.size,255)
    foreign.paste(official.crop(artwork.BOXES[0]),artwork.BOXES[0][:2])
    assert not artwork.artwork(fixture([(270,445)],foreign.resize(tile.size)),official)
    print("PASS independent official mark, name and subtitle; duplicates recognized, wrong title refused",flush=True)
    for screen,ok in ((one,True), (fixture([(623,989)]),True), (two,False),(fixture([]),False)):
        (directory/"test.frame").write_bytes(screen.tobytes())
        answer = invoke(directory,"match",ok)
        if ok:
            assert answer==b"unique_library_artwork"
    edited = one.copy()
    edited.putpixel((610,2442),255)
    (directory/"test.frame").write_bytes(edited.tobytes())
    invoke(directory,"tap",False)
    (directory/"test.frame").write_bytes(two.tobytes())
    invoke(directory,"tap",False)
    mixed = one.copy()
    mixed.paste(official.resize((295,395),Image.Resampling.LANCZOS),(1055,1155))
    (directory/"test.frame").write_bytes(mixed.tobytes())
    invoke(directory,"tap",False)
    (directory/"test.frame").write_bytes(one.tobytes())
    times = list(map(int,invoke(directory,"tap",True).split()))
    assert 0<times[0]<=times[1]<=times[2]-50000<=times[3]-50000
    print("PASS ARM exact live match, moved icon, rejected ambiguous and changed views, timed 50ms XTEST down/up",flush=True)
    reject = subprocess.run([*qemu,ROOT/"tile-input","match","artwork.guard"],cwd=directory,capture_output=True,timeout=10)
    assert reject.returncode and b"refused tap_geometry" in reject.stderr
    print("PASS production ARM program cannot substitute a fixture for the hardware framebuffer",flush=True)
