import { describe, expect, it } from "vitest";
import { compatibleCases, incumbentAt } from "./Comparisons";
describe("recorded comparisons", () => {
  const c = {
    id: "a",
    instance: "bologna",
    method: "ordered",
    budget_s: 5,
    repetition: 0,
    time_weight: 1,
    recording: true,
    control: false,
  };
  it("rejects mismatched instances objectives budgets and recording", () => {
    expect(compatibleCases(c, { ...c, method: "ortools", repetition: 2 })).toBe(
      true,
    );
    for (const change of [
      { instance: "modena" },
      { time_weight: 0.5 },
      { budget_s: 15 },
      { recording: false },
    ])
      expect(compatibleCases(c, { ...c, ...change })).toBe(false);
  });
  it("holds the last recorded incumbent without interpolation", () => {
    const first = {
        elapsed_s: 1,
        solution: {} as never,
        validation: {} as never,
      },
      second = { ...first, elapsed_s: 3 };
    expect(incumbentAt([first, second], 0.99)).toBe(null);
    expect(incumbentAt([first, second], 2)).toBe(first);
    expect(incumbentAt([first, second], 3)).toBe(second);
  });
});
