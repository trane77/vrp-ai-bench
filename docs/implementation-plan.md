# Implementation plan

Status: planning only. No executable components or solver results exist. This plan refines the roadmap without authorizing implementation of later milestones.

## Starting point and decisions

The local environment inspected on 2026-11-07 exposes Python 3.12.3, Node.js 24.15.0 and npm 11.12.1. These are observations, not a verified dependency matrix or pinned project requirements. Select and lock compatible dependencies when creating each executable component.

Use the provisional semantics in requirements.md explicitly: collection-only, one depot, homogeneous vans, inclusive service-start windows and duration measured through depot return. Keep capacities, package properties and fuel coefficients configurable. No real fleet calibration is implied.

The city, service area, depot and road provider remain open decisions needed before M2. They do not block synthetic domain tests in M1. Choose a project license before publication. Ai360 inspection and deployment remain deferred.

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

Implement one slice at a time, with the next concrete target being steps 1–2 and then the complete M1 CLI baseline. At each boundary, record the commands actually run, acceptance results and remaining limitations; update README.md and roadmap.md consistently. Keep downloads, runs, datasets and checkpoints out of Git. Do not add later-stage dependencies or fabricate operational data to satisfy a demonstration.
