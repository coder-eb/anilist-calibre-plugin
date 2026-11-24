# AniList Calibre plugin

Sync manga reading progress from Calibre to AniList, at **chapter** level.

> **Status:** the command line and the Calibre plugin both work (see below). The plugin is tested
> in Calibre against a fake AniList; it has not yet been used against a real AniList account from
> inside Calibre.

How it works:

1. Kobo Utilities stores your Kobo reading progress in Calibre (`#kobo_percent_read`).
2. Each manga volume has its chapter range in `#manga_chapters` (e.g. `19-27`).
3. Chapter completed = `(first - 1) + floor(percent x chapters)`, and the volume's last chapter once it is
   finished (~98%). Chapters are assumed equal length, so inside a volume this is an **estimate, rounded down**
   (about one chapter behind). A finished volume is exact.
4. The highest chapter across the series is sent to AniList. You read in order, so earlier chapters are implied.

A series is matched to AniList **only** by an `anilist:<id>` identifier saved on one of its volumes
(from `https://anilist.co/manga/<id>/...`). Series without one are never touched.

Guard rails: progress is never lowered; entries you marked COMPLETED / PAUSED / DROPPED are left alone;
the status is never set to COMPLETED; new series are added as CURRENT; only chapters are sent (no volume counts).

## Calibre plugin

Install: download the zip from the Releases page (or a build artifact from a CI run), or build it with
`python scripts/build_plugin.py`; then in Calibre use Preferences > Plugins > Load plugin from file and restart.
If the **AniList** button is not on the toolbar, add it under Preferences > Toolbars & menus.
Then open Customize plugin and set your AniList client id / token (see "Getting an AniList token").

The **AniList** toolbar menu:

- **Sync manga progress...** - a preview table of every manga series that has an AniList link: the chapter
  that would be sent (editable, never below what AniList already has), AniList's current chapter, and what
  will happen. **Sync now** pushes the ticked rows and writes each volume's completed chapter to
  `#manga_chapter_read`. Without a token it is a preview only (the column is still filled).
- **Link to AniList...** - paste `https://anilist.co/manga/<id>/...` for the selected books (empty = remove).
  *Check on AniList* shows which entry you are linking; by default every other volume of the series is
  linked too. The link is saved as an `anilist:<id>` identifier, like Goodreads' `goodreads:<id>`.
- **Linked entry > Open on AniList / Remove link**.

Needs the columns `#manga_chapters` (text, e.g. `19-27`) and `#manga_chapter_read` (integer), and the Kobo
Utilities percent-read column. Column and tag names are configurable. The token is stored as plain text in
Calibre's config folder (as Goodreads Sync does for its login).

## Being kind to AniList (and Calibre)

AniList is a free community service, so this plugin asks as little of it as it can:

- **Nothing runs on its own.** No timers, no background sync, no hooks on Kobo connect or library
  changes. The Kobo -> Calibre step is entirely Kobo Utilities; this plugin does nothing then.
- **Opening Sync = at most one request.** All linked series are looked up together (AniList allows 50
  per request), and only series that actually have a chapter to send are included. 13 linked series:
  1 request, not 13.
- **A series that was already sent is not looked up again** until its chapter goes above what was sent
  (progress is never lowered, so there is nothing to change). Nothing new anywhere = 0 requests.
- **No token = 0 requests** (preview only). Typing or pasting a link sends nothing; only the
  "Check on AniList" button does (1 request).
- **Pushing sends only changed series**, one request each, at least 0.7 s apart, chapters only.
- **If AniList answers 429 (slow down) the whole batch stops**: it honours `Retry-After`, makes at most
  3 retries, and does not go on to the next series. Server errors back off 2 s, 4 s, 6 s.
- Requests identify themselves (`anilist-calibre-plugin/<version>` with the repository URL).
- Interactive checks have an 8 s timeout so Calibre never hangs on a bad connection.

Measured (copy of a 664-book library, 13 linked series, fake AniList): loading records 2.6 ms, planning
0.4 ms, writing 74 volumes to the chapter column 3 ms, a repeat run writes 0 books; plugin import ~100 ms
once at Calibre start. `tests/integration_plugin.py` asserts the request counts above.

## Icon

The toolbar icon is AniList's public logo (https://anilist.co), used only to identify the service the
plugin talks to. It is AniList's trademark and is not covered by this repository's MIT licence.

## Command line

```
# preview only - nothing is sent, no token needed
python -m anilist_manga_sync.cli --library ~/"Calibre Library" --series Kagurabachi

# push (asks before each series)
export ANILIST_TOKEN=...        # in your own terminal; never paste it elsewhere
python -m anilist_manga_sync.cli --library ~/"Calibre Library" --series Kagurabachi --apply
```

The library is only read (from a temporary copy of `metadata.db`).

### Getting an AniList token

1. https://anilist.co/settings/developer -> **Create New Client**, redirect URL `https://anilist.co/api/v2/oauth/pin`.
2. Open `https://anilist.co/api/v2/oauth/authorize?client_id=<your client id>&response_type=token`, approve,
   and copy the token shown.
3. `export ANILIST_TOKEN=<token>` in your terminal. Tokens last about a year.

## Builds

Every push to `main` runs the tests and builds the zip (a downloadable artifact on the Actions run).
Pushing a tag `vX.Y.Z` (it must match `version` in `calibre_plugin/__init__.py`) publishes a GitHub Release
with the zip attached.

## Tests

`python -m unittest discover -s tests` (or pytest). `tests/integration_plugin.py` runs inside Calibre's Python
against a copy of a library and a fake AniList (instructions in the file).
