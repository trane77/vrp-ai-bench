import { test, expect } from "@playwright/test";
import { writeFile, mkdir } from "node:fs/promises";

test("records client frame intervals independently from solver timing", async ({
  page,
  request,
  browser,
}) => {
  const jobs = await (await request.get("/api/v1/jobs")).json();
  let id: string | undefined;
  for (const job of jobs) {
    if (
      job.kind === "solve" &&
      job.status === "completed" &&
      job.feasibility === "feasible"
    ) {
      const run = await (
        await request.get(`/api/v1/jobs/${job.id}/result`)
      ).json();
      if (
        run.scenario.dataset.city_id === "bologna" &&
        run.scenario.stops.length === 20
      ) {
        id = job.id;
        break;
      }
    }
  }
  expect(id, "run the real-city demo acceptance first").toBeTruthy();
  await page.addInitScript(
    ({ id }) => {
      localStorage.setItem("vrp-job", id!);
      localStorage.removeItem("vrp-replay");
    },
    { id },
  );
  await page.goto("/");
  await expect(
    page.getByRole("button", { name: "Esecuzione del percorso", exact: true }),
  ).toBeEnabled({ timeout: 60000 });
  await page
    .getByRole("button", { name: "Esecuzione del percorso", exact: true })
    .click();
  await page.getByRole("button", { name: "Riproduci", exact: true }).click();
  const frames = await page.evaluate(
    () =>
      new Promise<number[]>((resolve) => {
        const samples: number[] = [];
        let last = performance.now();
        const tick = (now: number) => {
          samples.push(now - last);
          last = now;
          if (samples.length === 120) resolve(samples);
          else requestAnimationFrame(tick);
        };
        requestAnimationFrame(tick);
      }),
  );
  const renderer = await page.evaluate(() => {
    const canvas =
      document.querySelector<HTMLCanvasElement>(".maplibregl-canvas");
    const gl = canvas?.getContext("webgl2") ?? canvas?.getContext("webgl");
    if (!gl) return "unavailable";
    const info = gl.getExtension("WEBGL_debug_renderer_info");
    return String(
      gl.getParameter(info ? info.UNMASKED_RENDERER_WEBGL : gl.RENDERER),
    );
  });
  const sorted = [...frames].sort((a, b) => a - b);
  const record = {
    schema_version: 1,
    profile: "headless Chromium, route playback; not a universal UI guarantee",
    browser: browser.version(),
    renderer,
    viewport: { width: 1440, height: 1100 },
    job_id: id,
    sample_count: frames.length,
    frame_interval_ms: { p50: sorted[59], p95: sorted[113], max: sorted[119] },
    prototype_target_p95_ms: 50,
    target_met: sorted[113] <= 50,
  };
  await mkdir("../runs", { recursive: true });
  await writeFile(
    "../runs/m2-client-performance.json",
    JSON.stringify(record, null, 2) + "\n",
  );
  expect(frames).toHaveLength(120);
  console.log(JSON.stringify(record));
});
