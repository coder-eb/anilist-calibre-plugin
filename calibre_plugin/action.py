from calibre.gui2 import error_dialog, info_dialog, open_url, question_dialog
from calibre.gui2.actions import InterfaceAction
from qt.core import QDialog, QMenu, QTimer, QToolButton, QUrl

from . import autosync, library, links, ops, planner, sync
from .anilist import AniListClient
from .config import PREFS, library_prefs
from .dialogs import LinkDialog, SyncDialog


class AniListAction(InterfaceAction):
    name = 'AniList Manga Sync'
    action_spec = ('AniList', None, 'Sync manga reading progress to AniList', None)
    popup_type = QToolButton.ToolButtonPopupMode.InstantPopup
    action_type = 'current'

    def genesis(self):
        try:
            self.qaction.setIcon(get_icons('images/anilist.png', 'AniList Manga Sync'))  # noqa: F821
        except Exception:
            pass                                    # no icon is better than no plugin
        self.menu = QMenu(self.gui)
        self.qaction.setMenu(self.menu)
        self.menu.addAction('Sync manga progress...').triggered.connect(lambda checked=False: self.sync_progress())
        self.menu.addSeparator()
        self.menu.addAction('Link to AniList...').triggered.connect(self.link_books)
        sub = self.menu.addMenu('Linked entry')
        sub.addAction('Open on AniList').triggered.connect(self.open_link)
        sub.addAction('Remove link').triggered.connect(self.remove_links)
        self.menu.addSeparator()
        self.menu.addAction('Customize plugin...').triggered.connect(self.show_config)

    def initialization_complete(self):
        """Offer a sync right after Kobo Utilities stores reading positions (like Goodreads Sync)."""
        self._auto_open = False
        try:
            from calibre_plugins.koboutilities.features import locations
        except Exception:
            print('[AniList] Kobo Utilities not found: no automatic sync prompt')
            return
        ok = autosync.install(locations, self._positions_stored, log=print)
        if not ok:
            print('[AniList] automatic sync prompt not active: %s' % autosync.STATE.reason)

    def _positions_stored(self, changed_ids):
        if not PREFS['auto_prompt'] or not PREFS['token'] or self._auto_open:
            return
        # Let Kobo Utilities finish and the library refresh before we look at the columns.
        QTimer.singleShot(800, lambda: self._maybe_prompt(changed_ids))

    def _maybe_prompt(self, changed_ids):
        recs = library.load_records(self.gui.current_db, library_prefs())
        items = autosync.affected_items(changed_ids, recs)
        known = {int(k): v for k, v in dict(PREFS['pushed']).items()}
        opts = planner.Options(count_in_progress=PREFS['count_in_progress'],
                               update_inactive=PREFS['update_inactive'])
        if not items or not autosync.worth_prompting(items, opts, known):
            return                                  # nothing new for AniList: stay quiet
        autosync.STATE.prompts += 1
        self._auto_open = True
        try:
            self.sync_progress(only_series=[s for s, _ in items])
        finally:
            self._auto_open = False

    # -- helpers ---------------------------------------------------------------------------
    @property
    def api(self):
        return self.gui.current_db.new_api

    def _selected(self):
        return list(self.gui.library_view.get_selected_ids())

    def _refresh(self, ids):
        if ids:
            try:
                self.gui.iactions['Edit Metadata'].refresh_gui(list(ids))
            except Exception:
                pass

    def show_config(self):
        self.interface_action_base_plugin.do_user_config(self.gui)

    # -- Sync manga progress ---------------------------------------------------------------
    def sync_progress(self, only_series=None):
        recs = library.load_records(self.gui.current_db, library_prefs())
        series_map = sync.group_by_series(recs)
        linked = [(s, r) for s, r in series_map.items() if sync.anilist_id_for(r)
                  and (only_series is None or s in only_series)]
        if not linked:
            return info_dialog(self.gui, 'AniList', 'No manga series has an AniList link yet.\n\n'
                               'Select a volume and use  AniList > Link to AniList...', show=True)
        token = PREFS['token']
        # Gentle on AniList: at most 30 requests a minute (one per 2 s), and only for series with news.
        engine = sync.Engine(AniListClient(token, min_interval=2.0), planner.Options(
            count_in_progress=PREFS['count_in_progress'], update_inactive=PREFS['update_inactive']))
        column = PREFS['chapter_column']
        if column and column not in self.api.field_metadata.all_field_keys():
            error_dialog(self.gui, 'AniList',
                         'The column %s does not exist in this library, so it will not be filled. '
                         'Create it (Preferences > Add your own columns) or change the name in the '
                         'plugin settings.' % column, show=True)
            column = ''

        def known_pushed():
            return {int(k): v for k, v in dict(PREFS['pushed']).items()}

        def plan_fn(items):
            return engine.plan_all(items, fetch_entries=bool(token), known_pushed=known_pushed())

        def push_fn(plan, target):
            if plan.current_progress is not None and target < plan.current_progress:
                raise ValueError('refusing to lower AniList progress')
            plan.target = target
            return engine.apply(plan)

        def remember_fn(anilist_id, chapter):
            # AniList has at least this chapter: next time, no lookup unless there is news.
            known = dict(PREFS['pushed'])
            if known.get(str(anilist_id), -1) < chapter:
                known[str(anilist_id)] = chapter
                PREFS['pushed'] = known

        def write_fn(values):
            changed = ops.write_chapter_column(self.api, column, values)
            self._refresh(changed)
            return len(changed)

        dlg = SyncDialog(self.gui, linked, plan_fn, push_fn, write_fn, bool(token), column,
                         icon=self.qaction.icon(), remember_fn=remember_fn)
        dlg.exec()
        dlg.deleteLater()                              # do not leave a hidden dialog under the main window

    # -- Link to AniList -------------------------------------------------------------------
    def link_books(self):
        ids = self._selected()
        if not ids:
            return error_dialog(self.gui, 'AniList', 'Select one or more manga volumes first.', show=True)
        api = self.api
        current = sorted({(api.field_for('identifiers', b) or {}).get(ops.LINK_TYPE) for b in ids} - {None})
        has_series = any(api.field_for('series', b) for b in ids)
        what = ('"%s"' % api.field_for('title', ids[0])) if len(ids) == 1 else '%d books' % len(ids)
        dlg = LinkDialog(self.gui, 'Link %s to an AniList manga.' % what,
                         links.page_url(current[0]) if len(current) == 1 else '',
                         AniListClient(PREFS['token'], timeout=8).media, has_series)
        accepted = dlg.exec() == QDialog.DialogCode.Accepted
        result = dlg.result_link()
        dlg.deleteLater()
        if not accepted:
            return
        anilist_id, whole_series = result
        targets = ops.series_siblings(api, ids) if whole_series else ids
        if anilist_id is None:
            if not question_dialog(self.gui, 'Remove link', 'Remove the AniList link from %d book(s)?' % len(targets)):
                return
            changed = ops.remove_anilist_link(api, targets)
            msg = 'Removed the AniList link from %d book(s).' % len(changed)
        else:
            changed = ops.set_anilist_link(api, targets, anilist_id)
            msg = 'Linked %d book(s) to AniList manga %d.' % (len(changed), anilist_id)
        self._refresh(changed)
        self.gui.status_bar.show_message(msg, 6000)

    def open_link(self):
        for bid in self._selected():
            ident = (self.api.field_for('identifiers', bid) or {}).get(ops.LINK_TYPE)
            if ident and str(ident).isdigit():
                return open_url(QUrl(links.page_url(int(ident))))
        error_dialog(self.gui, 'AniList', 'The selected book has no AniList link.', show=True)

    def remove_links(self):
        ids = self._selected()
        if not ids:
            return
        if not question_dialog(self.gui, 'Remove link', 'Remove the AniList link from the %d selected book(s)?' % len(ids)):
            return
        changed = ops.remove_anilist_link(self.api, ids)
        self._refresh(changed)
        self.gui.status_bar.show_message('Removed the AniList link from %d book(s).' % len(changed), 6000)
