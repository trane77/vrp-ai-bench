# VRP AI

A reproducible urban fleet-routing research laboratory: determine whether learned search improves solution quality and computation time compared with classical methods, while making the search observable through live visualization and replay.

Status: requirements and architecture only. No solver, trained model, executable client or measured AI advantage exists yet.

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
- [Project decisions](docs/decisions.md)
- [Development instructions](AGENTS.md)

Start with milestone M0, then implement one complete classical-solver execution with recording and replay before adding learned search. Hardware acceleration is optional and must be verified on the target machine.

## Planned structure

`engine/` will contain domain models, independent validation, solvers, experiments and the service. `client/` will contain the interactive application. `schemas/` will define versioned interchange formats. `tests/` will contain small reference scenarios and integration checks. These directories will be created when their implementations begin.

No runtime versions are pinned yet. Select compatible supported versions and commit dependency lockfiles at implementation time. No external service account or paid API is currently required or configured.
