# AniList Calibre plugin

Sync manga reading progress from Calibre to AniList, at **chapter** level.

> **Status:** the command-line tool works today (preview and push). The Calibre menu plugin
> (a toolbar button with a preview table) is the next step and is not built yet.

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

## Tests

`python -m pytest tests`
