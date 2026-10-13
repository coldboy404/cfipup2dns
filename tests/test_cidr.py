import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('sync_cidr', Path(__file__).parents[1] / 'docker/sync_cidr.py')
assert spec is not None and spec.loader is not None
cidr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cidr)


class CidrTests(unittest.TestCase):
    def test_managed_upgrade_and_custom_preservation(self):
        with tempfile.TemporaryDirectory() as root:
            project, bundled = Path(root) / 'data', Path(root) / 'bundled'
            bundled.mkdir()
            (bundled / 'ipv4cidr.txt').write_text('1.1.1.0/24\n')
            (bundled / 'ipv6cidr.txt').write_text('2606:4700::/32\n')
            cidr.sync(project, bundled)
            (bundled / 'ipv4cidr.txt').write_text('1.0.0.0/24\n')
            cidr.sync(project, bundled)
            self.assertEqual((project / 'ipv4cidr.txt').read_text(), '1.0.0.0/24\n')
            (project / 'ipv4cidr.txt').write_text('8.8.8.0/24\n')
            state = cidr.sync(project, bundled)
            self.assertFalse(state['ipv4cidr.txt']['managed'])
            self.assertEqual((project / 'ipv4cidr.txt').read_text(), '8.8.8.0/24\n')
            (project / '.cidr-manifest.json').unlink()
            cidr.sync(project, bundled)
            self.assertEqual((project / 'ipv4cidr.txt').read_text(), '8.8.8.0/24\n')
            (project / 'ipv4cidr.txt').unlink()
            state = cidr.sync(project, bundled)
            self.assertTrue(state['ipv4cidr.txt']['managed'])

    def test_invalid_bundle_does_not_overwrite_existing(self):
        with tempfile.TemporaryDirectory() as root:
            project, bundled = Path(root) / 'data', Path(root) / 'bundled'
            project.mkdir()
            bundled.mkdir()
            (project / 'ipv4cidr.txt').write_text('1.1.1.0/24\n')
            (bundled / 'ipv4cidr.txt').write_text('<html>error</html>')
            with self.assertRaises(ValueError):
                cidr.sync(project, bundled)
            self.assertEqual((project / 'ipv4cidr.txt').read_text(), '1.1.1.0/24\n')
