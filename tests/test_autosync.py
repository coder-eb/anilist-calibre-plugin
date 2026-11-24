import sys
import types
import unittest
from pathlib import Path

import nonet  # noqa: F401  (blocks real network access in tests)

pkg = types.ModuleType('ams_test_auto')
pkg.__path__ = [str(Path(__file__).resolve().parents[1] / 'anilist_manga_sync')]
sys.modules[pkg.__name__] = pkg
from ams_test_auto import autosync, planner, sync  # noqa: E402


def rec(i, series, idx, pct, rng, anilist='100'):
    return sync.BookRecord(i, 'T%d' % i, series, idx, {'anilist': anilist} if anilist else {}, pct, rng)


RECS = [rec(1, 'A', 1, 100, '1-9'), rec(2, 'A', 2, 50, '10-18'),
        rec(3, 'B', 1, 100, '1-5', anilist=None), rec(4, 'C', 1, 100, '1-5', anilist='300')]


class Affected(unittest.TestCase):
    def test_only_linked_series_with_changed_book(self):
        items = autosync.affected_items([2, 3], RECS)
        self.assertEqual([s for s, _ in items], ['A'])          # B unlinked, C unchanged

    def test_none(self):
        self.assertEqual(autosync.affected_items([], RECS), [])


class Worth(unittest.TestCase):
    O = planner.Options()

    def test_new_chapters_prompt(self):
        items = autosync.affected_items([1], RECS)
        self.assertTrue(autosync.worth_prompting(items, self.O, {}))

    def test_already_known_is_silent(self):
        items = autosync.affected_items([1], RECS)
        self.assertFalse(autosync.worth_prompting(items, self.O, {100: 18}))

    def test_higher_than_known_prompts(self):
        items = autosync.affected_items([1], RECS)
        self.assertTrue(autosync.worth_prompting(items, self.O, {100: 4}))


class Hook(unittest.TestCase):
    def _mod(self):
        m = types.SimpleNamespace()
        m.calls = []
        m._read_completed = lambda job, *a, **k: m.calls.append('orig') or 'r'
        return m

    def test_runs_original_then_callback(self):
        m, seen = self._mod(), []
        self.assertTrue(autosync.install(m, seen.extend))
        job = types.SimpleNamespace(failed=False, result=({1: {}, 2: {}}, None))
        self.assertEqual(m._read_completed(job), 'r')
        self.assertEqual((m.calls, seen), (['orig'], [1, 2]))

    def test_failed_or_empty_job_does_nothing(self):
        m, seen = self._mod(), []
        autosync.install(m, seen.extend)
        m._read_completed(types.SimpleNamespace(failed=True, result=None))
        m._read_completed(types.SimpleNamespace(failed=False, result=({}, None)))
        self.assertEqual(seen, [])

    def test_callback_error_never_breaks_kobo_utilities(self):
        m = self._mod()
        autosync.install(m, lambda ids: 1 / 0)
        self.assertEqual(m._read_completed(types.SimpleNamespace(failed=False, result=({1: {}}, None))), 'r')

    def test_idempotent_uninstall_and_missing(self):
        m = self._mod()
        orig = m._read_completed
        autosync.install(m, lambda i: None)
        w = m._read_completed
        autosync.install(m, lambda i: None)
        self.assertIs(m._read_completed, w)
        autosync.uninstall(m)
        self.assertIs(m._read_completed, orig)
        self.assertFalse(autosync.install(types.SimpleNamespace(), lambda i: None))
        self.assertTrue(autosync.STATE.reason)


if __name__ == '__main__':
    unittest.main()
