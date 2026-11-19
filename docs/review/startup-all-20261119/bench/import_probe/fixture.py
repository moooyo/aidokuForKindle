"""Deterministic, redistributable grayscale CBZs for an isolated import experiment."""

import binascii
import hashlib
import io
import json
import struct
import zlib
import zipfile


BOOKS = 20
TARGETS = (1, 2, 3, 4)
SMALL = (96, 144)
LARGE = (1024, 1536)
STAMP = (2026, 1, 1, 0, 0, 0)
def sha(b):
    return hashlib.sha256(b).hexdigest()


def title(i):
    return f'Fixture S{i:02}'


def png(w, h, seed):
    def chunk(tag, b):
        return struct.pack('>I',len(b))+tag+b+struct.pack('>I',binascii.crc32(tag+b)&0xffffffff)
    tile = [bytes((((x//7)*23+(y//11)*41+((x+3*y+seed*7)%41)*3+seed*11)&255)
                  for x in range(128)) for y in range(64)]
    raw = b''.join(b'\0'+(tile[(y+seed)%64]*((w+127)//128))[:w] for y in range(h))
    return (b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',w,h,8,0,0,0,0))+
            chunk(b'IDAT', zlib.compress(raw, 6))+chunk(b'IEND',b''))


def bundle():
    files = {}; books = []
    for i in range(BOOKS):
        size = LARGE if i in TARGETS else SMALL
        first, second = png(*size,seed=2*i+1), png(*SMALL,seed=2*i+2)
        # Reverse the archive order deliberately: the importer must sort page 1 before page 2.
        contents = [('002.png',second),('001.png',first),('ComicInfo.xml',
                    (f'<ComicInfo><Title>{title(i)}</Title><Writer>Generated</Writer><Number>1</Number></ComicInfo>').encode())]
        out = io.BytesIO()
        with zipfile.ZipFile(out,'w') as archive:
            for name,data in contents:
                z = zipfile.ZipInfo(name, STAMP); z.create_system=3; z.external_attr=0o100644<<16
                archive.writestr(z,data,compress_type=zipfile.ZIP_STORED)
        name=f'S{i:02}.cbz'; files['comics/'+name]=out.getvalue()
        books.append({'i':i,'title':title(i),'file':name,'archive_sha256':sha(out.getvalue()),
                      'page_sha256':[sha(first),sha(second)],'cover_size':list(size)})
    record={'version':'synthetic-local-scribe-v1','targets':list(TARGETS),'books':books,
            'scope':'Twenty newly generated two-page local comics. Four initially offscreen covers are 1024x1536; all other pages are 96x144.'}
    files['fixture.json']=(json.dumps(record,sort_keys=True,indent=2)+'\n').encode()
    files['archives.sha256']=''.join(f'{b["archive_sha256"]}  {b["file"]}\n' for b in books).encode()
    return files
