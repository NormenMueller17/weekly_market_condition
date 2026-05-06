/**
 * Cloudflare Worker — Zertifikate Portfolio API
 *
 * Bindings required (set in Cloudflare Dashboard → Worker → Settings → Variables):
 *   D1 binding : DB          → zertifikate_portfolio  (D1 Database)
 *   Secret     : API_SECRET  → beliebiges Passwort (mind. 16 Zeichen empfohlen)
 *
 * Endpoints:
 *   GET  /api/portfolio         → alle offenen + geschlossenen Trades
 *   POST /api/kauf              → neuen Kauf speichern
 *   POST /api/verkauf           → Position schließen
 */

const CORS = {
  "Access-Control-Allow-Origin":  "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization",
};

export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") {
      return new Response(null, { headers: CORS });
    }

    // ── Auth ─────────────────────────────────────────────────────────────────
    const auth = request.headers.get("Authorization") || "";
    if (auth !== `Bearer ${env.API_SECRET}`) {
      return json({ error: "Unauthorized" }, 401);
    }

    // ── Routing + DB init (alles im try/catch damit CORS-Header immer gesendet werden) ──
    const path   = new URL(request.url).pathname;
    const method = request.method;

    try {
      await initDb(env.DB);

      if (path === "/api/portfolio" && method === "GET") {
        return await getPortfolio(env.DB);
      }
      if (path === "/api/kauf" && method === "POST") {
        return await addKauf(request, env.DB);
      }
      if (path === "/api/verkauf" && method === "POST") {
        return await addVerkauf(request, env.DB);
      }
      return json({ error: "Not found" }, 404);
    } catch (e) {
      return json({ error: e.message }, 500);
    }
  },
};

// ── Handler: GET /api/portfolio ───────────────────────────────────────────────
async function getPortfolio(db) {
  const [open, closed] = await Promise.all([
    db.prepare("SELECT * FROM trades_open   ORDER BY kauf_datum    DESC").all(),
    db.prepare("SELECT * FROM trades_closed ORDER BY verkauf_datum DESC").all(),
  ]);
  return json({ open: open.results, closed: closed.results });
}

// ── Handler: POST /api/kauf ───────────────────────────────────────────────────
async function addKauf(request, db) {
  const t = await request.json();
  await db.prepare(`
    INSERT INTO trades_open
      (id, basiswert, company, schein_isin, schein_name, kauf_datum,
       faelligkeitsdatum, kauf_kurs_schein, kauf_kurs_basiswert, anzahl,
       investiert, strike, hebel_kauf, restlaufzeit_kauf_monate, notizen)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
  `).bind(
    t.id, t.basiswert, t.company ?? null, t.schein_isin ?? null, t.schein_name ?? null,
    t.kauf_datum, t.faelligkeitsdatum ?? null,
    t.kauf_kurs_schein, t.kauf_kurs_basiswert ?? null,
    t.anzahl, t.investiert, t.strike ?? null, t.hebel_kauf ?? null,
    t.restlaufzeit_kauf_monate ?? null, t.notizen ?? null,
  ).run();
  return json({ ok: true });
}

// ── Handler: POST /api/verkauf ────────────────────────────────────────────────
async function addVerkauf(request, db) {
  const v = await request.json();

  const pos = await db.prepare("SELECT * FROM trades_open WHERE id = ?")
    .bind(v.id).first();
  if (!pos) return json({ error: "Position nicht gefunden" }, 404);

  const erloes  = Math.round(v.verkauf_kurs_schein * pos.anzahl * 100) / 100;
  const pl      = Math.round((erloes - pos.investiert) * 100) / 100;
  const plPct   = Math.round(pl / pos.investiert * 10000) / 100;

  await db.batch([
    db.prepare(`
      INSERT INTO trades_closed
        (id, basiswert, company, schein_isin, schein_name, kauf_datum,
         faelligkeitsdatum, kauf_kurs_schein, kauf_kurs_basiswert, anzahl,
         investiert, strike, hebel_kauf, restlaufzeit_kauf_monate, notizen,
         verkauf_datum, verkauf_kurs_schein, verkauf_kurs_basiswert,
         verkauf_erloes, gewinn_verlust, gewinn_verlust_pct, verkauf_grund)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    `).bind(
      pos.id, pos.basiswert, pos.company, pos.schein_isin, pos.schein_name,
      pos.kauf_datum, pos.faelligkeitsdatum, pos.kauf_kurs_schein, pos.kauf_kurs_basiswert,
      pos.anzahl, pos.investiert, pos.strike, pos.hebel_kauf, pos.restlaufzeit_kauf_monate,
      pos.notizen,
      v.verkauf_datum, v.verkauf_kurs_schein, v.verkauf_kurs_basiswert ?? null,
      erloes, pl, plPct, v.verkauf_grund,
    ),
    db.prepare("DELETE FROM trades_open WHERE id = ?").bind(v.id),
  ]);

  return json({ ok: true, gewinn_verlust: pl, gewinn_verlust_pct: plPct });
}

// ── DB Init ───────────────────────────────────────────────────────────────────
async function initDb(db) {
  // D1 exec() unterstützt nur ein Statement pro Aufruf — daher getrennt
  await db.exec(`CREATE TABLE IF NOT EXISTS trades_open (
    id                       TEXT PRIMARY KEY,
    basiswert                TEXT NOT NULL,
    company                  TEXT,
    schein_isin              TEXT,
    schein_name              TEXT,
    kauf_datum               TEXT NOT NULL,
    faelligkeitsdatum        TEXT,
    kauf_kurs_schein         REAL NOT NULL,
    kauf_kurs_basiswert      REAL,
    anzahl                   INTEGER NOT NULL,
    investiert               REAL NOT NULL,
    strike                   REAL,
    hebel_kauf               REAL,
    restlaufzeit_kauf_monate INTEGER,
    notizen                  TEXT
  )`);
  await db.exec(`CREATE TABLE IF NOT EXISTS trades_closed (
    id                       TEXT PRIMARY KEY,
    basiswert                TEXT NOT NULL,
    company                  TEXT,
    schein_isin              TEXT,
    schein_name              TEXT,
    kauf_datum               TEXT,
    faelligkeitsdatum        TEXT,
    kauf_kurs_schein         REAL,
    kauf_kurs_basiswert      REAL,
    anzahl                   INTEGER,
    investiert               REAL,
    strike                   REAL,
    hebel_kauf               REAL,
    restlaufzeit_kauf_monate INTEGER,
    notizen                  TEXT,
    verkauf_datum            TEXT,
    verkauf_kurs_schein      REAL,
    verkauf_kurs_basiswert   REAL,
    verkauf_erloes           REAL,
    gewinn_verlust           REAL,
    gewinn_verlust_pct       REAL,
    verkauf_grund            TEXT
  )`);
}

// ── Helper ────────────────────────────────────────────────────────────────────
function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { ...CORS, "Content-Type": "application/json" },
  });
}
