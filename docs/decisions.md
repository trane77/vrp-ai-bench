# Decision register

## Accepted direction

- Research objective: assess AI effectiveness and efficiency, without presuming superiority.
- One city per run, 1–5 vans, appointments, optional tour cap, both service modes, time/fuel optimization and replay.
- Separate Python engine and TypeScript/React graphical client, prepared for future LAN deployment.
- CPU-compatible first implementation; optional acceleration after verification.

## Provisional defaults

Collection-only service, single depot, identical vans, mandatory unsplit stops, standard package mass/volume, fixed experiment departure time, service-start windows, per-van duration cap, Italian interface and fixed road-path policy. See requirements.md for precise semantics. These defaults remain distinguishable from direct user decisions.

## Open before affected implementation

| Decision | Needed by | Default or action |
| --- | --- | --- |
| Initial city/service area/depot | M2 | Bologna and Modena selected; configurable demo centers/depots, 5 km service radius |
| Road data and routing provider | M2 | Geofabrik Nord-Est snapshot and pinned local OSRM car-profile MLD |
| Van capacity and package characteristics | M1 | Configurable synthetic fixtures; no claims of real fleet calibration |
| Fuel coefficients and idle policy | M1 | Explicit uncalibrated model with versioned parameters |
| Objective preferences | M3 | Present time/fuel alternatives; no hidden weights |
| Compatible runtime versions | M1/M2 | CPython 3.12 and Node 24; resolved dependencies in lockfiles |
| Ai360 OS, RAM and remote access | M5 | Inspect before deployment; do not assume GPU/NPU compatibility |
| Project license and remote repository | Before publication | Undecided; local repository only |

## M1 implementation decisions

The user selected a complete M1 CLI baseline and configurable normalized scalar
weights, initially equal, for time/fuel selection. Scales remain explicit scenario
inputs. Pareto alternatives and controlled comparisons remain M3 work.

M1 runs from source on CPython 3.12 without third-party dependencies. Synthetic
fixtures configure capacities, package demands and an uncalibrated linear-load
fuel model with explicit idle policy; these are not real fleet calibration.
M1 contract details are in ../schemas/README.md.

## Scope safeguards

Do not quietly turn the project into delivery dispatch, multi-city routing, a production logistics platform or an LLM route generator. Learned optimization policy, software-development assistance and graphical animation are separate components with distinct evidence requirements.

## M2 implementation decisions

The user selected Bologna and Modena, locally prepared OSM road data, OSRM in a
container, seeded demo appointments with map editing, and an online basemap.
The browser application remains local; no Sites publication or LAN exposure is
part of M2. Docker became accessible outside the development sandbox without
changing host configuration.

Use the shared Nord-Est snapshot, separate city manifests and fixed minimum-weight
OSRM car-profile paths. Appointment demand is synthetic; travel duration and fuel
are model estimates. Default depots, capacities and fuel coefficients are demo
settings rather than verified operational facts. The car profile does not certify
van access or ZTL authorization.

A separately launched single worker owns computation. HTTP disconnection does not
cancel it; worker interruption is recorded without automatic continuation.
Scenario/run v2 adds road provenance while v1 remains readable. See
[M2 operations and limitations](m2-local.md) for API, persistence and replay details.
