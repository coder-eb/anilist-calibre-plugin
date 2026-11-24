"""Group volumes into series and plan/push their AniList updates.

No Calibre imports: the Calibre plugin and the command line both feed it
:class:`BookRecord` objects. A series is matched to AniList **only** by an
``anilist`` identifier saved on one of its volumes; nothing is searched or guessed.
"""

import collections

from . import chapters as ch
from . import planner


class BookRecord(object):
    def __init__(self, book_id, title, series, series_index, identifiers=None,
                 percent=None, chapter_range_text=None):
        self.book_id = book_id
        self.title = title
        self.series = series
        self.series_index = series_index
        self.identifiers = identifiers or {}
        self.percent = percent
        self.chapter_range_text = chapter_range_text


def group_by_series(records):
    """``OrderedDict series -> [BookRecord]`` (volume order); books without a series are dropped."""
    out = collections.OrderedDict()
    for r in sorted((r for r in records if r.series), key=lambda r: (r.series.casefold(), r.series_index or 0)):
        out.setdefault(r.series, []).append(r)
    return out


def anilist_id_for(records):
    """The AniList id saved on any volume of the series, or None."""
    for r in records:
        ident = str((r.identifiers or {}).get('anilist') or '').strip()
        if ident.isdigit():
            return int(ident)
    return None


def volume_inputs(records):
    return [planner.VolumeInput(r.book_id, r.title, r.series_index, r.percent,
                                ch.parse_range(r.chapter_range_text)) for r in records]


class Engine(object):
    def __init__(self, anilist, options=None):
        self.anilist = anilist
        self.options = options or planner.Options()

    def plan(self, series, records, fetch_entry=True):
        anilist_id = anilist_id_for(records)
        entry, media_chapters = None, None
        if anilist_id and fetch_entry:
            media = self.anilist.entry(anilist_id)
            entry = media.get('mediaListEntry')
            media_chapters = media.get('chapters')
        return planner.plan_series(series, volume_inputs(records), anilist_id, entry,
                                   media_chapters, self.options, entry_known=fetch_entry)

    def plan_all(self, items, fetch_entries=True, known_pushed=None):
        """Plan every ``(series, records)`` item with as few AniList requests as possible.

        Everything that can be decided locally is decided first (no network). Only series that
        actually have a chapter to send are looked up on AniList, all in one batched request
        (50 per request); with nothing to send, or no token, no request is made at all.

        ``known_pushed`` is ``{anilist_id: chapter}`` AniList is known to have already (from what this
        plugin sent, or from an earlier lookup). A series whose chapter is not above that is treated as
        up to date *without asking AniList*: progress is never lowered, so there is nothing to change
        and nothing worth a request.
        """
        known_pushed = known_pushed or {}
        prepared = []
        for series, recs in items:
            anilist_id = anilist_id_for(recs)
            vols = volume_inputs(recs)
            local = planner.plan_series(series, vols, anilist_id, None, None, self.options,
                                        entry_known=False)
            prepared.append((series, vols, anilist_id, local))
        for _s, _v, anilist_id, local in prepared:
            sent = known_pushed.get(anilist_id)
            if fetch_entries and anilist_id and local.target and sent is not None and local.target <= sent:
                local.action = 'skip'
                local.reason = 'AniList already had chapter %d when last checked (not asked again)' % sent
                local.target = sent
        need = [anilist_id for _s, _v, anilist_id, local in prepared
                if anilist_id and local.target and local.action != 'skip']
        media = self.anilist.entries(need) if (fetch_entries and need) else {}
        plans = []
        for series, vols, anilist_id, local in prepared:
            if local.action == 'skip' or not (fetch_entries and anilist_id and local.target):
                plans.append(local)             # unlinked / nothing to send / preview without a token
                continue
            found = media.get(anilist_id)
            if found is None:
                local.action = 'skip'
                local.reason = 'AniList has no manga with id %d - check the link' % anilist_id
                plans.append(local)
                continue
            plans.append(planner.plan_series(series, vols, anilist_id, found.get('mediaListEntry'),
                                             found.get('chapters'), self.options))
        return plans

    def apply(self, plan):
        """Push one plan; returns AniList's saved entry."""
        if not plan.will_push:
            raise ValueError('nothing to push for %s' % plan.series)
        return self.anilist.save_progress(plan.anilist_id, plan.target, None, plan.status)
