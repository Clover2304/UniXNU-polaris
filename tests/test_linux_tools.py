"""Dependency download failure recovery without apt or network access."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('linux_tools',
    Path(__file__).resolve().parents[1] / 'scripts/ensure-linux-tools.py')
tools = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tools)


class LinuxToolMirrorTests(unittest.TestCase):
    def invoke(self, outcomes):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            key = work / 'ubuntu.gpg'
            key.touch()
            with patch.object(tools.platform, 'freedesktop_os_release',
                              return_value={'ID': 'ubuntu', 'VERSION_CODENAME': 'noble'}), \
                 patch.object(tools.subprocess, 'check_output', return_value='amd64\n'), \
                 patch.object(tools, 'UBUNTU_KEYRING', key), \
                 patch.object(tools.subprocess, 'run', side_effect=outcomes) as run:
                error = None
                try:
                    tools.install_packages(['qemu-system-arm'], work)
                except SystemExit as caught:
                    error = caught
                sources = [p.read_text() for p in sorted(work.glob('apt-*/ubuntu.sources'))]
                commands = [call.args[0] for call in run.call_args_list]
                return error, sources, commands

    def test_failed_update_skips_install_and_uses_fresh_signed_mirror(self):
        error, sources, commands = self.invoke([subprocess.CalledProcessError(100, 'apt-get'), None, None])
        self.assertIsNone(error)
        self.assertEqual(len(commands), 3)
        self.assertEqual(commands[0][-1], 'update')
        self.assertEqual(commands[1][-1], 'update')
        self.assertIn('install', commands[2])
        self.assertIn(tools.APT_MIRRORS[0], sources[0])
        self.assertIn(tools.APT_MIRRORS[1], sources[1])
        list_paths = []
        for command in commands:
            self.assertIn('APT::Update::Error-Mode=any', command)
            self.assertIn('Dir::Etc::sourceparts=-', command)
            self.assertNotIn('--allow-unauthenticated', command)
            list_paths.append(next(x for x in command if x.startswith('Dir::State::lists=')))
        self.assertNotEqual(list_paths[0], list_paths[1])
        self.assertEqual(list_paths[1], list_paths[2])
        for source in sources:
            self.assertIn('Suites: noble noble-updates noble-security', source)
            self.assertIn('Signed-By:', source)
            self.assertNotIn('trusted', source)

    def test_install_timeout_advances_to_next_mirror(self):
        error, sources, commands = self.invoke([None, subprocess.TimeoutExpired('apt-get', 195), None, None])
        self.assertIsNone(error)
        self.assertEqual(len(sources), 2)
        self.assertEqual(len(commands), 4)
        self.assertTrue(all('180s' in command and '--kill-after=5s' in command for command in commands))

    def test_all_mirrors_failed_never_installs_from_stale_lists(self):
        error, sources, commands = self.invoke([subprocess.CalledProcessError(100, 'apt-get')
                                               for _ in tools.APT_MIRRORS])
        self.assertIsNotNone(error)
        self.assertEqual(len(sources), len(tools.APT_MIRRORS))
        self.assertTrue(all(command[-1] == 'update' for command in commands))
