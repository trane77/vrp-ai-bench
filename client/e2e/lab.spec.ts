import { test, expect } from "@playwright/test";

test("real-city run survives browser closure and replays its saved solution", async ({
  page,
  context,
  request,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Dalle decisioni ai percorsi." }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Crea demo", exact: true })
    .first()
    .click();
  await expect(
    page.getByRole("button", { name: "Prepara percorsi", exact: true }),
  ).toBeEnabled();
  await page
    .getByRole("button", { name: "Prepara percorsi", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Avvia ricerca", exact: true }),
  ).toBeEnabled({ timeout: 60000 });
  await page.getByLabel("Budget ricerca (s)").fill("5");
  const previousId = await page.evaluate(() => localStorage.getItem("vrp-job"));
  await page
    .getByRole("button", { name: "Avvia ricerca", exact: true })
    .click();
  await expect
    .poll(() => page.evaluate(() => localStorage.getItem("vrp-job")))
    .not.toBe(previousId);
  const id = await page.evaluate(() => localStorage.getItem("vrp-job"));
  const running = await (await request.get(`/api/v1/jobs/${id}`)).json();
  expect(running.kind).toBe("solve");
  await page.close();
  const reopened = await context.newPage();
  reopened.on("pageerror", (e) => errors.push(e.message));
  await reopened.goto("/");
  await expect(
    reopened.getByRole("button", {
      name: "Esecuzione del percorso",
      exact: true,
    }),
  ).toBeEnabled({ timeout: 60000 });
  const result = await (await request.get(`/api/v1/jobs/${id}/result`)).json();
  expect(result.validation.feasible).toBe(true);
  await reopened
    .getByRole("button", { name: "Esecuzione del percorso", exact: true })
    .click();
  await expect(
    reopened.getByText("Movimento interpolato sulle geometrie salvate"),
  ).toBeVisible();
  await reopened.getByRole("slider", { name: "Posizione replay" }).fill("10");
  await reopened.screenshot({
    path: "test-results/m2-bologna.png",
    fullPage: true,
  });
  await reopened.reload();
  await expect(
    reopened.getByRole("button", {
      name: "Esecuzione del percorso",
      exact: true,
    }),
  ).toBeEnabled({ timeout: 60000 });
  await expect(
    reopened.getByText("Movimento interpolato sulle geometrie salvate"),
  ).toBeVisible();
  await expect(
    reopened.getByRole("slider", { name: "Posizione replay" }),
  ).toHaveValue("10");
  const snapshot = await (
    await request.get(`/api/v1/jobs/${id}/snapshot`)
  ).json();
  expect(snapshot.best.validation).toEqual(result.validation);
  expect(errors).toEqual([]);
  await reopened.close();
});

test("map editor adds and moves stops and invalidates prepared data", async ({
  page,
}) => {
  await page.goto("/");
  await page
    .getByRole("button", { name: "Crea demo", exact: true })
    .first()
    .click();
  await expect(
    page.getByRole("button", { name: "Prepara percorsi", exact: true }),
  ).toBeEnabled();
  const before = await page.locator(".stop-list button").count();
  await page
    .getByRole("button", { name: "Aggiungi fermata", exact: true })
    .click();
  await page.getByTestId("route-map").click({ position: { x: 250, y: 220 } });
  await expect(page.locator(".stop-list button")).toHaveCount(before + 1);
  await expect(page.getByText("Modifiche da preparare")).toBeVisible();
  await page.getByLabel("Pacchi", { exact: true }).fill("4");
  await page.getByRole("button", { name: "Sposta punto", exact: true }).click();
  await page.getByTestId("route-map").click({ position: { x: 270, y: 210 } });
  await page.getByRole("button", { name: "Rimuovi", exact: true }).click();
  await expect(page.locator(".stop-list button")).toHaveCount(before);
  await expect(
    page.getByRole("button", { name: "Avvia ricerca", exact: true }),
  ).toBeDisabled();
});

test("Modena remains usable without online basemap and with reduced motion", async ({
  page,
}) => {
  await page.route("https://tiles.openfreemap.org/**", (route) =>
    route.abort(),
  );
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/");
  await page
    .getByRole("combobox", { name: "Città", exact: true })
    .selectOption("modena");
  await page.getByLabel("Fermate", { exact: true }).fill("3");
  await page
    .getByRole("button", { name: "Crea demo", exact: true })
    .first()
    .click();
  await page
    .getByRole("button", { name: "Prepara percorsi", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Avvia ricerca", exact: true }),
  ).toBeEnabled({ timeout: 60000 });
  await page
    .getByRole("button", { name: "Avvia ricerca", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Esecuzione del percorso", exact: true }),
  ).toBeEnabled({ timeout: 60000 });
  await expect(
    page.getByText("Sfondo online non disponibile.", { exact: false }),
  ).toBeVisible();
  await expect(
    page.getByLabel("Movimento ridotto", { exact: true }),
  ).toBeChecked();
  await expect(
    page.getByRole("button", { name: "Riproduci", exact: true }),
  ).toBeDisabled();
  await page.screenshot({
    path: "test-results/m2-modena-offline.png",
    fullPage: true,
  });
});
