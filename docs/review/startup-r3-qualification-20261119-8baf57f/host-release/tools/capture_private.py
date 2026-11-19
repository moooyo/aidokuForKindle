"""Keep exact physical snapshots private and recheck them after the device lease."""

import gzip
import hashlib
import io
import shlex
import tarfile
from pathlib import Path


HEADER = b'P6\n# bpp=8 rotation=3\n1860 2480\n255\n'
WIDTH, HEIGHT, BAR = 1860, 2480, 72
SIZE = len(HEADER) + WIDTH * HEIGHT * 3
MAX_GZIP = SIZE + 64 * 1024
SUFFIXES = ('.native.ppm.gz', '.sha', '.right.tsv')
NAMES = ('before','after','wake','baseline-default-library','baseline-default-wake','candidate-default-library','candidate-default-wake')
VAULT = '''case "$RUN" in ''|-*|*[!0-9a-z-]*) exit 1;; esac
test "${#RUN}" -le 48; VAULT=/var/tmp/aidoku-all-private-pixels-$RUN
vault_owned() { [ -d "$VAULT" ] && [ ! -L "$VAULT" ] && [ "$(readlink -f "$VAULT")" = "$VAULT" ] &&
    [ "$(stat -c '%u:%a' "$VAULT")" = 0:700 ] && [ -f "$VAULT/owner" ] && [ ! -L "$VAULT/owner" ] &&
    printf '%s\\n' "$RUN" | cmp -s - "$VAULT/owner"; }
'''


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(b):
    return hashlib.sha256(b).hexdigest()


def materialize(smoke):
    before = b'    right_toolbar "$PIXTMP/frame" "$1" || exit 1\n'
    after = before + VAULT.encode() + b'''    # The userstore can ignore Unix modes; raw frames stay in a private native directory.
    if [ ! -e "$VAULT" ] && [ ! -L "$VAULT" ]; then mkdir -m 700 "$VAULT" && new_line "$RUN" "$VAULT/owner" || exit 1; fi
    vault_owned || exit 1
    case "$1" in before.right.tsv|after.right.tsv|wake.right.tsv) label=${1%.right.tsv};;
      "${OUT:-/no-case}"/library.right.tsv|"${OUT:-/no-case}"/wake.right.tsv)
        case "${OUT##*/}" in baseline-default|candidate-default) ;; *) exit 1;; esac
        leaf=${1##*/}; label=${OUT##*/}-${leaf%.right.tsv};; *) exit 1;; esac
    stored=$VAULT/$label.native.ppm.gz
    absent "$stored" && (set -C; gzip -1c "$PIXTMP/frame" >"$stored") || exit 1
    regular "$stored" && [ "$(wc -c <"$stored")" -gt 0 ] && [ "$(wc -c <"$stored")" -le 13903972 ] || exit 1
'''
    require(smoke.count(before) == 1, 'Unexpected capture observer source')
    return smoke.replace(before, after, 1)


def collection_shell(aliases):
    """The caller supplies its owned staging cwd/RUN. Accept only fixed sources."""
    require(len(aliases)>0 and set(aliases)<=set(NAMES),'Unknown private image labels')
    code=VAULT+'vault_owned\n'
    files=[]
    for label,relative in aliases.items():
        parts=label.rsplit('-',1)
        require(relative==(label if len(parts)==1 else 'out/'+parts[0]+'/'+parts[1]),'Unexpected image artifact location')
        for suffix in SUFFIXES:
            name=label+suffix; files.append(shlex.quote(name))
            if suffix=='.native.ppm.gz':
                code+=f'test -f "$VAULT/{name}" && test ! -L "$VAULT/{name}"\n';continue
            src=shlex.quote(relative+suffix)
            code+=f'test -f {src} && test ! -L {src} && test "$(wc -c <{src})" -le 256\n'
            code+=f'test ! -L "$VAULT/{name}"\nif [ -e "$VAULT/{name}" ]; then cmp -s {src} "$VAULT/{name}"; else (set -C; cat {src} >"$VAULT/{name}"); fi\n'
    return code+'tar -cf - -C "$VAULT" '+' '.join(files)+'\n'


def cleanup_shell():
    leaves=' '.join(k+s for k in NAMES for s in SUFFIXES)
    return VAULT+'''if [ -e "$VAULT" ] || [ -L "$VAULT" ]; then
    vault_owned || exit 1
    for leaf in '''+leaves+'''; do
        test ! -L "$VAULT/$leaf" || exit 1
        if [ -e "$VAULT/$leaf" ]; then [ -f "$VAULT/$leaf" ] && rm -f "$VAULT/$leaf" || exit 1; fi
    done
    test "$(ls -A "$VAULT")" = owner && rm "$VAULT/owner" && rmdir "$VAULT" || exit 1
fi
'''


def physical(raw):
    require(len(raw) == SIZE and raw.startswith(HEADER), 'Unexpected native capture geometry or header')
    payload = memoryview(raw)[len(HEADER):]
    body = sha(payload[WIDTH*BAR*3:])
    left = WIDTH // 2
    region = b''.join(payload[3*(WIDTH*y+left):3*WIDTH*(y+1)] for y in range(BAR))
    channels = [region[i::3] for i in range(3)]
    extrema = [(min(c), max(c)) for c in channels]
    require(any(a != b for a,b in extrema), 'Physical right toolbar is uniform')
    right = (f'{left}\t0\t{WIDTH-left}\t{BAR}\t{len(region)}\t' +
             '\t'.join(str(v) for pair in extrema for v in pair) + '\t' + sha(region.hex().encode('ascii')) + '\n')
    return body.encode('ascii') + b'\n', right.encode('ascii')


def check_capture_archive(archive, labels=('before', 'after', 'wake'), known_case_records=None):
    """Return public checks without returning raw images, checksums or artwork."""
    archive = Path(archive)
    require(archive.is_file() and not archive.is_symlink() and archive.stat().st_size <= len(labels)*(MAX_GZIP+32768),
            'Invalid private snapshot archive')
    names = {label+s for label in labels for s in SUFFIXES}; files={}
    with tarfile.open(archive, 'r:') as tar:
        for entry in tar:
            name=entry.name
            require(name in names and name not in files and entry.isreg() and not entry.sparse and
                    0<entry.size<=(MAX_GZIP if name.endswith('.gz') else 256), 'Unexpected private snapshot tar member')
            stream=tar.extractfile(entry); blob=stream.read(entry.size+1)
            require(len(blob)==entry.size,'Truncated private snapshot member')
            files[name]=blob
    require(files.keys()==names,'Missing private physical capture')
    observations=[]
    for label in labels:
        with gzip.GzipFile(fileobj=io.BytesIO(files.pop(label+'.native.ppm.gz')),mode='rb') as image:
            raw=image.read(SIZE+1)
        p=physical(raw)
        require(p==(files[label+'.sha'],files[label+'.right.tsv']),'Private physical bytes differ from on-device checks')
        if known_case_records is not None:
            require(known_case_records.get(label)==p,'Native capture does not match the independent case evidence')
        observations.append(p)
    require(len(set(observations))==1,'Library or right toolbar differs between physical captures')
    return {'native_p6_count':len(labels),'width':WIDTH,'height':HEIGHT,'excluded_status_rows':BAR,
            'body_pixels_match':True,'right_toolbar_pixels_match':True,'right_toolbar_observable':True}
