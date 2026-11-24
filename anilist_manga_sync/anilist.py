"""Minimal AniList GraphQL client: search, read your entry, save progress.

Authentication is a personal access token (see README): create an API client at
https://anilist.co/settings/developer, authorize it once, paste the token into
the plugin settings. Reading/searching public data needs no token; changing
your list does.
"""

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


def display_title(media):
    t = media.get('title') or {}
    return t.get('english') or t.get('romaji') or t.get('native') or str(media.get('id'))


class AniListClient(object):
    def __init__(self, token=None, transport=urllib_transport, sleep=time.sleep,
                 max_retries=3):
        self.token = (token or '').strip() or None
        self.transport = transport
        self.sleep = sleep
        self.max_retries = max_retries

    def _post(self, query, variables, auth=False):
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json',
                   'User-Agent': USER_AGENT}
        if auth:
            if not self.token:
                raise AuthError('No AniList token set - add one in the plugin settings')
            headers['Authorization'] = 'Bearer ' + self.token
        body = json.dumps({'query': query, 'variables': variables}).encode('utf-8')
        for attempt in range(self.max_retries + 1):
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
            raise AniListError('AniList rate limit hit; try again in a minute')
        if data.get('errors'):
            raise AniListError('; '.join(e.get('message') or 'error' for e in data['errors']))
        if status != 200:
            raise AniListError('AniList HTTP %s' % status)
        return data.get('data') or {}

    def search(self, title, per_page=6):
        """Manga matching ``title`` (MANGA format only: no novels or one-shots)."""
        data = self._post(SEARCH_QUERY, {'search': title, 'perPage': per_page})
        return (data.get('Page') or {}).get('media') or []

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
