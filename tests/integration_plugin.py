"""Integration test for the Calibre plugin, run inside Calibre's own Python with a throwaway config:

    python3 scripts/build_plugin.py
    CD=$(mktemp -d); mkdir -p $CD/lib; cp ~/"Calibre Library/metadata.db" $CD/lib/
    export HOME=$CD CALIBRE_CONFIG_DIRECTORY=$CD/cfg QT_QPA_PLATFORM=offscreen
    calibre-customize -a dist/AniList-Manga-Sync.zip
    calibre-debug tests/integration_plugin.py -- $CD/lib

Uses a copy of the library's metadata.db and a fake AniList: nothing real is read or written.
"""
import json
import sys
import time
import types

from qt.core import QApplication, QAction, QDialog, QMainWindow, Qt

app = QApplication([])

from calibre.customize.ui import find_plugin, initialize_plugins  # noqa: E402
from calibre.db.legacy import LibraryDatabase  # noqa: E402

initialize_plugins()
plugin = find_plugin('AniList Manga Sync')
print('plugin loaded:', plugin.name, plugin.version)

from calibre_plugins.anilist_calibre_plugin import anilist, library, ops, planner, sync  # noqa: E402
from calibre_plugins.anilist_calibre_plugin.config import ConfigWidget, PREFS, library_prefs  # noqa: E402
from calibre_plugins.anilist_calibre_plugin.dialogs import LinkDialog, SyncDialog  # noqa: E402

failures = []


def check(cond, msg):
    print(('PASS  ' if cond else 'FAIL  ') + msg)
    if not cond:
        failures.append(msg)


