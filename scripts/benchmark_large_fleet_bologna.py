"""Benchmark large fleet (50 vehicles) local search with policy v4 on Bologna."""
import time
from engine.contracts import load_json
from engine.drafts import make_draft
from engine.learning import Policy
from engine.solver import proposal, solve
from engine.validation import validate_solution

def run_benchmark():
    # Load Bologna city manifest
    manifest = load_json("data/cities/bologna/manifest.json")
    
    # 1. Draft generation for 50 vehicles on Bologna
    print("Generating 50-vehicle scenario draft on Bologna...")
    t0 = time.perf_counter()
    draft = make_draft(manifest, count=150, vehicles=50, seed=42)
    draft_ms = (time.perf_counter() - t0) * 1000.0
    print(f"Draft generated in {draft_ms:.1f}ms: {len(draft['stops'])} stops, {len(draft['vehicle_ids'])} vehicles")
    
    # Check witness solution feasibility
    witness_sol = proposal(draft, draft['generation']['witness_routes'])
    val = validate_solution(draft, witness_sol)
    print(f"Witness solution feasible: {val['feasible']}, total distance: {val['total_distance_m']}m")
    assert val['feasible'], "Witness solution must be feasible"
    
    # 2. Load policy v4 model
    print("Loading Policy v4 GPU checkpoint...")
    model = load_json("runs/m4-v4-gpu/checkpoint.json")
    policy = Policy(model)
    print(f"Policy loaded (v{model['schema_version']}, {len(model['feature_schema']['features'])} features, hash: {model['model_hash'][:16]}...)")
    
    # 3. Solve with policy v4 (budget 10s)
    print("Running local search solver with Policy v4 and spatial pruning (budget=10s)...")
    t0 = time.perf_counter()
    result = solve(draft, budget_s=10.0, seed=42, selection_policy=policy)
    solve_duration = time.perf_counter() - t0
    
    print(f"Solve completed in {solve_duration:.2f}s:")
    print(f"  Feasible: {result['feasibility']}")
    print(f"  Evaluations: {result['evaluations']}")
    print(f"  Termination: {result['termination_reason']}")
    print(f"  Best distance: {result['solution']['distance_m']:.1f}m")
    print(f"  Active routes: {sum(1 for r in result['solution']['routes'] if r['stop_ids'])} / {len(draft['vehicle_ids'])}")
    
    # 4. Compare with basic solver (without learned policy)
    print("Running solver with basic default ordering (budget=10s)...")
    t0 = time.perf_counter()
    basic_result = solve(draft, budget_s=10.0, seed=42, selection_policy=None)
    basic_duration = time.perf_counter() - t0
    print(f"Basic solve completed in {basic_duration:.2f}s:")
    print(f"  Feasible: {basic_result['feasibility']}")
    print(f"  Evaluations: {basic_result['evaluations']}")
    print(f"  Best distance: {basic_result['solution']['distance_m']:.1f}m")
    
    diff_distance = basic_result['solution']['distance_m'] - result['solution']['distance_m']
    print(f"\nPolicy v4 distance reduction: {diff_distance:.1f}m ({100.0 * diff_distance / basic_result['solution']['distance_m']:.2f}% improvement)")

if __name__ == '__main__':
    run_benchmark()
