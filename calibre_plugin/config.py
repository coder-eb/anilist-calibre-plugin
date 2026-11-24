import webbrowser

from calibre.utils.config import JSONConfig
from qt.core import (QApplication, QCheckBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                     QPushButton, QWidget, Qt)

from .anilist import AniListClient, AniListError

PREFS = JSONConfig('plugins/anilist_calibre_plugin')
PREFS.defaults['token'] = ''
PREFS.defaults['client_id'] = ''
PREFS.defaults['tag'] = 'Manga'
PREFS.defaults['percent_column'] = '#kobo_percent_read'
PREFS.defaults['range_column'] = '#manga_chapters'
PREFS.defaults['chapter_column'] = '#manga_chapter_read'
PREFS.defaults['count_in_progress'] = False
PREFS.defaults['update_inactive'] = False
PREFS.defaults['auto_prompt'] = True      # offer a sync after Kobo Utilities stores new reading positions
PREFS.defaults['pushed'] = {}      # {anilist id: chapter AniList is known to have}, saves needless lookups

AUTH_URL = 'https://anilist.co/api/v2/oauth/authorize?client_id=%s&response_type=token'


def library_prefs():
    """The subset ``library.load_records`` needs."""
    return {'tag': PREFS['tag'], 'percent_column': PREFS['percent_column'],
            'range_column': PREFS['range_column']}


class ConfigWidget(QWidget):
    def __init__(self):
        super().__init__()
        layout = QFormLayout(self)

        self.client_id = QLineEdit(PREFS['client_id'], self)
        self.client_id.setPlaceholderText('from anilist.co/settings/developer')
        open_btn = QPushButton('Open authorization page', self)
        open_btn.clicked.connect(self._open_auth)
        row = QHBoxLayout()
        row.addWidget(self.client_id)
        row.addWidget(open_btn)
        layout.addRow('AniList client id', row)

        self.token = QLineEdit(PREFS['token'], self)
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setPlaceholderText('paste the token shown after you approve')
        test_btn = QPushButton('Test token', self)
        test_btn.clicked.connect(self._test)
        row = QHBoxLayout()
        row.addWidget(self.token)
        row.addWidget(test_btn)
        layout.addRow('AniList token', row)
        self.test_result = QLabel('', self)
        self.test_result.setWordWrap(True)
        layout.addRow('', self.test_result)
        note = QLabel('The token is stored as plain text in Calibre\'s config folder (as Goodreads Sync does '
                      'for its login). Create the client at anilist.co/settings/developer with redirect URL '
                      'https://anilist.co/api/v2/oauth/pin.', self)
        note.setWordWrap(True)
        note.setStyleSheet('color: gray; font-style: italic;')
        layout.addRow('', note)

        self.tag = QLineEdit(PREFS['tag'], self)
        self.percent = QLineEdit(PREFS['percent_column'], self)
        self.range = QLineEdit(PREFS['range_column'], self)
        self.chapter = QLineEdit(PREFS['chapter_column'], self)
        layout.addRow('Manga tag', self.tag)
        layout.addRow('Percent-read column (from Kobo Utilities)', self.percent)
        layout.addRow('Chapter-range column', self.range)
        layout.addRow('Chapter-read column (written by this plugin)', self.chapter)

        self.count_in_progress = QCheckBox('Also count the chapter I am currently in', self)
        self.count_in_progress.setChecked(PREFS['count_in_progress'])
        self.update_inactive = QCheckBox('Also update entries marked Paused / Dropped on AniList', self)
        self.update_inactive.setChecked(PREFS['update_inactive'])
        self.auto_prompt = QCheckBox('Offer to sync after Kobo Utilities stores new reading positions', self)
        self.auto_prompt.setChecked(PREFS['auto_prompt'])
        layout.addRow('', self.auto_prompt)
        layout.addRow('', self.count_in_progress)
        layout.addRow('', self.update_inactive)

    def _open_auth(self):
        cid = self.client_id.text().strip()
        if not cid:
            self.test_result.setText('Enter your client id first.')
            return
        webbrowser.open(AUTH_URL % cid)

    def _test(self):
        token = self.token.text().strip()
        if not token:
            self.test_result.setText('Paste a token first.')
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            who = AniListClient(token, timeout=8).viewer()
            self.test_result.setText('Signed in to AniList as %s.' % who.get('name', '?'))
        except AniListError as exc:
            self.test_result.setText('Failed: %s' % exc)
        finally:
            QApplication.restoreOverrideCursor()

    def save_settings(self):
        PREFS['client_id'] = self.client_id.text().strip()
        PREFS['token'] = self.token.text().strip()
        PREFS['tag'] = self.tag.text().strip() or 'Manga'
        PREFS['percent_column'] = self.percent.text().strip()
        PREFS['range_column'] = self.range.text().strip()
        PREFS['chapter_column'] = self.chapter.text().strip()
        PREFS['count_in_progress'] = bool(self.count_in_progress.isChecked())
        PREFS['update_inactive'] = bool(self.update_inactive.isChecked())
        PREFS['auto_prompt'] = bool(self.auto_prompt.isChecked())
