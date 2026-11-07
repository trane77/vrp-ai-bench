# Roadmap

Only M0 documentation is currently implemented. All later milestones are planned.

## M0 — Requirements and repository

- [x] Capture confirmed scope, defaults and open decisions.
- [x] Define architecture, interface direction and evaluation protocol.
- [x] Establish development guidance and ignore generated artifacts.
- [ ] Select initial city and inspect development runtimes before implementation.

Acceptance: documents are consistent, linked, and make no unsupported implementation or performance claims.

## M1 — Domain model and independently validated baseline

- Versioned scenarios/results, time/load accounting and deterministic fuel model.
- CPU classical solver, one depot, 1–5 vans, both service modes, windows and duration limit.
- Small synthetic fixtures with known outcomes and explicit provenance.

Acceptance: independent validation catches duplicate/missing stops, capacity violations, late starts and late depot returns; hand-calculated schedules agree; impossible and timeout cases are distinguished. No UI required to run and save a fixture.

## M2 — First end-to-end city experience

- Prepare first real city dataset and routing paths with provenance; build installed-city selector.
- Service/worker separation, job lifecycle and durable event recording.
- Styled client, route map, timelines, metrics, search replay and route playback.

Acceptance: launch a real baseline run, inspect its verified solution, close/reopen the client and replay the saved result. Replay terminal state matches saved solution metrics. A second installed city can be selected without mixing datasets. Performance is measured on the actual client.

## M3 — Comparative experiments

- Matched classical baselines, budget controls, paired scenarios and reporting.
- Multiple time/fuel preferences and nondominated solution reporting.

Acceptance: a reproducible command/configuration produces comparable results and manifests; computation and rendering timings are separated; model estimates and reference solution status are explicit.

## M4 — Learned search

- Train a policy to select improvements or destroy/repair choices.
- Compare against identical operators with classical selection.
- Held-out evaluation and training-cost accounting.

Acceptance: reproducible evaluation supports a scoped positive, neutral or negative result; AI superiority is not a release requirement.

## M5 — Ai360 engine deployment

- Inspect host OS, RAM, access, dependencies and supported runtimes.
- Deploy authenticated LAN service; test reconnection and persistence.
- Benchmark CPU first; evaluate GPU/NPU only where compatible and useful.

Acceptance: the main computer controls and replays an Ai360 run, client disconnection does not stop computation, hardware and timing provenance are recorded. This deployment can be moved earlier if required; acceleration remains optional.

## Later

Stochastic service times, traffic simulation, calibrated fuel validation, alternative road paths, additional operational constraints and desktop packaging.
