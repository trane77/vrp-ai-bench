"""Startup regressions: preserve running services and report Docker diagnostics."""
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock, patch

from engine.city import OSRM_IMAGE, docker, serve
from engine.contracts import InputError


class DockerDiagnosticsTests(unittest.TestCase):
    def test_original_error_is_visible_with_or_without_stdout_capture(self):
        detail = 'Error response from daemon: Conflict. The container name is already in use.'
        for capture in (False, True):
            with self.subTest(capture=capture):
                error = subprocess.CalledProcessError(125, ['docker', 'run'], stderr=detail)
                with patch('engine.city.subprocess.run', side_effect=error):
                    with self.assertRaises(RuntimeError) as raised:
                        docker('run', capture=capture)
                self.assertIn(detail, str(raised.exception))
                self.assertIn('exit 125', str(raised.exception))

    def test_success_keeps_stdout_and_warning(self):
        result = subprocess.CompletedProcess(['docker', 'inspect'], 0,
                                             stdout=' container-id\n', stderr='Docker warning\n')
        with patch('engine.city.subprocess.run', return_value=result), patch('engine.city.sys.stderr') as stderr:
            self.assertEqual(docker('inspect', capture=True), 'container-id')
            stderr.write.assert_called_once_with('Docker warning\n')


class CityServeTests(unittest.TestCase):
    def setUp(self):
        self.registry = Mock(root=Path('/tmp/vrp-ai-startup-test'))
        self.manifest = {'source_sha256': '1' * 64}
        self.registry.verify.return_value = self.manifest
        self.container = {
            'Id': 'existing-container-id',
            'State': {'Running': True},
            'Config': {'Image': OSRM_IMAGE,
                       'Labels': {'org.vrp-ai.source_sha256': self.manifest['source_sha256']}},
            'Mounts': [{'Destination': '/data', 'Source': str(self.registry.root / 'source'), 'RW': False}],
            'NetworkSettings': {'Ports': {'5000/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '5000'}]}}
        }
        self.patch = patch('engine.city.CityRegistry', return_value=self.registry)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def existing_docker(self, *args, **kwargs):
        if args[0] == 'ps':
            return 'existing-container-id'
        if args[0] == 'inspect':
            return json.dumps([self.container])
        self.fail(f'existing container must not be changed: {args}')

    def test_repeated_serve_reuses_matching_container(self):
        with patch('engine.city.docker', side_effect=self.existing_docker):
            self.assertEqual(serve(), 'existing-container-id')
            self.assertEqual(serve(), 'existing-container-id')
        self.assertEqual(self.registry.verify.call_count, 4)

    def test_different_snapshot_is_not_reused_or_replaced(self):
        self.container['Config']['Labels']['org.vrp-ai.source_sha256'] = '2' * 64
        with patch('engine.city.docker', side_effect=self.existing_docker):
            with self.assertRaisesRegex(InputError, 'does not match'):
                serve()

    def test_public_binding_is_not_reused(self):
        self.container['NetworkSettings']['Ports']['5000/tcp'][0]['HostIp'] = '0.0.0.0'
        with patch('engine.city.docker', side_effect=self.existing_docker):
            with self.assertRaisesRegex(InputError, 'loopback'):
                serve()

    def test_stopped_container_has_actionable_error_and_is_not_deleted(self):
        self.container['State']['Running'] = False
        with patch('engine.city.docker', side_effect=self.existing_docker):
            with self.assertRaisesRegex(InputError, 'docker logs vrp-ai-osrm'):
                serve()

    def test_first_start_retains_loopback_binding_and_readonly_dataset(self):
        with patch('engine.city.docker', side_effect=['', 'new-container-id']) as command:
            self.assertEqual(serve(), 'new-container-id')
        args = command.call_args_list[-1].args
        self.assertEqual(args[0], 'run')
        self.assertIn('127.0.0.1:5000:5000', args)
        self.assertIn(f'{self.registry.root / "source"}:/data:ro', args)
        self.assertIn(OSRM_IMAGE, args)


if __name__ == '__main__':
    unittest.main()
