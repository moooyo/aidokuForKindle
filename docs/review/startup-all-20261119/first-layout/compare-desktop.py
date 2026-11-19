#!/usr/bin/env python3
"""Compare Library pixels and projected state across two modes of one desktop executable."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess

from PIL import Image, ImageChops


PROFILE = {'pw5': ((618, 824), 8), 'scribe': ((930, 1240), 15)}
SCENES = {'L01': 'library', 'L02': 'library-page-two', 'L03': 'library-sort', 'L04': 'library-selection'}
LIBRARY_STATE = ('route', 'visible_books', 'current_book', 'library_count', 'reading_count',
                 'planned_count', 'completed_count', 'updated_count', 'library_sort', 'library_filter',
                 'library_ascending', 'library_filters', 'page', 'page_count', 'selection_mode',
                 'modal', 'modal_context', 'cover_visible_count', 'book_tags', 'unread_count')
# The last qualified c6867d1 desktop captures of L01, distinct from the original frozen scene series.
L01_REFERENCE = {'pw5': '3cc458b29667ec29e6c48c8e89cb40b36a690f21734a2027fa6d5fe3caa46440',
                 'scribe': '9438dfb83f50d0723bbe03cd8761cee78aa2aa1a7d7047cd979591d585f74793'}


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def differences(left, right):
    with Image.open(left) as a, Image.open(right) as b:
        if a.size != b.size:
            raise ValueError(f'Pixel geometry differs: {a.size} vs {b.size}')
        bands = ImageChops.difference(a.convert('RGB'), b.convert('RGB')).split()
        high = ImageChops.lighter(ImageChops.lighter(bands[0], bands[1]), bands[2])
        return a.width * a.height - high.histogram()[0]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def seed_mixed_titles(reference, data):
    # Only use a fresh copy of the previously qualified redistributable demo.
    require((reference / '.demo-v1-seeded').is_file(), 'Expected the original offline demo seed')
    data.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect((reference / 'library.sqlite3').as_uri() + '?mode=ro', uri=True) as src, \
            sqlite3.connect(data / 'library.sqlite3') as dest:
        src.backup(dest)
        for key, raw in dest.execute('SELECT id, document FROM books ORDER BY id').fetchall():
            book = json.loads(raw)
            require(key.startswith('8:mangadex:demo-') and book['remote_id'].startswith('demo-'),
                    'Refusing a non-demo book')
            number = int(book['remote_id'][5:])
            title = ('Q' + chr(ord('A') + number // 2) if number % 2 == 0 else
                     f'A long shared series title whose distinguishing volume is at the end {number:03}')
            book['title'] = title
            if book['cover']:
                old_art = Path(book['cover']).relative_to(reference / 'demo-assets')
                book['cover'] = str(data / 'demo-assets' / old_art)
            dest.execute('UPDATE books SET title=?,document=? WHERE id=?', (title, json.dumps(book), key))
    (data / '.demo-v1-seeded').write_bytes((reference / '.demo-v1-seeded').read_bytes())


def capture(args, profile, scene, mode):
    work = args.output / profile / scene / mode
    work.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ, SLINT_BACKEND='winit-software', WINIT_X11_SCALE_FACTOR='1', SLINT_SCALE_FACTOR='1')
    if args.mixed_title_fixture:
        seed_mixed_titles(args.mixed_title_fixture / profile / 'L01/reference/data/demo', work / 'data/demo')
    if mode != 'reference':
        if args.mode_env:
            env[args.mode_env] = mode
        elif args.config_env:
            cfg = work / 'startup-options.conf'
            cfg.write_text(f'first_layout={mode}\n')
            env[args.config_env] = str(cfg)
        else:
            data = work / 'data' / 'demo'
            data.mkdir(parents=True, exist_ok=True)
            (data / 'startup-options').write_text(f'first_layout={mode}\n')
    png, tracefile = work / 'view.png', work / 'timing.json'
    command = ['xvfb-run', '-a', '-s', '-screen 0 1600x1800x24', str(args.binary),
               '--device', profile, '--demo', '--offline', '--screen', 'library', '--data-dir', str(work / 'data'),
               '--capture', str(png), '--capture-settle-covers', '--performance-trace', str(tracefile)]
    delay = 1200
    if scene != 'L01':
        replay = args.replays / profile / f'{SCENES[scene]}.json'
        actions = json.loads(replay.read_text())
        require(actions and all(a['section'] == 'pointer' for a in actions), 'Unexpected frozen input replay')
        command.extend(['--actions', str(replay)])
        delay = max(a['at_ms'] for a in actions) + 1600
    command.extend(['--exit-after-ms', str(delay)])
    with (work / 'stdout').open('wb') as stdout, (work / 'stderr').open('wb') as stderr:
        subprocess.run(command, env=env, stdout=stdout, stderr=stderr, check=True, timeout=45)
    state = json.loads(png.with_suffix('.state.json').read_text())
    metadata = json.loads(png.with_suffix('.metadata.json').read_text())
    trace = json.loads(tracefile.read_text())
    events = {e['name']: e['at_us'] for e in trace['events']}
    require(trace['success'] and trace['dropped_events'] == 0, 'Unsuccessful or incomplete trace')
    marker = None
    projection_us = None
    if mode != 'reference':
        marker = getattr(args, mode + '_marker')
        other = args.optimized_marker if mode == 'baseline' else args.baseline_marker
        require(marker in events and other not in events, f'Trace does not identify the {mode} path')
        first = [e for e in trace['events'] if events['startup_view_started'] <= e['at_us'] <= events['startup_view_attached']
                 and e['name'] in ('library_page_projection_started', marker)]
        require([e['name'] for e in first] == ['library_page_projection_started', marker],
                'The selected path has to execute before the initial Library projection completes')
        projection_us = first[1]['at_us'] - first[0]['at_us']
    size, visible = PROFILE[profile]
    require(tuple(metadata['snapshot_size']) == tuple(metadata['window_size']) == size and
            metadata['scale_factor'] == 1 and not metadata['landscape'] and metadata['font_initializations'] == 1,
            'Unexpected layout geometry or font count')
    require(state['route'] == 'library' and not state['capture_projection_pending'] and
            state['cover_visible_failure_count'] == 0, 'Capture did not settle on the Library')
    require(len(state['visible_books']) == visible and state['page'] == (2 if scene == 'L02' else 1),
            'Incorrect grid window or pagination')
    require(state['selection_mode'] == (scene == 'L04') and
            sum(int(b['selected']) for b in state['visible_books']) == int(scene == 'L04'),
            'Unexpected Library selection')
    require(state['modal'] == ('choices' if scene == 'L03' else '') and
            state['modal_context'] == ('library-filter' if scene == 'L03' else ''), 'Unexpected Library modal')
    metrics = {'png_sha256': digest(png), 'visible_rows': visible, 'page': state['page'],
               'selected_rows': sum(int(b['selected']) for b in state['visible_books']),
               'font_initializations': metadata['font_initializations'], 'failed_covers': 0,
               'selected_path_marker': marker,
               'first_grid_projection_us_single_sample': projection_us,
               'view_attachment_us_single_sample': events['startup_view_attached'] - events['startup_view_started']}
    if scene == 'L01':
        if args.mixed_title_fixture:
            short = sum(len(b['title']) == 2 for b in state['visible_books'])
            long = sum(len(b['title']) > 50 for b in state['visible_books'])
            require(short > 0 and long > 0 and short + long == visible, 'Both title-fit branches must be represented')
            metrics.update(short_title_rows=short, long_title_rows=long)
        else:
            metrics['matches_prior_l01_png'] = metrics['png_sha256'] == L01_REFERENCE[profile]
            if args.reference_only:
                require(metrics['matches_prior_l01_png'], f'{profile} reference did not reproduce prior L01')
    return png, {key: state[key] for key in LIBRARY_STATE}, metrics


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--binary', type=Path, required=True)
    p.add_argument('--replays', type=Path, required=True, help='Frozen 2026-11-14 baseline replay directory')
    p.add_argument('--output', type=Path, required=True, help='New directory for all raw captures and report')
    p.add_argument('--reference-captures', type=Path, help='Qualified reference-only output for prior-product pixel checks')
    p.add_argument('--mixed-title-fixture', type=Path, help='Replace demo titles in fresh copies of the qualified reference databases')
    p.add_argument('--scenes', nargs='+', choices=SCENES, default=list(SCENES))
    p.add_argument('--reference-only', action='store_true', help='Qualify replay/capture, without claiming an A/B')
    env = p.add_mutually_exclusive_group()
    env.add_argument('--mode-env', help='Environment variable carrying the production mode value')
    env.add_argument('--config-env', help='Environment variable carrying the production options-file path')
    env.add_argument('--data-options', action='store_true', help='Write the production startup-options file in the isolated data directory')
    p.add_argument('--baseline-marker', help='Trace event emitted only by the baseline path')
    p.add_argument('--optimized-marker', help='Trace event emitted only by the optimized path')
    args = p.parse_args()
    if not args.reference_only and (not (args.mode_env or args.config_env or args.data_options) or not args.baseline_marker or
                                    not args.optimized_marker or args.baseline_marker == args.optimized_marker):
        p.error('paired A/B requires the production switch and two distinct path markers')
    args.binary, args.replays, args.output = [path.resolve() for path in (args.binary, args.replays, args.output)]
    if args.mixed_title_fixture:
        require(args.scenes == ['L01'], 'The title stress case uses the initial Library scene only')
        args.mixed_title_fixture = args.mixed_title_fixture.resolve(strict=True)
    if not args.reference_only and args.reference_captures:
        args.reference_captures = args.reference_captures.resolve()
        prior = json.loads((args.reference_captures / 'desktop-results.json').read_text())
        require(prior['reference_only'] and prior['passed'], 'Not a qualified reference capture')
    args.output.mkdir(parents=True, exist_ok=False)
    report = {'scope': 'Worker desktop; offline demo; settled full-frame RGB and Library state. '
              'Single attachment intervals are context, not a launch-time benchmark.',
              'reference_only': args.reference_only, 'binary_sha256': digest(args.binary), 'results': []}
    for profile in PROFILE:
        for scene in args.scenes:
            data = {}
            for mode in (('reference',) if args.reference_only else ('baseline', 'optimized')):
                data[mode] = capture(args, profile, scene, mode)
            row = {'profile': profile, 'scene': scene, 'size': PROFILE[profile][0],
                   'modes': {k: v[2] for k, v in data.items()}}
            if not args.reference_only:
                a, b = data['baseline'], data['optimized']
                row['differing_pixels'] = differences(a[0], b[0])
                row['same_library_state'] = a[1] == b[1]
                require(not row['differing_pixels'] and row['same_library_state'],
                        f'Different {profile} {scene}: {row["differing_pixels"]} pixels; '
                        f'state keys {[k for k in a[1] if a[1][k] != b[1][k]]}')
                if args.reference_captures:
                    old_png = args.reference_captures / profile / scene / 'reference/view.png'
                    row['differing_pixels_vs_prior_binary'] = {k: differences(old_png, v[0]) for k, v in data.items()}
                    require(not any(row['differing_pixels_vs_prior_binary'].values()),
                            f'Different {profile} {scene} relative to the prior executable')
            report['results'].append(row)
    report['passed'] = True
    destination = args.output / 'desktop-results.json'
    destination.write_text(json.dumps(report, indent=2) + '\n')
    print(destination)


if __name__ == '__main__':
    main()
