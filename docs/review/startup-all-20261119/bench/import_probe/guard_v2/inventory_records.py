"""Private inventory records. Public summaries contain counts only."""

import hashlib
import json
import re


def inventory_read(path):
    raw=path.read_bytes()
    if not raw or len(raw)>6*1024*1024: raise ValueError('incomplete_inventory')
    lines=raw.decode('ascii').splitlines(); records={}; count=0
    for line in lines[:-1]:
        parts=line.split('\t')
        if len(parts)!=6 or parts[0]!='F': raise ValueError('unknown_inventory_record')
        _,name,size,sec,ns,digest=parts
        if (not re.fullmatch(r'(?:raw|processed)(?:/[a-zA-Z0-9._-]+){1,5}',name) or len(name)>511 or
            any(x in ('.','..') for x in name.split('/')) or name in records or not re.fullmatch(r'0|[1-9][0-9]{0,9}',size) or
            not re.fullmatch(r'-?(?:0|[1-9][0-9]{0,17})',sec) or not re.fullmatch(r'\d{9}',ns) or
            not re.fullmatch('[a-f0-9]{64}',digest)):raise ValueError('invalid_private_record')
        count+=int(size);records[name]=[int(size),int(sec),int(ns),digest]
    if len(records)>8192 or count>512*1024*1024 or lines[-1]!=f'END\t{len(records)}\t{count}':raise ValueError('truncated_inventory')
    return records


def legacy_member(legacy):
    lines=legacy.splitlines()
    if (len(lines)<17 or not re.fullmatch(rb'[a-f0-9]{64}  -',lines[8]) or
        not lines[9].endswith(b'/cache/shared-v1/index.sqlite3') or
        not (lines[10]==b'absent index.sqlite3-wal' or lines[10].endswith(b'/cache/shared-v1/index.sqlite3-wal')) or
        not re.fullmatch(rb'[a-f0-9]{64}  -',lines[11])): raise ValueError('unexpected_guard_shape')
    return lines[11].split()[0].decode()


def inventory_legacy(records):
    items=[f'{n}:{v[0]}:{v[1]}\n'.encode() for n,v in records.items()]
    return hashlib.sha256(b''.join(sorted(items))).hexdigest()


def only_legacy_file_stat_changed(first,second):
    legacy_member(first);legacy_member(second)
    a,b=first.splitlines(),second.splitlines()
    return len(a)==len(b) and [i for i,(l,r) in enumerate(zip(a,b)) if l!=r]==[11]


def inventory_compare(pre,post,batch=None):
    private=[]; counts={k:0 for k in ('added','removed','size','mtime','content')}
    for k in sorted(pre.keys()|post.keys()):
        a,b=pre.get(k),post.get(k)
        if a==b:continue
        tags=[]
        if a is None:tags.append('added')
        elif b is None:tags.append('removed')
        else:
            if a[0]!=b[0]:tags.append('size')
            if a[1:3]!=b[1:3]:tags.append('mtime')
            if a[3]!=b[3]:tags.append('content')
        for t in tags:counts[t]+=1
        private.append({'path':k,'changed':tags,'before':a,'after':b})
    if batch is not None and private:
        # Fresh host run dirs are private; never publish this file or raw inventories.
        (batch/'cache-private-differences.json').write_text(json.dumps(private,indent=2)+'\n')
    return {'file_count_before':len(pre),'file_count_after':len(post),'changed_files':len(private),
            'changes':counts,'exactly_equal':not private}