def wait_until(cond, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


# ---- a fake AniList (no network) ---------------------------------------------------------
saved_calls = []
ENTRY = {'status': 'CURRENT', 'progress': 18}


def fake_transport(method, url, body, headers):
    payload = json.loads(body)
    q, v = payload['query'], payload['variables']
    if 'SaveMediaListEntry' in q:
        saved_calls.append(v)
        return 200, {}, json.dumps({'data': {'SaveMediaListEntry': {'id': 1, 'progress': v['progress'],
                                                                     'status': v.get('status', 'CURRENT')}}})
    if 'mediaListEntry' in q:
        return 200, {}, json.dumps({'data': {'Media': {'id': v['id'], 'chapters': None, 'mediaListEntry': ENTRY}}})
    if 'Viewer' in q:
        return 200, {}, json.dumps({'data': {'Viewer': {'id': 1, 'name': 'tester'}}})
    return 200, {}, json.dumps({'data': {'Media': {'id': v['id'], 'format': 'MANGA', 'chapters': 54,
                                                   'status': 'RELEASING', 'title': {'romaji': 'Kagurabachi'}}}})


client = anilist.AniListClient('token', transport=fake_transport)

# ---- LinkDialog --------------------------------------------------------------------------
dlg = LinkDialog(None, 'Link "x"', '', client.media, True)
ok_btn = dlg.buttons.button(dlg.buttons.StandardButton.Ok)
dlg.edit.setText('nonsense')
check(not ok_btn.isEnabled(), 'link dialog: junk text disables OK')
dlg.edit.setText('https://anilist.co/anime/21/One-Piece')
check(not ok_btn.isEnabled() and 'anime' in dlg.status.text(), 'link dialog: an anime link is refused')
dlg.edit.setText('https://anilist.co/manga/169355/Kagurabachi/')
check(ok_btn.isEnabled() and dlg.result_link() == (169355, True), 'link dialog: pasted manga URL -> id 169355, whole series on')
dlg.check_btn.click()
check('Kagurabachi' in dlg.status.text() and 'MANGA' in dlg.status.text(), 'link dialog: "Check on AniList" shows the title (%s)' % dlg.status.text())
dlg.edit.setText('')
check(ok_btn.isEnabled() and dlg.result_link()[0] is None, 'link dialog: empty = remove the link')

# ---- the real library copy ---------------------------------------------------------------
lib_path = sys.argv[1]
db = LibraryDatabase(lib_path)
api = db.new_api
recs = library.load_records(db, library_prefs())
check(len(recs) == 156, 'library: 156 manga loaded (%d)' % len(recs))

kag = [r for r in recs if r.series == 'Kagurabachi']
vol3 = next(r for r in kag if r.series_index == 3)
check(vol3.book_id == 107 and vol3.chapter_range_text == '19-27', 'library: Kagurabachi vol 3 = book 107, range 19-27')

# Link to AniList, whole series (as the dialog would)
sib = ops.series_siblings(api, [107])
check(len(sib) == 9, 'link: 9 Kagurabachi volumes found as siblings (%d)' % len(sib))
changed = ops.set_anilist_link(api, sib, 169355)
check(len(changed) == 8, 'link: 8 newly linked (vol 3 already was) (%d)' % len(changed))
check(ops.set_anilist_link(api, sib, 169355) == [], 'link: running it again changes nothing')
check(api.field_for('identifiers', 107) == {'isbn': '9781974756360', 'anilist': '169355'}, 'link: vol 3 keeps its isbn')

# ---- SyncDialog end to end (fake AniList, real planner, real DB) --------------------------
recs = library.load_records(db, library_prefs())
linked = [(s, r) for s, r in sync.group_by_series(recs).items() if sync.anilist_id_for(r)]
check([s for s, _ in linked] == ['Kagurabachi'], 'sync: only the linked series is offered (%s)' % [s for s, _ in linked])
engine = sync.Engine(client, planner.Options())
column = '#manga_chapter_read'
check(column in api.field_metadata.all_field_keys(), 'sync: %s exists in the library' % column)


def plan_fn(series, rs):
    return engine.plan(series, rs, fetch_entry=True)


def push_fn(plan, target):
    if plan.current_progress is not None and target < plan.current_progress:
        raise ValueError('refusing to lower AniList progress')
    plan.target = target
    return engine.apply(plan)


written = {}


def write_fn(values):
    written.update(values)
    return len(ops.write_chapter_column(api, column, values))


sd = SyncDialog(None, linked, plan_fn, push_fn, write_fn, True, column)
check(wait_until(lambda: sd.sync_btn.isEnabled()), 'sync dialog: planning finished')
plan = sd.plans[0]
expected = 26   # vol 3 is at 95% of 19-27 in this library copy -> 8 of 9 chapters -> 26
check(plan.action == 'update' and plan.target == expected, 'sync dialog: Kagurabachi plans update 18 -> %d (got %s %s)' % (expected, plan.action, plan.target))
check(sd.table.item(0, 0).checkState() == Qt.CheckState.Checked, 'sync dialog: row is ticked')
check(sd.spins[0].minimum() == 18 and sd.spins[0].value() == expected, 'sync dialog: chapter box starts at %d and cannot go below AniList\'s 18' % expected)
sd.sync_btn.click()
check(wait_until(lambda: 'pushed' in sd.table.item(0, 5).text() or 'ERROR' in sd.table.item(0, 5).text()), 'sync dialog: push finished')
check(saved_calls == [{'mediaId': 169355, 'progress': expected}], 'sync dialog: AniList got chapters only: %s' % saved_calls)
check(wait_until(lambda: sd.wrote is not None), 'sync dialog: chapter column written')
check({r.book_id: r.completed for r in plan.results if r.completed}.items() <= written.items(), 'sync dialog: values handed to write_fn')
print('   summary:', sd.summary.text())

# the column values are really in the database
db.close()
import sqlite3  # noqa: E402
con = sqlite3.connect(lib_path + '/metadata.db')
cid = con.execute("select id from custom_columns where label='manga_chapter_read'").fetchone()[0]
rows = dict(con.execute('select book,value from custom_column_%d' % cid).fetchall())
check(rows.get(107) == expected and rows.get(114) == 18, 'database: vol 3 = %d, vol 2 = 18 (got %s, %s)' % (expected, rows.get(107), rows.get(114)))
check(1 + 0 <= len(rows) and rows.get(84) == 0, 'database: the unrelated stray 0 on Kimi no Iru Machi #18 is untouched')
check(con.execute("select count(*) from identifiers where type='anilist'").fetchone()[0] == 9, 'database: 9 anilist links')
con.close()

# lowering is refused even if someone forces it
try:
    push_fn(plan, 5)
    check(False, 'push_fn refuses to lower')
except ValueError:
    check(True, 'push_fn refuses to lower AniList progress')

# ---- preview-only without a token --------------------------------------------------------
sd2 = SyncDialog(None, linked, lambda s, r: engine.plan(s, r, fetch_entry=False), push_fn, write_fn, False, column)
check(wait_until(lambda: sd2.sync_btn.isEnabled()), 'no token: preview ready')
check(sd2.table.item(0, 0).checkState() != Qt.CheckState.Checked and 0 not in sd2.spins, 'no token: nothing can be ticked for pushing')
sd2.done_waiting()

# ---- settings page + the toolbar action --------------------------------------------------
w = ConfigWidget()
w.token.setText('abc')
w.save_settings()
check(PREFS['token'] == 'abc' and PREFS['chapter_column'] == '#manga_chapter_read', 'settings: saved and defaults correct')
w._test()
check('Failed' in w.test_result.text() or 'Signed in' in w.test_result.text(), 'settings: "Test token" reports a result without crashing')

from calibre_plugins.anilist_calibre_plugin.action import AniListAction  # noqa: E402
act = AniListAction.__new__(AniListAction)
act.gui = QMainWindow()
act.qaction = QAction('AniList', act.gui)
act.genesis()
texts = [a.text() for a in act.menu.actions() if a.text()]
check(texts[:3] == ['Sync manga progress...', 'Link to AniList...', 'Linked entry'], 'action: menu = %s' % texts)
check([a.text() for a in act.menu.actions()[3].menu().actions()] == ['Open on AniList', 'Remove link'], 'action: "Linked entry" submenu')

print('\n%s' % ('ALL PASSED' if not failures else '%d FAILED' % len(failures)))
sys.exit(1 if failures else 0)
