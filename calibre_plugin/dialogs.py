"""Dialogs for the AniList plugin.

Deliberately independent of Calibre's internals: data and callbacks go in, results come out,
so they can be exercised without a full Calibre window.
"""

from qt.core import (QAbstractItemView, QApplication, QByteArray, QCheckBox, QDesktopServices, QDialog,
                     QDialogButtonBox, QFont, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton,
                     QSplitter, QStyle, Qt, QTableWidget, QTableWidgetItem, QThread, QUrl, QVBoxLayout,
                     QWidget, pyqtSignal)

from . import links
from .anilist import display_title


class Worker(QThread):
    """Runs callables one after another off the GUI thread."""
    done = pyqtSignal(int, object, object)      # index, result, error text (or None)
    all_done = pyqtSignal()

    def __init__(self, jobs, parent=None):
        super().__init__(parent)
        self.jobs = list(jobs)

    def run(self):
        for i, job in enumerate(self.jobs):
            if self.isInterruptionRequested():
                break
            try:
                self.done.emit(i, job(), None)
            except Exception as exc:                 # noqa: BLE001 - shown to the user per row
                self.done.emit(i, None, str(exc) or exc.__class__.__name__)
                if getattr(exc, 'abort_batch', False):
                    break                            # AniList is overloaded: do not send the rest
        self.all_done.emit()


# --------------------------------------------------------------------------------------------
class LinkDialog(QDialog):
    """Paste an AniList link for the selected book(s); empty = remove the link."""

    def __init__(self, parent, intro, current_link, fetch_media, has_series):
        super().__init__(parent)
        self.setWindowTitle('Link to AniList')
        self.fetch_media = fetch_media
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(intro, self))
        self.edit = QLineEdit(current_link or '', self)
        self.edit.setPlaceholderText('https://anilist.co/manga/169355/Kagurabachi/   (leave empty to remove the link)')
        self.edit.setMinimumWidth(520)
        layout.addWidget(self.edit)

        row = QHBoxLayout()
        self.check_btn = QPushButton('Check on AniList', self)
        row.addWidget(self.check_btn)
        row.addStretch(1)
        layout.addLayout(row)
        self.status = QLabel('', self)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.whole_series = QCheckBox('Also link every other volume of the same series', self)
        self.whole_series.setChecked(True)
        self.whole_series.setEnabled(has_series)
        layout.addWidget(self.whole_series)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self.edit.textChanged.connect(self._validate)
        self.check_btn.clicked.connect(self._check)
        self._validate()

    def _parsed(self):
        return links.parse_link(self.edit.text())

    def _validate(self):
        ident, problem = self._parsed()
        ok = not problem
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(ok)
        self.check_btn.setEnabled(ident is not None)
        if problem:
            self.status.setText(problem)
        elif ident is None:
            self.status.setText('Empty: the AniList link will be removed.')
        else:
            self.status.setText('AniList manga id %d' % ident)

    def _check(self):
        ident, _problem = self._parsed()
        if ident is None:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            media = self.fetch_media(ident)
        except Exception as exc:                     # noqa: BLE001
            self.status.setText('Could not check: %s' % exc)
            return
        finally:
            QApplication.restoreOverrideCursor()
        if not media:
            self.status.setText('No manga with id %d on AniList.' % ident)
            return
        chapters = media.get('chapters')
        self.status.setText('%s  -  %s, %s chapters, %s' % (
            display_title(media), media.get('format', '?'), chapters if chapters else 'unknown',
            (media.get('status') or '').replace('_', ' ').lower()))

    def result_link(self):
        """``(anilist_id or None, link_whole_series)``."""
        return self._parsed()[0], bool(self.whole_series.isChecked() and self.whole_series.isEnabled())


# --------------------------------------------------------------------------------------------
# --------------------------------------------------------------------------------------------
GEOMETRY_KEY = 'anilist_sync_dialog'

