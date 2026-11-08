import { test, expect } from "@playwright/test";

test.describe("LAN connection settings", () => {
  test("validates HTTPS origins and persists endpoint and token in sessionStorage", async ({
    page,
  }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Dalle decisioni ai percorsi." })).toBeVisible();

    const endpointInput = page.getByLabel("Ai360 HTTPS");
    const tokenInput = page.getByLabel("Token Ai360");
    const connectButton = page.getByRole("button", { name: "Connetti" });

    await expect(endpointInput).toBeVisible();
    await expect(tokenInput).toBeVisible();
    await expect(connectButton).toBeVisible();

    // 1. Invalid endpoint: HTTP without TLS
    await endpointInput.fill("http://ai360.lan:8000");
    await connectButton.click();
    await expect(
      page.getByText("L'endpoint Ai360 deve essere un'origine HTTPS senza percorso."),
    ).toBeVisible();

    // 2. Invalid endpoint: HTTPS with path
    await endpointInput.fill("https://ai360.lan:8000/api");
    await connectButton.click();
    await expect(
      page.getByText("L'endpoint Ai360 deve essere un'origine HTTPS senza percorso."),
    ).toBeVisible();

    // 3. Valid HTTPS endpoint and token
    const testEndpoint = "https://ai360.lan:8000";
    const testToken = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678";

    await endpointInput.fill(testEndpoint);
    await tokenInput.fill(testToken);
    await connectButton.click();

    // Verify sessionStorage values after reload
    await expect.poll(async () => {
      return page.evaluate(() => ({
        endpoint: sessionStorage.getItem("vrp-endpoint"),
        token: sessionStorage.getItem("vrp-token"),
      }));
    }).toEqual({
      endpoint: testEndpoint,
      token: testToken,
    });

    // Verify inputs remain populated from sessionStorage
    await expect(endpointInput).toHaveValue(testEndpoint);
    await expect(tokenInput).toHaveValue(testToken);
  });
});
