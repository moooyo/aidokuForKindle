"""Verify only the generated sources, decoded cover pixels and screen contents."""

from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re

from PIL import Image

import fixture


def need(condition, reason):
    if not condition:
        raise ValueError(reason)


@lru_cache(maxsize=1)
def reference():
    return fixture.bundle()['fixture.json']


def verify_case(directory, phase, arm, *, host_fixture_data=None):
    expected=reference(); need((directory/'fixture.json').read_bytes()==expected,'changed_synthetic_archive_contract')
    books=json.loads(expected)['books']; hashes={x['title']:x['page_sha256'] for x in books}
    original_labels={v[0]:k for k,v in hashes.items()}; need(len(original_labels)==20,'nonunique_generated_pages')
    targets={fixture.title(i) for i in fixture.TARGETS}
    modes=(directory/'observed-options').read_text().splitlines(); expected_mode='off' if arm=='baseline' else 'idle'
    need(modes.count('import_thumbnail='+expected_mode)==1 and sum(s.startswith('import_thumbnail=') for s in modes)==1,'wrong_private_import_mode')
    state=json.loads((directory/'state.json').read_text()); need(state['route']=='library' and state['page']==1 and state['library_count']==20
        and state['modal']=='' and state['cover_visible_failure_count']==0 and state['cover_pending_count']==0
        and state['demo'] is False and state['offline'] is True,'unexpected_library_state')
    rowids={}; pages=set()
    if host_fixture_data is None:
        run=directory.parents[2].name; trial=directory.name.rsplit('-',1)[0]
        need(re.fullmatch('aidoku-all-[a-z][a-z0-9-]{0,12}-[0-9a-f]{8}',run) and trial.startswith(arm+'-'),'unbound_fixture_root')
        data_root='/mnt/us/.'+run+'/cases/'+trial+'/data'
    else: data_root=str(host_fixture_data)
    for row in (directory/'imported-rows.tsv').read_text().splitlines():
        name,bookid,pos,local,cover=row.split('|'); pos=int(pos)
        need(name in hashes and pos in (0,1) and (name,pos) not in pages,'unexpected_local_book')
        need(re.fullmatch(r'5:local:[0-9a-f]{64}',bookid) is not None,'wrong_local_id')
        need(re.fullmatch(re.escape(data_root)+r'/local/content/'+bookid.rsplit(':',1)[-1]+r'/[0-9a-f]{64}/00000[12]\.png',local) is not None,'local_source_outside_trial')
        need(local.endswith(f'/{pos+1:06}.png') and (pos!=0 or local==cover),'cover_source_not_first_page')
        pages.add((name,pos)); rowids[bookid]=name
    need(len(pages)==40 and len(rowids)==20,'incorrect_book_or_page_count')
    checked={}
    for row in (directory/'page-hashes.tsv').read_text().splitlines():
        name,pos,observed=row.split('|'); pos=int(pos)
        need((name,pos) in pages and (name,pos) not in checked and hashes[name][pos]==observed,'source_image_changed');checked[name,pos]=observed
    need(len(checked)==40,'source_image_check_missing')
    initial={fixture.title(i) for i in range(5,20)}; opened={fixture.title(i) for i in range(15)}
    visible=[row['title'] for row in state['visible_books']]; expected_visible=initial if phase=='ingest' else opened
    need(len(visible)==15 and set(visible)==expected_visible and rowids[state['current_book']]==fixture.title(0),'unexpected_presented_books')
    need(visible==([fixture.title(i) for i in range(19,4,-1)] if phase=='ingest' else [fixture.title(i) for i in range(15)]),'unexpected_presented_order')
    need(all(rowids.get(row['id'])==row['title'] for row in state['visible_books']),'visible_identity_mismatch')
    keys=set((directory/'processed-keys').read_text().splitlines()); drawings={}; disk=0
    paths=list((directory/'derivatives').glob('*.png')); need({p.stem for p in paths}==keys and len(keys)<=24,'unverified_derivative_set')
    for p in paths:
        need(not p.is_symlink() and re.fullmatch('[a-f0-9]{64}',p.stem) and 0<p.stat().st_size<=3145728,'invalid_synthetic_derivative')
        with Image.open(p) as im:
            need(im.format=='PNG' and im.mode=='L' and im.width<=376 and im.height<=514 and im.width>0 and im.height>0,'wrong_image_format_or_size')
            info=json.loads(im.info['AidokuRender']); k=info['render_key']; identity=k['source_key'].rsplit(':',1)[-1]
            need(identity in original_labels and k['width']==376 and k['height']==514 and k['dither'] is True
                 and k['contrast']==0 and k['vertical'] is False and k['offset']==0 and k['zoom_milli']==1000,'wrong_cover_provenance')
            title=original_labels[identity]; need(title not in drawings,'duplicate_derivative')
            drawing=f'{im.width}x{im.height}:'.encode()+im.tobytes(); drawings[title]=hashlib.sha256(drawing).hexdigest()
            disk+=p.stat().st_size
    n=len(targets & drawings.keys()); expected_count=0 if phase=='ingest' and arm=='baseline' else 4
    need(n==expected_count and set(visible)<=set(drawings) and fixture.title(0) in drawings,'wrong_source_bound_cache_state')
    return {'source_images_verified':True,'image_sha256_by_title':dict(sorted(drawings.items())),
            'targets':sorted(targets),'visible_titles':visible,'target_derivative_count':n,
            'persisted_derivative_count':len(drawings),'persisted_derivative_bytes':disk,'fixture_sha256':fixture.sha(expected)}


def screen_pair(left,right):
    with Image.open(left/'native-settled.ppm') as a, Image.open(right/'native-settled.ppm') as b:
        need(a.size==b.size==(1860,2480) and a.mode==b.mode=='RGB','wrong_native_pixel_geometry')
        for box in ((0,72,1860,2480),(930,0,1860,72)):
            ac=a.crop(box);bc=b.crop(box); lo,hi=ac.convert('L').getextrema()
            need(hi-lo>80 and ac.tobytes()==bc.tobytes(),'native_visible_image_mismatch')
