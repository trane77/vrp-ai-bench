# VRP AI

A reproducible urban fleet-routing research laboratory: determine whether learned search improves solution quality and computation time compared with classical methods, while making the search observable through live visualization and replay.

Status: M1–M8 are implemented and verified. M4 training and the full 336-case held-out Ai360 campaign passed independent audit, verified import and browser acceptance. M5 authenticated LAN service is deployed on Ai360, verified over TLS and WSS, and accepted from the main computer. M6 adds the 38-feature learned policy v2 (`38→64→32→2`), scale-ladder campaign support (`20 / 50 / 100` stops, `1–5` vans, `constant` and `packages` service modes), an independent Monte Carlo stochastic robustness evaluator, an interactive R&D laboratory panel in the client, and map bundle code-splitting. M7 adds OSRM alternative road paths (`?alternatives=3`), objective- and time-window-aware multi-path selection, backward-compatible operational constraints (heterogeneous vehicle capacities, mandatory driver breaks with per-van timeline segments, mixed pickup/delivery loads), and an R&D single-path vs multi-path comparison endpoint and UI tab. M8 adds offline longitudinal traffic & emissions microsimulation (`engine/microsim.py`) with SUMO `<emission-export>` XML ingestion, closed-form least-squares calibration of `linear-load-v1` with recorded residuals and validity bounds, and strict separation of uncalibrated vs simulation-calibrated (`microsim_calibrated`) fuel metrics across schemas, API and client views. No measured AI advantage is claimed.

## Scope

- One selectable city per experiment, using a versioned road network.
- Fleet of 1–5 vans, appointment time windows and an optional route-duration limit.
- Constant stop duration or duration based on packages collected.
- Time and estimated fuel objectives, with explicit feasibility checks.
- Attractive interactive map, vehicle timelines, comparative charts and recorded replay.
- Python engine and TypeScript/React client; future engine deployment on Ai360 over the local network.

## Documents

- [Requirements and assumptions](docs/requirements.md)
- [Architecture and deployment](docs/architecture.md)
- [Experimental protocol](docs/experimental-protocol.md)
- [Interface and replay](docs/interface.md)
- [Milestones and acceptance criteria](docs/roadmap.md)
- [Implementation plan](docs/implementation-plan.md)
- [M2 local setup, contracts and verification](docs/m2-local.md)
- [M3 frozen classical comparisons](docs/m3-comparisons.md)
- [M4 supervised learning and Ai360 batch operations](docs/m4-learning.md)
- [M5 LAN service operations and acceptance](docs/m5-lan.md)
- [Project decisions](docs/decisions.md)
- [Development instructions](AGENTS.md)

M3 adds frozen CLI campaigns, isolated classical solver comparisons, descriptive reports and a client comparison view. Full pilot verification is recorded in the M3 operations document. Hardware acceleration is optional and must be verified on the target machine.

## Run the local city laboratory

See [M2 setup](docs/m2-local.md) for the one-time Docker/OSM preparation and
installation from dependency lockfiles. After preparing the datasets and building
the client, run OSRM, the worker and HTTP service separately:

```sh
.venv/bin/python -m engine city serve
.venv/bin/python -m engine worker
# In another terminal:
.venv/bin/python -m engine serve
```

Open `http://127.0.0.1:8000`. Bologna and Modena use a frozen OSM road snapshot;
appointments remain explicitly synthetic and editable. Search replay shows only
recorded changes; route playback interpolates saved geometry. No public hosting
or LAN access is enabled.

## Run the M1 baseline

Use CPython 3.12 from the repository root. No installation, external account,
download or third-party runtime/test dependency is required. `requirements.lock`
now locks the optional M2 service/test environment; M1 core execution still uses
only the standard library. `pyproject.toml` describes source-tree execution.
Timezone validation requires the system IANA timezone database.

```sh
python3 -m engine validate tests/fixtures/two-stops.json
python3 -m engine solve tests/fixtures/two-stops.json --seed 42 --budget-s 2 --output runs/example.json
python3 -m engine validate tests/fixtures/two-stops.json --solution runs/example.json
python3 -m unittest discover -s tests -v
```

The output file is published atomically; rerunning requires a new path or explicit
`--overwrite`. A solution proposal may also be validated directly, without a run.
See [v1 contracts and units](schemas/README.md) and the checked-in fixture for input
fields. Fixture provenance explicitly identifies hand-authored synthetic data.

The solver uses earliest-deadline insertion, seeded shuffled restarts and first
improvement over relocation, swap and within-route reversal. Every complete
candidate is independently validated. Time/fuel selection uses explicit normalized
weights (0.5 each in the fixture); both raw objectives are saved. This heuristic
can miss feasible solutions and does not certify infeasibility or optimality.

`--restarts` defaults to 8. `--max-evaluations` optionally bounds candidate
evaluations for repeatability; pair it with an ample wall-clock budget. Identical
seeds alone do not guarantee identical outcomes under wall-clock stopping.
Budget checks occur between evaluations; input validation, preparation and final
incumbent validation are mandatory and can overrun the soft budget. Measured solve
time includes JSON reading, scenario preparation, search and final independent
validation. Process/import/argument startup is reported separately. Output
serialization and file publication are excluded. `search_s` includes candidate
validation; final revalidation is included only in `solve_s`.

Exit codes: `0` success, `1` operational error, `2` invalid input, `3` infeasible
route proposal, `4` completed search without a feasible solution. An impossible
fixture is available as `tests/fixtures/impossible.json`; a heuristic failure on
it remains `unknown`, not a proof. The standalone M1 CLI does not record replay events; M2 queued runs do.

`engine/` contains contracts, accounting, solver, OSRM preparation/adapter, durable
queue, worker and HTTP service. `client/` contains the local React application;
`schemas/` defines scenario v1/v2, experimental run v3 and manifest/report formats; `tests/` and client tests cover domain,
queue, service, replay and browser acceptance. M3 includes the ordered/shuffled custom policies and an optional OR-Tools time adapter. M4 adds a learned move-ordering policy and SSH batch transport. M5 adds authenticated HTTPS/WSS LAN service deployment on Ai360 and main-computer client control. See [M4 operations](docs/m4-learning.md) and [M5 LAN operations](docs/m5-lan.md).
