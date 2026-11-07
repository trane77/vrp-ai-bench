# M3 classical comparison pilot

The pilot compares classical search on **two constructed 20-stop instances**, one
Bologna M2 witness demo and one Modena M2 witness demo. Road paths, license and
provenance come from their frozen M2 scenarios. These observations do not support
city-level generalization, an AI advantage, certified optima or proven
infeasibility. No new city pipeline, learned policy or Ai360 deployment is included.

## Install and submit

Use the existing Python 3.12 environment. OR-Tools is optional for M1/M2 but required
for a complete M3 pilot. Its resolved dependencies are separate from the M2 lock:

```sh
.venv/bin/python -m pip install -r requirements-experiment.lock
.venv/bin/python -m engine experiment freeze PATH_TO_BOLOGNA_PREPARATION.json PATH_TO_MODENA_PREPARATION.json --output runs/pilot-manifest.json
.venv/bin/python -m engine experiment run runs/pilot-manifest.json
# The preceding command prints a queued job ID and needs no HTTP service.
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 .venv/bin/python -m engine worker
.venv/bin/python -m engine experiment status JOB_ID
.venv/bin/python -m engine experiment report JOB_ID
.venv/bin/python scripts/accept_m3.py runs/JOB_ID
```

Freeze takes **preparation results**, not solve results, from M2. It requires one
20-stop scenario per city and independently validates each recorded witness before
freezing. It embeds scenario copies, canonical scenario hashes, normalized objective
scales, witness references, the case schedule, software/hardware provenance and a
manifest digest. It rejects tampered schedules or references at submission and
execution. Keep the manifest and all case artifacts outside Git.

The fixed design has 84 main cases: two instances × two budgets (5/15 seconds) ×
three repetitions × seven cells (two custom methods × three preferences, plus one
OR-Tools time cell). Eighteen additional controls disable durable incumbent
recording for the 5-second time-only cells. Scheduling uses seed 42. Custom seeds
are 0, 1 and 2; OR-Tools has temporal repetitions and `seed: null`, with
`random_seed_control: false`. Its ignored randomness is never presented as a
controlled seed. Time-only custom cells are reused in both analyses. A combined
three-preference front costs **3 × the per-preference budget**.

## Solvers and objective compatibility

`engine.adapters.SolverRequest` carries scenario, method, budget, seed, incumbent
observer, candidate observer and cooperative cancellation. M2's default ordered
first-improvement behavior remains available. M3's `ordered` and `shuffled` methods
share construction, restart counts, relocation/swap/reversal operators and the
independent evaluator. Construction and neighborhood selection use separate random
generators so selection does not consume construction randomness.

The time family compares the two custom methods and OR-Tools using fleet elapsed
time: route driving, waiting, service and return to depot, summed over vehicles.
The time/fuel family contains only custom search with normalized weights 1/0,
0.5/0.5 and 0/1. Raw minutes and estimated liters remain available. Normalization
scales are scenario inputs. Comparisons require the same instance, budget,
objective preference and recording mode.

OR-Tools 9.15.6755 uses fixed departures, inclusive service-start windows, mandatory
visits, mass and volume pickup dimensions, individual duration caps and forbidden
unreachable arcs. Its only cost is the sum of vehicle spans of the time dimension;
there are no travel-arc or fixed vehicle costs. Empty tours have zero duration.
It uses `PATH_CHEAPEST_ARC` and `GUIDED_LOCAL_SEARCH`. Every callback solution is
reconstructed and independently validated, even when it does not improve the
recorded incumbent. Native status and full effective search parameters are saved;
heuristic termination never certifies optimality or infeasibility.

Decimal mass/volume values are converted by exact integer ratios with a shared
power-of-ten scale per dimension. Scales beyond 18 decimal places or values and
aggregate magnitudes reaching 2^60 are explicitly rejected. Time horizon times
fleet size must also remain below 2^60. There is no silent rounding. Adapter errors,
process errors, cancellation, budget exhaustion and search without a feasible
solution remain distinguishable.

