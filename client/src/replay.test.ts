import { describe, it, expect } from "vitest";
import {
  cityTime,
  along,
  emptyReplay,
  fold,
  mergeEvents,
  stateAt,
  position,
} from "./replay";
import type { Event, Scenario, Vehicle } from "./types";
const event = (seq: number, kind: string, payload = {}): Event => ({
  schema_version: 1,
  job_id: "test",
  seq,
  elapsed_s: seq,
  kind,
  payload,
});
describe("recorded replay", () => {
  it("deduplicates by stable sequence and preserves chronology", () => {
    expect(
      mergeEvents(
        [event(2, "metric")],
        [event(1, "initial"), event(2, "metric")],
      ).map((e) => e.seq),
    ).toEqual([1, 2]);
  });
  it("keeps partial candidates separate from feasible best", () => {
    const e = [
      event(1, "feasible_best", { partial: false }),
      event(2, "accepted_insertion", { partial: true }),
    ];
    const s = stateAt(e, 1);
    expect(s.best?.partial).toBe(false);
    expect(s.current?.partial).toBe(true);
    expect(fold(s, e[0])).toBe(s);
  });
  it("terminal best agrees with saved best without fabricating moves", () => {
    const best = { partial: false };
    const s = fold(
      emptyReplay,
      event(1, "terminal", { best, status: "completed" }),
    );
    expect(s.best).toEqual(best);
    expect(s.current).toBeNull();
  });
  it("interpolates only on saved geometry", () => {
    expect(
      along(
        [
          [0, 0],
          [1, 0],
          [1, 1],
        ],
        0.5,
      ),
    ).toEqual([1, 0]);
    expect(
      along(
        [
          [0, 0],
          [1, 0],
        ],
        2,
      ),
    ).toEqual([1, 0]);
  });
  it("respects travel waiting service and loaded depot return timing", () => {
    const s = {
      start_s: 0,
      depot_id: "depot",
      locations: [
        { id: "depot", snapped: [0, 0] },
        { id: "a", snapped: [1, 0] },
      ],
      legs: [
        {
          source: "depot",
          target: "a",
          geometry: [
            [0, 0],
            [1, 0],
          ],
        },
        {
          source: "a",
          target: "depot",
          geometry: [
            [1, 0],
            [0, 0],
          ],
        },
      ],
    } as Scenario;
    const v = {
      legs: [
        { source: "depot", target: "a", travel_s: 10 },
        { source: "a", target: "depot", travel_s: 10 },
      ],
      visits: [
        { stop_id: "a", arrival_s: 10, service_start_s: 20, departure_s: 30 },
      ],
    } as Vehicle;
    expect(position(s, v, 5)).toEqual({
      coordinate: [0.5, 0],
      phase: "travel",
    });
    expect(position(s, v, 15).phase).toBe("waiting");
    expect(position(s, v, 25).phase).toBe("service");
    expect(position(s, v, 35)).toEqual({
      coordinate: [0.5, 0],
      phase: "travel",
    });
    expect(position(s, v, 40).phase).toBe("returned");
  });
});

it("displays local city time across DST without altering elapsed seconds", () => {
  const scenario = {
    reference_instant: "2026-03-29T00:30:00+00:00",
    start_s: 0,
    timezone: "Europe/Rome",
  } as Scenario;
  expect(cityTime(scenario, 0)).toBe("01:30:00");
  expect(cityTime(scenario, 3600)).toBe("03:30:00");
});
