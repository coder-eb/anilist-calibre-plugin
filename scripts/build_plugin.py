#!/usr/bin/env python3
"""Build the Calibre plugin zip.

The zip holds the files in ``calibre_plugin/`` (icons under ``images/``) plus the shared, Calibre-free modules from
``anilist_manga_sync/`` (listed in SHARED). The build fails if any module the plugin imports
is missing from the zip, so a forgotten file cannot ship silently.

    python scripts/build_plugin.py              # -> dist/AniList-Manga-Sync.zip
    python scripts/build_plugin.py --version    # prints the version from calibre_plugin/__init__.py
"""
import argparse
import ast
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / 'calibre_plugin'
SHARED_DIR = ROOT / 'anilist_manga_sync'
SHARED = ['anilist', 'chapters', 'http', 'library', 'links', 'ops', 'planner', 'sync']


def plugin_version():
    text = (PLUGIN_DIR / '__init__.py').read_text()
    m = re.search(r'version\s*=\s*\((\d+),\s*(\d+),\s*(\d+)\)', text)
    if not m:
        raise SystemExit('no version = (x, y, z) found in calibre_plugin/__init__.py')
    return '%s.%s.%s' % m.groups()


def collect():
    files = [(p.name, p) for p in sorted(PLUGIN_DIR.iterdir())
             if p.is_file() and p.suffix != '.pyc' and p.name != '.DS_Store']
    # Icons live in images/ inside the zip (Calibre's get_icons('images/x.png') reads from there).
    images = PLUGIN_DIR / 'images'
    if images.is_dir():
        files += [('images/' + p.name, p) for p in sorted(images.iterdir())
                  if p.is_file() and p.suffix in ('.png', '.svg')]
    files += [('%s.py' % m, SHARED_DIR / ('%s.py' % m)) for m in SHARED]
    names = [n for n, _ in files]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        raise SystemExit('name clash between calibre_plugin/ and anilist_manga_sync/: %s' % sorted(dupes))
    return files


def missing_imports(files):
    """Relative imports (``from . import x`` / ``from .x import y``) that have no file in the zip."""
    names = {n[:-3] for n, _ in files if n.endswith('.py')}
    missing = set()
    for name, path in files:
        if not name.endswith('.py'):
            continue
        for node in ast.walk(ast.parse(path.read_text(), name)):
            if isinstance(node, ast.ImportFrom) and node.level == 1:
                wanted = [node.module] if node.module else [a.name for a in node.names]
                missing.update('%s imports %s' % (name, w.split('.')[0]) for w in wanted
                               if w and w.split('.')[0] not in names)
    return sorted(missing)


def build(out):
    files = collect()
    problems = missing_imports(files)
    if problems:
        raise SystemExit('the zip would be missing modules:\n  ' + '\n  '.join(problems))
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, path in files:
            z.write(path, name)
    return out, [n for n, _ in files]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--version', action='store_true', help='print the plugin version and exit')
    ap.add_argument('--out', default=str(ROOT / 'dist' / 'AniList-Manga-Sync.zip'))
    args = ap.parse_args(argv)
    if args.version:
        print(plugin_version())
        return 0
    out, names = build(Path(args.out))
    print('Built %s (%d files, version %s)' % (out, len(names), plugin_version()))
    return 0


if __name__ == '__main__':
    sys.exit(main())
