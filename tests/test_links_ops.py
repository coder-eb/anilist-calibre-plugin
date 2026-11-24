import json
import sys
import types
import unittest
from pathlib import Path

pkg = types.ModuleType('ams_test2')
pkg.__path__ = [str(Path(__file__).resolve().parents[1] / 'anilist_manga_sync')]
sys.modules[pkg.__name__] = pkg
from ams_test2 import anilist, links, ops  # noqa: E402


class ParseLinkTests(unittest.TestCase):
    def test_good_links(self):
        for text in ('https://anilist.co/manga/169355/Kagurabachi/', 'http://anilist.co/manga/169355',
                     'anilist.co/manga/169355/x', ' https://anilist.co/manga/169355/Kagurabachi/?x=1 ',
                     'anilist:169355', 'ANILIST:169355', '169355'):
            self.assertEqual(links.parse_link(text), (169355, ''), text)

    def test_empty_means_clear(self):
        self.assertEqual(links.parse_link(''), (None, ''))
        self.assertEqual(links.parse_link('   '), (None, ''))
        self.assertEqual(links.parse_link(None), (None, ''))

    def test_not_a_manga_link(self):
        for bad in ('https://anilist.co/anime/21/One-Piece', 'https://www.goodreads.com/book/show/123',
                    'kagurabachi', 'anilist:abc', '12x'):
            ident, problem = links.parse_link(bad)
            self.assertIsNone(ident, bad)
            self.assertTrue(problem, bad)
        self.assertIn('anime', links.parse_link('https://anilist.co/anime/21')[1])

    def test_page_url(self):
        self.assertEqual(links.page_url(169355), 'https://anilist.co/manga/169355')


class FakeApi(object):
    def __init__(self, series, identifiers=None, columns=None):
        self.series, self.idents, self.cols, self.writes = series, identifiers or {}, columns or {}, []

    def all_book_ids(self):
        return list(self.series)

    def field_for(self, name, bid):
        if name == 'series':
            return self.series.get(bid)
        if name == 'identifiers':
            return dict(self.idents.get(bid, {}))
        return self.cols.get((name, bid))

    def set_field(self, name, changes):
        self.writes.append((name, dict(changes)))
        for bid, val in changes.items():
            if name == 'identifiers':
                self.idents[bid] = dict(val)
            else:
                self.cols[(name, bid)] = val


class OpsTests(unittest.TestCase):
    def api(self):
        return FakeApi({1: 'Kagurabachi', 2: 'Kagurabachi', 3: 'kagurabachi', 4: 'Other', 5: None},
                       {1: {'isbn': '9'}, 4: {'goodreads': '7'}})

    def test_series_siblings(self):
        self.assertEqual(sorted(ops.series_siblings(self.api(), [1])), [1, 2, 3])
        self.assertEqual(ops.series_siblings(self.api(), [5]), [5])             # no series: just itself

    def test_set_link_merges_and_keeps_other_identifiers(self):
        api = self.api()
        self.assertEqual(ops.set_anilist_link(api, [1, 4], 169355), [1, 4])
        self.assertEqual(api.idents[1], {'isbn': '9', 'anilist': '169355'})
        self.assertEqual(api.idents[4], {'goodreads': '7', 'anilist': '169355'})

    def test_set_link_is_idempotent_and_replaces(self):
        api = self.api()
        ops.set_anilist_link(api, [1], 169355)
        api.writes.clear()
        self.assertEqual(ops.set_anilist_link(api, [1], 169355), [])
        self.assertEqual(api.writes, [])                                       # nothing written
        self.assertEqual(ops.set_anilist_link(api, [1], 111), [1])
        self.assertEqual(api.idents[1]['anilist'], '111')

    def test_remove_link(self):
        api = self.api()
        ops.set_anilist_link(api, [1, 2], 169355)
        self.assertEqual(ops.remove_anilist_link(api, [1, 2, 3]), [1, 2])
        self.assertEqual(api.idents[1], {'isbn': '9'})

    def test_write_chapter_column_only_changes(self):
        api = FakeApi({1: 'K', 2: 'K'}, columns={('#manga_chapter_read', 1): 8})
        self.assertEqual(ops.write_chapter_column(api, '#manga_chapter_read', {1: 8, 2: 18}), [2])
        self.assertEqual(api.writes, [('#manga_chapter_read', {2: 18})])
        api.writes.clear()
        self.assertEqual(ops.write_chapter_column(api, '#manga_chapter_read', {1: 8, 2: 18}), [])
        self.assertEqual(api.writes, [])


class FakeTransport(object):
    def __init__(self, response):
        self.response, self.calls = response, []

    def __call__(self, method, url, body, headers):
        self.calls.append((json.loads(body), headers))
        return 200, {}, json.dumps(self.response)


class MediaTests(unittest.TestCase):
    def test_media_is_public_and_returns_details(self):
        t = FakeTransport({'data': {'Media': {'id': 169355, 'format': 'MANGA', 'chapters': None,
                                              'title': {'romaji': 'Kagurabachi'}}}})
        media = anilist.AniListClient(transport=t).media(169355)
        self.assertEqual(anilist.display_title(media), 'Kagurabachi')
        body, headers = t.calls[0]
        self.assertIn('type: MANGA', body['query'])
        self.assertEqual(body['variables'], {'id': 169355})
        self.assertNotIn('Authorization', headers)

    def test_media_missing(self):
        t = FakeTransport({'data': {'Media': None}})
        self.assertEqual(anilist.AniListClient(transport=t).media(1), {})


if __name__ == '__main__':
    unittest.main()
