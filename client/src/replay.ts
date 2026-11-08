import type { Coord, Event, Replay, Scenario, Vehicle } from "./types";
export const emptyReplay: Replay = {
  seq: 0,
  current: null,
  best: null,
  terminal: null,
};
export function fold(state: Replay, event: Event): Replay {
  if (event.seq <= state.seq) return state;
  const next = { ...state, seq: event.seq };
  if (["initial", "accepted_insertion", "accepted_move"].includes(event.kind))
    next.current = event.payload;
  if (event.kind === "feasible_best") next.best = event.payload;
  if (["terminal", "recovered"].includes(event.kind)) {
    next.terminal = event.payload;
    if (event.payload.best) next.best = event.payload.best;
  }
  return next;
}
export function mergeEvents(old: Event[], incoming: Event[]): Event[] {
  return [
    ...new Map([...old, ...incoming].map((e) => [e.seq, e])).values(),
  ].sort((a, b) => a.seq - b.seq);
}
export function stateAt(events: Event[], index: number): Replay {
  return events.slice(0, index + 1).reduce(fold, emptyReplay);
}
export function along(path: Coord[], fraction: number): Coord {
  if (!path.length) return [0, 0];
  if (path.length === 1) return path[0];
  const lengths = path
    .slice(1)
    .map((p, i) =>
      Math.hypot(
        (p[0] - path[i][0]) * Math.cos((p[1] * Math.PI) / 180),
        p[1] - path[i][1],
      ),
    );
  const total = lengths.reduce((a, b) => a + b, 0);
  let target = Math.max(0, Math.min(1, fraction)) * total;
  for (let i = 0; i < lengths.length; i++) {
    if (target <= lengths[i]) {
      const t = lengths[i] ? target / lengths[i] : 0;
      return [
        path[i][0] + (path[i + 1][0] - path[i][0]) * t,
        path[i][1] + (path[i + 1][1] - path[i][1]) * t,
      ];
    }
    target -= lengths[i];
  }
  return path.at(-1)!;
}
export function position(
  s: Scenario,
  v: Vehicle,
  elapsed: number,
): { coordinate: Coord; phase: string } {
  const points = new Map(s.locations.map((p) => [p.id, p.snapped]));
  let departure = s.start_s;
  const now = elapsed + s.start_s;
  for (let i = 0; i < v.legs.length; i++) {
    const leg = v.legs[i],
      arrival = departure + leg.travel_s;
    if (now < arrival) {
      const saved = s.legs.find(
        (l) => l.source === leg.source && l.target === leg.target,
      );
      const alt = leg.path_id
        ? saved?.alternatives?.find((a) => a.id === leg.path_id)
        : undefined;
      return {
        coordinate: along(
          alt?.geometry ?? saved?.geometry ?? [],
          (now - departure) / Math.max(1, leg.travel_s),
        ),
        phase: "travel",
      };
    }
    const visit = v.visits[i];
    if (visit) {
      const breakS = visit.break_s ?? 0;
      if (now < visit.service_start_s)
        return { coordinate: points.get(visit.stop_id)!, phase: "waiting" };
      if (now < visit.departure_s - breakS)
        return { coordinate: points.get(visit.stop_id)!, phase: "service" };
      if (now < visit.departure_s)
        return { coordinate: points.get(visit.stop_id)!, phase: "break" };
      departure = visit.departure_s;
    }
  }
  return { coordinate: points.get(s.depot_id)!, phase: "returned" };
}

export function cityTime(s: Scenario, elapsed: number): string {
  return new Intl.DateTimeFormat("it-IT", {
    timeZone: s.timezone,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).format(
    new Date(Date.parse(s.reference_instant) + (s.start_s + elapsed) * 1000),
  );
}