# Master table (one row per series) and the per-volume detail table underneath it.
MASTER_COLS = ['Status', 'Series', 'Volumes read', 'Latest volume', 'New chapter', 'On AniList', 'Comment']
DETAIL_COLS = ['Vol', 'Title', 'Kobo %', 'Chapters', 'Completed', 'Note']
C_STATUS, C_SERIES, C_READ, C_LATEST, C_NEW, C_NOW, C_NOTE = range(7)
ROLE_PLAN = Qt.ItemDataRole.UserRole            # plan index, on the series cell
ROLE_MIN = Qt.ItemDataRole.UserRole + 1         # lowest allowed chapter, on the "new chapter" cell
ROLE_PREV = Qt.ItemDataRole.UserRole + 2        # last accepted value, to revert a bad edit


def _geometry_store():
    try:
        from calibre.gui2 import gprefs
        return gprefs
    except Exception:                           # not inside Calibre: nothing is remembered
        return None


def status_of(plan, has_token):
    """``(key, text)`` describing a plan the way the Goodreads dialog's Status column does."""
    if plan.action in ('add', 'update'):
        return ('ready', 'Ready') if has_token else ('preview', 'Preview')
    if plan.action == 'preview':
        return 'preview', 'Preview'
    if plan.action == 'skip':
        if (plan.reason or '').startswith('AniList is already'):
            return 'uptodate', 'Up to date'
        return 'skipped', 'Skipped'
    if plan.action == 'unlinked':
        return 'skipped', 'No link'
    return 'nothing', 'Nothing to send'


def focus_result(plan):
    """The volume that decided the chapter: highest completed chapter (latest volume on a tie)."""
    done = [r for r in plan.results if r.completed]
    if not done:
        return None
    return max(done, key=lambda r: (r.completed, r.volume if r.volume is not None else -1))


