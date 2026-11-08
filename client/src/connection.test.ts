import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

class MemoryStorage {
  private store: Record<string, string> = {};
  getItem(key: string) {
    return this.store[key] ?? null;
  }
  setItem(key: string, value: string) {
    this.store[key] = String(value);
  }
  removeItem(key: string) {
    delete this.store[key];
  }
  clear() {
    this.store = {};
  }
}

const mockStorage = new MemoryStorage();
vi.stubGlobal("sessionStorage", mockStorage);
vi.stubGlobal("location", new URL("http://localhost:3000"));

import { apiUrl, endpoint, request, streamUrl, token } from "./connection";

describe("connection helper", () => {
  beforeEach(() => {
    mockStorage.clear();
    vi.restoreAllMocks();
  });

  afterEach(() => {
    mockStorage.clear();
    vi.restoreAllMocks();
  });

  it("defaults endpoint to location.origin and token to empty string", () => {
    expect(endpoint()).toBe("http://localhost:3000");
    expect(token()).toBe("");
  });

  it("reads endpoint and token from sessionStorage when configured", () => {
    mockStorage.setItem("vrp-endpoint", "https://ai360.lan:8000");
    mockStorage.setItem("vrp-token", "secret-test-token-12345");
    expect(endpoint()).toBe("https://ai360.lan:8000");
    expect(token()).toBe("secret-test-token-12345");
  });

  it("constructs apiUrl with base endpoint", () => {
    mockStorage.setItem("vrp-endpoint", "https://ai360.lan:8000");
    expect(apiUrl("/api/v1/health")).toBe("https://ai360.lan:8000/api/v1/health");
  });

  it("converts http to ws and https to wss in streamUrl, appending token", () => {
    // Default loopback http
    expect(streamUrl("/api/v1/jobs/1/stream")).toBe("ws://localhost:3000/api/v1/jobs/1/stream");

    // Remote https with token
    mockStorage.setItem("vrp-endpoint", "https://ai360.lan:8000");
    mockStorage.setItem("vrp-token", "tok-123");
    expect(streamUrl("/api/v1/jobs/1/stream")).toBe(
      "wss://ai360.lan:8000/api/v1/jobs/1/stream?token=tok-123",
    );
  });

  it("requires a token when a remote endpoint is configured", async () => {
    mockStorage.setItem("vrp-endpoint", "https://ai360.lan:8000");
    mockStorage.removeItem("vrp-token");
    await expect(request("/api/v1/health")).rejects.toThrow(
      "Inserisci il token Ai360 nelle impostazioni di connessione.",
    );
  });

  it("sends X-VRP-Token header when token is configured", async () => {
    mockStorage.setItem("vrp-endpoint", "https://ai360.lan:8000");
    mockStorage.setItem("vrp-token", "valid-token");
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await request("/api/v1/health");
    expect(fetchMock).toHaveBeenCalledWith(
      "https://ai360.lan:8000/api/v1/health",
      expect.objectContaining({
        headers: expect.objectContaining({
          "X-VRP-Token": "valid-token",
        }),
      }),
    );
  });

  it("throws clear error on 401 unauthorized", async () => {
    mockStorage.setItem("vrp-token", "bad-token");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("Unauthorized", { status: 401 })));

    await expect(request("/api/v1/health")).rejects.toThrow("Token Ai360 mancante o rifiutato.");
  });

  it("throws actionable message on network or TLS certificate error", async () => {
    mockStorage.setItem("vrp-token", "test-token");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    await expect(request("/api/v1/health")).rejects.toThrow(
      "Connessione non riuscita. Verifica endpoint HTTPS, rete e certificato attendibile.",
    );
  });

  it("unwraps JSON error detail on bad request responses", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "revision changed" }), {
          status: 409,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    await expect(request("/api/v1/jobs", "POST", {})).rejects.toThrow("revision changed");
  });
});
