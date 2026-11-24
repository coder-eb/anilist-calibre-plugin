"""Integration test for the Calibre plugin, run inside Calibre's own Python with a throwaway config:

    python3 scripts/build_plugin.py
    CD=$(mktemp -d); mkdir -p $CD/lib; cp ~/"Calibre Library/metadata.db" $CD/lib/
    export HOME=$CD CALIBRE_CONFIG_DIRECTORY=$CD/cfg QT_QPA_PLATFORM=offscreen
    calibre-customize -a dist/AniList-Manga-Sync.zip
    calibre-debug tests/integration_plugin.py -- $CD/lib

Uses a copy of the library's metadata.db and a fake AniList: nothing real is read or written
(apart from the "Test token" button, which is pointed at the fake too).
"""
import json
import socket
import sys
import time
import urllib.request


def _blocked(*a, **k):
    raise AssertionError('the integration test tried to use the real network')


urllib.request.urlopen = _blocked        # everything must go through the fake AniList below
socket.create_connection = _blocked

from qt.core import QAction, QApplication, QMainWindow, Qt

app = QApplication([])

from calibre.customize.ui import find_plugin, initialize_plugins  # noqa: E402
from calibre.db.legacy import LibraryDatabase  # noqa: E402

initialize_plugins()
plugin = find_plugin('AniList Manga Sync')
print('plugin loaded:', plugin.name, plugin.version)

from calibre_plugins.anilist_calibre_plugin import anilist, library, ops, planner, sync  # noqa: E402
from calibre_plugins.anilist_calibre_plugin.config import ConfigWidget, PREFS, library_prefs  # noqa: E402
from calibre_plugins.anilist_calibre_plugin.dialogs import (  # noqa: E402
    C_NEW, C_NOTE, C_SERIES, C_STATUS, LinkDialog, SyncDialog)

failures = []


def check(cond, msg):
    print(('PASS  ' if cond else 'FAIL  ') + msg)
    if not cond:
        failures.append(msg)