The model follows the official [time dimension guide](https://developers.google.com/optimization/routing/vrptw)
and [dimension/span cost reference](https://or-tools.github.io/docs/python/classortools_1_1constraint__solver_1_1pywrapcp_1_1RoutingDimension.html).
The guide's arc-cost example is not copied: adding travel costs would change this
pilot's elapsed-time objective.

## Execution, recording and timing

The existing worker lock serializes experiments with M2 prepare/solve jobs. Each
case runs in a new spawned child process; no case starts before the preceding
result and process metadata have been published. Completed, failed and cancelled
cases remain available. Worker interruption marks the campaign interrupted and
retains published cases and event files. There is no automatic resumption.
Re-running a manifest submits a new campaign rather than skipping failed cases.

The campaign archives its engine source files and separate frozen scenario copies.
Each case reads only its own scenario and verifies its frozen hash. Campaign lists
return manifest summaries; the full manifest is fetched explicitly, keeping road
geometries out of repeated status responses.

Case directories contain `result.json`, a derived versioned `summary.json`,
`process.json`, `publication.json` and,
when enabled, fsynced `events.jsonl` containing only improving validated incumbents.
Partial construction and accepted non-best moves are not recorded in M3. The
observer also retains nondominated validated alternatives from evaluated complete
candidates. Timely and late alternatives are retained separately so a late
improvement cannot erase an alternative that was available within budget.

Measured `solve_s` starts before reading the frozen scenario copy, and includes
scenario validation, adapter preparation, search, candidate validation, observation
work and final mandatory validation. Import/process/IPC startup, shared road
preparation and final publication are separate. `process_setup_s` includes explicit
OR-Tools import before solving. `startup_import_ipc_s` is the parent-observed
residual around the child's recorded wall time; it is not an isolated import
microbenchmark. Peak resident memory is recorded in KiB on this Linux runtime.
Native thread environment, effective solver parameters, package versions, source
hashes, Git revision/dirty state and lockfile hashes are retained.

`observer_s` includes alternative retention and incumbent observation/persistence,
and is included in solve time. Controls disable durable incumbent event recording;
minimal in-memory quality/target accounting and final solution validation remain
necessary. Controls do not assert identical trajectories under wall-clock stopping.
Report their measured quality differences and observer times rather than treating
recording time as a universal causal slowdown estimate.

The nominal budget is soft during mandatory operations. Actual duration and
positive overrun are saved. Curves use validation completion time. A solution
validated after the deadline can remain in the final run, but contributes neither
to reported budget quality nor to the budget's combined front. Target failures
are censored at the nominal budget; unknown feasibility is not infeasibility.

## Reports and client

The independent witness is a feasible initial quality target, never a certified
optimum. The manifest fixes a hypervolume reference of twice its time and fuel,
with minima of one second and one liter. Hypervolume uses the frozen scenario
normalization scales. References never move after results are observed.

`report.json`, `report.csv` and `report.md` contain per-instance/per-repetition
feasibility, first feasible time, censored time to target, quality, raw metrics,
paired method differences, recording controls and descriptive variability. Custom pairs
share seeds; pairs involving OR-Tools use temporal repetition indices and explicitly
do not assert matched random seeds.
JSON includes recorded anytime curves and fronts with full alternative provenance.
Conditional score statistics retain failure/feasibility counts alongside them;
there are no city-level confidence intervals. A front is the set of nondominated
alternatives found, not a complete optimal Pareto front.

HTTP routes under `/api/v1/experiments` provide submission (`POST` with a frozen
`manifest`), listing, `/{id}` status, `/{id}/cancel`, `/{id}/manifest`, `/{id}/cases`,
`/{id}/cases/{case_id}` (full run, or `?view=summary` without road geometry),
`/{id}/cases/{case_id}/events`, `/{id}/report` and
`/{id}/export/{json|csv|md}`. The existing job events expose case completion
progress; immutable case results are available during a campaign. A report can be
regenerated from retained artifacts after interruption using the CLI. Local host
and origin protection remains in force.

The client's **Confronti classici** section loads/submits a frozen manifest, polls
progress, filters the case table, exports reports and plots recorded curves and
found time/fuel alternatives. Only compatible cases can be paired. Two maps show
the last independently validated incumbent at a shared computation time; no
interpolated search moves or invented OR-Tools explanations appear. Map code and shared scenario geometry load
on demand; case summaries and immutable replay data are cached. Rendering remains separate from
solver measurements. The documented M2 SwiftShader p95 around 100 ms remains an
open limitation; M3 does not claim to fix it.

## Verification

```sh
.venv/bin/python -m unittest discover -s tests -v
npm --prefix client test
npm --prefix client run build
# With the local HTTP service pointed at the campaign's runs root:
PLAYWRIGHT_BROWSERS_PATH=/tmp/vrp-ai-browsers VRP_M3_JOB=JOB_ID npm --prefix client run test:e2e -- comparisons.spec.ts
```

The tiny complete enumeration test checks hand-accounted waiting, service past
closing, depot return, unused vehicles, decimal capacities, asymmetric/unreachable
arcs and impossible cases. Its completed enumeration is a fixture reference; no
solver run acquires a certification from it. Further checks cover frozen design
hashes/references, deadline inclusion, target censoring, dominance, hypervolume,
child isolation, cancellation, interruption retention, HTTP routes and exports.
The real-road audit revalidates all exposed solutions, verifies source hashes and
checks sequential process intervals. Browser checks use saved real-road results.

### Local verification on 2026-11-07

- The complete Python regression suite passed 70 tests, including the M1/M2
  checks and M3 adapter, accounting, manifest, report, subprocess and API checks.
- Eight client unit checks and the TypeScript/Vite production build passed.
- Chromium exercised saved real-road campaign filters, incompatibility controls,
  curves, found fronts, synchronized maps, exports and reopening. Online basemap
  requests were deliberately blocked; the saved road geometries remained visible.
- `pip check` reported no broken requirements. The map module is loaded as a
  separate chunk; Vite still warns about its approximately 1.79 MB uncompressed
  size. No rendering-speed claim follows from chunking.
- Earlier cancelled preflight campaigns remain outside Git and are not treated
  as completed pilots.


### Completed pilot

Campaign `3d418c86-e3df-4379-acfa-aac442a8481a` completed all **102 cases**
(84 main plus 18 recording controls) with no adapter/process errors. Its manifest
hash is `99ffcb35d604c40cdf0aac63ce79b1f2b20b80da4ccc26b2baaabbc709f7dcd8`.
Artifacts are under `runs/m3/3d418c86-e3df-4379-acfa-aac442a8481a/`; the frozen
submission is `runs/m3-pilot-v1.json`. These generated files are ignored by Git.

The independent audit performed **866 solution reconstructions**, checked frozen
engine source hashes and verified nonoverlapping child-process intervals. There
are 24 found-front records, 78 paired differences and 18 recording controls.
Across these exact cases, 83 had a validated solution within budget and 19 target
observations were censored. OR-Tools found no feasible solution in its 18 cases
under the prescribed configuration; its feasibility remains unknown. This is a
pilot outcome on constructed instances, not a general ranking of algorithms.

The archived engine sources identify the code actually used for measurement.
After the campaign, interface-only fixes added explicit qualified run IDs for new
cases and CLI recognition of legacy v3 external envelopes without a `run_id`.
Compact derived views and request caching were also added for presentation.
Measured artifacts were left unchanged; derived views are published outside solve
time and the accepted pilot received them after completion. CLI validation was exercised on both a
native envelope without a solution and a feasible custom envelope. Neither fix
changes search, scenario accounting or the saved solve measurements.


The completed-campaign Chromium check passed with saved real-road geometries and
blocked online tiles. It verified filters, incompatible-option disabling, found
custom fronts, normalized anytime curves, synchronized custom maps, the empty
OR-Tools incumbent state, exports and reopening the selected campaign. This is a
functional check, not evidence that the M2 SwiftShader frame-time limit is fixed.

CLI report regeneration reproduced the original report SHA-256
`109a04032564bb3e15e03bf9414930779d04eb25cbdbab8ef04f38ee5efbc568`.
The final regression run passed 70 Python tests; the client passed eight unit tests
and its production build. The final Chromium comparison check passed using the
compact case cache. Verification logs are retained in `runs/m3-verification/`.
