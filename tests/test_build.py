import importlib.util
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('build_plugin', ROOT / 'scripts' / 'build_plugin.py')
build_plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_plugin)


class BuildTests(unittest.TestCase):
    def test_version_comes_from_the_plugin(self):
        self.assertRegex(build_plugin.plugin_version(), r'^\d+\.\d+\.\d+$')

    def test_zip_is_flat_and_complete(self):
        with tempfile.TemporaryDirectory() as d:
            out, names = build_plugin.build(Path(d) / 'x.zip')
            with zipfile.ZipFile(out) as z:
                self.assertEqual(sorted(z.namelist()), sorted(names))
                # flat, except the icons under images/
                self.assertFalse([n for n in z.namelist() if '/' in n and not n.startswith('images/')])
        for needed in ('__init__.py', 'action.py', 'dialogs.py', 'config.py', 'anilist.py', 'sync.py',
                       'planner.py', 'ops.py', 'links.py', 'images/anilist.png',
                       'plugin-import-name-anilist_calibre_plugin.txt'):
            self.assertIn(needed, names)

    def test_no_module_the_plugin_imports_is_missing(self):
        self.assertEqual(build_plugin.missing_imports(build_plugin.collect()), [])

    def test_a_missing_shared_module_would_be_caught(self):
        files = [f for f in build_plugin.collect() if f[0] != 'ops.py']
        self.assertTrue(any('imports ops' in p for p in build_plugin.missing_imports(files)))

    def test_cli_is_not_shipped(self):
        names = [n for n, _ in build_plugin.collect()]
        self.assertNotIn('cli.py', names)


if __name__ == '__main__':
    unittest.main()
