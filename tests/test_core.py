import json
import sys
import types
import unittest
from pathlib import Path

pkg = types.ModuleType('ams_test')
pkg.__path__ = [str(Path(__file__).resolve().parents[1] / 'anilist_manga_sync')]
sys.modules[pkg.__name__] = pkg
from ams_test import anilist, chapters, planner, sync  # noqa: E402
from ams_test.planner import Options, VolumeInput, plan_series, resolve_volume  # noqa: E402


class ParseRangeTests(unittest.TestCase):
    def test_formats(self):
        p = chapters.parse_range
        self.assertEqual(p('19-27'), (19, 27))
        self.assertEqual(p(' 19 - 27 '), (19, 27))
        self.assertEqual(p('19\u201327'), (19, 27))            # en dash
        self.assertEqual(p('5'), (5, 5))
        self.assertEqual(p('1134-1144'), (1134, 1144))

    def test_rejects_junk(self):
        p = chapters.parse_range
        for bad in (None, '', 'abc', '27-19', '0', '0-0', '19-', '-27', '19,27'):
            self.assertIsNone(p(bad), bad)


class CompletedFromRangeTests(unittest.TestCase):
    def test_the_worked_example_vol3_19_27(self):
        # 9 chapters; 88% -> floor(7.92)=7 -> chapters 19..25 completed
        self.assertEqual(chapters.completed_from_range(19, 27, 88), (25, False))
        self.assertEqual(chapters.completed_from_range(19, 27, 100), (27, True))

    def test_table_for_17_25(self):
        expect = {10: 16, 25: 18, 50: 20, 75: 22, 90: 24, 97: 24}
        for pct, want in expect.items():
            self.assertEqual(chapters.completed_from_range(17, 25, pct), (want, False), pct)
        self.assertEqual(chapters.completed_from_range(17, 25, 100), (25, True))

    def test_finished_threshold(self):
        self.assertEqual(chapters.completed_from_range(17, 25, 98), (25, True))
        self.assertEqual(chapters.completed_from_range(17, 25, 97)[1], False)

    def test_nothing_finished_yet(self):
        self.assertEqual(chapters.completed_from_range(19, 27, 5), (18, False))   # prior volume's last
        self.assertEqual(chapters.completed_from_range(1, 8, 5), (None, False))   # first volume of a series
        self.assertEqual(chapters.completed_from_range(19, 27, 0), (None, False))
        self.assertEqual(chapters.completed_from_range(19, 27, None), (None, False))

    def test_count_in_progress_option(self):
        self.assertEqual(chapters.completed_from_range(19, 27, 88, True), (26, False))
        self.assertEqual(chapters.completed_from_range(19, 27, 97, True), (27, False))

    def test_single_chapter_volume(self):
        self.assertEqual(chapters.completed_from_range(5, 5, 50), (4, False))   # chapter 5 unfinished: 4 is done
        self.assertEqual(chapters.completed_from_range(5, 5, 100), (5, True))

    def test_never_runs_ahead_of_the_estimate(self):
        for pct in range(0, 98):
            done, _ = chapters.completed_from_range(10, 18, pct)
            self.assertTrue(done is None or done <= 9 + (pct / 100.0) * 9 + 1e-9)


class FakeTransport(object):
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, body, headers):
        self.calls.append((method, url, json.loads(body) if body else None, headers))
        status, hdrs, payload = self.responses.pop(0)
        return status, hdrs, payload if isinstance(payload, str) else json.dumps(payload)


