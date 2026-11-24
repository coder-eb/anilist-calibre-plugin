"""Writes the plugin makes to the Calibre library.

Pure: ``api`` is Calibre's ``db.new_api`` (or anything with ``field_for`` / ``set_field`` /
``all_book_ids``), so these run against fakes in unit tests and against a real library in the
integration test. Each function returns the ids it actually changed (unchanged books are not touched).
"""

LINK_TYPE = 'anilist'


def series_siblings(api, book_ids):
    """``book_ids`` plus every other book in the same series (case-insensitive)."""
    names = {(api.field_for('series', b) or '').casefold() for b in book_ids} - {''}
    out = list(book_ids)
    if names:
        for bid in api.all_book_ids():
            if bid not in out and (api.field_for('series', bid) or '').casefold() in names:
                out.append(bid)
    return out


def set_anilist_link(api, book_ids, anilist_id):
    changes = {}
    for bid in book_ids:
        idents = dict(api.field_for('identifiers', bid) or {})
        if idents.get(LINK_TYPE) == str(anilist_id):
            continue
        idents[LINK_TYPE] = str(anilist_id)
        changes[bid] = idents
    if changes:
        api.set_field('identifiers', changes)
    return list(changes)


def remove_anilist_link(api, book_ids):
    changes = {}
    for bid in book_ids:
        idents = dict(api.field_for('identifiers', bid) or {})
        if LINK_TYPE in idents:
            del idents[LINK_TYPE]
            changes[bid] = idents
    if changes:
        api.set_field('identifiers', changes)
    return list(changes)


def write_chapter_column(api, column, values):
    """Store ``{book_id: chapter}`` in an integer column, only where it differs."""
    changes = {b: v for b, v in values.items() if api.field_for(column, b) != v}
    if changes:
        api.set_field(column, changes)
    return list(changes)
