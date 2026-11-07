# Development instructions

## Purpose and scope

Read README.md and the relevant documents in docs/ before changing the project. Preserve the user's confirmed scope. Treat provisional decisions as assumptions, not user-confirmed facts. Keep source code, identifiers and technical documentation in English; user-facing discussion may be in Italian.

## Scientific integrity

- Never claim an AI advantage without reproducible comparative evidence.
- Keep the solution validator independent from the search policy. Validate every reported feasible solution, including solutions produced by external solvers.
- Distinguish proven infeasibility, time-budget exhaustion and failure to find a feasible solution.
- Distinguish certified optima, bounds and best-known reference solutions.
- Label fuel values as model estimates and record model parameters and units.
- Keep training, validation and held-out evaluation instances separate. Record seeds, versions, budgets and actual hardware.
- Include learned-policy inference and required transfers in measured solve time. Report training separately.

## Implementation

- Keep domain logic independent of HTTP, storage and rendering.
- Implement CPU execution first; detect optional acceleration rather than assuming Radeon/NPU support.
- Run jobs outside the request-serving process. Client disconnection must not cancel a job.
- Use versioned scenario, result and event formats, explicit units and stable identifiers.
- Never animate unrecorded search steps as if they occurred. Distinguish replay interpolation from recorded events.
- Do not silently replace road distances with straight-line distances or real inputs with synthetic inputs.
- Avoid external downloads, generated datasets, recordings, checkpoints and credentials in Git. Record provenance and licensing for city data.
- Add dependencies only when needed; lock resolved versions.
- Use tests for domain invariants, meaningful edge cases and end-to-end acceptance; do not claim checks that were not run.
- Keep documentation and milestone status consistent with actual implementation. Do not implement future milestones solely because they appear in the roadmap.

## Validation priorities

Test time-window boundaries, early waiting, service duration, return-to-depot duration, changing pickup loads, unreachable roads, asymmetric travel times, duplicate/missing visits, impossible instances and event replay consistency.

## Remote deployment

Ai360 is an intended future compute host, not a verified deployment. Do not modify its drivers, packages or system configuration as part of ordinary local development. Inspect hardware/runtime compatibility before choosing GPU or NPU backends. Bind locally by default; require authenticated access when enabling LAN service.
