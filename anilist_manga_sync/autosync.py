"""Offer an AniList sync right after Kobo Utilities stores new reading positions.

Goodreads Sync is asked about progress after Kobo Utilities has stored it; this does the same for
AniList. Two pure pieces (no Calibre imports, unit tested) plus one small hook:

* :func:`affected_items` - the linked series that contain a book whose position just changed.
* :func:`worth_prompting` - decided locally, with no network: is there a chapter to send that AniList is
  not already known to have? If not, stay silent, so connecting the Kobo never nags.
* :func:`install` - wraps Kobo Utilities' ``_read_completed`` callback on the running object only.
  Nothing on disk changes; the wrapper disappears when Calibre closes. If Kobo Utilities is missing or
  has changed, it does not install and says why.
"""
from . import planner, sync

HOOK_MODULE = 'calibre_plugins.koboutilities.features.locations'
HOOK_NAME = '_read_completed'


class State(object):
    installed = False
    reason = 'not started'
    prompts = 0


STATE = State()


def affected_items(changed_ids, records):
    """``[(series, records)]`` for linked series with at least one changed volume."""
    changed = set(changed_ids)
    out = []
    for series, recs in sync.group_by_series(records).items():
        if sync.anilist_id_for(recs) and any(r.book_id in changed for r in recs):
            out.append((series, recs))
    return out


def worth_prompting(items, options, known_pushed):
    """True if some series has a chapter above what AniList is known to have. No network."""
    known = known_pushed or {}
    for series, recs in items:
        anilist_id = sync.anilist_id_for(recs)
        plan = planner.plan_series(series, sync.volume_inputs(recs), anilist_id, None, None,
                                   options, entry_known=False)
        if not plan.target:
            continue
        sent = known.get(anilist_id)
        if sent is None or plan.target > sent:
            return True
    return False


def changed_ids_from_job(job):
    """Book ids Kobo Utilities found changes for (``job.result[0]`` is ``{book_id: ...}``)."""
    if getattr(job, 'failed', True):
        return []
    result = getattr(job, 'result', None)
    try:
        return list(result[0] or {})
    except (TypeError, IndexError, KeyError):
        return []


def install(module, on_changed, log=lambda m: None, state=STATE):
    """Wrap ``module._read_completed``. ``on_changed(ids)`` runs after Kobo Utilities finished."""
    original = getattr(module, HOOK_NAME, None)
    if not callable(original):
        state.installed = False
        state.reason = "Kobo Utilities has no '%s' (the plugin changed)" % HOOK_NAME
        return False
    if getattr(original, '_anilist_auto', False):
        state.installed, state.reason = True, 'active'
        return True

    def wrapped(job, *args, **kwargs):
        try:
            return original(job, *args, **kwargs)
        finally:
            try:
                ids = changed_ids_from_job(job)
                if ids:
                    on_changed(ids)
            except Exception as exc:                 # never break Kobo Utilities' own flow
                log('[AniList] auto sync check failed: %s' % exc)

    wrapped._anilist_auto = True
    wrapped.__wrapped__ = original
    setattr(module, HOOK_NAME, wrapped)
    state.installed, state.reason = True, 'active'
    return True


def uninstall(module, state=STATE):
    cur = getattr(module, HOOK_NAME, None)
    if getattr(cur, '_anilist_auto', False):
        setattr(module, HOOK_NAME, cur.__wrapped__)
    state.installed, state.reason = False, 'removed'
