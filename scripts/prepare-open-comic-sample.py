#!/usr/bin/env python3
"""Prepare an attributed, unmodified open-comic CBZ on the Linux worker."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

from PIL import Image


TITLE = "Perf06 Pepper & Carrot CN - Episode 01"
FILENAME = "Perf06-Pepper-and-Carrot-CN-E01.cbz"
BASE_URL = "https://www.peppercarrot.com/0_sources/ep01_Potion-of-Flight/hi-res/"
LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
SOURCE_PAGES = [
    "https://www.peppercarrot.com/cn/webcomic/ep01_Potion-of-Flight.html",
    "https://www.peppercarrot.com/cn/webcomic-sources/ep01_Potion-of-Flight__files.html",
    "https://www.peppercarrot.com/en/webcomic/ep01_Potion-of-Flight.html",
    "https://www.peppercarrot.com/en/webcomic-sources/ep01_Potion-of-Flight__files.html",
    "https://www.peppercarrot.com/en/documentation/120_License_best_practices.html",
    "https://www.davidrevoy.com/",
]
PINS = [
    (1, 2197391, "44de7390f1f056b9d1be168696b853db78d6fc79f5684dd2c9e422f59cbd831b"),
    (2, 2209540, "71ba31c38a91472b619f973e16452bdfb85c8b6cbdd1d0571436ecf71e981732"),
    (3, 1478103, "22cb05db2775ad1a984eb797f7c8e82e934b52c71134182e2b079c30e4f7fbdc"),
]
MAX_BOOK_BYTES = 32 * 1024 * 1024
MAX_PAGE_BYTES = 8 * 1024 * 1024


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_page(data: bytes, size: int, digest: str) -> None:
    if len(data) != size or sha256(data) != digest:
        raise ValueError("An official page differs from its pinned source identity")
    with Image.open(io.BytesIO(data)) as image:
        image.load()
        if image.format != "JPEG" or image.mode != "RGB" or image.size != (2481, 3503):
            raise ValueError("An official page has unexpected decoded geometry")


def source_page(directory: Path, number: int, size: int, digest: str) -> tuple[bytes, str]:
    filename = f"cn_Pepper-and-Carrot_by-David-Revoy_E01P{number:02}.jpg"
    url = BASE_URL + filename
    path = directory / filename
    if path.is_symlink():
        raise ValueError("The source cache cannot contain symlinks")
    if path.exists():
        with path.open("rb") as stream:
            data = stream.read(MAX_PAGE_BYTES + 1)
    else:
        request = urllib.request.Request(url, headers={
            "User-Agent": "Aidoku manual performance fixture preparation",
        })
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status != 200:
                raise ValueError("A full official image download must return HTTP 200")
            data = response.read(MAX_PAGE_BYTES + 1)
            declared = response.headers.get("Content-Length")
            if declared is not None and int(declared) != len(data):
                raise ValueError("The official image HTTP response is incomplete")
        verify_page(data, size, digest)
        with path.open("xb") as stream:
            stream.write(data)
    verify_page(data, size, digest)
    return data, url


def attribution(urls: list[str]) -> str:
    return (
        "Pepper&Carrot - Episode 1: The Potion of Flight\n"
        "Art and scenario: David Revoy.\n"
        "Chinese (Simplified) translation: Ran Zhuang.\n\n"
        "Hereva universe credits:\n"
        "Creation: David Revoy. Lead Maintainer: Craig Maloney.\n"
        "Writers: Craig Maloney, Nicolas Artance, Scribblemaniac, Valvin.\n"
        "Correctors: Alex Gryson, CGand, Hali, Marno van der Maas, Moini, Willem Sonke.\n\n"
        "License: Creative Commons Attribution 4.0 International (CC BY 4.0).\n"
        + LICENSE_URL + "\n\n"
        "Official source and attribution pages:\n" + "\n".join(SOURCE_PAGES)
        + "\n\nOriginal story page URLs:\n" + "\n".join(urls)
        + "\n\nImages unmodified. No crop, resize, recolor, redraw or re-encoding was performed.\n"
        "Only the container and metadata were prepared for manual performance testing.\n"
        "This CBZ contains the complete three-page story P01-P03. The source also offers\n"
        "a cover, header P00 and narrow footer P04; those are not inserted as story pages.\n"
        "The official credits are transcribed above and retained in this file and ComicInfo.xml.\n"
    )


def zip_member(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    entry.compress_type = zipfile.ZIP_STORED
    entry.create_system = 3
    entry.external_attr = 0o100644 << 16
    archive.writestr(entry, data)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    if os.name == "nt" or not sys.platform.startswith("linux"):
        parser.error("Generate and verify through ssh test-env on the Linux worker")
    root = args.root.resolve()
    output = root / "out" / FILENAME
    manifest_path = output.with_suffix(".json")
    if output.exists() or manifest_path.exists():
        parser.error("Use a fresh output root; existing books are never overwritten")
    directory = root / "sources" / "peppercarrot-ep01-cn"
    directory.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    pages = [source_page(directory, *pin) for pin in PINS]
    credits = attribution([url for _, url in pages])
    metadata = ET.Element("ComicInfo")
    fields = {
        "Title": TITLE, "Series": TITLE, "Number": "1", "PageCount": "3",
        "Writer": "David Revoy", "Translator": "Ran Zhuang", "LanguageISO": "zh-CN",
        "Web": SOURCE_PAGES[0], "Summary": credits,
    }
    for key, value in fields.items():
        ET.SubElement(metadata, key).text = value
    xml = ET.tostring(metadata, encoding="utf-8", xml_declaration=True)
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_STORED) as archive:
        for number, (data, _) in enumerate(pages, start=1):
            zip_member(archive, f"{number:03}.jpg", data)
        zip_member(archive, "ComicInfo.xml", xml)
        zip_member(archive, "ATTRIBUTION.txt", credits.encode("utf-8"))
    if output.stat().st_size >= MAX_BOOK_BYTES:
        raise ValueError("The complete comic exceeds the 32 MiB limit")
    records = []
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None or len(archive.infolist()) != 5:
            raise ValueError("The final CBZ failed CRC or member-count validation")
        if any(item.compress_type != zipfile.ZIP_STORED for item in archive.infolist()):
            raise ValueError("All CBZ members must use ZIP_STORED")
        for (number, size, digest), (_, url) in zip(PINS, pages):
            name = f"{number:03}.jpg"
            verify_page(archive.read(name), size, digest)
            records.append({"path": name, "source_url": url, "bytes": size,
                            "sha256": digest, "dimensions": [2481, 3503], "mode": "RGB",
                            "format": "JPEG", "full_decode_passed": True, "images_unmodified": True})
    report = {
        "schema_version": 1, "id": "Perf06", "title": TITLE, "filename": FILENAME,
        "bytes": output.stat().st_size, "sha256": sha256(output.read_bytes()), "pages": 3,
        "compression": "ZIP_STORED", "page_records": records,
        "copyright": {"art_and_scenario": "David Revoy", "translation": "Ran Zhuang",
                      "license": "CC BY 4.0", "license_url": LICENSE_URL,
                      "official_source_pages": SOURCE_PAGES, "images_unmodified": True,
                      "credits_preserved_in": ["ComicInfo.xml", "ATTRIBUTION.txt"]},
        "checks": {"zip_crc_passed": True, "full_decode_pages": 3,
                   "source_byte_identity_passed": True, "below_32_mib": True},
        "scope": "Three unmodified official Chinese story pages; cover, header and narrow footer excluded; not a network-download benchmark",
        "prepared_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    with manifest_path.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(json.dumps({key: report[key] for key in ("filename", "bytes", "sha256", "pages", "checks")}))


if __name__ == "__main__":
    main()
