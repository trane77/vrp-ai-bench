"""Version 1 contracts and strict structural/semantic input checks.

JSON schemas are exported from these definitions by `python -m engine.contracts`.
The small validator implements only the keywords used in these schemas.
"""
import copy
import json
import math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class InputError(ValueError):
    """An invalid or incompatible interchange document."""


def obj(**properties):
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


def array(items, **constraints):
    return {"type": "array", "items": items, **constraints}


TEXT = {"type": "string", "minLength": 1}
NUM = {"type": "number", "minimum": 0}
INT = {"type": "integer", "minimum": 0}
BOOL = {"type": "boolean"}
NULL = {"type": "null"}
VERSION = {"const": 1, "type": "integer"}
POS = {"type": "number", "exclusiveMinimum": 0}


def enum(*values):
    return {"enum": list(values)}


SEGMENT = obj(distance_m=NUM, driving_category=TEXT)
LEG = obj(source=TEXT, target=TEXT, reachable=BOOL, travel_s=INT,
          segments=array(SEGMENT))
STOP = obj(id=TEXT, window_open_s=INT, window_close_s=INT,
           packages=INT, mass_kg=NUM, volume_m3=NUM)
SCENARIO = obj(
    schema_version=VERSION, id=TEXT,
    provenance=obj(kind=enum("synthetic"), description=TEXT,
                   dataset_id=TEXT, dataset_version=TEXT,
                   path_policy=enum("fixed_directed_paths")),
    reference_instant=TEXT, timezone=TEXT, start_s=INT, depot_id=TEXT,
    vehicle_ids=array(TEXT, minItems=1, maxItems=5, uniqueItems=True),
    capacity=obj(mass_kg=POS, volume_m3=POS),
    duration_cap_s={"anyOf": [INT, NULL]},
    stops=array(STOP), legs=array(LEG),
    service=obj(mode=enum("constant", "packages"), constant_s=INT,
                fixed_s=INT, per_package_s=INT),
    fuel_model=obj(version=enum("linear-load-v1"),
                   kind=enum("load_dependent", "distance_equivalent_control"),
                   calibration=enum("uncalibrated"),
                   label=enum("experimental estimate", "distance-equivalent control estimate"),
                   categories={"type": "object", "minProperties": 1,
                               "additionalProperties": obj(base_l_per_km=NUM,
                                                            load_l_per_km_kg=NUM)},
                   idle_policy=enum("off", "waiting_and_service"),
                   idle_l_per_s=NUM),
    objective=obj(time_weight=NUM, fuel_weight=NUM,
                  time_scale_s=POS, fuel_scale_l=POS))
ROUTE = obj(vehicle_id=TEXT, stop_ids=array(TEXT))
# Solutions are route proposals. Reports are always recomputed, never trusted.
SOLUTION = obj(schema_version=VERSION, scenario_id=TEXT, routes=array(ROUTE))
METRICS = obj(fleet_elapsed_s=INT, completion_s=INT, travel_s=INT,
              waiting_s=INT, service_s=INT, distance_m=NUM,
              estimated_fuel_l=NUM, used_vans=INT, available_vans=INT)
VISIT = obj(stop_id=TEXT, arrival_s=INT, service_start_s=INT, departure_s=INT,
            load_kg=NUM, load_m3=NUM)
LEG_REPORT = obj(source=TEXT, target=TEXT, travel_s=INT, distance_m=NUM,
                 load_kg=NUM, load_m3=NUM, estimated_fuel_l=NUM)
VEHICLE_REPORT = obj(vehicle_id=TEXT, stop_ids=array(TEXT), return_s=INT,
                     visits=array(VISIT), legs=array(LEG_REPORT), metrics=METRICS)
REPORT = obj(feasible=BOOL, violations=array(TEXT), metrics=METRICS,
             vehicles=array(VEHICLE_REPORT), fuel_label=TEXT)
RUN = obj(
    schema_version=VERSION, run_id=TEXT, scenario_hash=TEXT, scenario=SCENARIO,
    solver=obj(name=enum("insertion-local-search"), version=TEXT),
    config=obj(seed={"type": "integer"}, budget_s=NUM,
               max_evaluations={"anyOf": [INT, NULL]}, restarts=INT),
    runtime=obj(python=TEXT, platform=TEXT, machine=TEXT, cpu_model=TEXT,
                logical_cpus=INT, device=enum("cpu"), worker_count=INT,
                thread_count=INT),
    timing=obj(solve_s=NUM, search_s=NUM, startup_s=NUM,
               time_to_first_feasible_s={"anyOf": [NUM, NULL]}),
    evaluations=INT, job_status=enum("completed"),
    feasibility=enum("feasible", "unknown"),
    termination_reason=enum("budget_exhausted", "evaluation_limit", "search_exhausted"),
    proof_status=enum("not_certified"),
    solution={"anyOf": [SOLUTION, NULL]},
    validation={"anyOf": [REPORT, NULL]})
SCHEMAS = {"scenario": SCENARIO, "solution": SOLUTION, "run": RUN}


