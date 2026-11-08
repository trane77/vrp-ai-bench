# M4 supervised move ordering

M4 adds a two-head supervised MLP that orders the shared relocation, swap and
reversal neighborhood. Construction, restart randomness, acceptance criteria and
the independent validator are shared with ordered/shuffled search. The learned
policy never removes candidates. A positive, neutral or negative outcome is valid;
no advantage or city-level generalization follows from implementation alone.

## Runtime and commands

`environments/m4/Containerfile` freezes the AMD PyTorch image by digest. Its
Python is 3.12.3, PyTorch is 2.9.1+rocm7.1.1.git351ff442, ROCm/HIP is
7.1.52802-26aae437f6 and NumPy is 2.4.1. Package versions, native thread settings,
source hashes, source revision/dirty state, lockfile hashes and the container image
are recorded with the experiment. The base digest freezes transitive packages;
`requirements-learning.lock` identifies the two directly used learning packages.
The host's Python, drivers, kernel and system packages are not changed.

Ai360 uses native rootless Podman so `/dev/kfd` and `/dev/dri` belong to the host.
The Docker Desktop VM is not used to infer host GPU compatibility. The container
has no network and publishes no HTTP ports. Fedora 44 is outside AMD's validated
OS matrix for this combination; see the [AMD ROCm 7.1.1 matrix](https://rocm.docs.amd.com/projects/radeon-ryzen/en/docs-7.1.1/docs/compatibility/compatibilityryz/native_linux/native_linux_compatibility.html).
CPU forward/backward/update is tested first. Training records a GPU
forward/backward/update probe and its duration; a failed probe selects CPU and
records its reason. With the verified Podman ROCm configuration (`--device /dev/kfd --device /dev/dri --security-opt label=disable`, detailed in [M5 LAN operations](m5-lan.md)), the GPU probe succeeds on the host's AMD Radeon 890M (`gfx1150`) in ~0.75 s, enabling GPU-accelerated training. NPU execution remains outside M4. Experimental inference always
uses NumPy FP32 on CPU with one BLAS thread, including cold checkpoint reading,
feature extraction, sorting, route copies and mandatory validations in solve time.

```sh
# Local OSRM must already serve the M2 snapshot.
.venv/bin/python -m engine learn dataset --output runs/m4-dataset
.venv/bin/python -m engine remote doctor
.venv/bin/python -m engine remote submit runs/m4-dataset/dataset.json --output runs/m4-submission.json
.venv/bin/python -m engine remote status runs/m4-submission.json
.venv/bin/python -m engine remote cancel runs/m4-submission.json
.venv/bin/python -m engine remote fetch runs/m4-submission.json --root runs/m4-imports
```

The cancel command is optional. Submit uploads a SHA-256-verified source/data
bundle into the existing remote checkout's ignored `runs/m4-remote/` tree. It does
not replace tracked files or clone another repository. A detached Podman worker
survives SSH disconnection and obtains the queue's exclusive worker lock.
Status/cancellation use small metadata files and queue flags. Collection and
training are queue job kinds; browser clients cannot start training.

Individual phases are also available for inspection:

```sh
python -m engine learn collect DATASET.json --output runs/samples --root runs/m4-jobs
python -m engine worker --root runs/m4-jobs --once
python -m engine learn train runs/samples/collection.json --output runs/training --root runs/m4-jobs
python -m engine worker --root runs/m4-jobs --once
python -m engine learn evaluate DATASET.json --checkpoint runs/training/checkpoint.json --output runs/frozen --root runs/m4-jobs
python -m engine worker --root runs/m4-jobs --once
python -m engine learn audit runs/m4-jobs/CAMPAIGN_ID
```

Run training and evaluation only in the frozen application runtime. `--cpu` on
training/campaign skips GPU probing. Missing/incompatible checkpoints fail
explicitly. There is no classical-policy fallback.

## Frozen data and labels

The dataset contains 40 new 20-stop, two-van constructed instances, equally split
between Bologna and Modena. Training seeds are 1000–1011 per city (24 total),
validation 2000–2003 (eight), and held-out test 3000–3003 (eight). All use M2's
60-second service, capacity/fuel model and no individual duration cap. Witnesses
are independently validated feasible references, never certified optima. Road
provenance, licensing and resampling counts remain in each scenario. Dataset
preparation uses four processes and a ten-minute soft limit. Existing completed
scenario files can be verified and reused after a failed preparation attempt;
failed attempts must be disclosed separately.

A layout fingerprint sorts snapped stop coordinates, ignoring IDs, order, demands
and appointments. Duplicate layouts across any scenarios/splits are rejected.
All preferences of one scenario inherit its split. M3's seed-42 regression
instances are excluded. Dataset and scenario content hashes are checked before
collection and final freezing.

Ordered/shuffled collection uses seed 10, one restart, 1,000 evaluations and a
30-second soft trajectory limit for each of three preferences, on training and
validation only (192 trajectories). At most the first feasible incumbent and its
next two improvements are retained. Every retained state's entire neighborhood
is labeled by independent validation: feasible strict improvement and positive
normalized objective gain. Missing states and errors remain explicit. Four CPU
processes are permitted for collection; no collection/training overlaps final
case execution.

Feature v1 freezes 30 values: operator one-hot (three), objective weights (two),
current normalized score (one), source/destination route length, move position,
current duration, mass and volume (ten), first/second stop mass, volume, opening,
closing, current slack and presence (12), and raw driving/zero-load-fuel arc deltas
(two). Stop indices/lengths use stop count, loads use vehicle capacities, and
windows/durations/arc deltas use the scenario's objective scales. An absent stop
has six zeros. Unreachable candidate arcs receive an explicit finite scale-sized
feature sentinel, never a substitute road. Features do not evaluate the candidate
or inspect labels. Zero-load fuel is a feature, not a claimed route fuel estimate.

The network is 30→32→16→2 with ReLU hidden layers. Training uses weighted BCE
for improvement and Smooth L1 on softplus positive gain, Adam lr 0.001,
batch 1,024, 50 epochs and seeds 0, 1, 2. Validation selection minimizes mean
normalized rank of the first true improvement; groups without improvement score
one. Ties use earlier epoch, then lower seed. Selection requires observed
improvements in both training and validation. Stable ordering uses sigmoid
probability × predicted positive gain, with original move order breaking ties.
Every epoch checks portable CPU numerical parity; the final JSON includes schema,
feature order, architecture, metadata and a canonical SHA-256 model hash.

## Campaign, timing and artifacts

The supervisor caps collection at 25 minutes, training at ten, final evaluation at
65 and audit at ten, reserving ten minutes for preparation/transfer within the
120-minute plan. Dataset preparation publishes a timing receipt. Remote submit checks the combined
preparation/transfer limit before launching; the supervisor uses that receipt for
the global deadline and additionally caps remote work at 110 minutes. For the
first measured campaign, before receipt enforcement was added, filesystem and
supervisor timestamps independently bounded preparation through worker launch at
246.7 seconds; remote work still had the conservative 110-minute cap. Setup/image downloads
and development preflights are separate. Podman event start/died timestamps recover the preflight container durations:
CPU/update plus GPU availability 2.8554 s, GPU forward/backward/update 2.4291 s,
and CPU training/export fixture verification 3.1125 s. These total 8.3970 s;
including the measured training timer gives 61.0710 s. The complete training
queue phase adds 0.9035 s of startup/queue/publication overhead, giving 61.9746 s
including preflight. These components are retained separately in the cost ledger. The 1.0863 s GPU probe
inside training is already included and is not counted twice. Image downloads
and subsequent acceptance checks remain separate setup/verification costs.

Before held-out execution, checkpoint, witness references, hypervolume reference,
weights, budgets, seeds and case order are frozen. The design is eight instances ×
three methods × two budgets (5/15 s) × three preferences × two seeds (0/1):
288 main cases plus 48 five-second time-only recording controls. Schedule seed is
42; nominal solve budget totals 3,120 seconds. Time-only cells also participate in
multiobjective analysis. OR-Tools remains available in M3 but is excluded here.
No result-dependent reduction of the design is allowed.

Final cases run sequentially, each in a fresh child process. Deadlines stop new
cases and cooperatively cancel the active case; mandatory final validation can
exceed soft limits. Late validations are retained but excluded from budget quality
and found fronts. Target failures are censored; heuristic failure is unknown
feasibility, not proven infeasibility. Hypervolume references stay fixed. Fuel
values are uncalibrated model estimates with scenario parameters and units.

Dataset/samples/model are v1; experimental manifests/reports are v2; runs are v4.
M1–M3 readers and behavior remain supported. Each case references a shared
scenario file and checked checkpoint, rather than embedding road geometry.
Reports aggregate compact summaries; HTTP hydrates geometries only on demand.
Final publication and process/import startup are outside solve time and recorded
separately; inference/observation and required copies are inside solve time.

Fetch verifies tar members and SHA-256 inventories in staging, audits exposed
solutions, copies complete campaign artifacts and registers imported results
without rerunning jobs. Repeated imports reuse the same campaign ID; conflicting
manifests fail. Incomplete/corrupt transfers stay in staging. Failed pre-training
campaigns still retain their samples and diagnostics even without an experiment
manifest. A campaign is complete only after all 336 cases and independent audit.
The client supports learned checkpoint/device/cost fields alongside compatible
filters, recorded curves, found fronts and synchronized maps. The M2 SwiftShader
frame-time limitation remains open.

## Verification status

Implementation checks and campaign outcomes are recorded here only after they
actually complete. Generated datasets, samples, checkpoints, recordings, transfer
tickets and verification logs stay outside Git under `runs/`.

### Implementation and training checks

- The Python suite passed 83 tests with one local skip (PyTorch is supplied by the
  application container). The 11 initial M4 checks, including actual three-seed
  training and export parity, also passed inside that container.
- Eight client unit tests and the TypeScript/Vite build passed. Vite retains the
  existing large map-chunk warning; this is not a frame-time improvement claim.
- The dataset froze as
  `8dbf94791da9c51e99765bdacc490d79cf16f4ecf6d21ca841d57a89b3eab339`.
  The initial serial preparation attempt failed on an OSRM endpoint mismatch at
  seed 1005. The corrected recorded-resampling guard reused five verified complete
  scenarios and prepared the remaining scenarios in parallel; this successful
  attempt took about 183.4 seconds. Preparation through remote worker launch was
  about 246.7 seconds according to filesystem/supervisor timestamps.
- Collection completed 192 trajectories and retained 528 observed states (397
  training, 131 validation). No held-out trajectories entered collection.
- All three seeds completed 50 epochs. Validation selected seed 0, epoch 50;
  mean normalized first-improvement rank was 0.1112872430. The GPU probe passed
  forward/backward/Adam update in 1.0863 seconds. Recorded training duration was
  52.6740 seconds; maximum checked PyTorch/NumPy absolute error was
  `3.725290298461914e-09`.
- Frozen checkpoint:
  `7e57446e0613aec7baeac4d32383da8ae770d78a870d99810576b3daf1578fa6`.
  Final campaign ID: `f1a436df-020a-4e40-90b5-a6f05e93449a`.
  The detached worker was observed progressing across separate SSH connections.

These are implementation/training observations, not evidence of held-out method
superiority. Final acceptance is recorded below after all 336 cases, audit, verified import
and browser checks completed. The campaign's archived sources identify the exact measured
implementation. Subsequent local changes strengthened timing receipts, standalone
phase limits, schema exports, report fields and presentation; they do not replace
or rewrite measured case results.

### Completed campaign and acceptance on 2026-11-07

Campaign `f1a436df-020a-4e40-90b5-a6f05e93449a` completed **336/336** cases,
with no failed or cancelled cases and no unexecuted cases. Manifest hash:
`63c663db6581d4ce18f069a82244b5ac5d8609bf32fb22119a0c362f88494778`.
The independent remote audit and the subsequent local import audit each
reconstructed **3,571 solutions** and passed. All 336 cases used distinct child
PIDs, had exit code zero and had nonoverlapping process intervals. The source
checkout on Ai360 remained clean; the detached main container exited zero.

The supervisor's collection-through-audit duration was 1,723.7664 s (28.73 min),
plus about 246.6725 s from preparation start through remote launch, within the
120-minute design. Queue phase durations were collection 144.5524 s, training
53.5776 s and final case campaign 1,406.7436 s. Freezing inputs and final audit
also belong to supervisor time. Budgets were not reduced: searches may exhaust
the configured restarts/neighborhoods before their nominal 5/15-second limits.
Peak child resident memory was 2,693,872 KiB. Actual solve hardware was Ryzen AI 9
HX 370/Radeon 890M, Python 3.12.3 on the Fedora 44 kernel via the pinned Ubuntu
container, CPU execution, one worker and one native thread. Learned feature/
scoring/sorting timers summed to 45.6450 s across the 112 learned cases, already
included in solve time; checkpoint loading and remaining solve work are included
in solve time as well.

All three methods found feasible solutions within budget in all 96 main cells;
all 336 witness targets were reached within budget. This does not certify
optimality. Descriptive main-cell comparisons of learned versus each classical
method use matched instance, budget, preference, seed and recording mode; ties
use absolute normalized-score difference at most `1e-9`. Each instance has 12
such comparisons per classical method. Entries below are learned wins/ties/losses:

| Instance | Versus ordered | Versus shuffled |
| --- | ---: | ---: |
| bologna-3000 | 2 / 10 / 0 | 4 / 8 / 0 |
| bologna-3001 | 0 / 11 / 1 | 0 / 10 / 2 |
| bologna-3002 | 2 / 6 / 4 | 3 / 8 / 1 |
| bologna-3003 | 2 / 7 / 3 | 0 / 10 / 2 |
| modena-3000 | 0 / 8 / 4 | 2 / 10 / 0 |
| modena-3001 | 6 / 4 / 2 | 4 / 6 / 2 |
| modena-3002 | 2 / 6 / 4 | 2 / 4 / 6 |
| modena-3003 | 0 / 6 / 6 | 0 / 8 / 4 |
| Exact-case totals | 14 / 58 / 24 | 15 / 64 / 17 |

The outcome is mixed and mostly tied, with more losses than wins on these exact
cells. It does not demonstrate a consistent learned-policy advantage. These are
constructed-instance observations, not city-level estimates or confidence
intervals. Raw per-instance/per-seed results, preferences, curves, fronts,
recording controls and censored-target accounting remain in the exported reports.
Fuel remains an uncalibrated model estimate.

Local artifacts are under
`runs/m4-imports/f1a436df-020a-4e40-90b5-a6f05e93449a/`: `report.json`,
`report.csv`, `report.md`, `analysis.json`, `acceptance.json`, `audit.json`,
`training-cost.json`, shared scenarios, checkpoint, source/lockfile archive and
all case artifacts. Dataset, samples, training diagnostics, original transfer
archives and source queue metadata remain in ignored data/staging directories.
Two real SSH fetches verified their inventories and imported the same campaign
ID with exactly one local job and no recomputation. Extracting over prior staging
content, corrupt bundles and conflicting existing artifacts is rejected.

Verification completed:

- The local Python suite ran 87 tests: 86 passed and the PyTorch test was skipped
  locally. All 17 M4 tests, including that training test, passed in the pinned
  container. Additional audit checks validate archived source and lockfile hashes.
- Eight client unit tests and the TypeScript/Vite build passed. The existing map
  chunk-size warning and SwiftShader frame-time limitation remain open.
- Chromium passed both the retained M3 regression and the imported M4 acceptance:
  learned checkpoint/CPU device, training/preflight costs, compatible filters,
  recorded curves, found fronts, synchronized maps, exports and reopening. Online
  basemap requests were blocked; saved real-road geometries remained available.
  The first M4 browser attempt exposed a test wait that matched the total case
  count before summaries loaded; the wait and loading label were corrected and
  the complete check passed.
- A separate detached remote cancellation fixture reached `cancelled` with its
  artifact retained and no training or held-out evaluation executed.
- Report regeneration preserves the derived JSON hash; measured raw case
  artifacts remain unchanged. Verification logs and event-derived cost evidence
  are retained under `runs/m4-*` outside Git.

The archived search, move, feature/inference and independent validator sources
match the corresponding current core files. Later changes to orchestration,
contracts, import verification and derived report/UI fields are described above;
raw measurements retain the exact sources used. No additional held-out cases,
model selection or parameter changes followed inspection of these results.