def wait_until(cond, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


# ---- a fake AniList (no network) that counts every request --------------------------------
requests = []          # ('batch'|'save'|'viewer'|'media', variables)
saves = []
state = {'rate_limit_from_save': None}
NOW = {}               # anilist id -> chapter already on the (fake) AniList


def fake_transport(method, url, body, headers):
    payload = json.loads(body)
    q, v = payload['query'], payload['variables']
    if 'SaveMediaListEntry' in q:
        requests.append(('save', v))
        n = len(saves)
        saves.append(v)
        if state['rate_limit_from_save'] is not None and n >= state['rate_limit_from_save']:
            return 429, {'Retry-After': '0'}, ''
        return 200, {}, json.dumps({'data': {'SaveMediaListEntry': {
            'id': 1, 'progress': v['progress'], 'status': v.get('status', 'CURRENT')}}})
    if 'id_in' in q:
        requests.append(('batch', v))
        media = [{'id': i, 'chapters': None,
                  'mediaListEntry': {'status': 'CURRENT', 'progress': NOW.get(i, 0)}} for i in v['ids']]
        return 200, {}, json.dumps({'data': {'Page': {'media': media}}})
    if 'Viewer' in q:
        requests.append(('viewer', v))
        return 200, {}, json.dumps({'data': {'Viewer': {'id': 1, 'name': 'tester'}}})
    requests.append(('media', v))
    return 200, {}, json.dumps({'data': {'Media': {'id': v['id'], 'format': 'MANGA', 'chapters': 54,
                                                   'status': 'RELEASING', 'title': {'romaji': 'Kagurabachi'}}}})


def make_client():
    return anilist.AniListClient('token', transport=fake_transport, sleep=lambda s: None)


def count(kind):
    return sum(1 for k, _ in requests if k == kind)


# ---- LinkDialog --------------------------------------------------------------------------
client = make_client()
dlg = LinkDialog(None, 'Link "x"', '', client.media, True)
ok_btn = dlg.buttons.button(dlg.buttons.StandardButton.Ok)
dlg.edit.setText('nonsense')
check(not ok_btn.isEnabled(), 'link dialog: junk text disables OK')
dlg.edit.setText('https://anilist.co/anime/21/One-Piece')
check(not ok_btn.isEnabled() and 'anime' in dlg.status.text(), 'link dialog: an anime link is refused')
before = len(requests)
dlg.edit.setText('https://anilist.co/manga/169355/Kagurabachi/')
check(len(requests) == before, 'link dialog: typing/pasting sends NO request (only the button does)')
check(ok_btn.isEnabled() and dlg.result_link() == (169355, True), 'link dialog: pasted manga URL -> id 169355, whole series on')
dlg.check_btn.click()
check(count('media') == 1 and 'Kagurabachi' in dlg.status.text(), 'link dialog: "Check on AniList" = exactly 1 request, shows the title')
dlg.edit.setText('')
check(ok_btn.isEnabled() and dlg.result_link()[0] is None, 'link dialog: empty = remove the link')

# ---- the real library copy ---------------------------------------------------------------
lib_path = sys.argv[1]
db = LibraryDatabase(lib_path)
api = db.new_api
recs = library.load_records(db, library_prefs())
check(len(recs) == 156, 'library: 156 manga loaded (%d)' % len(recs))
vol3 = next(r for r in recs if r.series == 'Kagurabachi' and r.series_index == 3)
check(vol3.book_id == 107 and vol3.chapter_range_text == '19-27', 'library: Kagurabachi vol 3 = book 107, range 19-27')

# link the whole Kagurabachi series (as the dialog would), then every other manga series with a fake id
sib = ops.series_siblings(api, [107])
check(len(sib) == 9 and len(ops.set_anilist_link(api, sib, 169355)) == 8, 'link: Kagurabachi, 9 volumes (8 newly linked)')
check(ops.set_anilist_link(api, sib, 169355) == [], 'link: running it again changes nothing')
for i, (name, rs) in enumerate(sync.group_by_series(recs).items()):
    if name != 'Kagurabachi':
        ops.set_anilist_link(api, [r.book_id for r in rs], 2000 + i)
recs = library.load_records(db, library_prefs())
linked = [(s, r) for s, r in sync.group_by_series(recs).items() if sync.anilist_id_for(r)]
check(len(linked) == 13, 'sync: 13 linked series offered (%d)' % len(linked))
NOW[169355] = 18

column = '#manga_chapter_read'
check(column in api.field_metadata.all_field_keys(), 'sync: %s exists in the library' % column)
written = {}


def write_fn(values):
    written.update(values)
    return len(ops.write_chapter_column(api, column, values))


pushed_memory = {}


def make_engine():
    return sync.Engine(anilist.AniListClient('token', transport=fake_transport, sleep=lambda s: None),
                       planner.Options())


def plan_fn(items):
    return make_engine().plan_all(items, fetch_entries=True, known_pushed=dict(pushed_memory))


def push_fn(plan, target):
    if plan.current_progress is not None and target < plan.current_progress:
        raise ValueError('refusing to lower AniList progress')
    plan.target = target
    return make_engine().apply(plan)


def remember(aid, chapter):
    pushed_memory[aid] = max(pushed_memory.get(aid, -1), chapter)


# ---- 1) opening the dialog: ONE batched request for 13 series -----------------------------
requests.clear(); saves.clear()
sd = SyncDialog(None, linked, plan_fn, push_fn, write_fn, True, column, remember_fn=remember)
check(wait_until(lambda: sd.sync_btn.isEnabled()), 'sync dialog: planning finished')
check(count('batch') == 1 and count('save') == 0 and len(requests) == 1,
      'AniList load: opening the dialog made exactly %d request (for %d linked series)' % (len(requests), len(linked)))
asked = requests[0][1]['ids']
with_news = [p for p in sd.plans if p.target and p.action != 'nothing']
check(set(asked) == {p.anilist_id for p in sd.plans if p.target}, 'AniList load: only series with something to send were asked about (%d of 13)' % len(asked))
check(sd.table.rowCount() == 13, 'table: one row per linked series')

rows = {sd.table.item(r, C_SERIES).text(): r for r in range(sd.table.rowCount())}
kag = rows['Kagurabachi']
check(sd.table.item(kag, C_STATUS).text() == 'Ready' and not sd.table.item(kag, C_STATUS).icon().isNull(), 'table: Kagurabachi status "Ready" with an icon')
check(sd.table.item(kag, C_STATUS).checkState() == Qt.CheckState.Checked, 'table: ready rows are ticked')
check(sd.table.item(kag, 3).text().startswith('vol 3 (95%'), 'table: latest volume column shows "vol 3 (95%%)" (%s)' % sd.table.item(kag, 3).text())
check(sd.table.item(kag, C_NEW).data(Qt.ItemDataRole.EditRole) == 26, 'table: new chapter = 26')
print('   counts:', sd.counts.text())
check('ready' in sd.counts.text(), 'table: live status counts shown')

sd.table.selectRow(kag)
app.processEvents()
check(sd.detail.rowCount() == 3 and [sd.detail.item(i, 0).text() for i in range(3)] == ['1', '2', '3'], 'detail table: Kagurabachi shows its 3 read volumes')
check(sd.detail.item(2, 3).text() == '19-27' and sd.detail.item(2, 4).text() == '26', 'detail table: vol 3 range 19-27 -> completed 26')
check(sd.detail.item(0, 5).text() == 'finished' and 'estimate' in sd.detail.item(2, 5).text(), 'detail table: finished vs estimate notes')
check(sd.open_btn.isEnabled(), 'open on AniList button enabled for a linked series')

item = sd.table.item(kag, C_NEW)
item.setData(Qt.ItemDataRole.EditRole, 10)
app.processEvents()
check(item.data(Qt.ItemDataRole.EditRole) == 26 and 'at least 18' in sd.table.item(kag, C_NOTE).text(), 'edit: a chapter below what AniList has is refused and reverted')
item.setData(Qt.ItemDataRole.EditRole, 25)
app.processEvents()
check(item.data(Qt.ItemDataRole.EditRole) == 25, 'edit: a valid lower-than-computed chapter (>= AniList) is accepted')
item.setData(Qt.ItemDataRole.EditRole, 26)
sd.table.sortByColumn(C_SERIES, Qt.SortOrder.DescendingOrder)
app.processEvents()
kag = {sd.table.item(r, C_SERIES).text(): r for r in range(sd.table.rowCount())}['Kagurabachi']
check(sd.table.item(kag, C_NEW).data(Qt.ItemDataRole.EditRole) == 26, 'table: sorting keeps each row\'s values together')

# ---- 2) AniList says "slow down" mid-push: stop, do not hammer ----------------------------
n_ready = sum(1 for k in sd.state if k == 'ready')
check(n_ready >= 3, 'rate-limit test needs 3+ ready series (have %d)' % n_ready)
requests.clear(); saves.clear()
state['rate_limit_from_save'] = 1          # 1st save succeeds, the 2nd and later are answered 429
sd.sync_btn.click()
check(wait_until(lambda: sd.close_btn.text() == 'Close'), 'rate limit: the batch finished')
saved_ok = sum(1 for p in sd.plans if p is not None and sd.state[sd.plans.index(p)] == 'done')
n_save_requests = count('save')
check(saved_ok == 1, 'rate limit: exactly 1 series was pushed before AniList said slow down (%d)' % saved_ok)
check(n_save_requests == 1 + (anilist.AniListClient('t').max_retries + 1), 'rate limit: 1 good push + %d attempts at the next, then STOP (%d save requests total, %d series were ready)' % (anilist.AniListClient('t').max_retries + 1, n_save_requests, n_ready))
unsent = [i for i, k in enumerate(sd.state) if k == 'skipped' and sd.table.item(sd.table.findItems(sd.plans[i].series, Qt.MatchFlag.MatchExactly)[0].row(), C_STATUS).text() == 'Not sent']
check(len(unsent) >= 1, 'rate limit: the rest are marked "Not sent" (%d rows), none attempted' % len(unsent))
state['rate_limit_from_save'] = None
print('   summary:', sd.summary.text())
sd.done_waiting()

# ---- 3) opening it again: what was sent is not looked up again ----------------------------
requests.clear()
sd2 = SyncDialog(None, linked, plan_fn, push_fn, write_fn, True, column, remember_fn=remember)
check(wait_until(lambda: sd2.sync_btn.isEnabled()), 'repeat: planning finished')
asked2 = requests[0][1]['ids'] if requests else []
done_ids = {sd.plans[i].anilist_id for i, k in enumerate(sd.state) if k == 'done'}
check(done_ids and not (set(asked2) & done_ids),
      'repeat: the series just pushed are NOT asked about again (%d pushed, %d asked)' % (len(done_ids), len(asked2)))
check(len(requests) <= 1, 'repeat: at most one request (%d)' % len(requests))
sent_rows = [sd2.table.item(r, C_NOTE).text() for r in range(sd2.table.rowCount()) if 'already had' in sd2.table.item(r, C_NOTE).text()]
check(len(sent_rows) >= 1, 'repeat: those rows say "AniList already had chapter N ... not asked again" (%d)' % len(sent_rows))
sd2.done_waiting()

# ---- 4) nothing new anywhere -> zero requests ---------------------------------------------
requests.clear()
all_known = {p.anilist_id: p.target for p in plan_fn(linked) if p.target}
for aid, target in all_known.items():
    pushed_memory[aid] = max(target, pushed_memory.get(aid, 0))
requests.clear()
plans = plan_fn(linked)
check(requests == [], 'nothing new: opening the dialog sends ZERO requests (%d series checked locally)' % len(plans))

# the column values are really in the database
sd3 = SyncDialog(None, linked, plan_fn, push_fn, write_fn, True, column, remember_fn=remember)
wait_until(lambda: sd3.sync_btn.isEnabled())
sd3._after_push()
db.close()
import sqlite3  # noqa: E402
con = sqlite3.connect(lib_path + '/metadata.db')
cid = con.execute("select id from custom_columns where label='manga_chapter_read'").fetchone()[0]
rows_db = dict(con.execute('select book,value from custom_column_%d' % cid).fetchall())
check(rows_db.get(107) == 26 and rows_db.get(114) == 18, 'database: vol 3 = 26, vol 2 = 18 (got %s, %s)' % (rows_db.get(107), rows_db.get(114)))
# Kimi no Iru Machi is linked in this scenario, so its volumes get real values (replacing the stray 0)
check(rows_db.get(84) == written.get(84) and rows_db.get(84) not in (None, 0), 'database: a linked volume gets its computed chapter (Kimi #18 -> %s)' % rows_db.get(84))
con.close()
sd3.done_waiting()

# ---- 5) the gap found by the user: a lookup alone (nothing pushed) must be remembered ------------
pushed_memory.clear()
NOW[169355] = 26                      # AniList already has chapter 26: nothing to push
requests.clear()
sd5 = SyncDialog(None, [i for i in linked if i[0] == 'Kagurabachi'], plan_fn, push_fn, write_fn, True, column, remember_fn=remember)
check(wait_until(lambda: sd5.sync_btn.isEnabled()), 'learn: first open finished')
check(count('batch') == 1, 'learn: first open asked AniList once (%d)' % count('batch'))
check(sd5.table.item(0, C_STATUS).text() == 'Up to date', 'learn: Kagurabachi shows "Up to date"')
check(pushed_memory.get(169355) == 26, 'learn: AniList\'s chapter (26) was remembered even though nothing was pushed (%s)' % pushed_memory)
sd5.done_waiting()
requests.clear()
sd6 = SyncDialog(None, [i for i in linked if i[0] == 'Kagurabachi'], plan_fn, push_fn, write_fn, True, column, remember_fn=remember)
check(wait_until(lambda: sd6.sync_btn.isEnabled()), 'learn: second open finished')
check(requests == [], 'learn: second open sent ZERO requests (%d)' % len(requests))
check(sd6.table.item(0, C_STATUS).text() == 'Up to date', 'learn: ... and still shows "Up to date"')
sd6.done_waiting()

# ---- push_fn never lowers; no token = preview only ----------------------------------------
plan = next(p for p in plans if p.series == 'Kagurabachi')
plan.current_progress = 20
try:
    push_fn(plan, 5)
    check(False, 'push_fn refuses to lower')
except ValueError:
    check(True, 'push_fn refuses to lower AniList progress')

requests.clear()
sd4 = SyncDialog(None, linked, lambda items: make_engine().plan_all(items, fetch_entries=False), push_fn, write_fn, False, column)
check(wait_until(lambda: sd4.sync_btn.isEnabled()), 'no token: preview ready')
check(requests == [], 'no token: ZERO requests')
check(all(sd4.table.item(r, C_STATUS).checkState() != Qt.CheckState.Checked for r in range(sd4.table.rowCount())), 'no token: nothing can be ticked for pushing')
sd4.done_waiting()

# ---- settings page + the toolbar action ---------------------------------------------------
w = ConfigWidget()
w.token.setText('abc')
w.save_settings()
check(PREFS['token'] == 'abc' and PREFS['chapter_column'] == '#manga_chapter_read' and PREFS['pushed'] == {}, 'settings: saved; defaults correct')

from calibre_plugins.anilist_calibre_plugin.action import AniListAction  # noqa: E402
act = AniListAction.__new__(AniListAction)
act.gui = QMainWindow()
act.qaction = QAction('AniList', act.gui)
act.genesis()
texts = [a.text() for a in act.menu.actions() if a.text()]
check(texts[:3] == ['Sync manga progress...', 'Link to AniList...', 'Linked entry'], 'action: menu = %s' % texts)
check(not act.qaction.icon().isNull(), 'action: the AniList icon is loaded from the zip')

print('\n%s' % ('ALL PASSED' if not failures else '%d FAILED' % len(failures)))
sys.exit(1 if failures else 0)
