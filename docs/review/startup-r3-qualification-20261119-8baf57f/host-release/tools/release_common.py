"""Frozen 2026-11-18 installed identities and shared read-only acceptance helpers."""

import hashlib
import json
import re
from pathlib import Path
from types import ModuleType


HERE=Path(__file__).resolve().parent
ROOT=Path('/root/aidoku-all-release-20261119')
PAST=Path('/root/aidoku-next-promotion-20261118/tools')
PREVIOUS_PACKAGE=PAST.parent/'status-release-inputs/package'
PACKAGED_ZIP='bcc625b919936eef55eb496c16cce3738cd8c1dede12c5e82e182514c5b8ee29'
PACKAGED_KPM='1670ba5a1f166cd21bcfd5da9d2364b2d6d9b089e8bb222bf0bbf593df054af8'
APP,LAUNCH,EXTRA='aidoku/app/aidoku','aidoku/launch.sh','aidoku/app/aidoku-startup-portrait'
QUERY,JPEG='aidoku/app/aidoku-native-query','aidoku/app/kindle-jpeg-worker'
PREVIOUS='62de2771891b5bf8f720572c4544a3a732d619fa95131111cb00bccb6e0bc612'
RETIRED='d9be7683b614e97a84d7183d25649371bae5d2533f91edd2fede16b0fdc650f6'
BASE='9d92dc4cd01005a6ba2f48fe61786e5df5570c43feb5709fe3159bb164807d0f'
LAUNCH_SHA='10432125b74ab5b69c8a0ec54a34abd5a74dc4a3cc9ed9b9d5cc8d03bbd75296'
QUERY_SHA='84c2f27d8233d8695001aa86d880e29454fe188deb9884aca1c6694c82a5887d'
JPEG_SHA='1b41abcab8083ef70d3cb76ad16303d8b7934c382c4e49927f9789d11a7abf41'
DEPLOYED={APP:BASE,LAUNCH:LAUNCH_SHA,EXTRA:'absent'}
REG='/var/local/kindle-catalog-entry/v1-2db100d0bcb60a76571cd8de96bed7ea6c016e6ab73e388a885f692955cacd86/aidoku-library-dispatch'
FEATURES={'supervisor','prewarm','grace','layout','resident','import'}
PIN={
    'run.py':'56863607d2b4c83b680b12a5a4cce6b6f1c79e4dd181946aad66030ba4838885',
    'transaction.sh':'bf79367683c1dd17e2a5b57e330bf86a7ea8e4b8873a01c83848b7715dd7e0ee',
    'smoke.sh':'7634e54d3100fd13a5903249c40d8adf27d415822b59b6d1385a082287fdc501',
    'default_lifecycle.py':'6e2b9b1bf4acb86a39e466ade98ef6b3da073ed35a1d7c3faf548159ff403a1e',
    'lifecycle_device.sh':'060104428df0bf5bbe89024e4e421b4002dfe3224dab6ee566dfc81dc175e5b5',
    'analyze_lifecycle.py':'01b34db824f64102a3dd8573f97004a312649e642269b5670d364c2808684110',
    'prepare.py':'adcced81a18e701b8b3191481de271b6ed92f7d2bbbe1fc816e0f09a78cfcbff',
}


def require(value, message):
    if not value: raise ValueError(message)


def sha(content): return hashlib.sha256(content).hexdigest()