class AniListTests(unittest.TestCase):
    def test_search_sends_manga_only_query_with_user_agent(self):
        t = FakeTransport((200, {}, {'data': {'Page': {'media': [{'id': 61499}]}}}))
        res = anilist.AniListClient(transport=t).search('Nisekoi')
        self.assertEqual(res, [{'id': 61499}])
        method, url, body, headers = t.calls[0]
        self.assertIn('format_in: [MANGA]', body['query'])
        self.assertEqual(body['variables']['search'], 'Nisekoi')
        self.assertIn('anilist-calibre-plugin', headers['User-Agent'])
        self.assertNotIn('Authorization', headers)           # search needs no token

    def test_changes_need_a_token(self):
        c = anilist.AniListClient(transport=FakeTransport())
        with self.assertRaises(anilist.AuthError):
            c.save_progress(1, 5)
        with self.assertRaises(anilist.AuthError):
            c.entry(1)

    def test_save_progress_payload_and_bearer(self):
        t = FakeTransport((200, {}, {'data': {'SaveMediaListEntry': {'id': 9, 'progress': 26}}}))
        out = anilist.AniListClient('tok', transport=t).save_progress(61499, 26, 3, 'CURRENT')
        self.assertEqual(out['progress'], 26)
        _m, _u, body, headers = t.calls[0]
        self.assertEqual(headers['Authorization'], 'Bearer tok')
        self.assertEqual(body['variables'], {'mediaId': 61499, 'progress': 26,
                                             'progressVolumes': 3, 'status': 'CURRENT'})

    def test_optional_fields_omitted(self):
        t = FakeTransport((200, {}, {'data': {'SaveMediaListEntry': {}}}))
        anilist.AniListClient('tok', transport=t).save_progress(1, 5)
        self.assertEqual(t.calls[0][2]['variables'], {'mediaId': 1, 'progress': 5})

    def test_invalid_token_is_an_auth_error(self):
        t = FakeTransport((400, {}, {'errors': [{'message': 'Invalid token'}]}))
        with self.assertRaises(anilist.AuthError):
            anilist.AniListClient('bad', transport=t).viewer()
        t = FakeTransport((401, {}, {}))
        with self.assertRaises(anilist.AuthError):
            anilist.AniListClient('bad', transport=t).viewer()

    def test_rate_limit_is_retried_using_retry_after(self):
        sleeps = []
        t = FakeTransport((429, {'Retry-After': '7'}, ''), (200, {}, {'data': {'Viewer': {'id': 1, 'name': 'x'}}}))
        v = anilist.AniListClient('tok', transport=t, sleep=sleeps.append).viewer()
        self.assertEqual(v['name'], 'x')
        self.assertEqual(sleeps, [7])

    def test_persistent_rate_limit_raises(self):
        t = FakeTransport(*[(429, {}, '')] * 4)
        with self.assertRaises(anilist.AniListError):
            anilist.AniListClient('tok', transport=t, sleep=lambda s: None).viewer()

    def test_graphql_errors_surface(self):
        t = FakeTransport((200, {}, {'errors': [{'message': 'boom'}]}))
        with self.assertRaises(anilist.AniListError) as cm:
            anilist.AniListClient(transport=t).search('x')
        self.assertIn('boom', str(cm.exception))

    def test_entry_returns_list_entry(self):
        media = {'id': 5, 'chapters': 240, 'mediaListEntry': {'status': 'CURRENT', 'progress': 10}}
        t = FakeTransport((200, {}, {'data': {'Media': media}}))
        self.assertEqual(anilist.AniListClient('t', transport=t).entry(5)['mediaListEntry']['progress'], 10)

    def test_display_title(self):
        self.assertEqual(anilist.display_title({'id': 1, 'title': {'romaji': 'R', 'english': 'E'}}), 'E')
        self.assertEqual(anilist.display_title({'id': 1, 'title': {'romaji': 'R'}}), 'R')



def vol(book_id, volume, percent, rng='1-8', title=None):
    return VolumeInput(book_id, title or 'Vol %g' % volume, volume, percent, chapters.parse_range(rng))


class ResolveVolumeTests(unittest.TestCase):
    def test_range_and_percent(self):
        r = resolve_volume(vol(1, 3, 88, '19-27'))
        self.assertEqual((r.completed, r.source, r.finished), (25, 'range', False))

    def test_unread(self):
        self.assertEqual(resolve_volume(vol(1, 3, None, '19-27')).source, 'unread')
        self.assertEqual(resolve_volume(vol(1, 3, 0, '19-27')).source, 'unread')

    def test_read_but_no_range(self):
        r = resolve_volume(vol(1, 3, 100, None))
        self.assertEqual((r.completed, r.source), (None, 'no-range'))


