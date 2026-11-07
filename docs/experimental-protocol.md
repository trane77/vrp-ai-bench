# Experimental protocol

## Questions

1. At equal wall-clock budgets, does learned search find more feasible solutions or better time/fuel alternatives?
2. Does it reach a specified quality target faster?
3. Does any gain generalize to unseen stops, time windows, package distributions and problem sizes?
4. How many solves are needed to justify training cost, if a measurable saving exists?

## Baselines and controls

- Simple feasible-construction heuristic plus local improvement.
- Classical and learned selection over the same move operators, evaluator and initialization policy.
- Independent OR-Tools reference with its supported objective configuration documented. Do not label unequal objectives as a controlled comparison.
- Small instances with exhaustive or exact reference solutions where practical; store proof status and limits.

Never claim optimality from a heuristic result. If only a reference solution is available, report relative difference to that reference rather than optimality gap. Report timeout without solution as unknown feasibility, unless infeasibility was proved independently.

## Dataset design

Initial size ladder: 20, 50 and 100 stops, adjusted after profiling. Fleet sizes 1–5; both service modes; loose/tight time windows; duration cap on/off; uniform/clustered demand; low/high capacity utilization.

Construct known-feasible fixtures from a valid schedule, plus deliberately impossible edge cases. Separate constructed benchmarks from naturally sampled instances so feasibility construction bias is visible. Never infer all random instances are feasible.

Freeze train/validation/test manifests and seeds before final evaluation. Do not tune on held-out tests. Reuse identical city data, scenarios and fuel models across methods. Assess other cities only in separate one-city experiments, with cross-city generalization explicitly labeled.

## Timing and fairness

- Use a monotonic wall clock and identical explicit solve budgets.
- Include instance-specific preprocessing, policy inference, device synchronization and transfers in end-to-end solve time. Also report search-only time and shared cache preparation separately.
- State warm/cold model loading policy and report startup cost separately.
- Exclude rendering. Use matched recording modes, measure recording overhead and disclose it.
- Record CPU/GPU/NPU actually used, software versions, worker/thread limits and memory peaks. Run matched comparisons on the same host without competing experiment jobs.
- Repeat stochastic methods with paired instance/seed schedules. Choose repetition counts after a pilot and freeze them before the final test.

## Reported outcomes

Feasibility rate; time to first feasible solution; time to a preregistered quality target; fleet elapsed time; completion time; distance; estimated fuel; per-van load and timing; used vans; anytime quality curves; variability and paired differences.

Time-to-target failures are censored at the budget and reported, never discarded. Report quality conditional on feasibility alongside overall feasibility rates. For multiple objectives, compare nondominated sets using fixed normalization/reference points established before evaluation; retain raw minutes and liters.

Training time, data generation, tuning cost, energy if actually measured, and model size are separate costs. Do not invent energy estimates from runtime alone. Claims of superiority must state scenario scope, budget and uncertainty; negative or mixed results remain valid findings.

## Implemented M3 pilot

The confirmed two-instance, 84-case classical pilot plus 18 recording controls is
documented in [M3 operations](m3-comparisons.md). Its fixed manifest is a constructed
benchmark design, not train/validation/test evidence for learned policies or a
representative sample of either city. The broader questions above remain research
questions; this pilot verifies the comparison infrastructure and descriptive outputs.
