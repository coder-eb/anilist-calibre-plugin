import json
import sys
import types
import unittest
from pathlib import Path

pkg = types.ModuleType('ams_test3')
pkg.__path__ = [str(Path(__file__).resolve().parents[1] / 'anilist_manga_sync')]
sys.modules[pkg.__name__] = pkg
from ams_test3 import anilist, http, planner, sync  # noqa: E402


class Counting(object):
    """A fake AniList that answers batch queries and counts requests."""

    def __init__(self, progress_by_id=None, missing=()):
        self.calls, self.progress, self.missing = [], progress_by_id or {}, set(missing)

    def __call__(self, method, url, body, headers):
        payload = json.loads(body)
        self.calls.append(payload)
        ids = [i for i in payload['variables'].get('ids', []) if i not in self.missing]
        media = [{'id': i, 'chapters': None,
                  'mediaListEntry': ({'status': 'CURRENT', 'progress': self.progress[i]}
                                     if i in self.progress else None)} for i in ids]
        return 200, {}, json.dumps({'data': {'Page': {'media': media}}})


def rec(book_id, series, index, identifiers=None, percent=None, rng=None):
    return sync.BookRecord(book_id, '%s %g' % (series, index), series, index, identifiers, percent, rng)


def linked_item(name, anilist_id, percent=100, rng='1-8'):
    return (name, [rec(anilist_id, name, 1, {'anilist': str(anilist_id)}, percent, rng)])


class EntriesTests(unittest.TestCase):
    def test_many_series_one_request(self):
        t = Counting({1: 3, 2: 0})
        got = anilist.AniListClient('tok', transport=t).entries([1, 2, 3])
        self.assertEqual(len(t.calls), 1)
        self.assertEqual(sorted(got), [1, 2, 3])
        self.assertEqual(got[1]['mediaListEntry']['progress'], 3)
        self.assertIsNone(got[3]['mediaListEntry'])
        self.assertEqual(t.calls[0]['variables']['ids'], [1, 2, 3])
        self.assertIn('id_in', t.calls[0]['query'])

    def test_requests_are_chunked_at_fifty(self):
        t = Counting()
        got = anilist.AniListClient('tok', transport=t).entries(range(1, 121))
        self.assertEqual([len(c['variables']['ids']) for c in t.calls], [50, 50, 20])
        self.assertEqual(len(got), 120)

    def test_duplicates_and_empty(self):
        t = Counting()
        c = anilist.AniListClient('tok', transport=t)
        self.assertEqual(len(c.entries([5, 5, 5])), 1)
        self.assertEqual(len(t.calls), 1)
        self.assertEqual(c.entries([]), {})
        self.assertEqual(len(t.calls), 1)                       # nothing asked, nothing sent

    def test_unknown_ids_are_just_missing(self):
        got = anilist.AniListClient('tok', transport=Counting(missing={9})).entries([1, 9])
        self.assertEqual(sorted(got), [1])

    def test_needs_a_token(self):
        with self.assertRaises(anilist.AuthError):
            anilist.AniListClient(transport=Counting()).entries([1])


class PlanAllTests(unittest.TestCase):
    def engine(self, transport):
        return sync.Engine(anilist.AniListClient('tok', transport=transport))

    def test_thirteen_series_cost_one_request(self):
        t = Counting({i: 3 for i in range(1, 14)})
        plans = self.engine(t).plan_all([linked_item('S%d' % i, i) for i in range(1, 14)])
        self.assertEqual(len(t.calls), 1)
        self.assertEqual([p.action for p in plans], ['update'] * 13)
        self.assertEqual([p.series for p in plans], ['S%d' % i for i in range(1, 14)])   # order kept

    def test_series_with_nothing_to_send_are_not_looked_up(self):
        t = Counting()
        items = [linked_item('read', 1), linked_item('unread', 2, percent=None),
                 ('unlinked', [rec(3, 'unlinked', 1, None, 100, '1-8')])]
        plans = self.engine(t).plan_all(items)
        self.assertEqual(t.calls[0]['variables']['ids'], [1])       # only the one that can change
        self.assertEqual([p.action for p in plans], ['add', 'nothing', 'unlinked'])

    def test_no_request_at_all_when_nothing_can_change(self):
        t = Counting()
        plans = self.engine(t).plan_all([linked_item('unread', 1, percent=None)])
        self.assertEqual(t.calls, [])
        self.assertEqual(plans[0].action, 'nothing')

    def test_no_token_means_no_requests(self):
        t = Counting()
        plans = self.engine(t).plan_all([linked_item('a', 1), linked_item('b', 2)], fetch_entries=False)
        self.assertEqual(t.calls, [])
        self.assertEqual([p.action for p in plans], ['preview', 'preview'])

    def test_missing_on_anilist_is_reported_not_pushed(self):
        plans = self.engine(Counting(missing={2})).plan_all([linked_item('a', 1), linked_item('b', 2)])
        self.assertEqual(plans[0].action, 'add')
        self.assertEqual(plans[1].action, 'skip')
        self.assertIn('no manga with id 2', plans[1].reason)
        self.assertFalse(plans[1].will_push)

    def test_already_up_to_date_still_skips(self):
        plans = self.engine(Counting({1: 99})).plan_all([linked_item('a', 1)])
        self.assertEqual((plans[0].action, plans[0].target), ('skip', 99))

    def test_matches_the_single_series_planner(self):
        recs = [rec(1, 'K', 1, {'anilist': '5'}, 100, '1-8'), rec(2, 'K', 2, None, 88, '9-18')]
        t1 = Counting({5: 3})
        batch = self.engine(t1).plan_all([('K', recs)])[0]
        single = sync.Engine(anilist.AniListClient('tok', transport=lambda m, u, b, h: (
            200, {}, json.dumps({'data': {'Media': {'id': 5, 'chapters': None,
                                                    'mediaListEntry': {'status': 'CURRENT', 'progress': 3}}}})))).plan('K', recs)
        self.assertEqual((batch.action, batch.target, batch.reason), (single.action, single.target, single.reason))


