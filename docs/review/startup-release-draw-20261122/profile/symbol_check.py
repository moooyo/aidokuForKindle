"""Verify DWARF package-path/inline evidence on known ELF symbols, before looking at any frame."""

import argparse
import json
from pathlib import Path
import struct
import subprocess

from report import lookup
from measure import elf_segments, need


def verify(binary):
    listing = subprocess.check_output(['nm', '-n', '-S', '--defined-only', binary], text=True, timeout=60)
    requests = {'text': ('sharedparley', 'i-slint-core-'), 'renderer': ('render_buffer_impl','i-slint-renderer-software-'),
                'glyph': ('scale_outline','/swash-'), 'shape': ('shape_text','/parley-')}
    selected = {}
    for line in listing.splitlines():
        fields = line.split(None,3)
        if len(fields) != 4 or fields[2] not in ('t','T'):
            continue
        address, size = (int(value,16) for value in fields[:2]); name = fields[3]
        for group, (needle, _) in requests.items():
            if needle in name and size >= 32:
                bucket = selected.setdefault(group, [])
                if len(bucket) < 24:
                    bucket.extend([hex(address), hex(address + size // 3), hex(address + 2 * size // 3)])
    all_addresses = set().union(*selected.values())
    frames = lookup(binary,all_addresses)
    check = {group: any(path_part in path for address in selected.get(group,[]) for _,path in frames[address])
             for group,(_,path_part) in requests.items()}
    need(check['text'] and check['renderer'], 'known_Slint_DWARF_source_not_available')
    headers = subprocess.check_output(['readelf', '-S', '-W', binary],text=True)
    need(all(name in headers for name in ('.symtab', '.debug_line', '.debug_info')), 'missing_ELF_debug_tables')
    need(elf_segments(binary), 'missing_executable_segment')
    return {'known_package_paths': check, 'debug_line_info_and_symbols': True, 'inline_chains_seen_in_static_check': any(len(v)>1 for v in frames.values())}


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('binary',type=Path); parser.add_argument('output',type=Path)
    args=parser.parse_args()
    with args.output.open('x') as stream:
        json.dump(verify(args.binary),stream,indent=2); stream.write('\n')
