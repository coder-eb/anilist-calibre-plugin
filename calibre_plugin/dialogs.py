"""Dialogs for the AniList plugin.

Deliberately independent of Calibre's internals: data and callbacks go in, results come out,
so they can be exercised without a full Calibre window.
"""

from qt.core import (QApplication, QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView,
                     QLabel, QLineEdit, QPushButton, QSpinBox, Qt, QTableWidget, QTableWidgetItem,
                     QThread, QVBoxLayout, pyqtSignal, QAbstractItemView)

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
COLS = ['Sync', 'Series', 'Volumes read', 'New chapter', 'AniList now', 'Result']


class SyncDialog(QDialog):
    """Preview what would be sent to AniList for each linked series, then do it.

    ``items``     [(series, records)] for linked series.
    ``plan_fn``   (series, records) -> SeriesPlan        (runs in a worker thread)
    ``push_fn``   (plan, target) -> saved entry          (runs in a worker thread)
    ``write_fn``  ({book_id: chapter}) -> number written (runs on the GUI thread)
    """

    def __init__(self, parent, items, plan_fn, push_fn, write_fn, has_token, chapter_column):
        super().__init__(parent)
        self.setWindowTitle('Sync manga progress to AniList')
        self.items, self.plan_fn, self.push_fn, self.write_fn = items, plan_fn, push_fn, write_fn
        self.has_token = has_token
        self.plans = [None] * len(items)
        self.pushed = 0
        self.wrote = None
        self.worker = None
        self.resize(900, 360)

        layout = QVBoxLayout(self)
        if not has_token:
            banner = QLabel('No AniList token is set, so this is a preview only. Add one in the plugin '
                            'settings to push. The chapter column can still be filled.', self)
            banner.setWordWrap(True)
            banner.setStyleSheet('color: #b36b00;')
            layout.addWidget(banner)

        self.table = QTableWidget(len(items), len(COLS), self)
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)
        self.spins = {}
        for i, (series, recs) in enumerate(items):
            self._set(i, 0, '')
            self._set(i, 1, series)
            self._set(i, 2, str(sum(1 for r in recs if r.percent)))
            self._set(i, 3, '')
            self._set(i, 4, '')
            self._set(i, 5, 'checking AniList...' if has_token else 'preview')
            first = self.table.item(i, 0)
            first.setFlags(Qt.ItemFlag.NoItemFlags)

        self.write_box = QCheckBox('Also write each volume\'s completed chapter to %s' % chapter_column, self)
        self.write_box.setChecked(bool(chapter_column))
        self.write_box.setEnabled(bool(chapter_column))
        layout.addWidget(self.write_box)
        self.summary = QLabel('', self)
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        row = QHBoxLayout()
        row.addStretch(1)
        self.sync_btn = QPushButton('Sync now', self)
        self.sync_btn.setEnabled(False)
        self.close_btn = QPushButton('Close', self)
        row.addWidget(self.sync_btn)
        row.addWidget(self.close_btn)
        layout.addLayout(row)
        self.sync_btn.clicked.connect(self._sync)
        self.close_btn.clicked.connect(self.accept)

        self._start_planning()

    # -- table helpers ---------------------------------------------------------------------
    def _set(self, row, col, text):
        item = self.table.item(row, col)
        if item is None:
            item = QTableWidgetItem(text)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, col, item)
        else:
            item.setText(text)
        return item

    # -- planning --------------------------------------------------------------------------
    def _start_planning(self):
        jobs = [(lambda s=s, r=r: self.plan_fn(s, r)) for s, r in self.items]
        self.worker = Worker(jobs, self)
        self.worker.done.connect(self._planned)
        self.worker.all_done.connect(self._planning_done)
        self.worker.start()

    def _planned(self, i, plan, err):
        if err:
            self._set(i, 5, 'error: %s' % err)
            return
        self.plans[i] = plan
        if plan.current_progress is not None:
            self._set(i, 4, 'chapter %d (%s)' % (plan.current_progress, (plan.current_status or '?').lower()))
        elif plan.action == 'add':
            self._set(i, 4, 'not on your list')
        note = plan.reason
        if plan.warnings:
            note += '  |  ' + '; '.join(plan.warnings)
        self._set(i, 5, note)
        self._set(i, 3, str(plan.target) if plan.target else '-')
        if plan.will_push and self.has_token:
            item = self.table.item(i, 0)
            item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            item.setCheckState(Qt.CheckState.Checked)
            spin = QSpinBox(self)
            spin.setRange(plan.current_progress or 0, max(99999, plan.target))
            spin.setValue(plan.target)
            self.table.setCellWidget(i, 3, spin)
            self.spins[i] = spin

    def _planning_done(self):
        self.sync_btn.setEnabled(True)
        n = sum(1 for p in self.plans if p is not None and p.will_push)
        self.summary.setText('%d series to push.' % n if self.has_token else 'Preview ready.')

    # -- syncing ---------------------------------------------------------------------------
    def _sync(self):
        self.sync_btn.setEnabled(False)
        jobs, rows = [], []
        for i, plan in enumerate(self.plans):
            item = self.table.item(i, 0)
            if plan is None or not plan.will_push or not self.has_token:
                continue
            if item.checkState() != Qt.CheckState.Checked:
                continue
            target = self.spins[i].value()
            rows.append(i)
            self._set(i, 5, 'pushing...')
            jobs.append(lambda p=plan, t=target: self.push_fn(p, t))
        self._push_rows = rows
        if not jobs:
            self._after_push()
            return
        self.worker = Worker(jobs, self)
        self.worker.done.connect(self._pushed)
        self.worker.all_done.connect(self._after_push)
        self.worker.start()

    def _pushed(self, k, saved, err):
        i = self._push_rows[k]
        if err:
            self._set(i, 5, 'ERROR: %s' % err)
        else:
            self.pushed += 1
            self._set(i, 5, 'pushed: chapter %s (%s)' % (saved.get('progress'), (saved.get('status') or '').lower()))
            self.table.item(i, 0).setCheckState(Qt.CheckState.Unchecked)

    def _after_push(self):
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

    def done_waiting(self):
        if self.worker is not None:
            self.worker.requestInterruption()
            self.worker.wait(5000)

    def reject(self):
        self.done_waiting()
        super().reject()

    def accept(self):
        self.done_waiting()
        super().accept()
