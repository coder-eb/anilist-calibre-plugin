"""Parse an AniList link the way a person would paste it."""

import re

_URL = re.compile(r'anilist\.co/(?P<kind>manga|anime)/(?P<id>\d+)', re.IGNORECASE)
_IDENT = re.compile(r'^anilist:(\d+)$', re.IGNORECASE)
_DIGITS = re.compile(r'^\d+$')


def parse_link(text):
    """``(anilist_id, problem)``: ``(169355, '')`` for a good link, ``(None, '')`` for empty
    input, ``(None, message)`` for something that is not an AniList manga link.

    Accepts ``https://anilist.co/manga/169355/Kagurabachi/``, ``anilist.co/manga/169355``,
    ``anilist:169355`` and a bare id.
    """
    text = (text or '').strip()
    if not text:
        return None, ''
    m = _URL.search(text)
    if m:
        if m.group('kind').lower() == 'anime':
            return None, 'That is an anime page; this plugin links manga'
        return int(m.group('id')), ''
    m = _IDENT.match(text)
    if m:
        return int(m.group(1)), ''
    if _DIGITS.match(text):
        return int(text), ''
    return None, 'Not an AniList manga link (expected https://anilist.co/manga/<id>/...)'


def page_url(anilist_id):
    return 'https://anilist.co/manga/%d' % int(anilist_id)
