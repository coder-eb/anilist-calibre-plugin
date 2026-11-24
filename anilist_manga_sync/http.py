"""Tiny HTTP layer shared by the AniList and MangaDex clients.

``Transport`` is any callable ``(method, url, body, headers) -> (status, headers, text)``
so tests (and the Calibre glue) can swap it. The default uses ``urllib``.
"""

import urllib.error
import urllib.request

USER_AGENT = 'anilist-calibre-plugin/0.1 (+https://github.com/coder-eb/anilist-calibre-plugin)'


def urllib_transport(method, url, body, headers, timeout=30):
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as exc:       # 4xx/5xx still carry a useful body
        text = ''
        try:
            text = exc.read().decode('utf-8', 'replace')
        except Exception:
            pass
        return exc.code, dict(exc.headers or {}), text
