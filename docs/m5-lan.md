# M5 LAN service operations and acceptance

Milestone 5 (M5) connects the main-computer web client to a durable routing engine on Ai360 (`192.168.178.100`) over the local area network using direct HTTPS and token-based authentication.

M5 LAN deployment and main-computer acceptance were verified on 2026-11-07. All REST and WebSocket authentication checks, certificate validation, browser disconnection/reconnection cursor streaming, independent solution validation, and graceful cancellation passed.

## Configuration & Architecture

Ai360 runs three isolated rootless Podman containers based on the pinned base runtime:
- `vrp-m5-osrm`: OSRM routing backend serving frozen Bologna and Modena road networks.
- `vrp-m5-service`: Uvicorn/FastAPI service bound to LAN IP `192.168.178.100:8000` with TLS encryption and token verification.
- `vrp-m5-worker`: Durable background worker consuming queued jobs from `runs/jobs.sqlite`.

Configuration files (server certificate, private key, token) reside in `/home/benz/.config/vrp-ai-m5/` (permissions `0600`/`0700`), strictly outside Git.

```sh
# Service invocation on Ai360:
python -m engine serve \
  --root /app/runs \
  --data-root /app/data \
  --host 192.168.178.100 \
  --server-name Ai360.fritz.box \
  --port 8000 \
  --certfile /config/server.crt \
  --keyfile /config/server.key \
  --token-file /config/token \
  --allowed-origin http://127.0.0.1:8000 \
  --allowed-client-ip 192.168.178.92
```

The browser client is served on the main computer. The user enters the Ai360 HTTPS origin and token in the connection bar in the header. Both values persist in browser `sessionStorage` and are cleared when the browser session closes. REST requests include the `X-VRP-Token` header. WebSocket streams connect over `wss` with the `token` query parameter. Access logs are automatically suppressed when a token is configured.

Interactive solve jobs support both the classical heuristic (`policy: "cpu"`) and the M4 supervised policy (`policy: "learned"`), selectable in the client scenario panel. When `"learned"` is selected, the worker resolves `VRP_LEARNED_CHECKPOINT` or the newest imported checkpoint under `runs/m4-imports/*/checkpoint.json` and runs portable NumPy FP32 CPU inference. When OSRM runs in a dedicated sibling container (`vrp-m5-osrm`), `VRP_OSRM_EXTERNAL_MANIFEST_HASHES` authorizes the verified city manifest hash without requiring a host Docker socket inside the worker container. CPU execution is the primary solve runtime on Ai360; GPU acceleration was evaluated and used for M4 training probes, while NPU execution remains excluded.

To rotate the token: generate a new random token in a restricted file, update the configuration path, restart the service process (`podman restart vrp-m5-service`), and enter the new token in the client.

## Acceptance Evidence (2026-11-07)

Acceptance was audited independently from the main development environment using `scripts/accept_m5.py`. Results are recorded in `runs/m5-acceptance.json`.

- **Target endpoint**: `https://192.168.178.100:8000`
- **Server certificate SHA-256**: `d288c6e44aecb2dff91717fec2a2a97db64948dd7cea0319bd9184e25d70732f`
- **Local CA certificate SHA-256**: `c9e2adf51e2e65d0b984c691f02c9cfe6986c9793f5919a60c7f2ae44ffda54d`
- **REST authentication matrix**:
  - Unauthenticated `GET /api/v1/health` -> HTTP 401
  - Invalid token `GET /api/v1/health` -> HTTP 401
  - Unauthorized origin `GET /api/v1/health` -> HTTP 403
  - Unauthorized export `GET /api/v1/experiments/x/export/json` -> HTTP 401
  - Authorized `GET /api/v1/health` -> HTTP 200 (`{"service":"ok","worker":"separate process","schema_version":1}`)
  - City verification: Bologna installed and verified (`manifest_hash: c135557e706cf3dc...`).
- **WebSocket authentication matrix**:
  - Missing token -> handshake rejected (1008 close / HTTP 403)
  - Invalid token -> handshake rejected (1008 close / HTTP 403)
  - Unauthorized origin -> handshake rejected (1008 close / HTTP 403)
  - Authorized connection -> stream accepted and events received.
- **Solve lifecycle & reconnection cursor**:
  - 20-stop Bologna scenario prepared via remote OSRM (`prep_job_id: 6f2fe537-f7fb-4646-b3f4-fbf2558521f6`).
  - Solve job (`1b9fe6f4-0d7b-4a01-952b-1f29fe9f345c`) submitted with budget 3.0s, 2 restarts.
  - WebSocket stream connected: 3 events received before simulated browser disconnection.
  - Client reconnected with cursor `?after=3`: 32 subsequent events and terminal status event received without loss or duplication.
  - Measured timings: solve time 3.23s (total wall time 4.44s, search 2.21s, recording 1.02s).
  - Independent validation: solution confirmed feasible (35,495 m distance, 2 vans, 4.52 L fuel estimate).
- **Graceful cancellation**:
  - Solve job `cd216cca-e535-4629-97e9-40247f82232b` cancelled during execution; job reached status `cancelled`.
- **Target environment**:
  - Host: Linux Ai360 7.2.10-200.fc44.x86_64
  - CPU: AMD Ryzen AI 9 HX 370 w/ Radeon 890M (24 logical cores)
  - RAM: 28 GiB total (8.6 GiB used)
  - Containers: `vrp-m5-osrm`, `vrp-m5-service`, `vrp-m5-worker` (rootless Podman)
- **Automated test suite verification**:
  - Python suite: 92 tests passed (`tests/test_m5.py` verifies TLS requirements, CLI arguments, client IP filtering, REST auth, and WebSocket rejection/acceptance).
  - Client unit tests: 17 Vitest tests passed (`client/src/connection.test.ts`).
  - Client browser tests: Playwright test passed (`client/e2e/lan.spec.ts` verifies connection form, validation errors, and `sessionStorage` persistence).
