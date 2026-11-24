"""Decide what to push to AniList for one manga series.

Pure logic, no Calibre or network imports. Inputs: the series' volumes (chapter
range + Kobo percent) and your current AniList entry. Guard rails:

* progress is only ever raised, never lowered;
* only chapters are sent (no volume counts), and never a status of COMPLETED;
* entries you marked COMPLETED / PAUSED / DROPPED are left alone;
* because you read in order, the highest chapter reached wins - gaps in the
  library do not matter.
"""

from . import chapters as ch


class Options(object):
    def __init__(self, count_in_progress=False, new_status='CURRENT', update_inactive=False):
        self.count_in_progress = count_in_progress   # also count the chapter you are in
        self.new_status = new_status                 # status for a series not on your list yet
        self.update_inactive = update_inactive       # also update PAUSED / DROPPED entries


class VolumeInput(object):
    def __init__(self, book_id, title, volume, percent, chapter_range=None):
        self.book_id = book_id
        self.title = title
        self.volume = volume                # Calibre series index (display only)
        self.percent = percent              # Kobo percent read, or None
        self.chapter_range = chapter_range  # (first, last) from #manga_chapters, or None


class VolumeResult(object):
    def __init__(self, v, completed, finished, source, note=''):
        self.book_id = v.book_id
        self.title = v.title
        self.volume = v.volume
        self.percent = v.percent
        self.chapter_range = v.chapter_range
        self.completed = completed          # highest chapter fully read, or None
        self.finished = finished
        self.source = source                # 'range' | 'unread' | 'no-range'
        self.note = note


def resolve_volume(v, options=None):
    options = options or Options()
    if not v.percent:
        return VolumeResult(v, None, False, 'unread')
    if not v.chapter_range:
        return VolumeResult(v, None, False, 'no-range', 'read, but no chapter range entered')
    done, finished = ch.completed_from_range(v.chapter_range[0], v.chapter_range[1],
                                             v.percent, options.count_in_progress)
    return VolumeResult(v, done, finished, 'range')


class SeriesPlan(object):
    def __init__(self, series, anilist_id):
        self.series = series
        self.anilist_id = anilist_id
        self.results = []
        self.target = None              # chapter to set (int) or None
        self.status = None              # status to set, or None to leave as is
        self.action = 'nothing'         # add | update | skip | preview | unlinked | nothing
        self.reason = ''
        self.warnings = []
        self.current_progress = None
        self.current_status = None

    @property
    def will_push(self):
        return self.action in ('add', 'update')


def plan_series(series, volumes, anilist_id=None, entry=None, media_chapters=None, options=None,
                entry_known=True):
    """Plan the AniList update for one series.

    ``entry`` is your current AniList list entry (``{status, progress}``) or None
    when the series is not on your list. ``entry_known=False`` means AniList was not
    asked (no token): the result is a preview of the target only.
    """
    options = options or Options()
    plan = SeriesPlan(series, anilist_id)
    plan.results = [resolve_volume(v, options) for v in volumes]
    missing = [r for r in plan.results if r.source == 'no-range']
    if missing:
        plan.warnings.append('%d volume(s) read but without a chapter range, ignored: %s' % (
            len(missing), ', '.join('vol %g' % r.volume if r.volume is not None else r.title
                                    for r in missing[:6])))
    done = [r.completed for r in plan.results if r.completed]
    plan.target = max(done) if done else None

    if anilist_id is None:
        plan.action, plan.reason = 'unlinked', 'no AniList link saved on this series'
        return plan
    if plan.target is None:
        plan.action, plan.reason = 'nothing', 'no completed chapters known'
        return plan
    if media_chapters and plan.target > media_chapters:
        plan.warnings.append('chapter %d is past the %d chapters AniList lists; capped'
                             % (plan.target, media_chapters))
        plan.target = media_chapters

    if not entry_known:
        plan.action = 'preview'
        plan.reason = 'your AniList entry was not checked (no token)'
        return plan

    if entry is None:
        plan.action, plan.status = 'add', options.new_status
        plan.reason = 'not on your AniList yet'
        return plan

    plan.current_progress = entry.get('progress') or 0
    plan.current_status = entry.get('status')
    if plan.current_status == 'COMPLETED':
        plan.action, plan.reason = 'skip', 'marked COMPLETED on AniList; left alone'
        return plan
    if plan.current_status in ('PAUSED', 'DROPPED') and not options.update_inactive:
        plan.action = 'skip'
        plan.reason = 'marked %s on AniList; left alone' % plan.current_status
        return plan
    if plan.target <= plan.current_progress:
        plan.action = 'skip'
        plan.target = plan.current_progress
        plan.reason = 'AniList is already at chapter %d' % plan.current_progress
        return plan
    if plan.current_status == 'PLANNING':
        plan.status = 'CURRENT'
    plan.action = 'update'
    plan.reason = 'chapter %d -> %d' % (plan.current_progress, plan.target)
    return plan
