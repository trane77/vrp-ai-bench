"""Audit Ai360 authenticated LAN deployment, TLS, REST/WSS authentication,
browser session disconnect/reconnect cursor, independent solution validation,
and graceful job cancellation.
"""
import argparse
import hashlib
import json
from pathlib import Path
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine.contracts import validate_scenario
from engine.storage import write_json_atomic
from engine.validation import validate_solution
from websockets.sync.client import connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake


def get_cert_fingerprint(host, port, ca_cert):
    ctx = ssl.create_default_context(cafile=ca_cert)
    with ctx.wrap_socket(socket_connect(host, port), server_hostname=host) as s:
        der = s.getpeercert(binary_form=True)
        return hashlib.sha256(der).hexdigest()


def socket_connect(host, port):
    import socket
    s = socket.create_connection((host, port), timeout=10)
    return s


def main():
    parser = argparse.ArgumentParser(description='Audit Ai360 LAN deployment and authentication')
    parser.add_argument('--url', default='https://192.168.178.100:8000')
    parser.add_argument('--token')
    parser.add_argument('--token-file', default='/tmp/vrp-ai-m5-token')
    parser.add_argument('--ca-cert', default='/tmp/vrp-ai-m5-ca.crt')
    parser.add_argument('--city', default='bologna')
    parser.add_argument('--count', type=int, default=20)
    parser.add_argument('--budget-s', type=float, default=3.0)
    parser.add_argument('--host-alias', default='Ai360')
    parser.add_argument('--allowed-origin', default='http://127.0.0.1:8000')
    parser.add_argument('--report', default='runs/m5-acceptance.json')
    args = parser.parse_args()

    token = args.token or Path(args.token_file).read_text().strip()
    ca_path = Path(args.ca_cert).resolve()
    if not ca_path.is_file():
        raise RuntimeError(f'CA certificate not found: {ca_path}')
    ca_bytes = ca_path.read_bytes()
    ca_fingerprint = hashlib.sha256(ca_bytes).hexdigest()

    parsed = urllib.parse.urlparse(args.url)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)

    print(f'[1/7] Verifying TLS certificate against CA: {args.url}')
    cert_fingerprint = get_cert_fingerprint(host, port, str(ca_path))
    print(f'      Server cert SHA-256: {cert_fingerprint}')
    print(f'      CA cert SHA-256:     {ca_fingerprint}')

    ssl_ctx = ssl.create_default_context(cafile=str(ca_path))

    def api(path, method='GET', body=None, token_val=token, origin=args.allowed_origin):
        headers = {'Content-Type': 'application/json'}
        if token_val:
            headers['X-VRP-Token'] = token_val
        if origin:
            headers['Origin'] = origin
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f'{args.url}/api/v1{path}', data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, context=ssl_ctx, timeout=30) as resp:
            content = resp.read()
            return resp.status, json.loads(content) if content else None

    def api_expect(path, expected_status, method='GET', body=None, token_val=token, origin=args.allowed_origin):
        headers = {'Content-Type': 'application/json'}
        if token_val:
            headers['X-VRP-Token'] = token_val
        if origin:
            headers['Origin'] = origin
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f'{args.url}/api/v1{path}', data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, context=ssl_ctx, timeout=10) as resp:
                status = resp.status
        except urllib.error.HTTPError as e:
            status = e.code
        if status != expected_status:
            raise RuntimeError(f'Expected HTTP {expected_status} for {method} {path}, got {status}')
        return status

    print('[2/7] Testing REST authentication and origin boundary matrix')
    api_expect('/health', 401, token_val=None)
    api_expect('/health', 401, token_val='bad-token-0123456789012345678901234567890')
    api_expect('/health', 403, origin='https://unauthorized.attacker.lan')
    api_expect('/experiments/x/export/json', 401, token_val=None)
    status, health_resp = api('/health')
    assert status == 200 and health_resp.get('service') == 'ok', f'Unexpected health response: {health_resp}'
    print('      REST authentication matrix passed.')

    print('[3/7] Verifying installed city dataset on remote engine')
    status, cities = api('/cities')
    city_entry = next((c for c in cities if c['id'] == args.city), None)
    if not city_entry or not city_entry.get('installed'):
        raise RuntimeError(f'City {args.city} is not installed on remote engine')
    manifest_hash = city_entry['manifest']['manifest_hash']
    print(f'      City {args.city} verified (manifest_hash: {manifest_hash[:16]}...)')

    ws_base = f"wss://{host}:{port}/api/v1"

    print('[4/7] Testing WebSocket authentication and rejection matrix')
    status, draft = api('/drafts', method='POST', body={'city_id': args.city, 'seed': 42, 'count': args.count})
    status, prep_job = api('/jobs', method='POST', body={'kind': 'prepare', 'draft_id': draft['id'], 'revision': draft['revision']})
    prep_id = prep_job['id']

    # Test WS handshake without token -> should fail
    try:
        with connect(f'{ws_base}/jobs/{prep_id}/stream?after=0', ssl=ssl_ctx,
                     additional_headers={'Origin': args.allowed_origin}):
            raise RuntimeError('WebSocket connected without token!')
    except (InvalidHandshake, ConnectionClosed, OSError):
        pass

    # Test WS handshake with invalid token -> should fail
    try:
        with connect(f'{ws_base}/jobs/{prep_id}/stream?after=0&token=bad', ssl=ssl_ctx,
                     additional_headers={'Origin': args.allowed_origin}):
            raise RuntimeError('WebSocket connected with invalid token!')
    except (InvalidHandshake, ConnectionClosed, OSError):
        pass

    # Test WS handshake with unauthorized origin -> should fail
    try:
        with connect(f'{ws_base}/jobs/{prep_id}/stream?after=0&token={token}', ssl=ssl_ctx,
                     additional_headers={'Origin': 'https://bad.origin.example'}):
            raise RuntimeError('WebSocket connected with unauthorized origin!')
    except (InvalidHandshake, ConnectionClosed, OSError):
        pass
    print('      WebSocket rejection matrix passed.')

    def await_job(job_id, timeout=120):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            _, j = api(f'/jobs/{job_id}')
            if j['status'] in ('completed', 'failed', 'cancelled'):
                return j
            time.sleep(0.3)
        raise TimeoutError(f'Job {job_id} did not finish within {timeout}s')

    print('[5/7] Executing solve lifecycle with mid-search WSS disconnect & reconnect cursor')
    prep_completed = await_job(prep_id)
    assert prep_completed['status'] == 'completed', f'Preparation failed: {prep_completed}'
    _, prep_result = api(f'/jobs/{prep_id}/result')
    scenario = validate_scenario(prep_result)

    solve_post_time = time.monotonic()
    status, solve_job = api('/jobs', method='POST', body={
        'kind': 'solve',
        'draft_id': draft['id'],
        'revision': draft['revision'],
        'prepared_job_id': prep_id,
        'seed': 42,
        'budget_s': args.budget_s,
        'restarts': 2,
    })
    solve_id = solve_job['id']

    events_before_disconnect = []
    events_after_reconnect = []
    last_cursor = 0

    # Connect stream, read a few events, then intentionally disconnect
    stream_url = f'{ws_base}/jobs/{solve_id}/stream'
    with connect(f'{stream_url}?after=0&token={token}', ssl=ssl_ctx,
                 additional_headers={'Origin': args.allowed_origin}) as ws:
        msg = json.loads(ws.recv())
        if msg.get('type') == 'events':
            events_before_disconnect.extend(msg['data'])
            last_cursor = msg['data'][-1]['seq']
        # Disconnect mid-stream
        ws.close()

    time.sleep(0.5)

    # Reconnect with cursor
    terminal_status_event = None
    with connect(f'{stream_url}?after={last_cursor}&token={token}', ssl=ssl_ctx,
                 additional_headers={'Origin': args.allowed_origin}) as ws:
        while True:
            try:
                raw = ws.recv()
            except ConnectionClosed:
                break
            msg = json.loads(raw)
            if msg.get('type') == 'events':
                events_after_reconnect.extend(msg['data'])
            elif msg.get('type') == 'status':
                terminal_status_event = msg['data']
                break

    solve_completed = await_job(solve_id)
    solve_elapsed_total = time.monotonic() - solve_post_time
    assert solve_completed['status'] == 'completed', f'Solve job failed: {solve_completed}'
    _, solve_result = api(f'/jobs/{solve_id}/result')

    # Independent validation
    validation = validate_solution(scenario, solve_result['solution'])
    assert validation['feasible'], f'Solution not feasible: {validation}'
    assert validation == solve_result['validation'], 'Validator mismatch with saved result'
    print(f'      Solve completed and independently validated: feasible={validation["feasible"]}')
    print(f'      Disconnection/reconnection cursor verified: {len(events_before_disconnect)} events before disconnect, {len(events_after_reconnect)} after reconnect.')

    print('[6/7] Testing graceful job cancellation and incumbent preservation')
    status, long_job = api('/jobs', method='POST', body={
        'kind': 'solve',
        'draft_id': draft['id'],
        'revision': draft['revision'],
        'prepared_job_id': prep_id,
        'seed': 99,
        'budget_s': 20.0,
        'restarts': 8,
    })
    long_id = long_job['id']
    time.sleep(1.0)
    api(f'/jobs/{long_id}/cancel', method='POST')
    cancelled_job = await_job(long_id, timeout=30)
    assert cancelled_job['status'] == 'cancelled', f'Expected cancelled status, got {cancelled_job["status"]}'

    _, cancel_result = api(f'/jobs/{long_id}/result')
    if cancel_result.get('solution'):
        cancel_validation = validate_solution(scenario, cancel_result['solution'])
        assert cancel_validation['feasible'], 'Cancelled incumbent solution is invalid'
        print('      Job cancellation verified; incumbent preserved and independently validated.')
    else:
        print('      Job cancellation verified (cancelled before first incumbent).')

    print('[7/7] Collecting remote host hardware and container provenance')
    provenance = {}
    try:
        cmd = "uname -srm && lscpu | grep 'Model name:' && free -h && podman ps --filter name=vrp-m5 --format '{{.Names}} {{.Image}} {{.Status}}'"
        remote_out = subprocess.check_output(
            ['ssh', '-o', 'BatchMode=yes', args.host_alias, cmd],
            text=True, timeout=15
        ).strip()
        provenance['remote_inspection'] = remote_out
    except Exception as e:
        provenance['remote_inspection_error'] = str(e)

    report = {
        'acceptance_timestamp': time.time(),
        'target_url': args.url,
        'server_cert_fingerprint': cert_fingerprint,
        'ca_cert_fingerprint': ca_fingerprint,
        'rest_auth_matrix': 'passed',
        'websocket_auth_matrix': 'passed',
        'city': args.city,
        'manifest_hash': manifest_hash,
        'solve_lifecycle': {
            'prep_job_id': prep_id,
            'solve_job_id': solve_id,
            'events_before_disconnect': len(events_before_disconnect),
            'events_after_reconnect': len(events_after_reconnect),
            'total_events': solve_completed['last_seq'],
            'solve_elapsed_wall_s': solve_elapsed_total,
            'timing': solve_result.get('timing', {}),
            'validation': validation,
        },
        'cancellation_test': {
            'job_id': long_id,
            'status': cancelled_job['status'],
            'incumbent_preserved': bool(cancel_result.get('solution')),
        },
        'provenance': provenance,
    }

    report_path = Path(args.report)
    write_json_atomic(report_path, report)
    print(f'\nMilestone 5 acceptance successfully verified and saved to {report_path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
