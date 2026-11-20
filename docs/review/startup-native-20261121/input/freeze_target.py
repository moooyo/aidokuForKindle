import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile


def verify_worker(root, batch):
    if (root != root.resolve(strict=True) or batch != batch.resolve(strict=True) or
            batch.parent != root / "batches" or not re.fullmatch(r"navigation-[0-9a-f]{16}",batch.name)):
        raise ValueError("use a closed navigation batch on the shared worker")
    receipt = json.loads((batch / "receipt.json").read_bytes())
    release = receipt["release"]
    if (not receipt["ok"] or release["state"]!="released" or not release["owner_and_token_checked"] or
            not release["native_restored_and_connections_closed"] or
            not release["lease_directory_absent_after_release"] or any(op["status"] != 0 for op in receipt["operations"])):
        raise ValueError("navigation batch did not fully close")
    fields = {}
    with tarfile.open(batch/"navigation.tar.gz", "r|gz") as archive:
        for member in archive:
            if (not member.isreg() or member.name not in ("out/navigation/library.frame","out/navigation/qualified") or
                    member.name in fields or not 1<=member.size<=4_613_000):
                raise ValueError("unexpected navigation observation")
            fields[member.name] = archive.extractfile(member).read()
    if fields["out/navigation/qualified"]!=b"navigation stock_restored\n":
        raise ValueError("navigation was not restored")
    return fields["out/navigation/library.frame"]


def classify(root):
    from PIL import Image
    import artwork
    from public_fixture import FixtureDisplay
    frame = sys.stdin.buffer.read(4_613_001)
    if len(frame)>4_613_000:
        raise ValueError("unbounded input")
    header,raw=frame.split(b"\n",1)
    if (not re.fullmatch(rb"AFR2 [13] 18(60|72) (2480|4960) (0|2480) [0-9]+ [0-9]+",header) or
            len(raw)!=artwork.WIDTH*artwork.HEIGHT):
        raise ValueError("incorrect stock capture")
    page=Image.frombytes("L",(artwork.WIDTH,artwork.HEIGHT),raw)
    display=FixtureDisplay()
    try:
        projected = Image.new("L",page.size,255)
        footer = (0,2340,artwork.WIDTH,artwork.HEIGHT)
        projected.paste(page.crop(footer),footer)
        display.paint(projected)
        lib=root/"host-ocr/lib"
        result=subprocess.run(["qemu-arm","-cpu","cortex-a9",lib/"ld-linux-armhf.so.3",
                               "--inhibit-cache","--library-path",lib,root/"host-ocr/native-target",
                               "navigation",root/"host-ocr/models"],check=True,capture_output=True,timeout=45,
                              env=dict(os.environ,OMP_THREAD_LIMIT="1"))
        nav=json.loads(result.stdout)
        home,library=nav["home"],nav["library"]
        if (nav["screen"]!=list(page.size) or not nav["buttons_clear"] or nav["title"]!={"count":0} or
                home["count"]!=1 or library["count"]!=1 or not 0<=home["underline_pct"]<=20 or
                not 60<=library["underline_pct"]<=100 or home["box"][2]>=library["box"][0]):
            raise ValueError("the independently recognized tab is not Library")
    finally:
        display.close()
    official=Image.open(root/"aidoku-cover-v2.png").convert("L")
    hits=artwork.artwork(page,official)
    if len(hits)!=1:
        raise ValueError(f"official cover count: {len(hits)}")
    with tempfile.TemporaryDirectory() as temp:
        path=Path(temp)/"artwork.guard"
        artwork.write_guard(page,hits[0][1],path)
        (path.parent/"test.frame").write_bytes(raw)
        arm=subprocess.run(["qemu-arm","-cpu","cortex-a9",lib/"ld-linux-armhf.so.3",
                            "--inhibit-cache","--library-path",lib,root/"fixture-input","match","artwork.guard"],
                           cwd=path.parent,check=True,capture_output=True,timeout=10)
        if arm.stdout!=b"unique_library_artwork\n":
            raise ValueError("actual Library does not pass the ARM selector")
        sys.stdout.buffer.write(path.read_bytes())


def freeze(root,batch):
    raw=verify_worker(root,batch)
    tool=root/"operator/input"
    result=subprocess.run(["/opt/kindle-toolchain/ubuntu-run",tool,"xvfb-run","-a","-s","-screen 0 1860x2480x24",
                           "python3","-B","freeze_target.py","--classify"],input=raw,stdout=subprocess.PIPE,timeout=60)
    if result.returncode or not 0<len(result.stdout)<2_000_000 or not result.stdout.startswith(b"AIDOTAP1"):
        raise ValueError("live Library/official artwork qualification did not pass; do not click")
    outputs = {"input/dispatch.sh":str(tool/"dispatch.sh"), "input/tile-input":str(tool/"tile-input"),
               "input/artwork.guard":str(batch/"artwork.guard")}
    files = json.loads((tool/"navigation-files.json").read_bytes()) | outputs
    def preserve(path, value):
        if path.exists():
            if path.is_symlink() or not path.is_file() or path.read_bytes()!=value:
                raise ValueError("retained target disagrees with newly confirmed pixels")
        else:
            with path.open("xb") as stream:
                stream.write(value)
    preserve(batch/"artwork.guard",result.stdout)
    preserve(batch/"tap-files.json",json.dumps(files,indent=2).encode())
    print(batch/"tap-files.json")


if __name__ == "__main__":
    os.umask(0o077)
    if sys.argv[1:]==["--classify"]:
        classify(Path("/work"))
    else:
        parser=argparse.ArgumentParser(description="Freeze the one verified official tile from a closed stock-navigation batch.")
        parser.add_argument("work",type=Path)
        parser.add_argument("batch",type=Path)
        args=parser.parse_args()
        freeze(args.work,args.batch)
