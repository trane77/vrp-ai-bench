export type Coord = [number, number];
export interface Stop {
  id: string;
  coordinate?: Coord;
  window_open_s: number;
  window_close_s: number;
  packages: number;
  mass_kg: number;
  volume_m3: number;
  delivery_mass_kg?: number;
  delivery_volume_m3?: number;
}
export interface DriverBreak {
  enabled: boolean;
  threshold_s: number;
  duration_s: number;
}
export interface Parameters {
  start_s: number;
  vehicle_ids: string[];
  capacity: { mass_kg: number; volume_m3: number };
  duration_cap_s: number | null;
  path_optimization?: boolean;
  driver_break?: DriverBreak;
  vehicle_capacities?: Record<string, { mass_kg: number; volume_m3: number }>;
  service: {
    mode: "constant" | "packages";
    constant_s: number;
    fixed_s: number;
    per_package_s: number;
  };
  objective: {
    time_weight: number;
    fuel_weight: number;
    time_scale_s: number;
    fuel_scale_l: number;
  };
  fuel_model: {
    version: string;
    kind: string;
    calibration: string;
    label: string;
    categories: Record<
      string,
      { base_l_per_km: number; load_l_per_km_kg: number }
    >;
    idle_policy: string;
    idle_l_per_s: number;
  };
}
export interface Draft {
  schema_version: number;
  city_id: string;
  dataset_hash: string;
  seed: number;
  mode: string;
  depot: Coord;
  stops: Stop[];
  parameters: Parameters;
}
export interface Editor {
  id: string;
  revision: string;
  data: Draft;
}
export interface Metrics {
  fleet_elapsed_s: number;
  completion_s: number;
  travel_s: number;
  waiting_s: number;
  service_s: number;
  distance_m: number;
  estimated_fuel_l: number;
  used_vans: number;
  available_vans: number;
}
export interface Tour {
  vehicle_id: string;
  stop_ids: string[];
  path_ids?: string[];
}
export interface Solution {
  schema_version: number;
  scenario_id: string;
  routes: Tour[];
}
export interface Visit {
  stop_id: string;
  arrival_s: number;
  service_start_s: number;
  departure_s: number;
  load_kg: number;
  load_m3: number;
  break_s?: number;
}
export interface Vehicle {
  vehicle_id: string;
  stop_ids: string[];
  visits: Visit[];
  return_s: number;
  metrics: Metrics;
  legs: {
    source: string;
    target: string;
    travel_s: number;
    distance_m: number;
    load_kg: number;
    estimated_fuel_l: number;
    path_id?: string;
  }[];
}
export interface Validation {
  feasible: boolean;
  violations: string[];
  metrics: Metrics;
  vehicles: Vehicle[];
  fuel_label: string;
}
export interface PathAlternative {
  id: string;
  travel_s: number;
  distance_m: number;
  geometry?: Coord[];
}
export interface Leg {
  source: string;
  target: string;
  reachable: boolean;
  geometry: Coord[];
  travel_s: number;
  alternatives?: PathAlternative[];
}
export interface Scenario extends Parameters {
  id: string;
  schema_version: number;
  depot_id: string;
  reference_instant: string;
  timezone: string;
  dataset: {
    city_id: string;
    version: string;
    manifest_hash: string;
    osm_timestamp: string;
    attribution: string;
  };
  locations: {
    id: string;
    requested: Coord;
    snapped: Coord;
    snap_distance_m: number;
  }[];
  stops: Stop[];
  legs: Leg[];
  preparation_s: number;
  generation: { mode: string; seed: number; rejected_samples: number };
}
export interface Run {
  run_id?: string;
  scenario: Scenario;
  solution: Solution | null;
  validation: Validation | null;
  timing: { solve_s: number; search_s: number; recording_s: number };
  termination_reason: string;
  proof_status: string;
}
export interface Payload {
  solution?: Solution | null;
  validation?: Validation | null;
  partial?: boolean;
  move_type?: string;
  delta?: { fleet_elapsed_s: number; estimated_fuel_l: number };
  phase?: string;
  done?: number;
  total?: number;
  best?: Payload | null;
  status?: string;
  reason?: string;
}
export interface Event {
  schema_version: number;
  job_id: string;
  seq: number;
  elapsed_s: number;
  kind: string;
  payload: Payload;
}
export interface Replay {
  seq: number;
  current: Payload | null;
  best: Payload | null;
  terminal: Payload | null;
}
export interface City {
  id: string;
  name: string;
  center: Coord;
  installed: boolean;
  manifest: {
    version: string;
    osm_timestamp: string;
    attribution: string;
  } | null;
}
export interface Job {
  id: string;
  kind: "prepare" | "solve";
  status: string;
  request: {
    draft_id: string;
    revision: string;
    prepared_job_id?: string;
    seed?: number;
    budget_s?: number;
    policy?: "cpu" | "learned";
  };
  last_seq: number;
  error: string | null;
  feasibility: string;
  termination_reason: string | null;
  created: number;
  cancel_requested: boolean;
}