def canonical(obj): return json.dumps(obj,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode('ascii')


def raw(path, limit=96*1024*1024):
    require(path.is_absolute() and path.resolve(strict=True)==path and path.is_file() and path.stat().st_size<=limit,
            'Invalid frozen input: '+path.name)
    return path.read_bytes()


def exact(path, digest, limit=96*1024*1024):
    content=raw(path,limit); require(valid_sha(digest) and sha(content)==digest,'Input identity differs: '+path.name); return content


def valid_sha(v): return isinstance(v,str) and re.fullmatch('[a-f0-9]{64}',v) is not None


def unique_json(content):
    def unpack(pairs):
        value=dict(pairs); require(len(value)==len(pairs),'Duplicate JSON key'); return value
    return json.loads(content,object_pairs_hook=unpack)


def historical(name): return exact(PAST/name,PIN[name],4*1024*1024)


def once(source, original, replacement):
    require(source.count(original)==1,'Historical tool anchor changed: '+str(original)[:80]); return source.replace(original,replacement,1)


def module(name,source,filename):
    result=ModuleType(name);result.__file__=str(filename)
    exec(compile(source,str(filename),'exec'),result.__dict__); return result


def validate_policy(features):
    require(isinstance(features,dict) and set(features)==FEATURES and
            all(v in ('default_on','default_off_optional') for v in features.values()),'Unknown or missing startup feature policy')
    return sha(canonical(features))


def installed_reference():
    plan=unique_json(exact(PREVIOUS_PACKAGE/'plan.json','c9032b50f8a7643d8eb8fb6a8f5382ef358130c4b34b976743ecd0b824b986f0'))
    require(plan['changes']==[{'path':APP,'old':PREVIOUS,'new':BASE,'file':'delta/'+APP}] and plan['catalog_registry']==REG,
            'Historical installation has different program identities')
    verification=unique_json(raw(PAST.parent/'private/promote-f2889bdb575f/report.json'))
    require(all(verification.get(k) is True for k in ('success','qualified','lease_released','native_and_lighting_restored',
                'original_shared_registration_preserved','installed_default_document','same_process_short_sleep_wake')),
            'The current rollback target was not installed and qualified')


# A keeper uses a distinct argv[0] but the exact same executable. A bare pidof
# check is insufficient to identify every such process on supported BusyBox.
NO_PROGRAM='''no_program_image() (
    set +f
    for link in /proc/[0-9]*/exe; do
        target=$(readlink "$link" 2>/dev/null) || continue
        for expected do
            case "$target" in "$expected"|"$expected (deleted)") return 1;; esac
        done
    done
)
'''


DATA_SUFFIX='''
DB=/mnt/us/aidoku/data/library.sqlite3
test ! -L "$DB" && test -f "$DB"
queued=$(sqlite3 -batch -bail -readonly -cmd '.timeout 1500' "$DB" "SELECT count(*) FROM downloads WHERE state IN ('queued','downloading');")
test "$queued" = 0
# Classify probes by a stable logical projection. The normal application may
# update its own navigation checkpoint and access log during a real open.
# No raw rows enter the transport, shell variables, public reports or files.
protected_library=$( { { if sqlite3 -batch -bail -readonly -cmd '.timeout 1500' "$DB" <<'PROTECTED_SQL'
PRAGMA query_only=ON;
BEGIN;
SELECT 'B',hex(id),hex(source_id),hex(remote_id),hex(title),in_library,hex(category),updated_at,hex(document) FROM books ORDER BY id;
SELECT 'C',hex(id),hex(book_id),hex(source_id),hex(remote_id),quote(number),hex(document) FROM chapters ORDER BY id;
SELECT 'P',hex(chapter_id),position,hex(document) FROM pages ORDER BY chapter_id,position;
SELECT 'R',hex(chapter_id),hex(book_id),page,total,completed,updated_at FROM progress ORDER BY chapter_id;
SELECT 'M',hex(chapter_id),page,hex(book_id),created_at FROM bookmarks ORDER BY chapter_id,page;
SELECT 'F',hex(key),hex(document) FROM preferences WHERE key <> 'navigation-session' ORDER BY key;
SELECT 'S',hex(id),hex(document) FROM sources ORDER BY id;
SELECT 'D',hex(chapter_id),hex(book_id),hex(state),hex(document) FROM downloads ORDER BY chapter_id;
COMMIT;
PROTECTED_SQL
then printf 'sql-ok\\n' >&3; else printf 'sql-failed\\n' >&3; fi; } | sha256sum; } 3>&1 )
test "$(printf '%s\\n' "$protected_library" | wc -l)" -eq 2
printf '%s\\n' "$protected_library" | grep -Fxq sql-ok
proof=$(printf '%s\\n' "$protected_library" | sed -n 's/^\\([0-9a-f]\\{64\\}\\)  -$/\\1/p'); test "${#proof}" -eq 64
printf 'user-library-projection %s\\n' "$proof"
for setting in /mnt/us/aidoku/data/startup-options /mnt/us/aidoku/data/startup-options.conf; do
    # If a personal override exists, preserve it and do not claim defaults.
    test ! -e "$setting" && test ! -L "$setting"
done
'''