class PlanSeriesTests(unittest.TestCase):
    # Kagurabachi as it is today: vols 1-2 finished, vol 3 at 88%, the rest unread.
    VOLS = [vol(1, 1, 100, '1-8'), vol(2, 2, 100, '9-18'), vol(3, 3, 88, '19-27'),
            vol(4, 4, None, '28-36')]

    def plan(self, entry=None, anilist_id=169355, volumes=None, **kw):
        return plan_series('Kagurabachi', volumes if volumes is not None else self.VOLS,
                           anilist_id=anilist_id, entry=entry, **kw)

    def test_kagurabachi_today(self):
        p = self.plan()
        self.assertEqual((p.action, p.target, p.status), ('add', 25, 'CURRENT'))

    def test_highest_chapter_wins_regardless_of_order(self):
        p = self.plan(volumes=[vol(1, 9, 100, '75-85'), vol(2, 1, 100, '1-8')])
        self.assertEqual(p.target, 85)

    def test_gaps_in_the_library_do_not_matter(self):
        p = self.plan(volumes=[vol(1, 1, 100, '1-8'), vol(2, 112, 100, '1134-1144')])
        self.assertEqual(p.target, 1144)
        self.assertEqual(p.warnings, [])

    def test_update_raises_progress(self):
        p = self.plan(entry={'status': 'CURRENT', 'progress': 18})
        self.assertEqual((p.action, p.target, p.status, p.reason), ('update', 25, None, 'chapter 18 -> 25'))

    def test_never_lowers_progress(self):
        p = self.plan(entry={'status': 'CURRENT', 'progress': 40})
        self.assertEqual((p.action, p.target), ('skip', 40))
        self.assertFalse(p.will_push)

    def test_equal_progress_is_a_skip(self):
        self.assertEqual(self.plan(entry={'status': 'CURRENT', 'progress': 25}).action, 'skip')

    def test_completed_is_never_touched(self):
        p = self.plan(entry={'status': 'COMPLETED', 'progress': 3})
        self.assertEqual(p.action, 'skip')
        self.assertIn('COMPLETED', p.reason)

    def test_paused_and_dropped_skipped_unless_enabled(self):
        for status in ('PAUSED', 'DROPPED'):
            self.assertEqual(self.plan(entry={'status': status, 'progress': 1}).action, 'skip')
            p = self.plan(entry={'status': status, 'progress': 1}, options=Options(update_inactive=True))
            self.assertEqual(p.action, 'update')

    def test_planning_becomes_current_but_completed_is_never_set(self):
        p = self.plan(entry={'status': 'PLANNING', 'progress': 0})
        self.assertEqual((p.action, p.status), ('update', 'CURRENT'))
        for entry in (None, {'status': 'CURRENT', 'progress': 1}, {'status': 'PLANNING', 'progress': 0}):
            self.assertNotEqual(self.plan(entry=entry, volumes=[vol(1, 1, 100, '1-8')]).status, 'COMPLETED')

    def test_without_a_token_it_is_only_a_preview(self):
        p = self.plan(entry_known=False)
        self.assertEqual((p.action, p.target, p.will_push), ('preview', 25, False))

    def test_unlinked_series(self):
        p = self.plan(anilist_id=None)
        self.assertEqual((p.action, p.will_push), ('unlinked', False))

    def test_nothing_read(self):
        self.assertEqual(self.plan(volumes=[vol(1, 1, 0, '1-8')]).action, 'nothing')

    def test_read_volume_without_range_is_reported_and_ignored(self):
        p = self.plan(volumes=[vol(1, 9, 100, None), vol(2, 1, 100, '1-8')])
        self.assertTrue(any('vol 9' in w for w in p.warnings))
        self.assertEqual(p.target, 8)

    def test_cap_at_anilist_total(self):
        p = self.plan(media_chapters=20)
        self.assertEqual(p.target, 20)
        self.assertTrue(any('capped' in w for w in p.warnings))

    def test_count_in_progress_option(self):
        self.assertEqual(self.plan(options=Options(count_in_progress=True)).target, 26)


def rec(book_id, series, index, identifiers=None, percent=None, rng=None):
    return sync.BookRecord(book_id, '%s, Vol. %g' % (series, index or 0), series, index,
                           identifiers, percent, rng)


class SyncTests(unittest.TestCase):
    def test_group_by_series_orders_volumes_and_drops_seriesless(self):
        recs = [rec(3, 'B', 2), rec(1, 'A', 2), rec(2, 'A', 1), rec(4, None, 1)]
        grouped = sync.group_by_series(recs)
        self.assertEqual(list(grouped), ['A', 'B'])
        self.assertEqual([r.book_id for r in grouped['A']], [2, 1])

    def test_anilist_id_comes_only_from_the_saved_identifier(self):
        self.assertEqual(sync.anilist_id_for([rec(1, 'K', 1), rec(2, 'K', 2, {'anilist': '169355'})]), 169355)
        self.assertIsNone(sync.anilist_id_for([rec(1, 'K', 1, {'goodreads': '1', 'isbn': '9'})]))
        self.assertIsNone(sync.anilist_id_for([rec(1, 'K', 1, {'anilist': 'not-a-number'})]))

    def test_unlinked_series_never_calls_anilist(self):
        t = FakeTransport()
        engine = sync.Engine(anilist.AniListClient('tok', transport=t))
        plan = engine.plan('K', [rec(1, 'K', 1, None, 100, '1-8')])
        self.assertEqual(plan.action, 'unlinked')
        self.assertEqual(t.calls, [])

    def test_engine_end_to_end_with_a_fake_anilist(self):
        media = {'id': 169355, 'chapters': None,
                 'mediaListEntry': {'status': 'CURRENT', 'progress': 18}}
        t = FakeTransport((200, {}, {'data': {'Media': media}}),
                          (200, {}, {'data': {'SaveMediaListEntry': {'id': 1, 'progress': 25, 'status': 'CURRENT'}}}))
        engine = sync.Engine(anilist.AniListClient('tok', transport=t))
        recs = [rec(1, 'K', 1, None, 100, '1-8'), rec(2, 'K', 2, None, 100, '9-18'),
                rec(3, 'K', 3, {'anilist': '169355'}, 88, '19-27')]
        plan = engine.plan('K', recs)
        self.assertEqual((plan.action, plan.target), ('update', 25))
        saved = engine.apply(plan)
        self.assertEqual(saved['progress'], 25)
        variables = t.calls[1][2]['variables']
        # chapters only: no volume count, no status change for an entry already CURRENT
        self.assertEqual(variables, {'mediaId': 169355, 'progress': 25})

    def test_apply_refuses_when_there_is_nothing_to_push(self):
        plan = plan_series('K', [vol(1, 1, 0, '1-8')], 5, None)
        with self.assertRaises(ValueError):
            sync.Engine(anilist.AniListClient('tok', transport=FakeTransport())).apply(plan)


if __name__ == '__main__':
    unittest.main()
