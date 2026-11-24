"""Sync manga reading progress from a Calibre library to AniList, from the command line.

    python -m anilist_manga_sync.cli --library ~/"Calibre Library" --series Kagurabachi
    ANILIST_TOKEN=... python -m anilist_manga_sync.cli --library ... --series Kagurabachi --apply

Reads the library's ``metadata.db`` (a temporary copy; the library is never written to).
A series is synced only if one of its volumes has an ``anilist:<id>`` identifier. The
chapter comes from each volume's ``#manga_chapters`` range and Kobo's percent read.

Without ``--apply`` nothing is changed: it prints what it would do. With ``--apply`` it
asks before each push. Set the token in your own terminal (ANILIST_TOKEN); it is never
written to disk by this tool.
"""

import argparse
import os
import shutil
import sqlite3
import sys
import tempfile

from . import planner, sync
from .anilist import AniListClient, AniListError, AuthError, display_title


def _column_values(db, label):
    row = db.execute('select id from custom_columns where label=?', (label,)).fetchone()
    if row is None:
        return {}
    cid = row[0]
    link = 'books_custom_column_%d_link' % cid
    if db.execute('select 1 from sqlite_master where name=?', (link,)).fetchone():
        return dict(db.execute('select l.book, c.value from %s l join custom_column_%d c on c.id=l.value'
                               % (link, cid)).fetchall())
    return dict(db.execute('select book, value from custom_column_%d' % cid).fetchall())


def load_records(library, tag='Manga', percent_col='kobo_percent_read', range_col='manga_chapters'):
    """BookRecords for every book carrying ``tag`` (read-only)."""
    tmp = tempfile.mkdtemp(prefix='ams-')
    try:
        shutil.copy(os.path.join(library, 'metadata.db'), os.path.join(tmp, 'm.db'))
        db = sqlite3.connect(os.path.join(tmp, 'm.db'))
        percent, ranges = _column_values(db, percent_col), _column_values(db, range_col)
        idents = {}
        for book, typ, val in db.execute('select book, type, val from identifiers'):
            idents.setdefault(book, {})[typ] = val
        rows = db.execute("""
            select b.id, b.title, b.series_index,
                   (select s.name from series s join books_series_link l on l.series=s.id where l.book=b.id)
            from books b join books_tags_link bt on bt.book=b.id join tags t on t.id=bt.tag
            where t.name=? collate nocase""", (tag,)).fetchall()
        return [sync.BookRecord(bid, title, series, idx, idents.get(bid),
                                int(percent[bid]) if percent.get(bid) is not None else None,
                                ranges.get(bid))
                for bid, title, idx, series in rows]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def describe(plan, linked_title=''):
    lines = []
    read = [r for r in plan.results if r.source != 'unread']
    lines.append('%s%s' % (plan.series, ('  (AniList: %s)' % linked_title) if linked_title else ''))
    for r in read:
        lines.append('    vol %-4s %3s%%  range %-8s -> completed chapter %s%s' % (
            ('%g' % r.volume) if r.volume is not None else '?', r.percent,
            '%g-%g' % (r.chapter_range[0], r.chapter_range[1]) if getattr(r, 'chapter_range', None) else '',
            r.completed if r.completed else '-', '  (finished)' if r.finished else ''))
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--library', required=True, help='Calibre library folder')
    ap.add_argument('--tag', default='Manga')
    ap.add_argument('--series', help='only series whose name contains this')
    ap.add_argument('--token', default=os.environ.get('ANILIST_TOKEN'),
                    help='AniList token (prefer the ANILIST_TOKEN environment variable)')
    ap.add_argument('--apply', action='store_true', help='push to AniList (asks before each series)')
    ap.add_argument('--count-in-progress', action='store_true',
                    help='also count the chapter you are currently in')
    args = ap.parse_args(argv)

    if args.apply and not args.token:
        print('--apply needs your AniList token: export ANILIST_TOKEN=... in this terminal.')
        return 2
    records = load_records(os.path.expanduser(args.library), args.tag)
    series_map = sync.group_by_series(records)
    if args.series:
        series_map = {k: v for k, v in series_map.items() if args.series.casefold() in k.casefold()}
    client = AniListClient(args.token, min_interval=0.7 if args.apply else 0.0)
    engine = sync.Engine(client, planner.Options(count_in_progress=args.count_in_progress))

    try:
        if args.token:
            who = client.viewer()
            print('AniList account: %s\n' % who.get('name', '?'))
        pushed = 0
        linked = [(s, r) for s, r in series_map.items() if sync.anilist_id_for(r) is not None]
        # only linked series are ever touched; one batched AniList request covers all of them
        for (series, recs), plan in zip(linked, engine.plan_all(linked, fetch_entries=bool(args.token))):
            print('\n'.join(describe(plan)))
            print('    => %s: target chapter %s (%s)' % (plan.action.upper(), plan.target or '-', plan.reason))
            for w in plan.warnings:
                print('    ! ' + w)
            if args.apply and plan.will_push:
                ans = input('    Push chapter %d for "%s" to AniList now? [y/N] ' % (plan.target, series))
                if ans.strip().lower() == 'y':
                    saved = engine.apply(plan)
                    print('    pushed: progress=%s status=%s' % (saved.get('progress'), saved.get('status')))
                    pushed += 1
                else:
                    print('    skipped')
            print()
        if args.apply:
            print('%d series pushed.' % pushed)
    except AuthError as exc:
        print('AniList login problem: %s' % exc)
        return 1
    except AniListError as exc:
        print('AniList error: %s' % exc)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
