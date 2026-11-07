# Architecture

## Proposed technology choices

| Layer | Choice | Responsibility |
| --- | --- | --- |
| Domain and experiments | Python | Scenarios, objective accounting, independent validation and orchestration |
| Learned policy | PyTorch | Training and inference behind a replaceable interface |
| Classical reference | OR-Tools and custom local search | Baselines with explicit objective support |
| Service | FastAPI, HTTP and WebSocket | Job commands, results, progress and reconnection |
| Client | TypeScript, React, Vite | Experiment setup, observation and comparison |
| Map | MapLibre GL JS and deck.gl | City rendering and animated route layers |
| Storage | SQLite and versioned event files | Job metadata, results, checkpoints and replay |

This is an architectural selection, not a verified dependency matrix. Pin compatible versions when creating executable components. M2 uses local OSRM over a Geofabrik Nord-Est snapshot; map rendering does not supply travel-time matrices.

M1 implements the standard-library Python domain, independent validator and CPU
heuristic. M2 adds FastAPI, a separately launched worker, SQLite/event journals,
local OSRM preparation and the React/MapLibre/deck.gl client. M3 adds an optional OR-Tools adapter and isolated sequential experiment cases. PyTorch and
Ai360 deployment remain planned. See [M3 comparisons](m3-comparisons.md). See [M2 operations](m2-local.md) for current
contracts, job recovery, timing exclusions and limitations.

## Boundaries

    Browser client -> HTTP commands/results -> Engine service -> Worker process
    Browser client <- WebSocket progress <- Engine service <- Worker events
    Engine service / worker -> Metadata, snapshots and event storage

Workers own computation. The service stays responsive and owns job lifecycle/access. The domain validator has no UI or network dependency. Search implementations return the same solution representation and receive the same scenario, evaluation model and budget.

Initial solver interfaces: construct a solution, improve a solution, emit observations, return termination information. Shared neighborhoods allow controlled comparison between classical and learned move selection. Always validate the full resulting schedule after cross-vehicle moves.

## Data contracts to implement

- Scenario: schema version, city/data versions, reference time/timezone, depot, fleet, stops, demands, service mode, windows, duration cap, objective settings and fuel model.
- Solution: vehicle tours, schedules, leg loads, distance/time/fuel breakdown, violations and independent validation result.
- Run: scenario hash, solver/model identity, seeds, budgets, runtime/hardware and termination reason.
- Event: schema version, run ID, monotonic sequence, elapsed solve time, event kind and payload.

Use integer time units where solver adapters require them; document rounding consistently. Reject incompatible schemas explicitly. Maintain stable stop and vehicle IDs across representations.

## Event transport and persistence

Record initial state, accepted moves, feasible-best updates, objective alternatives, metric samples and terminal status. Detailed rejected-move tracing is optional and bounded. Periodic full snapshots support seeking without replaying the whole history.

The client subscribes after its last sequence number. Resend retained events or provide a snapshot plus subsequent events if the cursor is too old. Deduplicate sequence numbers. Batch updates and apply backpressure without losing the persisted best solutions. UI interpolation must not manufacture candidate-search history.

Start with SQLite metadata and compressed append-only event files; serialize writes and use atomic final-result publication. Cap trace volume and record trace mode and dropped optional observations.

## Local and future remote deployment

Initially the browser and engine run on the development computer, with the service bound to loopback. Later the same client connects to the engine on Ai360; configuration selects the endpoint.

User-reported target hardware: Ryzen AI 9 HX 370, Radeon 890M integrated GPU and NPU. Network connection is reported available, but OS, RAM, remote access mechanism, runtime, drivers and acceleration compatibility are not inspected. Do not infer that PyTorch can use the GPU or NPU.

CPU execution is required. Acceleration is optional and selected only after compatibility and end-to-end benchmarks. Keep native library thread counts and worker counts explicit to prevent oversubscription. Start with one benchmark job at a time.

LAN deployment requires authenticated access, restricted binding/firewall policy, and protection of tokens in transit appropriate to the network. Disconnecting the client never cancels a worker. Engine restart initially marks interrupted runs and preserves saved observations; training checkpoint resume is a separate capability.

## References

- https://developers.google.com/optimization/routing/vrptw
- https://maplibre.org/maplibre-gl-js/docs/
- https://deck.gl/docs
- https://eclipse.dev/sumo/docs/Tools/Emissions.html

SUMO is a possible later validation tool, not a first-release dependency. Tauri packaging is optional future work.
