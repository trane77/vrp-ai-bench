# Implementation plan

Status: steps 1–3 (M1) and steps 4–6 (M2) are implemented. The user authorized
M2 with Bologna/Modena, local OSRM, editable demo appointments and an online
basemap. Step 7 is implemented under the confirmed M3 pilot scope; step 8 is separately authorized by the M4 plan and implemented with the complete held-out campaign and
acceptance checks verified; step 9 (M5) authenticated LAN deployment on Ai360 and main-computer acceptance are implemented and verified. See [M3 operations](m3-comparisons.md), [M4 operations](m4-learning.md) and [M5 LAN operations](m5-lan.md) for actual verification and limitations.

## Starting point and decisions

The local environment inspected on 2026-11-07 exposes Python 3.12.3, Node.js 24.15.0 and npm 11.12.1. These are observations, not a verified dependency matrix or pinned project requirements. Select and lock compatible dependencies when creating each executable component.

Use the provisional semantics in requirements.md explicitly: collection-only, one depot, homogeneous vans, inclusive service-start windows and duration measured through depot return. Keep capacities, package properties and fuel coefficients configurable. No real fleet calibration is implied.

M2 uses Bologna and Modena, configurable demo depots, 5 km service areas and local OSRM over a frozen Geofabrik Nord-Est snapshot. Choose a project license before publication. Ai360 batch compatibility was verified for M4 and authenticated LAN service deployment was verified for M5.

## 1. Establish the executable foundation (M1)

Create Python project metadata, a dependency lockfile, a minimal command-line entry point and the initial engine/, schemas/ and tests/ directories. Add only dependencies required by this slice. Keep domain code independent of transport, storage and rendering.

Define versioned scenario, solution and run contracts with stable stop/vehicle identifiers and explicit seconds, meters, kilograms, volume units and liters. Specify input validation, unreachable-leg representation, integer-time rounding, schema rejection and serialization round trips. Separate job status, solution feasibility, termination reason and proof/reference status.

Deliverable: a command reads a labeled synthetic scenario and rejects malformed or incompatible inputs with actionable errors. Document the exact setup and command in README.md.

## 2. Implement accounting and independent validation (M1)

Implement schedule reconstruction from tours and scenario data, without trusting solver-reported totals. Account for early waiting, both service modes, load after collection, capacity on outgoing legs and the final loaded depot return. Define unused-vehicle accounting explicitly.

Implement the deterministic fuel estimate with versioned coefficients, units, driving-category inputs and an explicit idle policy. Mark uncalibrated estimates and keep a distance-equivalent control model clearly identified. Reject incompatible or incomplete path data instead of substituting straight-line travel.

Deliverable: independent validation reports violations and recomputed time, distance, load and estimated fuel breakdowns. Hand-calculated fixtures cover inclusive window endpoints, waiting, service beyond window closing, return duration, changing loads, unreachable and asymmetric legs, duplicate/missing visits, capacity limits and impossible cases. Include invalid identifier, negative-value and non-finite-value cases.

## 3. Complete a CPU baseline from the command line (M1)

Implement deterministic construction followed by bounded local improvement using explicit seeds and a monotonic wall-clock budget. Start with a small documented move set and fully revalidate cross-vehicle changes. Keep objective policy explicit; any scalar preference records normalization and weights. Retain raw time and fuel metrics.

Validate every solution reported as feasible through the independent validator. Save scenario identity, configuration, solver version, seed, actual runtime/hardware, budget, measured solve time and termination reason with the result. Budget exhaustion without a solution means feasibility is unknown; only an independent proof permits an infeasibility claim.

Deliverable: one documented command runs and saves a small fixture without a server or UI. Test feasible and impossible fixtures, budget exhaustion and deterministic seeded behavior where supported. Tiny exhaustive references can certify selected fixtures; heuristic outputs cannot certify optimality. Update milestone status only after these acceptance checks pass.

## 4. Prepare real road inputs (M2)

Select the initial city/service area/depot and compare routing providers for licensing, offline reproducibility, path geometry and preparation cost. Record the decision before integrating the provider. Implement a separate preparation operation that writes installed dataset manifests, provenance, license/attribution, network versions, snapped stops and directed travel/path data outside Git.

