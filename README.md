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
