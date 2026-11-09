import { describe, expect, it } from "vitest";
import { it as itDict } from "./it";
import { en as enDict } from "./en";
import { SPEED_OPTIONS } from "./i18n";

describe("i18n dictionary parity", () => {
  it("has matching top-level keys between Italian and English", () => {
    const itKeys = Object.keys(itDict).sort();
    const enKeys = Object.keys(enDict).sort();
    expect(enKeys).toEqual(itKeys);
  });

  it("has non-empty translations for all string keys in English", () => {
    for (const [key, value] of Object.entries(enDict)) {
      if (typeof value === "string") {
        expect(value.trim().length, `empty key ${key}`).toBeGreaterThan(0);
      }
    }
  });

  it("has identical keys in nested dictionary maps", () => {
    const maps = ["state", "event", "moveType", "reason"] as const;
    for (const mapName of maps) {
      const itSubKeys = Object.keys(itDict[mapName]).sort();
      const enSubKeys = Object.keys(enDict[mapName]).sort();
      expect(enSubKeys, `nested map mismatch: ${mapName}`).toEqual(itSubKeys);
      for (const [k, v] of Object.entries(enDict[mapName])) {
        expect(typeof v).toBe("string");
        expect((v as string).trim().length, `empty subkey ${mapName}.${k}`).toBeGreaterThan(0);
      }
    }
  });
});

describe("replay speed options", () => {
  it("includes 16x and 32x speed multipliers", () => {
    expect(SPEED_OPTIONS).toContain(16);
    expect(SPEED_OPTIONS).toContain(32);
    expect(SPEED_OPTIONS).toEqual([0.25, 0.5, 1, 2, 4, 8, 16, 32]);
  });

  it("has strictly positive and increasing speeds", () => {
    for (let i = 0; i < SPEED_OPTIONS.length; i++) {
      expect(SPEED_OPTIONS[i]).toBeGreaterThan(0);
      if (i > 0) {
        expect(SPEED_OPTIONS[i]).toBeGreaterThan(SPEED_OPTIONS[i - 1]);
      }
    }
  });
});