Use common fixed road paths for all solvers and disclose the restriction this places on time/fuel alternatives. Reject unreachable required legs explicitly. Keep small synthetic fixtures visibly separate from real city inputs.

Deliverable: reproducible preparation instructions and a real-city scenario accepted by the same validator and CLI baseline. Verify dataset identity and rejection of mixed-city inputs. Prepare a second installed city for the roadmap's city-selection acceptance check.

## 5. Add durable jobs and recorded events (M2)

Introduce SQLite metadata, append-only event storage and atomic final-result publication. Define event versions, sequence numbers, solve timestamps, initial state, accepted changes, feasible-best updates, snapshots and terminal state. Bound optional tracing and record dropped optional observations.

Run computation in a worker process outside FastAPI. Implement submission, status, result retrieval, cancellation and event subscription. Bind to loopback by default. Reconnection resumes from a sequence cursor or snapshot; engine restart marks interrupted jobs and retains recoverable data without promising automatic continuation.

Deliverable: integration checks show client disconnection leaves a job running, cancellation is explicit, reconnecting does not duplicate events, saved results survive restart and terminal replay reconstructs the saved validated solution and metrics.

## 6. Build the local client and replay (M2)

Create the locked TypeScript/React client after service contracts stabilize. Implement Italian externalized text, installed-city selection, scenario controls, MapLibre/deck.gl routes, vehicle details, timelines and raw metrics. Preserve attribution and label fuel estimates.

Separate recorded search replay from finished-route playback. Implement pause, speed, stepping, seek and feasible-best navigation using persisted events and snapshots. Label interpolation and distinguish compute elapsed time from simulated route time. Keep invalid candidates separate from the best feasible result.

Deliverable: launch a real baseline, inspect its validated routes, close/reopen the client and replay the same result. Check invalid input, no solution found, failure, cancellation, connection loss, reduced motion and empty/loading states. Measure rendering on the actual client before setting performance targets.

## 7. Establish controlled comparisons (M3)

Add an OR-Tools adapter with supported constraints/objectives documented and external results independently validated. Compare it and custom classical variants only under explicitly compatible objective settings. Add time/fuel alternatives and nondominated-set reporting.

Implement frozen manifests, paired seeds, matched budgets and recording modes, actual hardware/thread provenance and reproducible reports following experimental-protocol.md. Separate reference solutions, bounds and certified optima. Measure recording overhead and keep rendering outside solve time.

Deliverable: a documented command produces comparable raw results and reports feasibility, censored time-to-target outcomes, quality and uncertainty without an AI claim.

## 8. Evaluate learned search (M4)

Only after classical comparison works, add a replaceable learned policy over the same operators, evaluator and initialization. Freeze training, validation and held-out manifests, then record training and tuning cost separately from solving. Include inference, synchronization and required transfers in measured solve time.

Deliverable: a reproducible held-out comparison supports a scoped positive, neutral or negative conclusion. CPU execution remains available; no advantage is presumed.

## 9. Inspect and deploy to Ai360 (M5)

Inspect OS, RAM, remote access and runtime compatibility before selecting a backend. Do not change host drivers or system configuration as ordinary local development. Prepare authenticated LAN access and protected token transport before enabling remote service.

Deliverable: the main computer controls and replays a persistent Ai360 worker run, including reconnection. Benchmark CPU first; consider optional acceleration only after measured end-to-end compatibility and benefit. Moving this milestone earlier requires an explicit scope decision.

## Execution discipline

Implement one slice at a time. M1 and M2 are implemented; M3 follows the confirmed two-instance classical pilot; learned search follows the separately confirmed M4 scope; M5 authenticated LAN deployment is implemented and verified on Ai360. At each boundary,
record commands actually run, acceptance results and remaining limitations; update
README.md and roadmap.md consistently. Keep downloads, runs, datasets and
checkpoints out of Git. Do not add later-stage dependencies or fabricate
operational data to satisfy a demonstration.

### M1 implementation notes

- CPython 3.12 source-tree execution; no third-party dependencies or packaging
  installation required. Empty dependency lock recorded in requirements.lock.
- Strict v1 JSON schemas and semantic checks; M1 accepts synthetic inputs only.
- Independent reconstruction includes loaded depot return, uncalibrated
  category/load fuel estimates and optional idle consumption.
