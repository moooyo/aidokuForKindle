#!/usr/bin/env python3
"""Measure the L01 visible-grid SQL and JSON projection on an offline demo database."""

import argparse
import hashlib
import itertools
import json
from pathlib import Path
import platform
import sqlite3
import statistics
import time


# c6867d1 src/store/view_queries.rs, default Library interactions: recent-read, descending.
FILTER = """b.in_library = 1
    AND (CASE ?1 WHEN 1 THEN b.category = 'reading' WHEN 2 THEN b.category = 'planned'
         WHEN 3 THEN b.category = 'completed' WHEN 4 THEN b.category NOT IN
         ('reading', 'planned', 'completed') ELSE 1 END)
    AND (?2 = 0 OR json_extract(b.document, '$.unread') > 0)
    AND (?3 = 0 OR EXISTS(SELECT 1 FROM chapters c WHERE c.book_id = b.id
                        AND json_extract(c.document, '$.downloaded') = 1))
    AND (?4 = 0 OR EXISTS(SELECT 1 FROM chapters c WHERE c.book_id = b.id
                        AND json_extract(c.document, '$.downloaded') = 1) = (?4 = 1))
    AND (?5 = 0 OR (COALESCE(json_extract(b.document, '$.unread'), 0) > 0) = (?5 = 1))
    AND ?6 <= 0
    AND (?7 = 0 OR (lower(trim(COALESCE(json_extract(b.document, '$.status'), '')))
                    IN ('completed', 'complete', 'ended', 'finished', ?8)) = (?7 = 1))"""
ARGS = (0, 0, 0, 0, 0, 0, 0, 'completed')  # The localized operand is unreachable for these filters.
COUNT = f'SELECT COUNT(*) FROM books b WHERE {FILTER}'
VISIBLE = f"""WITH visible AS MATERIALIZED (
    SELECT b.id, COALESCE((SELECT MAX(p.updated_at) FROM progress p WHERE p.book_id = b.id), 0)
        AS sort_value, b.rowid AS tie_breaker FROM books b WHERE {FILTER}
    ORDER BY sort_value DESC, tie_breaker DESC, b.id DESC LIMIT ?9 OFFSET ?10)"""
PAGE = 'FROM visible v JOIN books b ON b.id = v.id'
ORDER = 'ORDER BY v.sort_value DESC, v.tie_breaker DESC, b.id DESC'
FLAGS = """EXISTS(SELECT 1 FROM chapters c WHERE c.book_id = b.id
        AND json_extract(c.document, '$.downloaded') = 1),
    p.book_id, p.chapter_id, p.page, p.total, p.updated_at, p.completed"""
PROGRESS = """LEFT JOIN progress p ON p.rowid = (SELECT r.rowid FROM progress r
    WHERE r.book_id = b.id ORDER BY r.updated_at DESC, r.rowid DESC LIMIT 1)"""
BASIC = f'{VISIBLE} SELECT b.document {PAGE} {ORDER}'
METADATA = f'SELECT b.document, {FLAGS} FROM books b {PROGRESS} WHERE b.id = ?1'
JOINED = f'{VISIBLE} SELECT b.document, {FLAGS} {PAGE} {PROGRESS} {ORDER}'
MODES = ('baseline', 'joined', 'batch_flags')


def project(conn, size, mode, decode=json.loads):
    """Return count plus ordered grid rows (display book, stored book, download flag, progress)."""
    t0 = time.perf_counter_ns()
    count = conn.execute(COUNT, ARGS).fetchone()[0]
    t1 = time.perf_counter_ns()
    projected = []
    if mode == 'joined':
        for document, *flags in conn.execute(JOINED, (*ARGS, size, 0)):
            book = decode(document)
            projected.append((book, book, flags))
        t2 = t3 = time.perf_counter_ns()
    else:
        books = [decode(document) for (document,) in conn.execute(BASIC, (*ARGS, size, 0))]
        t2 = time.perf_counter_ns()
        if mode == 'baseline':
            for book in books:
                document, *flags = conn.execute(METADATA, (book['id'],)).fetchone()
                projected.append((book, decode(document), flags))
        elif books:
            ids = [book['id'] for book in books]
            sql = (f'SELECT b.id, {FLAGS} FROM books b {PROGRESS} '
                   f'WHERE b.id IN ({",".join("?" for _ in ids)})')
            by_id = {key: flags for key, *flags in conn.execute(sql, ids)}
            projected = [(book, book, by_id[book['id']]) for book in books]
        t3 = time.perf_counter_ns()
    return (count, projected), (t1 - t0, t2 - t1, t3 - t2, t3 - t0)


