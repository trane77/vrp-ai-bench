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
| Initial city/service area/depot | M2 | User choice or clearly labeled demonstration city |
| Road data and routing provider | M2 | Compare offline reproducibility, licensing and preparation cost |
| Van capacity and package characteristics | M1 | Configurable synthetic fixtures; no claims of real fleet calibration |
| Fuel coefficients and idle policy | M1 | Explicit uncalibrated model with versioned parameters |
| Objective preferences | M3 | Present time/fuel alternatives; no hidden weights |
| Compatible runtime versions | M1 | Inspect environment and pin dependencies |
| Ai360 OS, RAM and remote access | M5 | Inspect before deployment; do not assume GPU/NPU compatibility |
| Project license and remote repository | Before publication | Undecided; local repository only |

## Scope safeguards

Do not quietly turn the project into delivery dispatch, multi-city routing, a production logistics platform or an LLM route generator. Learned optimization policy, software-development assistance and graphical animation are separate components with distinct evidence requirements.