class ClientBehaviourTests(unittest.TestCase):
    def test_pacing_spaces_requests(self):
        now, sleeps = [100.0], []
        t = Counting()
        c = anilist.AniListClient('tok', transport=t, min_interval=0.7,
                                  sleep=lambda s: (sleeps.append(round(s, 3)), now.__setitem__(0, now[0] + s)),
                                  clock=lambda: now[0])
        c.entries([1])
        now[0] += 0.2
        c.entries([2])
        self.assertEqual(sleeps, [0.5])                 # waited only the remaining 0.5 s
        now[0] += 5
        c.entries([3])
        self.assertEqual(sleeps, [0.5])                 # long enough since the last one: no wait

    def test_no_pacing_by_default(self):
        sleeps = []
        c = anilist.AniListClient('tok', transport=Counting(), sleep=sleeps.append)
        c.entries([1]); c.entries([2])
        self.assertEqual(sleeps, [])

    def test_short_timeout_reaches_the_default_transport(self):
        c = anilist.AniListClient('tok', timeout=8)
        self.assertEqual(c.transport.keywords, {'timeout': 8})
        self.assertIs(c.transport.func, http.urllib_transport)
        self.assertIs(anilist.AniListClient('tok').transport, http.urllib_transport)

    def test_a_custom_transport_is_left_alone(self):
        t = Counting()
        self.assertIs(anilist.AniListClient('tok', transport=t, timeout=8).transport, t)


if __name__ == '__main__':
    unittest.main()


class GentleTests(unittest.TestCase):
    """Rules that keep this plugin kind to AniList's servers."""

    def engine(self, t):
        return sync.Engine(anilist.AniListClient('tok', transport=t))

    def test_series_already_sent_are_not_looked_up(self):
        t = Counting()
        plans = self.engine(t).plan_all([linked_item('a', 1, rng='1-8')], known_pushed={1: 8})
        self.assertEqual(t.calls, [])                              # zero requests
        self.assertEqual(plans[0].action, 'skip')
        self.assertIn('already sent chapter 8', plans[0].reason)
        self.assertFalse(plans[0].will_push)

    def test_news_is_looked_up_even_if_something_was_sent_before(self):
        t = Counting({1: 8})
        plans = self.engine(t).plan_all([linked_item('a', 1, rng='1-8')], known_pushed={1: 5})
        self.assertEqual(len(t.calls), 1)                          # target 8 > sent 5: worth asking
        self.assertEqual(t.calls[0]['variables']['ids'], [1])

    def test_only_the_series_with_news_are_in_the_request(self):
        t = Counting({2: 1})
        items = [linked_item('quiet', 1, rng='1-8'), linked_item('news', 2, rng='1-8')]
        self.engine(t).plan_all(items, known_pushed={1: 8})
        self.assertEqual(t.calls[0]['variables']['ids'], [2])

    def test_known_pushed_is_ignored_in_preview_mode(self):
        t = Counting()
        plans = self.engine(t).plan_all([linked_item('a', 1)], fetch_entries=False, known_pushed={1: 8})
        self.assertEqual((t.calls, plans[0].action), ([], 'preview'))

    def test_rate_limit_stops_the_batch_and_is_not_hammered(self):
        sleeps, calls = [], []

        def always_429(method, url, body, headers):
            calls.append(1)
            return 429, {'Retry-After': '30'}, ''
        c = anilist.AniListClient('tok', transport=always_429, sleep=sleeps.append)
        with self.assertRaises(anilist.RateLimited) as cm:
            c.save_progress(1, 5)
        self.assertTrue(cm.exception.abort_batch)
        self.assertEqual(len(calls), c.max_retries + 1)            # a few polite retries, then give up
        self.assertEqual(sleeps, [30, 30, 30])                     # each one waited as AniList asked
        self.assertIn('busy', str(cm.exception))

    def test_other_errors_do_not_stop_a_batch(self):
        self.assertFalse(getattr(anilist.AniListError('x'), 'abort_batch', False))
        self.assertFalse(getattr(anilist.AuthError('x'), 'abort_batch', False))
