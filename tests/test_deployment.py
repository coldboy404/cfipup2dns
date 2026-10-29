import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DeploymentTests(unittest.TestCase):
    def run_up(self, mode, build=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = root / 'docker'
            binary.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$DOCKER_TEST_LOG"\n')
            binary.chmod(0o700)
            log = root / 'commands'
            env = dict(os.environ, PATH=f'{root}:{os.environ["PATH"]}',
                       NETWORK_MODE=mode, BUILD_LOCAL='1' if build else '0',
                       DOCKER_TEST_LOG=str(log))
            result = subprocess.run(['bash', str(ROOT / 'docker/up.sh')],
                                    env=env, capture_output=True, text=True)
            return result, log.read_text().splitlines() if log.exists() else []

    def test_bridge_is_default_compatible(self):
        result, commands = self.run_up('bridge')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(commands, [
            'compose config --quiet',
            'compose pull',
            'compose up -d --remove-orphans',
        ])

    def test_host_overlay_used_for_every_compose_command(self):
        for build in (False, True):
            with self.subTest(build=build):
                result, commands = self.run_up('host', build)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(len(commands), 3)
                self.assertTrue(all('-f docker-compose.host.yml' in c for c in commands))
                self.assertIn('build --pull' if build else 'pull', commands[1])

    def test_invalid_network_mode_has_no_docker_side_effects(self):
        result, commands = self.run_up('invalid')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(commands, [])

    def test_host_overlay_clears_port_mappings_and_binds_loopback(self):
        overlay = (ROOT / 'docker-compose.host.yml').read_text()
        self.assertIn('network_mode: host', overlay)
        self.assertIn('ports: !reset []', overlay)
        self.assertIn('WEB_BIND_HOST:-127.0.0.1', overlay)


if __name__ == '__main__':
    unittest.main()