def check(value, schema, path="$"):
    if "anyOf" in schema:
        for choice in schema["anyOf"]:
            try:
                check(value, choice, path)
                return
            except InputError:
                pass
        raise InputError(f"{path}: does not match an allowed type/contract")
    kind = schema.get("type")
    types = {"object": dict, "array": list, "string": str,
             "integer": int, "number": (int, float), "boolean": bool,
             "null": type(None)}
    if kind and (not isinstance(value, types[kind]) or
                 (kind in ("integer", "number") and isinstance(value, bool))):
        raise InputError(f"{path}: expected {kind}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            finite = math.isfinite(value)
        except OverflowError:
            finite = False
        if not finite:
            raise InputError(f"{path}: expected a finite number")
        if value < schema.get("minimum", -math.inf):
            raise InputError(f"{path}: below minimum")
        if value <= schema.get("exclusiveMinimum", -math.inf):
            raise InputError(f"{path}: must be positive")
    if "const" in schema and value != schema["const"]:
        raise InputError(f"{path}: unsupported value/version {value!r}")
    if "enum" in schema and value not in schema["enum"]:
        raise InputError(f"{path}: expected one of {schema['enum']}")
    if kind == "string" and len(value) < schema.get("minLength", 0):
        raise InputError(f"{path}: empty identifier/text")
    if kind == "object":
        if len(value) < schema.get("minProperties", 0):
            raise InputError(f"{path}: empty object")
        properties = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                raise InputError(f"{path}.{key}: required field")
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise InputError(f"{path}: object keys must be nonempty strings")
            rule = properties.get(key, schema.get("additionalProperties", True))
            if rule is False:
                raise InputError(f"{path}.{key}: unknown field")
            if isinstance(rule, dict):
                check(item, rule, f"{path}.{key}")
    if kind == "array":
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", math.inf):
            raise InputError(f"{path}: invalid array length")
        for i, item in enumerate(value):
            check(item, schema["items"], f"{path}[{i}]")
        if schema.get("uniqueItems") and len(set(value)) != len(value):
            raise InputError(f"{path}: duplicate entries")


def validate_scenario(data):
    if isinstance(data, dict) and data.get('schema_version') == 2:
        from .contracts_v2 import validate_road_scenario
        return validate_road_scenario(data)
    check(data, SCENARIO)
    try:
        instant = datetime.fromisoformat(data["reference_instant"])
        if instant.utcoffset() is None:
            raise ValueError("reference_instant requires an offset")
        ZoneInfo(data["timezone"])
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise InputError(f"reference time/timezone: {exc}") from exc
    ids = [s["id"] for s in data["stops"]]
    if len(set(ids)) != len(ids) or data["depot_id"] in ids:
        raise InputError("stop IDs must be unique and distinct from depot")
    for s in data["stops"]:
        if s["window_close_s"] < s["window_open_s"]:
            raise InputError(f"stop {s['id']}: reversed time window")
    nodes = set(ids) | {data["depot_id"]}
    pairs = set()
    for leg in data["legs"]:
        pair = (leg["source"], leg["target"])
        if not set(pair) <= nodes or pair[0] == pair[1] or pair in pairs:
            raise InputError(f"invalid/duplicate directed leg {pair}")
        pairs.add(pair)
        if not leg["reachable"] and (leg["travel_s"] != 0 or leg["segments"]):
            raise InputError(f"unreachable leg {pair}: use zero travel and empty segments")
        if leg["reachable"] and not leg["segments"]:
            raise InputError(f"reachable leg {pair}: segments required")
        for segment in leg["segments"]:
            if segment["driving_category"] not in data["fuel_model"]["categories"]:
                raise InputError(f"leg {pair}: missing fuel category")
    if pairs != {(a, b) for a in nodes for b in nodes if a != b}:
        raise InputError("complete directed leg data required; mark unreachable pairs explicitly")
    objective = data["objective"]
    if objective["time_weight"] + objective["fuel_weight"] <= 0:
        raise InputError("at least one objective weight must be positive")
    if data["fuel_model"]["idle_policy"] == "off" and data["fuel_model"]["idle_l_per_s"]:
        raise InputError("idle policy off requires zero idle consumption")
    fuel = data["fuel_model"]
    coefficients = list(fuel["categories"].values())
    if fuel["kind"] == "distance_equivalent_control":
        if (fuel["label"] != "distance-equivalent control estimate"
                or any(c["load_l_per_km_kg"] != 0 for c in coefficients)
                or len({c["base_l_per_km"] for c in coefficients}) != 1
                or fuel["idle_policy"] != "off"):
            raise InputError("distance-equivalent control requires its label, constant distance coefficient and no load/idle fuel")
    elif (fuel["label"] != "experimental estimate"
          or not any(c["load_l_per_km_kg"] > 0 for c in coefficients)):
        raise InputError("load-dependent fuel requires a positive load coefficient; label constant liters/km as distance-equivalent control")
    return copy.deepcopy(data)


def load_json(path):
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise InputError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        return json.loads(Path(path).read_text(encoding="utf-8"),
                          object_pairs_hook=unique_pairs)
    except (ValueError, RecursionError) as exc:
        raise InputError(f"invalid JSON: {exc}") from exc


if __name__ == "__main__":
    for name, definition in SCHEMAS.items():
        document = {"$schema": "https://json-schema.org/draft/2020-12/schema",
                    "title": f"VRP AI {name} v1", **definition}
        Path(f"schemas/{name}-v1.json").write_text(
            json.dumps(document, indent=2) + "\n", encoding="utf-8")
