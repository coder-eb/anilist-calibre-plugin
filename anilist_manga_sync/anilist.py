"""Minimal AniList GraphQL client: search, read your entry, save progress.

Authentication is a personal access token (see README): create an API client at
https://anilist.co/settings/developer, authorize it once, paste the token into
the plugin settings. Reading/searching public data needs no token; changing
your list does.
"""

import functools
import json
import time

from .http import USER_AGENT, urllib_transport

API_URL = 'https://graphql.anilist.co'

SEARCH_QUERY = '''
query($search: String, $perPage: Int) {
  Page(perPage: $perPage) {
    media(search: $search, type: MANGA, format_in: [MANGA], sort: SEARCH_MATCH) {
      id format status chapters volumes
      startDate { year }
      title { romaji english native }
      synonyms
    }
  }
}'''

ENTRY_QUERY = '''
query($id: Int) {
  Media(id: $id, type: MANGA) {
    id chapters volumes status
    title { romaji english native }
    mediaListEntry { id status progress progressVolumes }
  }
}'''

MEDIA_QUERY = '''
query($id: Int) {
  Media(id: $id, type: MANGA) {
    id format status chapters volumes
    title { romaji english native }
  }
}'''

# Up to 50 series' entries in ONE request (AniList's page-size limit), instead of one request each.
ENTRIES_QUERY = '''
query($ids: [Int], $perPage: Int) {
  Page(perPage: $perPage) {
    media(id_in: $ids, type: MANGA) {
      id chapters volumes status
      mediaListEntry { id status progress progressVolumes }
    }
  }
}'''
BATCH_SIZE = 50

VIEWER_QUERY = 'query { Viewer { id name } }'

SAVE_MUTATION = '''
mutation($mediaId: Int, $progress: Int, $progressVolumes: Int, $status: MediaListStatus) {
  SaveMediaListEntry(mediaId: $mediaId, progress: $progress,
                     progressVolumes: $progressVolumes, status: $status) {
    id status progress progressVolumes
  }
}'''


class AniListError(Exception):
    pass


class AuthError(AniListError):
    """Missing, invalid or expired token."""


class RateLimited(AniListError):
    """AniList said slow down (HTTP 429) and kept saying it: stop sending, do not try the next item."""
    abort_batch = True


def display_title(media):
    t = media.get('title') or {}
    return t.get('english') or t.get('romaji') or t.get('native') or str(media.get('id'))


class AniListClient(object):
    def __init__(self, token=None, transport=urllib_transport, sleep=time.sleep,
                 max_retries=3, timeout=None, min_interval=0.0, clock=time.monotonic):
        """``timeout`` (seconds) applies to the default transport: use a short one for calls a person
        is waiting on. ``min_interval`` spaces requests out so a burst of pushes stays well inside
        AniList's rate limit."""
        self.token = (token or '').strip() or None
        if timeout and transport is urllib_transport:
            transport = functools.partial(urllib_transport, timeout=timeout)
        self.transport = transport
        self.sleep = sleep
        self.max_retries = max_retries
        self.min_interval = min_interval
        self.clock = clock
        self._last_request = None

    def _post(self, query, variables, auth=False):
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json',
                   'User-Agent': USER_AGENT}
        if auth:
            if not self.token:
                raise AuthError('No AniList token set - add one in the plugin settings')
            headers['Authorization'] = 'Bearer ' + self.token
        body = json.dumps({'query': query, 'variables': variables}).encode('utf-8')
        for attempt in range(self.max_retries + 1):
            if self.min_interval and self._last_request is not None:
                wait = self._last_request + self.min_interval - self.clock()
                if wait > 0:
                    self.sleep(wait)
            self._last_request = self.clock()
            status, resp_headers, text = self.transport('POST', API_URL, body, headers)
            if status == 429 and attempt < self.max_retries:
                wait = 5
                for k, v in resp_headers.items():
                    if k.lower() == 'retry-after':
                        try:
                            wait = min(90, max(1, int(float(v))))
                        except ValueError:
                            pass
                self.sleep(wait)
                continue
            if status >= 500 and attempt < self.max_retries:
                self.sleep(2 * (attempt + 1))
                continue
            break
        try:
            data = json.loads(text) if text else {}
        except ValueError:
            raise AniListError('AniList returned a non-JSON response (HTTP %s)' % status)
        if status == 401 or any('invalid token' in (e.get('message') or '').lower()
                                for e in data.get('errors') or []):
            raise AuthError('AniList rejected the token - create a new one in the plugin settings')
        if status == 429:
            raise RateLimited('AniList is busy (rate limit); nothing more was sent. Try again in a few minutes.')
        if data.get('errors'):
            raise AniListError('; '.join(e.get('message') or 'error' for e in data['errors']))
        if status != 200:
            raise AniListError('AniList HTTP %s' % status)
        return data.get('data') or {}

    def search(self, title, per_page=6):
        """Manga matching ``title`` (MANGA format only: no novels or one-shots)."""
        data = self._post(SEARCH_QUERY, {'search': title, 'perPage': per_page})
        return (data.get('Page') or {}).get('media') or []

    def media(self, media_id):
        """Public details of a manga (no token needed); {} if it does not exist."""
        return self._post(MEDIA_QUERY, {'id': int(media_id)}).get('Media') or {}

    def entries(self, media_ids):
        """``{media_id: media}`` (with ``mediaListEntry``) for many series, 50 per request.

        Ids that are not manga / do not exist are simply absent from the result.
        """
        ids = list(dict.fromkeys(int(i) for i in media_ids))
        out = {}
        for start in range(0, len(ids), BATCH_SIZE):
            chunk = ids[start:start + BATCH_SIZE]
            data = self._post(ENTRIES_QUERY, {'ids': chunk, 'perPage': BATCH_SIZE}, auth=True)
            for media in (data.get('Page') or {}).get('media') or []:
                out[media['id']] = media
        return out

    def viewer(self):
        return self._post(VIEWER_QUERY, {}, auth=True).get('Viewer') or {}

    def entry(self, media_id):
        """The media plus *your* list entry (``mediaListEntry`` is None if not listed)."""
        return self._post(ENTRY_QUERY, {'id': int(media_id)}, auth=True).get('Media') or {}

    def save_progress(self, media_id, progress, progress_volumes=None, status=None):
        variables = {'mediaId': int(media_id), 'progress': int(progress)}
        if progress_volumes is not None:
            variables['progressVolumes'] = int(progress_volumes)
        if status:
            variables['status'] = status
        return self._post(SAVE_MUTATION, variables, auth=True).get('SaveMediaListEntry') or {}
