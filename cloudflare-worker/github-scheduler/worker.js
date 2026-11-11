/**
 * Cloudflare Worker — GitHub Actions Scheduler
 *
 * Ersetzt GitHubs eigene `schedule:`-Trigger (bei diesem Account/Repo
 * durchgehend 4-9h verspaetet) durch `workflow_dispatch`-Aufrufe zur exakten
 * Zielzeit. Tickt jede Minute, vergleicht die aktuelle UTC-Zeit gegen SCHEDULE
 * und loest bei Treffer den passenden Workflow aus.
 *
 * Secret erforderlich (wrangler secret put GITHUB_TOKEN):
 *   Fine-grained PAT, nur dieses Repo, Permission "Actions: Read and write".
 *
 * Optionales Secret fuer manuelles Testen ohne auf den Cron-Tick zu warten:
 *   wrangler secret put ADMIN_SECRET
 *   GET https://<worker>.workers.dev/test-dispatch?workflow=midweek-entry.yml&secret=...
 */

const OWNER = "NormenMueller17";
const REPO = "weekly_market_condition";

// Wochentag: 0=Sonntag ... 6=Samstag (UTC, wie Date.prototype.getUTCDay())
const SCHEDULE = [
  { day: 6, hour: 2, minute: 23, workflow: "weekly-market-report.yml" },
  { day: 6, hour: 9, minute: 0, workflow: "zertifikate-report.yml" },
  { day: 1, hour: 15, minute: 0, workflow: "monday-execute.yml" },
  { day: 2, hour: 15, minute: 0, workflow: "monday-execute.yml" },
  { day: 3, hour: 19, minute: 30, workflow: "midweek-entry.yml" },
  ...[1, 2, 3, 4, 5].map((day) => ({ day, hour: 15, minute: 0, workflow: "daily-stock-alert.yml" })),
  ...[1, 2, 3, 4, 5].map((day) => ({ day, hour: 16, minute: 0, workflow: "zertifikate-daily-alert.yml" })),
];

export default {
  async scheduled(event, env, ctx) {
    const now = new Date(event.scheduledTime);
    const day = now.getUTCDay();
    const hour = now.getUTCHours();
    const minute = now.getUTCMinutes();

    const matches = SCHEDULE.filter((e) => e.day === day && e.hour === hour && e.minute === minute);
    for (const entry of matches) {
      ctx.waitUntil(dispatch(entry.workflow, env.GITHUB_TOKEN));
    }
  },

  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/test-dispatch") {
      if (!env.ADMIN_SECRET || url.searchParams.get("secret") !== env.ADMIN_SECRET) {
        return new Response("unauthorized", { status: 401 });
      }
      const workflow = url.searchParams.get("workflow");
      if (!workflow) {
        return new Response("missing ?workflow=<file>.yml", { status: 400 });
      }
      // Alle weiteren Query-Parameter (ausser workflow/secret) werden 1:1 als
      // workflow_dispatch-Inputs durchgereicht, z.B. &dry_run=1 fuer einen
      // gefahrlosen Test ohne echte Orders.
      const inputs = {};
      for (const [key, value] of url.searchParams) {
        if (key === "workflow" || key === "secret") continue;
        inputs[key] = value;
      }
      const result = await dispatch(workflow, env.GITHUB_TOKEN, inputs);
      return new Response(`dispatched ${workflow} (inputs: ${JSON.stringify(inputs)}): ${result.status} ${result.body}`, { status: result.ok ? 200 : 502 });
    }
    return new Response("github-actions-scheduler worker is running.");
  },
};

async function dispatch(workflowFile, token, inputs = {}) {
  const url = `https://api.github.com/repos/${OWNER}/${REPO}/actions/workflows/${workflowFile}/dispatches`;
  const body = { ref: "main" };
  if (Object.keys(inputs).length > 0) {
    body.inputs = inputs;
  }
  const res = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "weekly-market-condition-scheduler",
    },
    body: JSON.stringify(body),
  });
  const responseBody = res.ok ? "" : await res.text();
  if (!res.ok) {
    console.error(`Dispatch failed for ${workflowFile}: ${res.status} ${responseBody}`);
  } else {
    console.log(`Dispatched ${workflowFile}`);
  }
  return { ok: res.ok, status: res.status, body: responseBody };
}
