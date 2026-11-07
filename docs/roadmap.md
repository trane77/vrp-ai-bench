# Roadmap

M0 documentation, the M1 CLI baseline and the M2 local city laboratory are implemented. M3 comparison tooling is implemented; M4 training and the full held-out campaign, audit, import and browser acceptance are verified. M5 LAN deployment remains planned. Full pilot acceptance is recorded in the M3 operations document.

## M0 — Requirements and repository

- [x] Capture confirmed scope, defaults and open decisions.
- [x] Define architecture, interface direction and evaluation protocol.
- [x] Establish development guidance and ignore generated artifacts.
- [x] Inspect the development runtime: CPython 3.12.3 used for M1 checks.
- [x] Select Bologna and Modena for M2 demonstration datasets.

Acceptance: documents are consistent, linked, and make no unsupported implementation or performance claims.

## M1 — Domain model and independently validated baseline

- [x] Versioned scenarios/results, time/load accounting and deterministic fuel model.
- [x] CPU classical solver, one depot, 1–5 vans, both service modes, windows and duration limit.
- [x] Small synthetic fixtures with known outcomes and explicit provenance.

Acceptance: independent validation catches duplicate/missing stops, capacity violations, late starts and late depot returns; hand-calculated schedules agree; impossible and timeout cases are distinguished. No UI required to run and save a fixture.

Verification: `python3 -m unittest discover -s tests -v` covers domain invariants,
CPU search and end-to-end CLI publication/revalidation. Impossible-fixture search
failure is reported as unknown feasibility, distinct from budget exhaustion;
M1 provides no infeasibility proof. See README.md for executable commands.

## M2 — First end-to-end city experience

- [x] Prepare Bologna/Modena datasets and routing paths with provenance; installed-city selector.
- [x] Service/worker separation, job lifecycle and durable event recording.
- [x] Styled client, route map, timelines, metrics, search replay and route playback.

Acceptance: launch a real baseline run, inspect its verified solution, close/reopen the client and replay the saved result. Replay terminal state matches saved solution metrics. A second installed city can be selected without mixing datasets. Performance is measured on the actual client.

Verification: both 20-stop seeded city demos ran through real local OSRM and
produced independently validated feasible solutions; terminal snapshots matched
saved results. Browser checks cover closing/reopening during a job, replay position,
map editing and Modena with online tiles blocked and reduced motion. Client frame
intervals are measured separately from solving. The measured SwiftShader playback
p95 is about 100 ms; the provisional 50 ms target remains unmet. Commands, provenance and remaining
limits are documented in [M2 local operations](m2-local.md).

## M3 — Comparative experiments

- [x] Ordered/shuffled custom policies and optional OR-Tools elapsed-time adapter.
- [x] Frozen 84-case main design plus 18 recording controls, isolated queue execution and descriptive exports.
- [x] Multiple time/fuel preferences, found nondominated alternatives, fixed hypervolume references and compatible client replay.

Verification and full-pilot evidence: [M3 operations](m3-comparisons.md). No method superiority is required or inferred from this constructed two-instance pilot.

Acceptance: a reproducible command/configuration produces comparable results and manifests; computation and rendering timings are separated; model estimates and reference solution status are explicit.

## M4 — Learned search

- [x] Train a supervised move-ordering policy over shared classical operators.
- [x] Freeze disjoint scenario splits, export a checked CPU model and record training cost.
- [x] Complete all 336 held-out cases, independent audit, verified repeatable import and browser acceptance.

Current evidence: [M4 operations](m4-learning.md).

Acceptance: reproducible evaluation supports a scoped positive, neutral or negative result; AI superiority is not a release requirement.

## M5 — Ai360 engine deployment

- Inspect host OS, RAM, access, dependencies and supported runtimes.
- Deploy authenticated LAN service; test reconnection and persistence.
- Benchmark CPU first; evaluate GPU/NPU only where compatible and useful.

Acceptance: the main computer controls and replays an Ai360 run, client disconnection does not stop computation, hardware and timing provenance are recorded. This deployment can be moved earlier if required; acceleration remains optional.

## Later

Stochastic service times, traffic simulation, calibrated fuel validation, alternative road paths, additional operational constraints and desktop packaging.
