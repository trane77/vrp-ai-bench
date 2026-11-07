# Version 1 interchange contracts

The three JSON Schema draft 2020-12 documents describe scenarios, route proposals
and saved runs. `engine.contracts` is the executable source of these definitions;
`python -m engine.contracts` regenerates the checked-in schemas. Tests verify that
these definitions agree. Runtime validation uses a small standard-library validator
for the schema keywords actually used here, followed by domain checks; it is not a
general JSON Schema implementation.

All object fields are required and unknown fields are rejected. Scenario and
solution versions are integer `1`. Seconds and package counts must be JSON integers
(no implicit rounding); distances, mass, volume and fuel use meters, kilograms,
cubic meters and liters. All numeric inputs must be finite and nonnegative, with
positive capacity and normalization scales. IDs are nonempty, stable strings.
Capacity comparisons sum the decimal representations of input quantities, so
0.1 plus 0.2 fits a capacity of 0.3 without an implicit feasibility tolerance.
Fuel estimates and normalized scores use floating-point arithmetic.
The reference instant requires a UTC offset and timezone must identify an installed
IANA timezone. Computation uses seconds relative to the reference, never formatted
display times.

M1 accepts **synthetic inputs only**. Provenance records their description and
synthetic dataset version. Real city manifests and route geometry belong to M2;
a synthetic matrix must not be labeled as real road routing.

The directed leg list must contain every distinct pair of depot/stop IDs exactly
once. No diagonal entry is allowed. A reachable leg contains a travel duration
and at least one distance/category segment; an unreachable leg has
`reachable: false`, `travel_s: 0`, and `segments: []`. Its zero is a storage sentinel,
not usable travel time. Missing pairs are invalid inputs. There is no Euclidean
fallback. All solvers share these fixed paths; path alternatives are not optimized.

A solution contains only scenario identity and vehicle stop sequences, without
explicit depot endpoints. Absent vehicles are unused; explicit empty routes are
also allowed. A vehicle can appear at most once and every mandatory stop must
appear exactly once. The independent validator rebuilds schedules, leg loads and
metrics. Reports for invalid/unreachable tours may contain partial accounting;
only reports with `feasible: true` describe usable complete routes.

The fuel model `linear-load-v1` computes each segment as:

    distance_m / 1000 * (base_l_per_km + load_l_per_km_kg * outgoing_load_kg)

Waiting and service add `(waiting_s + service_s) * idle_l_per_s` only under
`waiting_and_service`; `off` requires a zero idle coefficient. Pickup mass applies
to outgoing segments, including the loaded return to the depot. Coefficients and
categories are explicit; all values remain uncalibrated experimental estimates.
The `load_dependent` kind requires at least one positive load coefficient. A
constant liters/km model must instead use `distance_equivalent_control`, the
`distance-equivalent control estimate` label, identical distance coefficients
across categories, zero load coefficients and idle policy `off`.

The scalar objective is:

    time_weight * fleet_elapsed_s / time_scale_s
      + fuel_weight * estimated_fuel_l / fuel_scale_l

At least one weight must be positive. Fixture weights default to 0.5 each;
normalization scales are explicit scenario parameters, not data-dependent values
silently fitted by the solver. Raw metrics are always retained. Pareto alternatives
are deferred to M3.

Runs embed the full scenario, canonical SHA-256 (sorted compact JSON), solver
version, configuration, runtime/CPU identity, timings, solution and recomputed
validation. JSON object keys must be unique. `validate --solution` accepts either
a proposal or a run; saved-run validation also checks embedded scenario identity,
scenario hash and exact agreement with the reconstructed report.

`job_status: completed` describes CLI execution, independently of solution
feasibility (`feasible` or `unknown`) and termination (`budget_exhausted`,
`evaluation_limit`, `search_exhausted`). Search exhaustion means the configured
restart attempts and local neighborhoods finished; it does not exhaust all possible
tours. No M1 solver result is certified optimal or proven infeasible. Unknown
feasibility has null solution and validation. Full asynchronous job states and
event contracts are deferred to M2.

## M2 road-backed contracts

`scenario-v2.json` adds real OSM road identity, requested/snapped coordinates,
geometry, raw travel duration and annotations; the appointment provenance remains
explicitly synthetic. `run-v2.json` supports those scenarios, recorded-observer
time and cooperative cancellation. Route proposals remain `solution-v1.json`.
`event-v1.json` specifies sequenced recorded observations. Version 1 scenarios and
runs remain readable without rewriting them. Regenerate these additions with
`python -m engine.contracts_v2`.

The standalone independent validator accepts both scenario versions. Full road
artifact identity is checked by city preparation/verification and by the worker's
OSRM container check, rather than inferred from a geometry picture. See
[M2 contracts, endpoints and replay semantics](../docs/m2-local.md) for the draft
revision workflow, job lifecycle, snapshot recovery and timing boundaries.

M3 retains scenario v1/v2 and solution v1. Experimental envelopes are
[run v3](run-v3.json), [manifest v1](experiment-manifest-v1.json) and
[report v1](experiment-report-v1.json); the pilot manifest has additional semantic
checks for its exact design, scenario hashes and independently validated frozen
references. Run v3 includes the original scenario hash and the effective scenario
hash/objective separately. Failed cases remain envelopes with explicit errors,
rather than fabricated feasible runs. See [M3 operations](../docs/m3-comparisons.md).
Case event journals use event v1 with a campaign/case job identifier; their replay
contains only validated incumbent improvements. Controls have no event journal.

New experimental cases carry a qualified campaign/case `run_id`. Legacy v3
external envelopes are also recognized by their explicit `kind`; the frozen pilot
files retain their original metadata. Compact case views have a separate version-1
`experiment_case_summary` envelope and never replace the full run artifact.
