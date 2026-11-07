# M2 local city laboratory

M2 extends the M1 CPU heuristic with real OSM road paths, editable **synthetic**
appointments, a durable worker queue and recorded replay. It makes no AI advantage,
optimality, real fleet calibration or operational-access claim. Bologna and Modena
are separate city manifests over the same regional graph.

## Setup and operation

Use CPython 3.12, Node.js 24 and Docker with a running daemon. No host driver or
system-configuration changes are part of setup. In WSL, Docker Desktop integration
must already work. The service and OSRM bind to loopback; LAN is deliberately
unsupported in this milestone.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
npm --prefix client ci
npm --prefix client run build
.venv/bin/python -m engine city prepare
.venv/bin/python -m engine city verify --city bologna
.venv/bin/python -m engine city verify --city modena
.venv/bin/python -m engine city serve
```

`city prepare` downloads the Geofabrik Nord-Est PBF when it is absent, then runs
OSRM extract/partition/customize with two threads and a 10 GiB container memory
limit. It prepares both city manifests together. An existing PBF can be supplied
with `--source PATH`. The snapshot is frozen locally, not automatically refreshed;
retain it to reproduce a run. Download is about 600 MB for the inspected snapshot;
prepared files occupied about 1.7 GB here. This is an observation, not a future
size guarantee. Stop OSRM before rebuilding its data. Containers write with the
invoking user's UID/GID. `city serve` verifies artifact hashes before starting the
owned `vrp-ai-osrm` container with a 6 GiB memory limit.

`city serve` is idempotent: a running container is reused after its image,
snapshot, read-only mount and loopback binding are verified. An incompatible or
stopped container is reported without automatically stopping or deleting it.
Docker failures include the original diagnostic and exit code. For a container
problem, inspect `docker inspect vrp-ai-osrm` and `docker logs vrp-ai-osrm`; an
available daemon alone does not rule out a name conflict or an occupied port.

Start the following in **separate terminals**:

```sh
.venv/bin/python -m engine worker
.venv/bin/python -m engine serve
```

Open `http://127.0.0.1:8000`. Choose a city, create a demo, prepare paths, then
start the search. Add or move a point with the map toolbar, or use its coordinate
fields. Select a stop from the map or accessible stop list to edit appointments
and demands. Changing road-dependent inputs invalidates the prepared revision.
The worker survives browser and HTTP-server disconnection. Stopping/restarting the
HTTP service does not cancel computation. On worker restart, interrupted running
jobs become failed/interrupted; queued jobs remain available. There is no automatic
search continuation.

For frontend development, `npm --prefix client run dev` proxies `/api`, including
WebSockets, to the engine on port 8000. Use the printed loopback URL. Production
builds are served by the engine on the same origin. Neither Sites registration nor
publication is used.

Shutdown:

```sh
.venv/bin/python -m engine worker --stop
.venv/bin/python -m engine city stop
```

The worker stops after its current job; use the UI cancellation command first when
that job should be cancelled. Stop the HTTP server with Ctrl+C. `--root` selects a
runs directory for both worker and service; `--data-root` selects installed city
artifacts. Keep these settings identical between processes. The worker lock allows
only one worker per runs directory. Full database/schema migrations are not yet
provided; M1 JSON files remain readable in place without conversion.

## Data and model provenance

