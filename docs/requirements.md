# Requirements

## Confirmed user requirements

| ID | Requirement |
| --- | --- |
| R01 | Evaluate whether AI improves routing effectiveness and computational efficiency against classical methods. |
| R02 | Observe search live and replay recorded searches at human-readable speeds. |
| R03 | Each appointment has a specific time window. |
| R04 | An optional maximum tour duration must be supported. |
| R05 | Support constant service time and service time dependent on packages loaded at the stop. |
| R06 | Support a fleet of 1–5 vans. |
| R07 | Optimize time and estimated fuel consumption. |
| R08 | Select one city per experiment. |
| R09 | Provide a polished, futuristic, readable client. |
| R10 | Separate engine and client so the engine can later run on Ai360 over the LAN. |

## Provisional modeling defaults

These are explicit implementation defaults, not additional confirmed requirements. They are configurable or revisable before their implementation.

- Collection-only service: vans start empty, collect packages and return to one common depot. Deliveries and mixed pickup/delivery are deferred.
- Homogeneous vans; fixed available fleet size, with unused vans permitted. Record both available and used counts.
- Each mandatory stop is served exactly once by one van; no split service and no intermediate unloading.
- Per-van mass and volume capacity. Initial packages may have standard configurable mass and volume.
- Windows constrain service start, including both endpoints. Early arrival waits; service may finish after window closing. A service-completion deadline would be a different explicit mode.
- Vehicles start at the configured experiment start time. The optional duration cap applies individually from departure to depot return, including waiting and service. All vehicles must return.
- Deterministic travel and service times initially; no live traffic. Stochastic durations are a later experiment class.
- Italian initial interface, with text externalized for future localization.

## Time and load semantics

Use seconds from a scenario reference instant for computation, meters for distance, kilograms for mass and liters for displayed fuel. Persist the city timezone for display. Do not calculate durations by subtracting formatted clock strings.

For stop i, with window [a_i, b_i]:

    arrival_i = departure_previous + travel_time(previous, i)
    service_start_i = max(arrival_i, a_i)
    departure_i = service_start_i + service_duration_i
    feasible_window_i = service_start_i <= b_i

Service modes:

    constant: service_duration_i = configured_constant
    packages: service_duration_i = fixed_stop_time + package_count_i * time_per_package

All durations and demands are nonnegative. After collection, update the load before evaluating the outgoing leg. Validate capacity on every leg. Include the final loaded return leg in travel, duration and fuel accounting.

An unreachable required leg must not receive an artificial short travel time. Road times can be asymmetric. Reject invalid inputs; report inability to find a feasible route separately from a proof of infeasibility.

## Objectives

Feasibility is mandatory for final accepted solutions. Search may explore infeasible candidates if violations are recorded and clearly shown.

Report separately:

- Fleet elapsed time: sum of used-van route durations, including travel, service and waiting.
- Completion time: latest van return relative to experiment start.
- Travel time, distance, waiting and service totals.
- Total estimated fuel, used vans and per-van breakdowns.

Primary experimental trade-off: fleet elapsed time versus estimated fuel, with completion time also reported. Compare identical objective settings across solvers. Present nondominated alternatives; any scalar preference must state normalization and weights. A dominated solution is no better in either primary objective and worse in at least one.

Fixed total service time may be invariant across routes, but service still affects time-window feasibility, waiting and completion time.

## Fuel estimation

Start with a documented deterministic model depending on distance, segment driving category and carried mass, with an explicit idle-engine policy. Store coefficients, calibration status, model version and units. Label uncalibrated values as experimental estimates. Constant liters/km is allowed only as a clearly labeled distance-equivalent control model.

The selected road-path policy must be recorded: choosing only the fastest road path between stops restricts the time/fuel alternatives available to the optimizer. Initially use common fixed road paths for every solver and disclose this limitation. Alternative road paths and traffic microsimulation are later work.

## City data and experiments

Each experiment references one city dataset version, road-network provenance and license, service area, depot, snapped stop positions and travel/path data. Save generation rules and seeds. Selection initially covers installed city datasets; arbitrary city acquisition is a separate preparation operation. Bologna and Modena are the selected M2 demonstration cities; their depots are configurable demo locations, not real fleet premises.

Store configuration, algorithm/model version, seeds, machine/runtime information, wall-clock limits, objective policy, solution metrics, validator outcome and replay references. Synthetic fixtures must be labeled and cannot be presented as real road routing.

## Nonfunctional requirements

- Search runs independently of rendering and client connection state.
- Pause/seek/speed controls apply to replay; they do not imply the engine supports pausing computation.
- Engine jobs expose queued/running/completed/cancelled/failed status, with separate solution feasibility and termination reason.
- Persist completed results and periodic progress; interrupted jobs retain recoverable data. Automatic continuation after engine restart is deferred.
- Define input limits and visual performance targets after a measured prototype; do not promise universal frame rates.
- No public deployment by default. Authenticate LAN access and keep credentials out of repository and logs.

## Deferred scope

Multiple depots, heterogeneous fleet, electric charging, mixed pickup/delivery, driver breaks, real customer data, live dispatch, production shipping integration, live traffic, stochastic robustness, desktop packaging and hardware-specific acceleration.
