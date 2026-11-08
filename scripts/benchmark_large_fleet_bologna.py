"""Benchmark large fleet (up to 50 vehicles) on Bologna road network with policy v4."""
import time
from engine.city import CityRegistry
from engine.contracts import load_json
from engine.drafts import make_draft, prepare_draft
from engine.learning import Policy
from engine.routing import OSRM
from engine.solver import proposal, solve
from engine.validation import validate_solution

def run_benchmark():
    manifest = CityRegistry("data").get("bologna")
    osrm = OSRM()
    
    # 1. Prepare Bologna scenario with 50 vehicles, 50 stops
    print("Generating and preparing 50-vehicle scenario on Bologna road network...")
    t0 = time.perf_counter()
    draft = make_draft(manifest, count=50, vehicles=50, seed=42)
    scenario = prepare_draft(draft, manifest, osrm)
    prep_s = time.perf_counter() - t0
    print(f"Scenario prepared in {prep_s:.2f}s: {len(scenario['stops'])} stops, {len(scenario['vehicle_ids'])} vehicles, {len(scenario['legs'])} road legs")
    
    # Verify witness solution feasibility
    witness_sol = proposal(scenario, scenario['generation']['witness_routes'])
    val = validate_solution(scenario, witness_sol)
    print(f"Witness solution feasible: {val['feasible']}, total distance: {val['total_distance_m']}m")
    assert val['feasible'], "Witness solution must be feasible"
    
    # 2. Load policy v4 GPU-trained model
    print("\nLoading Policy v4 GPU checkpoint...")
    model = load_json("runs/m4-v4-gpu/checkpoint.json")
    policy = Policy(model)
    print(f"Policy loaded (v{model['schema_version']}, {len(model['feature_schema']['features'])} features, hash: {model['model_hash'][:16]}...)")
    
    # 3. Solve with Policy v4 and spatial pruning (budget: 10s)
    print("\nRunning local search solver with Policy v4 and spatial neighborhood pruning (budget=10s)...")
    t0 = time.perf_counter()
    result_v4 = solve(scenario, budget_s=10.0, seed=42, selection_policy=policy)
    dur_v4 = time.perf_counter() - t0
    
    print(f"Policy v4 solve completed in {dur_v4:.2f}s:")
    print(f"  Feasible: {result_v4['feasibility']}")
    print(f"  Evaluations: {result_v4['evaluations']}")
    print(f"  Termination: {result_v4['termination_reason']}")
    print(f"  Best distance: {result_v4['solution']['distance_m']:.1f}m")
    active_routes = sum(1 for r in result_v4['solution']['routes'] if r['stop_ids'])
    print(f"  Active routes: {active_routes} / {len(scenario['vehicle_ids'])}")
    
    # 4. Baseline solve (without learned policy, same budget)
    print("\nRunning baseline solver without learned policy (budget=10s)...")
    t0 = time.perf_counter()
    result_base = solve(scenario, budget_s=10.0, seed=42, selection_policy=None)
    dur_base = time.perf_counter() - t0
    
    print(f"Baseline solve completed in {dur_base:.2f}s:")
    print(f"  Feasible: {result_base['feasibility']}")
    print(f"  Evaluations: {result_base['evaluations']}")
    print(f"  Termination: {result_base['termination_reason']}")
    print(f"  Best distance: {result_base['solution']['distance_m']:.1f}m")
    
    dist_v4 = result_v4['solution']['distance_m']
    dist_base = result_base['solution']['distance_m']
    diff_m = dist_base - dist_v4
    print("\n=== Comparative Summary ===")
    print(f"Policy v4 Best Distance: {dist_v4:.1f} m")
    print(f"Baseline Best Distance:  {dist_base:.1f} m")
    print(f"Improvement:             {diff_m:.1f} m ({100.0 * diff_m / dist_base:.2f}%)")
    print(f"Evaluations (v4 / base): {result_v4['evaluations']} / {result_base['evaluations']}")

if __name__ == '__main__':
    run_benchmark()
