"""Chapters completed in a volume, from its chapter range and Kobo's percent read.

The plugin deliberately does not look inside the books: a volume is described only
by the range of chapters it contains (the ``#manga_chapters`` column, e.g. "19-27")
and how far through it you are (Kobo's percent read). Chapters are assumed to be
about the same length, so progress inside a volume is an estimate, rounded *down*:
AniList should lag the truth by about a chapter rather than run ahead of it. A
finished volume is exact, because its last chapter is known.
"""

import math
import re

FINISHED_PERCENT = 98      # Kobo rarely reports exactly 100 on the last page

_NUMBER = r'(\d+(?:\.\d+)?)'
_RANGE = re.compile(r'^\s*%s\s*[-–—]\s*%s\s*$' % (_NUMBER, _NUMBER))
_SINGLE = re.compile(r'^\s*%s\s*$' % _NUMBER)


def parse_range(text):
    """``"19-27"`` -> ``(19.0, 27.0)``; ``"5"`` -> ``(5.0, 5.0)``; anything else -> None."""
    if not text:
        return None
    m = _RANGE.match(str(text))
    if m:
        first, last = float(m.group(1)), float(m.group(2))
    else:
        m = _SINGLE.match(str(text))
        if not m:
            return None
        first = last = float(m.group(1))
    if last < first or last < 1:
        return None
    return first, last


def _floor(x):
    return int(math.floor(x + 1e-9))


def completed_from_range(first, last, percent, count_in_progress=False):
    """``(completed_chapter, finished)`` for a volume holding chapters first..last.

    ``completed_chapter`` is the highest chapter fully read (int), or None when
    nothing in this volume is finished yet.
    """
    if not percent or percent <= 0 or last is None:
        return None, False
    hi = _floor(last)
    if percent >= FINISHED_PERCENT:
        return hi, True
    lo = _floor(first)
    count = max(1, hi - lo + 1)
    done = _floor(percent / 100.0 * count)
    if count_in_progress:
        done = min(count, done + 1)
    result = lo - 1 + done
    return (result if result > 0 else None), False