class SyncDialog(QDialog):
    """Preview what would be sent to AniList for each linked series, then do it.

    ``items``     [(series, records)] for linked series.
    ``plan_fn``   (items) -> [SeriesPlan]          one call; makes at most one batched AniList request
    ``push_fn``   (plan, target) -> saved entry    (runs in a worker thread)
    ``write_fn``  ({book_id: chapter}) -> number written (runs on the GUI thread)
    """

    def __init__(self, parent, items, plan_fn, push_fn, write_fn, has_token, chapter_column, icon=None):
        super().__init__(parent)
        self.setWindowTitle('Sync Manga Progress')
        self.items, self.plan_fn, self.push_fn, self.write_fn = items, plan_fn, push_fn, write_fn
        self.has_token = has_token
        self.plans = [None] * len(items)
        self.state = ['planning'] * len(items)          # per-row status key
        self.pushed = 0
        self.wrote = None
        self.worker = None
        self._editing = False
        self.resize(1000, 560)

        layout = QVBoxLayout(self)
        title = QHBoxLayout()
        if icon is not None:
            logo = QLabel(self)
            logo.setPixmap(icon.pixmap(48, 48))
            title.addWidget(logo)
        heading = QLabel('Sync Manga Progress', self)
        font = QFont(heading.font())
        font.setPointSize(font.pointSize() + 6)
        heading.setFont(font)
        title.addWidget(heading)
        title.addStretch(1)
        layout.addLayout(title)

        hint = QHBoxLayout()
        self.hint = QLabel('Double-click a chapter number to change it. AniList progress is never lowered.', self)
        self.counts = QLabel('', self)
        hint.addWidget(self.hint, 1)
        hint.addWidget(self.counts)
        layout.addLayout(hint)
        if not has_token:
            banner = QLabel('No AniList token is set, so this is a preview only. Add one in the plugin '
                            'settings to push. The chapter column can still be filled.', self)
            banner.setWordWrap(True)
            banner.setStyleSheet('color: #b36b00;')
            layout.addWidget(banner)

        self.splitter = QSplitter(Qt.Orientation.Vertical, self)
        self.table = QTableWidget(len(items), len(MASTER_COLS), self)
        self.table.setHorizontalHeaderLabels(MASTER_COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        for col, width in ((C_STATUS, 165), (C_SERIES, 220), (C_READ, 100), (C_LATEST, 125), (C_NEW, 100), (C_NOW, 150)):
            self.table.setColumnWidth(col, width)
        self.splitter.addWidget(self.table)

        detail_box = QWidget()
        dl = QVBoxLayout(detail_box)
        dl.setContentsMargins(0, 0, 0, 0)
        self.detail_label = QLabel('Volumes', self)
        dl.addWidget(self.detail_label)
        self.detail = QTableWidget(0, len(DETAIL_COLS), self)
        self.detail.setHorizontalHeaderLabels(DETAIL_COLS)
        self.detail.verticalHeader().setVisible(False)
        self.detail.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.detail.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.detail.horizontalHeader().setStretchLastSection(True)
        for col, width in ((0, 45), (1, 250), (2, 75), (3, 90), (4, 90)):
            self.detail.setColumnWidth(col, width)
        dl.addWidget(self.detail)
        self.splitter.addWidget(detail_box)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)
        layout.addWidget(self.splitter, 1)

        for i, (series, recs) in enumerate(items):
            status = QTableWidgetItem('Checking...' if has_token else 'Preview')
            status.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(i, C_STATUS, status)
            name = self._cell(series)
            name.setData(ROLE_PLAN, i)
            self.table.setItem(i, C_SERIES, name)
            self.table.setItem(i, C_READ, self._cell('%d of %d' % (sum(1 for r in recs if r.percent), len(recs))))
            for col in (C_LATEST, C_NEW, C_NOW, C_NOTE):
                self.table.setItem(i, col, self._cell(''))

        self.write_box = QCheckBox('Also write each volume\'s completed chapter to %s' % chapter_column, self)
        self.write_box.setChecked(bool(chapter_column))
        self.write_box.setEnabled(bool(chapter_column))
        layout.addWidget(self.write_box)
        self.summary = QLabel('', self)
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        row = QHBoxLayout()
        self.open_btn = QPushButton('Open on AniList', self)
        self.open_btn.setEnabled(False)
        row.addWidget(self.open_btn)
        row.addStretch(1)
        self.close_btn = QPushButton('Cancel', self)
        self.sync_btn = QPushButton('Update AniList', self)
        self.sync_btn.setDefault(True)
        self.sync_btn.setEnabled(False)
        row.addWidget(self.close_btn)
        row.addWidget(self.sync_btn)
        layout.addLayout(row)

        self.sync_btn.clicked.connect(self._sync)
        self.close_btn.clicked.connect(self.accept)
        self.open_btn.clicked.connect(self._open_selected)
        self.table.itemChanged.connect(self._edited)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self._restore_geometry()
        self._update_counts()
        self._start_planning()

    # -- small helpers ---------------------------------------------------------------------
    @staticmethod
    def _cell(text):
        item = QTableWidgetItem(text)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        return item

    def _icon(self, key):
        style = self.style()
        sp = QStyle.StandardPixmap
        return style.standardIcon({
            'ready': sp.SP_DialogApplyButton, 'done': sp.SP_DialogApplyButton,
            'uptodate': sp.SP_MessageBoxInformation, 'preview': sp.SP_MessageBoxInformation,
            'nothing': sp.SP_MessageBoxInformation, 'planning': sp.SP_BrowserReload,
            'skipped': sp.SP_MessageBoxWarning, 'failed': sp.SP_MessageBoxCritical,
            'error': sp.SP_MessageBoxCritical}.get(key, sp.SP_MessageBoxInformation))

    def _row_of(self, plan_index):
        for r in range(self.table.rowCount()):
            if self.table.item(r, C_SERIES).data(ROLE_PLAN) == plan_index:
                return r
        return -1

    def _set_status(self, plan_index, key, text, checkable=None, checked=None):
        row = self._row_of(plan_index)
        item = self.table.item(row, C_STATUS)
        self.state[plan_index] = key
        item.setText(text)
        item.setToolTip(text)
        item.setIcon(self._icon(key))
        if checkable is not None:
            flags = Qt.ItemFlag.ItemIsEnabled
            if checkable:
                flags |= Qt.ItemFlag.ItemIsUserCheckable
            item.setFlags(flags)
        if checked is not None:
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)

    def _update_counts(self):
        ready = sum(1 for k in self.state if k == 'ready')
        attention = sum(1 for k in self.state if k in ('skipped', 'error', 'failed'))
        other = sum(1 for k in self.state if k in ('uptodate', 'nothing', 'preview', 'done'))
        self.counts.setText('%d ready, %d up to date or nothing to do, %d need attention' % (ready, other, attention))

    # -- planning --------------------------------------------------------------------------
    def _start_planning(self):
        self.worker = Worker([lambda: self.plan_fn(self.items)], self)
        self.worker.done.connect(self._planned)
        self.worker.start()

    def _planned(self, _index, plans, err):
        if err:
            for i in range(len(self.items)):
                self._set_status(i, 'error', 'Error')
                self.table.item(self._row_of(i), C_NOTE).setText(err)
            self._update_counts()
            return
        self.table.setSortingEnabled(False)
        self.plans = list(plans)
        for i, plan in enumerate(self.plans):
            row = self._row_of(i)
            key, text = status_of(plan, self.has_token)
            self._set_status(i, key, text, checkable=(key == 'ready'), checked=(key == 'ready'))
            focus = focus_result(plan)
            self.table.item(row, C_LATEST).setText(
                'vol %g (%s%%)' % (focus.volume, focus.percent) if focus and focus.volume is not None else '-')
            if plan.current_progress is not None:
                self.table.item(row, C_NOW).setText('chapter %d (%s)' % (
                    plan.current_progress, (plan.current_status or '?').replace('_', ' ').lower()))
            elif plan.action == 'add':
                self.table.item(row, C_NOW).setText('not on your list')
            note = plan.reason
            if plan.warnings:
                note += '  |  ' + '; '.join(plan.warnings)
            self.table.item(row, C_NOTE).setText(note)
            self.table.item(row, C_NOTE).setToolTip(note)
            new = self.table.item(row, C_NEW)
            if plan.target:
                self._editing = True
                new.setData(Qt.ItemDataRole.EditRole, int(plan.target))
                new.setData(ROLE_PREV, int(plan.target))
                new.setData(ROLE_MIN, int(plan.current_progress or 0))
                if key == 'ready':
                    new.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEditable)
                self._editing = False
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(C_SERIES, Qt.SortOrder.AscendingOrder)
        self._update_counts()
        n = sum(1 for k in self.state if k == 'ready')
        self.sync_btn.setEnabled(True)
        self.sync_btn.setText('Update AniList' if n else 'Fill chapter column' if self.write_box.isChecked() else 'Update AniList')
        self.summary.setText('%d series to push.' % n if self.has_token else 'Preview ready.')
        if self.table.rowCount():
            self.table.selectRow(0)

    # -- editing and selection -------------------------------------------------------------
    def _edited(self, item):
        if self._editing or item.column() != C_NEW:
            return
        previous, floor = item.data(ROLE_PREV), item.data(ROLE_MIN) or 0
        try:
            value = int(str(item.data(Qt.ItemDataRole.EditRole)).strip())
        except (TypeError, ValueError):
            value = None
        self._editing = True
        try:
            if value is None or value < floor:
                item.setData(Qt.ItemDataRole.EditRole, previous)
                row = item.row()
                self.table.item(row, C_NOTE).setText('Chapter must be a whole number, at least %d (what AniList has).' % floor)
            else:
                item.setData(Qt.ItemDataRole.EditRole, value)
                item.setData(ROLE_PREV, value)
        finally:
            self._editing = False

    def _selected_plan_index(self):
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        return self.table.item(rows[0].row(), C_SERIES).data(ROLE_PLAN) if rows else None

    def _selection_changed(self):
        idx = self._selected_plan_index()
        plan = self.plans[idx] if idx is not None and idx < len(self.plans) else None
        self.open_btn.setEnabled(bool(plan and plan.anilist_id))
        self.detail.setRowCount(0)
        if plan is None:
            self.detail_label.setText('Volumes')
            return
        self.detail_label.setText('Volumes read in %s' % plan.series)
        shown = [r for r in plan.results if r.source != 'unread']
        shown.sort(key=lambda r: (r.volume is None, r.volume or 0))
        self.detail.setRowCount(len(shown))
        for i, r in enumerate(shown):
            rng = '%g-%g' % r.chapter_range if r.chapter_range else '-'
            note = 'finished' if r.finished else ('no chapter range entered' if r.source == 'no-range' else 'estimate (rounded down)')
            for col, text in enumerate([('%g' % r.volume) if r.volume is not None else '?', r.title, '%s%%' % r.percent,
                                        rng, str(r.completed) if r.completed else '-', note]):
                cell = self._cell(text)
                cell.setToolTip(text)
                self.detail.setItem(i, col, cell)

    def _open_selected(self):
        idx = self._selected_plan_index()
        if idx is not None and self.plans[idx] and self.plans[idx].anilist_id:
            QDesktopServices.openUrl(QUrl('https://anilist.co/manga/%d' % self.plans[idx].anilist_id))

    # -- syncing ---------------------------------------------------------------------------
    def _sync(self):
        self.sync_btn.setEnabled(False)
        jobs, self._push_index = [], []
        for i, plan in enumerate(self.plans):
            if plan is None or self.state[i] != 'ready' or not self.has_token:
                continue
            row = self._row_of(i)
            if self.table.item(row, C_STATUS).checkState() != Qt.CheckState.Checked:
                continue
            target = int(self.table.item(row, C_NEW).data(Qt.ItemDataRole.EditRole))
            self._push_index.append(i)
            self._set_status(i, 'planning', 'Updating...')
            jobs.append(lambda p=plan, t=target: self.push_fn(p, t))
        if not jobs:
            self._after_push()
            return
        self.worker = Worker(jobs, self)
        self.worker.done.connect(self._pushed)
        self.worker.all_done.connect(self._after_push)
        self.worker.start()

    def _pushed(self, k, saved, err):
        i = self._push_index[k]
        row = self._row_of(i)
        if err:
            self._set_status(i, 'failed', 'Failed', checkable=False)
            self.table.item(row, C_NOTE).setText(err)
        else:
            self.pushed += 1
            self._set_status(i, 'done', 'Done', checkable=False, checked=False)
            self.table.item(row, C_NOTE).setText('pushed: chapter %s (%s)' % (
                saved.get('progress'), (saved.get('status') or '').replace('_', ' ').lower()))
            self.table.item(row, C_NEW).setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        self._update_counts()

    def _after_push(self):
        for i in getattr(self, '_push_index', []):
            if self.state[i] == 'planning':          # never reached: the batch was stopped early
                self._set_status(i, 'skipped', 'Not sent', checkable=False, checked=False)
                self.table.item(self._row_of(i), C_NOTE).setText('not sent - AniList asked us to slow down; try again later')
        self._update_counts()
        text = '%d series pushed to AniList.' % self.pushed
        if self.write_box.isChecked() and self.write_box.isEnabled():
            values = {}
            for plan in self.plans:
                if plan is not None:
                    values.update({r.book_id: r.completed for r in plan.results if r.completed})
            try:
                self.wrote = self.write_fn(values)
                text += '  Wrote the chapter column for %d volume(s).' % self.wrote
            except Exception as exc:                 # noqa: BLE001
                text += '  Could not write the chapter column: %s' % exc
        self.summary.setText(text)
        self.close_btn.setText('Close')

    # -- closing ---------------------------------------------------------------------------
    def _restore_geometry(self):
        store = _geometry_store()
        try:
            data = store and store.get(GEOMETRY_KEY)
            if data:
                self.restoreGeometry(QByteArray.fromBase64(data['window'].encode()))
                self.splitter.restoreState(QByteArray.fromBase64(data['splitter'].encode()))
        except Exception:
            pass

    def _save_geometry(self):
        store = _geometry_store()
        try:
            if store is not None:
                store[GEOMETRY_KEY] = {'window': bytes(self.saveGeometry().toBase64()).decode(),
                                       'splitter': bytes(self.splitter.saveState().toBase64()).decode()}
        except Exception:
            pass

    def done_waiting(self):
        if self.worker is not None:
            self.worker.requestInterruption()
            self.worker.wait(5000)
        self._save_geometry()

    def reject(self):
        self.done_waiting()
        super().reject()

    def accept(self):
        self.done_waiting()
        super().accept()