def profile_fixture(path, rounds):
    if not (path.parent / '.demo-v1-seeded').is_file():
        raise ValueError('Refusing to read a database without the offline demo fixture marker')
    con = sqlite3.connect(f'{path.resolve().as_uri()}?mode=ro&immutable=1', uri=True,
                          cached_statements=0, isolation_level=None)
    try:
        fixture = {'database_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                   'library_books': con.execute('SELECT COUNT(*) FROM books WHERE in_library = 1').fetchone()[0],
                   'chapters': con.execute('SELECT COUNT(*) FROM chapters').fetchone()[0],
                   'progress_rows': con.execute('SELECT COUNT(*) FROM progress').fetchone()[0]}
        cases = []
        for size in (6, 8, 15):
            expected, _ = project(con, size, 'baseline')
            if len(expected[1]) != size:
                raise ValueError('Fixture must contain at least 15 visible books')
            counts = {}
            for mode in MODES:
                calls = {'statements': 0, 'book_decodes': 0}

                def traced(_):
                    calls['statements'] += 1

                def decoded(document):
                    calls['book_decodes'] += 1
                    return json.loads(document)

                con.set_trace_callback(traced)
                try:
                    actual, _ = project(con, size, mode, decoded)
                finally:
                    con.set_trace_callback(None)
                if actual != expected:
                    raise ValueError(f'{mode} differs on {size} visible books')
                counts[mode] = calls
            timings = {mode: [] for mode in MODES}
            orders = list(itertools.permutations(MODES))
            for i in range(rounds + len(orders)):
                for mode in orders[i % len(orders)]:
                    _, measured = project(con, size, mode)
                    if i >= len(orders):
                        timings[mode].append(measured)
            names = ('count', 'select_and_decode_page', 'subsequent_metadata', 'total')
            metrics = {mode: {**counts[mode], 'median_us': {
                name: round(statistics.median(x[index] for x in timings[mode]) / 1000, 1)
                for index, name in enumerate(names)},
                'p90_total_us': round(statistics.quantiles([x[-1] for x in timings[mode]], n=10,
                                                          method='inclusive')[8] / 1000, 1)}
                       for mode in MODES}
            paired = {mode: round(statistics.median(base[-1] - alternate[-1]
                                    for base, alternate in zip(timings['baseline'], timings[mode])) / 1000, 1)
                      for mode in MODES[1:]}
            cases.append({'visible_books': size, 'ordered_rows_identical': True, 'modes': metrics,
                          'paired_median_savings_us': paired})
        return {'fixture': fixture, 'cases': cases}
    finally:
        con.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--demo-db', type=Path, required=True)
    parser.add_argument('--rounds', type=int, default=120)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.rounds < 6 or args.rounds % 6:
        parser.error('rounds must be a positive multiple of six')
    report = {'scope': 'Worker microbenchmark; visible grid SQL, SQLite stepping and Python JSON only. '
              'Excludes Rust/Slint, current book, daily updates, thumbnails, display and startup.',
              'baseline_source': 'c6867d1', 'python': platform.python_version(),
              'sqlite': sqlite3.sqlite_version, 'architecture': platform.machine(),
              'design': 'One read-only offline demo DB; fixed first page/default unfiltered recent-read. '
              'Six argument-order permutations, six warm-up rounds. OS/SQLite page caches warm. '
              'Python statement cache disabled to match uncached rusqlite prepare/query_row calls. '
              'Paired savings are baseline minus variant within a round; positive means less time.',
              'rounds_per_mode': args.rounds, **profile_fixture(args.demo_db, args.rounds)}
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(args.output)


if __name__ == '__main__':
    main()
