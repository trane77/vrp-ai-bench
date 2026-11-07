import { test, expect } from "@playwright/test";

test("saved real-road comparison filters, curves, fronts and synchronized maps reopen", async ({
  page,
  request,
}) => {
  test.setTimeout(180000);
  const campaigns = await (await request.get("/api/v1/experiments")).json();
  const requested = process.env.VRP_M4_JOB ?? process.env.VRP_M3_JOB;
  const campaign = requested
    ? campaigns.find((c: any) => c.id === requested)
    : campaigns.find((c: any) => ["completed", "cancelled"].includes(c.status));
  expect(
    campaign,
    "a completed or cancelled real-road campaign is required",
  ).toBeTruthy();
  const cases = await (
    await request.get(`/api/v1/experiments/${campaign.id}/cases`)
  ).json();
  const results = await Promise.all(
    cases
      .filter((c: any) => c.available && c.recording)
      .map(async (c: any) =>
        (
          await request.get(
            `/api/v1/experiments/${campaign.id}/cases/${c.id}?view=summary`,
          )
        ).json(),
      ),
  );
  const learned = campaign.request.manifest.main_cases === 288;
  let pair: any[] = [];
  for (const a of results)
    for (const b of results)
      if (
        a.case.id !== b.case.id &&
        a.scenario_available &&
        b.scenario_available &&
        a.case.method === "ordered" &&
        b.case.method === (learned ? "learned" : "shuffled") &&
        a.case.budget_s === 15 &&
        a.case.time_weight === 1 &&
        a.quality?.feasible_within_budget &&
        b.quality?.feasible_within_budget &&
        a.observations?.length &&
        b.observations?.length &&
        ["instance", "budget_s", "time_weight", "recording"].every(
          (k) => a.case[k] === b.case[k],
        )
      )
        pair = [a, b];
  expect(pair.length).toBe(2);
  const [a, b] = pair;
  if (learned) {
    expect(b.checkpoint_hash).toMatch(/^[a-f0-9]{64}$/);
    expect(b.learned.device).toBe("cpu");
    expect(b.training.training_duration_s).toBeGreaterThan(0);
  }
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.route("https://tiles.openfreemap.org/**", (route) =>
    route.abort(),
  );
  await page.goto("/");
  const section = page.locator("#comparisons");
  await section
    .getByLabel("Campagna", { exact: true })
    .selectOption(campaign.id);
  await expect(section.getByRole("status")).toContainText(
    `${cases.filter((c: any) => c.available).length}/${cases.length} casi salvati`,
    { timeout: 60000 },
  );
  await section
    .getByLabel("Istanza", { exact: true })
    .selectOption(a.case.instance);
  await section
    .getByLabel("Budget", { exact: true })
    .selectOption(String(a.case.budget_s));
  await section
    .getByLabel("Metodo", { exact: true })
    .selectOption(a.case.method);
  if (learned) {
    await section.getByLabel("Metodo", { exact: true }).selectOption("learned");
    await expect(section.locator("tbody tr").first()).toContainText("cpu");
    await expect(section.locator("tbody tr").first()).toContainText("training");
    await section
      .getByLabel("Metodo", { exact: true })
      .selectOption(a.case.method);
  }
  const rows = section.locator("tbody tr");
  expect(await rows.count()).toBeGreaterThan(0);
  for (const row of await rows.all())
    await expect(row).toContainText(a.case.method);
  await section.getByLabel("Sinistra", { exact: true }).selectOption(a.case.id);
  const incompatible = results.find(
    (r) =>
      r.case.instance !== a.case.instance ||
      r.case.budget_s !== a.case.budget_s ||
      r.case.time_weight !== a.case.time_weight,
  );
  if (incompatible)
    await expect(
      section
        .getByLabel("Destra", { exact: true })
        .locator(`option[value="${incompatible.case.id}"]`),
    ).toBeDisabled();
  await section.getByLabel("Destra", { exact: true }).selectOption(b.case.id);
  await expect(
    section.getByRole("img", { name: "Curve anytime" }),
  ).toBeVisible();
  await expect(
    section.getByRole("img", { name: "Diagramma tempo carburante" }),
  ).toBeVisible();
  await expect(
    section
      .getByRole("img", { name: "Diagramma tempo carburante" })
      .locator("circle")
      .first(),
  ).toBeVisible({ timeout: 30000 });
  await section
    .getByRole("button", { name: "Apri mappe sincronizzate" })
    .click();
  await expect(section.getByTestId("route-map")).toHaveCount(2, {
    timeout: 60000,
  });
  await section.getByLabel("Tempo di confronto").fill("0");
  await expect(section.getByText(/nessun incumbent registrato/)).toHaveCount(2);
  const position = Math.max(a.timing.solve_s, b.timing.solve_s);
  await section
    .getByLabel("Tempo di confronto")
    .fill(String(Math.floor(position * 100) / 100));
  await expect(section.getByText(/nessun incumbent registrato/)).toHaveCount(0);
  const external = results.find(
    (r) =>
      r.case.method === "ortools" &&
      r.case.instance === a.case.instance &&
      r.case.budget_s === a.case.budget_s &&
      r.case.time_weight === a.case.time_weight,
  );
  if (external) {
    await section
      .getByLabel("Destra", { exact: true })
      .selectOption(external.case.id);
    if (!external.observations?.length)
      await expect(
        section.getByText(/nessun incumbent registrato/),
      ).toHaveCount(1);
    await expect(section.getByTestId("route-map")).toHaveCount(2, {
      timeout: 60000,
    });
  }
  await page.screenshot({
    path: "test-results/m3-comparison.png",
    fullPage: true,
  });
  await page.reload();
  await expect(section.getByLabel("Campagna", { exact: true })).toHaveValue(
    campaign.id,
    { timeout: 30000 },
  );
  await expect(section.locator("tbody tr")).not.toHaveCount(0);
  const download = await request.get(
    `/api/v1/experiments/${campaign.id}/export/json`,
  );
  expect(download.ok()).toBe(true);
  const report = await download.json();
  if (learned) {
    expect(report.schema_version).toBe(2);
    expect(report.campaign_complete).toBe(true);
    expect(report.training_cost.training_and_preflight_s).toBeGreaterThan(
      report.training_cost.model_training_s,
    );
    await expect(
      section.getByText(
        `costo condiviso totale: ${report.training_cost.training_and_preflight_s.toFixed(3)} s.`,
        { exact: false },
      ),
    ).toBeVisible();
  }
  expect(report.manifest_hash).toBe(campaign.request.manifest.manifest_hash);
  expect(errors).toEqual([]);
});
