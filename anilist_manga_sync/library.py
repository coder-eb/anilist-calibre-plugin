"""Calibre-side adapter: library -> BookRecords.

Reads only. Used by the Calibre plugin (inside Calibre); the command line reads the
library's ``metadata.db`` directly (see ``cli.py``) and returns identical records.
Kobo's own columns are read, never written.
"""

from .sync import BookRecord


def _api(db):
    return db.new_api if hasattr(db, 'new_api') else db


def load_records(db, prefs):
    """BookRecords for every book carrying the manga tag.

    ``prefs`` needs ``tag`` (e.g. 'Manga'), ``percent_column`` ('#kobo_percent_read')
    and ``range_column`` ('#manga_chapters').
    """
    api = _api(db)
    tag = (prefs['tag'] or 'Manga').casefold()
    percent_col, range_col = prefs['percent_column'], prefs['range_column']
    out = []
    for bid in api.all_book_ids():
        tags = api.field_for('tags', bid) or ()
        if tag not in {t.casefold() for t in tags}:
            continue
        pct = api.field_for(percent_col, bid) if percent_col else None
        out.append(BookRecord(
            bid, api.field_for('title', bid) or '', api.field_for('series', bid),
            api.field_for('series_index', bid), dict(api.field_for('identifiers', bid) or {}),
            int(pct) if pct is not None else None,
            api.field_for(range_col, bid) if range_col else None))
    return out
