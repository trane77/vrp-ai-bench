"""LAN authentication, origin boundaries, and WebSocket security."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from engine.service import create_app
from engine.jobs import Jobs
from engine.__main__ import main


class LanServiceTests(unittest.TestCase):
    def test_lan_requires_tls_and_token(self):
        for args in (['--host', '192.0.2.10'],
                     ['--host', '192.0.2.10', '--certfile', 'cert.pem', '--keyfile', 'key.pem']):
            with patch('uvicorn.run') as run:
                self.assertEqual(main(['serve', *args]), 2)
                run.assert_not_called()
        with patch('uvicorn.run') as run:
            self.assertEqual(main(['serve']), 0)
            self.assertEqual(run.call_args.kwargs['host'], '127.0.0.1')
            self.assertTrue(run.call_args.kwargs['access_log'])

    def test_cli_origin_and_token_validation(self):
        with tempfile.TemporaryDirectory() as outside_dir:
            outside = Path(outside_dir)
            short_token = outside / 'short.token'
            short_token.write_text('short')
            with patch('uvicorn.run') as run:
                self.assertEqual(main(['serve', '--token-file', str(short_token)]), 2)
                run.assert_not_called()

            inside_token = Path(__file__).resolve().parent / 'test_inside.token'
            inside_token.write_text('x' * 40)
            try:
                with patch('uvicorn.run') as run:
                    self.assertEqual(main(['serve', '--token-file', str(inside_token)]), 2)
                    run.assert_not_called()
            finally:
                if inside_token.exists():
                    inside_token.unlink()

            valid_token = outside / 'valid.token'
            valid_token.write_text('t' * 40)

            # Invalid origins: path present, non-loopback http
            for bad_origin in ('https://main.lan/path', 'http://external.lan', 'ftp://main.lan'):
                with patch('uvicorn.run') as run:
                    self.assertEqual(main(['serve', '--token-file', str(valid_token), '--allowed-origin', bad_origin]), 2)
                    run.assert_not_called()

            # Valid origins: HTTPS or loopback HTTP
            cert = outside / 'server.crt'; cert.write_text('dummy-cert')
            key = outside / 'server.key'; key.write_text('dummy-key')
            with patch('uvicorn.run') as run:
                code = main(['serve', '--host', '192.168.1.100', '--server-name', 'ai360.lan',
                             '--certfile', str(cert), '--keyfile', str(key),
                             '--token-file', str(valid_token),
                             '--allowed-origin', 'https://main.lan',
                             '--allowed-origin', 'http://127.0.0.1:8000',
                             '--allowed-client-ip', '192.168.1.50'])
                self.assertEqual(code, 0)
                self.assertFalse(run.call_args.kwargs['access_log'])
                self.assertEqual(run.call_args.kwargs['ssl_certfile'], str(cert))
                self.assertEqual(run.call_args.kwargs['ssl_keyfile'], str(key))

    def test_authentication_and_origin(self):
        with tempfile.TemporaryDirectory() as root:
            app = create_app(root, client_dir='/missing', token='x' * 40,
                             allowed_origins=['https://main.lan'], allowed_hosts=['ai360.lan'])
            with TestClient(app, base_url='https://ai360.lan') as client:
                url = '/api/v1/health'
                self.assertEqual(client.get(url).status_code, 401)
                self.assertEqual(client.get(url, headers={'X-VRP-Token': 'wrong'}).status_code, 401)
                headers = {'X-VRP-Token': 'x' * 40, 'Origin': 'https://main.lan'}
                self.assertEqual(client.get(url, headers=headers).status_code, 200)
                self.assertEqual(client.get(url, headers={**headers, 'Origin': 'https://other.lan'}).status_code, 403)
                self.assertEqual(client.options(url, headers={'Origin': 'https://main.lan'}).status_code, 204)
                self.assertEqual(client.get('/api/v1/experiments/x/export/json').status_code, 401)

    def test_allowed_client_ips_rejection(self):
        with tempfile.TemporaryDirectory() as root:
            app_reject = create_app(root, client_dir='/missing', token='x' * 40,
                                    allowed_client_ips=['192.0.2.99'], allowed_hosts=['ai360.lan'])
            with TestClient(app_reject, base_url='https://ai360.lan') as client:
                res = client.get('/api/v1/health', headers={'X-VRP-Token': 'x' * 40})
                self.assertEqual(res.status_code, 403)
                self.assertEqual(res.json(), {'detail': 'client address rejected'})

            app_allow = create_app(root, client_dir='/missing', token='x' * 40,
                                   allowed_client_ips=['testclient'], allowed_hosts=['ai360.lan'])
            with TestClient(app_allow, base_url='https://ai360.lan') as client:
                res = client.get('/api/v1/health', headers={'X-VRP-Token': 'x' * 40})
                self.assertEqual(res.status_code, 200)

    def test_websocket_authentication_and_origin(self):
        with tempfile.TemporaryDirectory() as root:
            app = create_app(root, client_dir='/missing', token='k' * 40,
                             allowed_origins=['https://main.lan'], allowed_hosts=['ai360.lan'],
                             allowed_client_ips=['testclient'])
            jobs = Jobs(root)
            saved = jobs.save_draft({'stops': [], 'seed': 42}, id='d1')
            job = jobs.submit('prepare', {'city_id': 'bologna', 'seed': 42, 'count': 5,
                                          'draft_id': 'd1', 'revision': saved['revision']})
            stream_url = f"wss://ai360.lan/api/v1/jobs/{job['id']}/stream"

            with TestClient(app, base_url='https://ai360.lan') as client:
                # 1. Missing token -> close 1008
                with self.assertRaises(WebSocketDisconnect) as ctx:
                    with client.websocket_connect(f"{stream_url}?after=0", headers={'Origin': 'https://main.lan'}):
                        pass
                self.assertEqual(ctx.exception.code, 1008)

                # 2. Bad token -> close 1008
                with self.assertRaises(WebSocketDisconnect) as ctx:
                    with client.websocket_connect(f"{stream_url}?after=0&token=bad", headers={'Origin': 'https://main.lan'}):
                        pass
                self.assertEqual(ctx.exception.code, 1008)

                # 3. Bad origin -> close 1008
                with self.assertRaises(WebSocketDisconnect) as ctx:
                    with client.websocket_connect(f"{stream_url}?after=0&token={'k'*40}", headers={'Origin': 'https://bad.lan'}):
                        pass
                self.assertEqual(ctx.exception.code, 1008)

                # 4. Negative after cursor -> close 1008
                with self.assertRaises(WebSocketDisconnect) as ctx:
                    with client.websocket_connect(f"{stream_url}?after=-1&token={'k'*40}", headers={'Origin': 'https://main.lan'}):
                        pass
                self.assertEqual(ctx.exception.code, 1008)

                # 5. Missing job ID -> close 1008
                with self.assertRaises(WebSocketDisconnect) as ctx:
                    with client.websocket_connect(f"wss://ai360.lan/api/v1/jobs/missing-id/stream?after=0&token={'k'*40}",
                                                  headers={'Origin': 'https://main.lan'}):
                        pass
                self.assertEqual(ctx.exception.code, 1008)

                # 6. Valid token, allowed origin, valid job -> successfully connects
                with client.websocket_connect(f"{stream_url}?after=0&token={'k'*40}", headers={'Origin': 'https://main.lan'}) as ws:
                    self.assertIsNotNone(ws)


if __name__ == '__main__':
    unittest.main()
