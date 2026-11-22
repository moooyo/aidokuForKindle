"""Prepare only the independent one-line Library paper elimination in a disposable copy."""

from pathlib import Path
import sys


def without_second_paper(original):
    newline = b'\r\n' if b'\r\n' in original else b'\n'
    block = newline.join([b'export component LibraryPage inherits Rectangle {', b'    background: Theme.paper;', b'    clip: true;'])
    replacement = newline.join([b'export component LibraryPage inherits Rectangle {', b'    clip: true;'])
    if original.count(block) != 1:
        raise ValueError('Library root does not have the approved duplicate paper layer')
    return original.replace(block, replacement)


if __name__ == '__main__':
    if sys.argv[1:] != ['apply-in-disposable-workspace']:
        sys.exit('Explicitly select the disposable candidate only after freezing stock')
    root = Path('/work/source')
    if root.resolve() != root or not (root.parent / 'programs/stock').is_file():
        raise ValueError('A disposable worker build with a frozen control is required')
    source = root / 'ui/library.slint'
    source.write_bytes(without_second_paper(source.read_bytes()))