- Seeded insertion/local search with explicit scalar weights, soft wall-clock
  budget and optional deterministic evaluation limit. No proof of infeasibility
  or optimality, no events/replay and no real-road integration.
- Test command: `python3 -m unittest discover -s tests -v`. Acceptance includes
  atomic CLI output and independent revalidation of saved results. Full interface
  semantics and timing exclusions are documented in README.md and schemas/README.md.
- Manual CLI acceptance on CPython 3.12.3: validate the two-stop scenario, solve
  with seed 42 and a 2-second budget into a temporary result, then independently
  validate that saved run. All three commands returned 0. Recomputed fixture
  totals: 90 seconds fleet elapsed, 6,000 meters and 0.74 liters estimated fuel;
  one used van out of two. These synthetic totals are not a performance benchmark.

### M2 implementation notes

- Real-road preparation, artifact/manifest hashes and OSRM image/profile identity;
  no host configuration changes, external routing fallback or Git-stored data.
- Backward-readable v1 plus road scenario/run v2; independently validated CPU
  solutions. Appointments remain synthetic even on real roads.
- Durable SQLite queue, separate worker, cooperative cancellation, interrupted-job
  recovery, fsynced event journal, snapshots and WebSocket reconnection.
- Local Italian React client with map editing, vehicle details, timelines,
  recorded search replay, interpolated route playback and persistent history.
- Real Bologna/Modena acceptance and browser verification commands are recorded in
  [M2 local operations](m2-local.md). Result manifests, traces, browser screenshots
  and timing measurements remain outside Git. No M3/AI/Ai360 claims are made.

M2 verification: 49 Python tests, 6 client unit tests, production build and 4
Chromium checks passed, including the real-city acceptance flow. The software-WebGL
playback p95 was about 100 ms; the provisional 50 ms target remains unmet and is
recorded as a prototype limitation, not hidden by the functional test result.

### M3–M7 implementation notes

- **M3**: Ordered/shuffled classical variants, optional OR-Tools elapsed-time adapter,
  frozen 102-case pilot (84 main + 18 recording controls), anytime curves, found
  nondominated fronts and synchronized comparison maps. Verified in
  [M3 operations](m3-comparisons.md).
- **M4**: Two-head 30→32→16→2 supervised move-ordering policy over shared classical
  operators, 40 road-backed scenarios (24/8/8 splits), portable NumPy FP32 CPU
  inference, detached Ai360 Podman batch execution, and complete 336-case held-out
  campaign with independent audit and repeatable import. Verified in
  [M4 operations](m4-learning.md).
- **M5**: Authenticated HTTPS/WSS LAN service on Ai360 (`vrp-m5-osrm`,
  `vrp-m5-service`, `vrp-m5-worker`), mandatory TLS and external token file on LAN
  binds, origin/host/IP filtering, access-log suppression when token auth is active,
  `sessionStorage` client connection bar, interactive `cpu`/`learned` policy
  selection, and audited main-computer acceptance with mid-search WebSocket
  disconnect/reconnect cursor verification. Verified in [M5 LAN operations](m5-lan.md).
- **M6**: Feature Schema v2 (38 features, `38→64→32→2` MLP) with $O(1)$ route-delta
  caching for 20/50/100-stop neighborhoods, backward-compatible with M4 v1
  checkpoints; configurable scale-ladder campaigns (`schema_version=3`); independent
  seeded Monte Carlo stochastic robustness evaluator (`engine/stochastic.py`);
  interactive R&D laboratory panel (`client/src/RnDView.tsx`) wired to the sidebar
  `R&D` button; and Vite Rollup manual chunk splitting for `maplibre-gl` and
  `@deck.gl`.
- **M7**: OSRM multi-path extraction (`?alternatives=3`) with deduplicated candidate
  road paths per leg (`leg['alternatives']`) under `osrm_alternatives_evaluated`;
  independent validator and solver support for explicit (`route['path_ids']`) and
  objective-aware (`path_optimization=True`) leg path selection respecting
  destination time windows; backward-compatible operational constraints
  (`vehicle_capacities`, `driver_break`, and stop `delivery_mass_kg` /
  `delivery_volume_m3`); and R&D single-path vs multi-path comparison endpoint
  (`POST /api/v1/rnd/path-comparison`) and UI controls.