- Source: [Geofabrik Nord-Est](https://download.geofabrik.de/europe/italy/nord-est.html),
  with OSM timestamp, source URL and SHA-256 recorded. City manifests record service
  center/radius, graph-file hashes, OSRM image digest and car-profile hash.
- License: [OpenStreetMap ODbL](https://www.openstreetmap.org/copyright). The map keeps
  visible OSM, OpenFreeMap and OpenMapTiles attribution. Data and derived artifacts
  stay under ignored `data/`; jobs, traces and results stay under ignored `runs/`.
- Backend: pinned [OSRM MLD](https://github.com/Project-OSRM/osrm-backend#quick-start)
  with the unmodified car profile. Paths minimize that profile's weight
  (`routability`), which is not asserted to equal minimum driving time. Fuel/time
  alternatives over different road paths are not explored.
- The preparation worker verifies the owned local OSRM container's image, snapshot
  label, read-only graph mount and loopback port before road requests. OSRM responses
  alone do not authenticate a snapshot. Do not manually replace the owned container
  or modify graph files while it is running.
- Depot and requested appointments lie within 5 km of the selected center. Snapping
  is limited to 100 m and both requested/snapped points are retained. A provider
  timeout or malformed response fails preparation; it is not an unreachable road.
  NoRoute is an explicit unreachable directed leg. No straight-line routing fallback
  exists. Routes may leave the appointment service area.
- Demo seed defaults to 42, with 20 stops and two vans. Sampling initially uses a
  1.5 km radius, followed by recorded rejection/resampling of unsnappable or
  depot-disconnected samples. The generator creates windows around a validated
  witness schedule, so these are constructed benchmarks with feasibility bias.
  Edited demos preserve supplied points and windows and may be impossible.
- Each reachable leg retains full geometry, raw duration and distance/duration
  annotations. Computation uses `ceil(raw_duration_s)`. Driving categories use
  annotated speed: slow ≤30, urban ≤60, fast >60 km/h. Initial fuel coefficients
  are identical M1 experimental defaults across categories, explicitly editable
  and uncalibrated. Idling is off initially. Neither van/ZTL permission nor live
  traffic is verified by this research model.

## Replay and persistence

Scenario/run v2 carries road provenance and coordinates. Solution proposals remain
v1 route sequences; the validator independently reconstructs schedules for either
scenario version. The v1 CLI and its input rejection rules remain supported.

Events v1 carry job ID, increasing sequence, elapsed computation time, kind and
payload. They record initial state, accepted insertions/moves, feasible-best
updates, metric samples, prepared state and terminal/recovery status. Partial
construction is labeled separately from complete feasible solutions. Local moves
record their type and raw time/fuel deltas; rejected candidates are not animated.

SQLite WAL/FULL transactions index the durable event stream. JSONL journal writes
are fsynced before an event becomes visible. Segments close every 1,000 events and
are compressed at closure/termination. Snapshots are published at the initial
event, every 100 events and terminal/prepared/recovery events. A crash may leave
unindexed journal tail bytes; only committed SQLite events are replayed. Snapshot
files and SQLite snapshot records preserve current candidate and best feasible
state separately. Event traces have a 1 GiB file cap; persistence failure marks the
job failed where storage remains writable. Optional metric samples are throttled
to once per second and omitted counts are recorded at termination.

The result is atomically published before the terminal event and completed job
metadata. A crash before final metadata publication is recovered as interrupted,
with retained observations; an orphan result is not presented as completed.
Cancellation is checked between evaluations and OSRM requests. A single request
has a 10-second timeout. Cancellation of a queued job is processed when the worker
claims it. A cancelled search can retain its independently validated incumbent.

Search replay displays only recorded changes. Route playback interpolates along
saved road geometry proportionally to distance over each leg's duration; this is
visual interpolation, not a measured velocity trajectory. Waiting and service use
reconstructed schedule phases. Browser-local selection/mode/position complements
the server's durable job archive. Missing online basemap tiles do not remove local
routes, timelines, metrics or job results.

## Interfaces and timing

The OpenAPI description is available locally at `/docs`. API prefix `/api/v1`:

- `GET /cities`, `POST /drafts`, `GET|PUT /drafts/{id}`. Updates require the expected
  revision hash. A stale revision returns 409; invalid data returns 422.
- `POST /jobs`: prepare an immutable draft revision, or solve a completed preparation
  from that same revision. Solver settings include seed, soft wall-clock budget,
  restart count and optional evaluation limit.
- `GET /jobs`, `GET /jobs/{id}`, `POST /jobs/{id}/cancel`,
  `GET /jobs/{id}/result`, `GET /jobs/{id}/editor` for a prepared editable view.
- `GET /jobs/{id}/events?after=SEQ`, `GET /jobs/{id}/snapshot?before=SEQ`,
  `WS /jobs/{id}/stream?after=SEQ`. WebSocket batches contain `type: events`,
  `snapshot`, or `status`; clients deduplicate by sequence. A lag over 500 events
  starts from the latest snapshot. HTTP still provides full indexed history.

Host and Origin checks restrict browser access to the same loopback origin.
Cross-origin requests and WebSocket upgrades are rejected; credentials and LAN
exposure are not enabled. Editing is limited to 100 appointments, an operational
limit rather than a frame-rate promise.

Preparation duration includes road acquisition for the scenario, witness generation
and final scenario validation; the shared OSRM graph build is separate. Solve time
includes instance-specific validation/preparation, candidate validation, synchronous
search-event recording and final incumbent/run validation. `recording_s` measures
the synchronous observer time within solving and is already included in `solve_s`;
`search_s` also includes search-time recording. Queue delay, previous road-matrix
preparation, result serialization/publication and terminal journal publication are
not solve time. CLI import/argument startup is separate; a long-lived worker has
zero per-run import startup. The soft budget may be exceeded by mandatory
validation or one evaluation in progress. Rendering never contributes to solve
metrics. Reproducible experiments must match trace modes before comparing methods.

## Verification

```sh
.venv/bin/python -m unittest discover -s tests -v
npm --prefix client test
npm --prefix client run build
.venv/bin/python scripts/accept_m2.py
```

Python integration tests explicitly inject synthetic provider responses; the
acceptance script uses the real local OSRM service, both installed cities and the
separate worker. It checks independent validation and terminal replay/result
agreement and writes `runs/m2-acceptance.json`. No external routing server is used.

Browser verification requires Chromium and the running service/worker/OSRM:

```sh
PLAYWRIGHT_BROWSERS_PATH=/tmp/vrp-ai-browsers client/node_modules/.bin/playwright install chromium
PLAYWRIGHT_BROWSERS_PATH=/tmp/vrp-ai-browsers npm --prefix client run test:e2e
```

Browser tests exercise actual road jobs, closing/reopening the tab, restoring replay
position, map edits, Modena with unavailable online tiles and reduced motion.
The performance check writes `runs/m2-client-performance.json`: 120 requestAnimationFrame
intervals during route playback at 1440×1100 in headless Chromium/software WebGL,
with p50/p95/max and browser version. This is separate from solver timing. The
prototype target is p95 ≤50 ms for this 20-stop profile; the report records whether
it is met without pretending a universal frame-rate guarantee. Initial loading,
online tile availability, hardware acceleration and other clients can differ.

### Observed acceptance on 2026-11-07

- 49 Python tests passed, including the 28 M1 regressions; 6 client replay/unit
  tests passed; production TypeScript/Vite build passed; 4 Chromium browser tests
  passed. `pip check` reported no broken requirements and `git diff --check` passed.
- Real-road acceptance used the OSM snapshot timestamp `2026-11-06T21:21:21+00:00`,
  source SHA-256 `48416b024f8a03d087552224f73d3a36aa74c940aacd676a2787f2cce6f77e7c`,
  pinned OSRM image and checked car-profile hash. Both 20-stop seed-42 demos
  produced independently validated feasible results with replay/result agreement.
- Host recorded by the solver: AMD Ryzen 5 PRO 5675U with Radeon Graphics,
  12 logical CPUs, WSL2 Linux, CPython 3.12.3; solver device CPU, one worker and
  one solver thread. This is the development host, **not Ai360**.
- With a 5-second soft budget, example measured solve times were about 5.54 s
  (Bologna) and 5.33 s (Modena), including mandatory final contract validation.
  These acceptance observations are not isolated comparative benchmarks.
- The 1440×1100 Chromium 158.0.8127.18 playback profile reported an ANGLE/Vulkan
  SwiftShader renderer, 120 samples, p50 about 50.1 ms, p95 about 99.9 ms and max
  about 283.4 ms. **The provisional 50 ms p95 target was not met.** A preceding
  measurement and the report after static-layer caching are both retained under
  `runs/`; no measured rendering-speed advantage is claimed for that change.
- Initial map loading still includes a roughly 2.05 MB uncompressed application
  bundle plus a separate roughly 519 KB map worker. Rendering and initial-load
  optimization remain prototype limitations, separate from solver correctness.
