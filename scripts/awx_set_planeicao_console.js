/* AWX Set-Planeicao bulk updater — paste in the browser DevTools console with AWX open (logged in).
 * 1) Paste the PIFE list (with header) between the backticks of LIST.
 * 2) Run with DRY_RUN = true  -> shows the plan, changes nothing.
 * 3) Run with DRY_RUN = false -> updates planeicao in AWX, launches Set-Planeicao limited to those hosts,
 *    waits for the job and prints the per-host result.
 * Later re-check of a job:  awxCheckJob(<job_id>)
 */
const LIST = `
STATUS	SPN	S-MODE	A/C Register	SN	Job URL	Notas	Fecha
`;
const DRY_RUN = true;
const INVENTORY_ID = 4;      // AVA
const TEMPLATE_ID = 47;      // Set-Planeicao
const POLL_MINUTES = 20;

/* ---------- FAA N-number <-> ICAO ---------- */
const CS = "ABCDEFGHJKLMNPQRSTUVWXYZ", SUF = 601, B4 = 35, B3 = 10 * B4 + SUF, B2 = 10 * B3 + SUF, B1 = 10 * B2 + SUF;
function sfx(o) { if (!o) return ""; o -= 1; const c = Math.floor(o / 25), r = o % 25; return CS[c] + (r ? CS[r - 1] : ""); }
function icaoToN(hex) {
  let o = parseInt(hex, 16) - 0xA00001; if (isNaN(o) || o < 0 || o >= 9 * B1) return null;
  let out = "N" + (Math.floor(o / B1) + 1); o %= B1;
  for (const size of [B2, B3]) { if (o < SUF) return out + sfx(o); o -= SUF; out += Math.floor(o / size); o %= size; }
  if (o < SUF) return out + sfx(o); o -= SUF; out += Math.floor(o / B4); o %= B4;
  if (!o) return out; o -= 1; return out + (o < 24 ? CS[o] : String(o - 24));
}

/* ---------- parsing ---------- */
function parseList(text) {
  const lines = text.split(/\r?\n/).map(l => l.trim()).filter(Boolean);
  const hdr = lines.shift().split("\t").map(h => h.trim().toUpperCase());
  const ix = k => hdr.indexOf(k);
  const [iS, iM, iR, iF] = [ix("SPN"), ix("S-MODE"), ix("A/C REGISTER"), ix("FECHA")];
  if (iS < 0 || iM < 0) throw new Error("Header must contain SPN and S-MODE (tab separated)");
  const out = {};
  for (const l of lines) {
    const c = l.split("\t").map(x => x.trim());
    const spn = (c[iS] || "").toUpperCase(), code = (c[iM] || "").toUpperCase();
    if (!spn || !code) continue;
    const d = Date.parse((c[iF] || "").replace(/(\d+)(st|nd|rd|th)/, "$1")) || 0;
    if (!out[spn] || d >= out[spn].date) out[spn] = { spn, code, tail: (c[iR] || "").toUpperCase(), date: d };
  }
  return Object.values(out);
}
const PLANE_RX = /(["']?planeicao["']?\s*:\s*)(["']?)[^"',\n}]*\2/i;
const getPlane = v => { const m = (v || "").match(/["']?planeicao["']?\s*:\s*["']?([^"',\s}]*)/i); return m ? m[1].toUpperCase() : ""; };
const setPlane = (v, code) => PLANE_RX.test(v) ? v.replace(PLANE_RX, `$1"${code}"`) : null;

/* ---------- AWX API ---------- */
const csrf = () => (document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/) || [])[1];
async function api(path, method = "GET", body) {
  const opt = { method, credentials: "same-origin", headers: { "Content-Type": "application/json" } };
  if (method !== "GET") { const t = csrf(); if (!t) throw new Error("csrftoken cookie not readable"); opt.headers["X-CSRFToken"] = t; opt.body = JSON.stringify(body); }
  const r = await fetch(path, opt);
  if (!r.ok) throw new Error(`${method} ${path} -> HTTP ${r.status}: ${(await r.text()).slice(0, 300)}`);
  return r.status === 204 ? null : r.json();
}
async function allHosts() {
  let url = `/api/v2/inventories/${INVENTORY_ID}/hosts/?page_size=200&order_by=name`, res = [];
  while (url) { const d = await api(url); res = res.concat(d.results); url = d.next; }
  return res;
}
async function awxCheckJob(id) {
  const job = await api(`/api/v2/jobs/${id}/`);
  const s = (await api(`/api/v2/jobs/${id}/job_host_summaries/?page_size=200`)).results;
  const limit = (job.limit || "").split(",").map(x => x.trim()).filter(Boolean);
  const rows = limit.map(h => {
    const x = s.find(r => r.host_name === h);
    const res = !x ? "NOT REACHED" : x.dark ? "UNREACHABLE" : x.failures || x.failed ? "FAILED" : "OK";
    return { host: h, result: res, ok: x?.ok ?? 0, changed: x?.changed ?? 0 };
  });
  console.log(`Job ${id} status: ${job.status}  (${location.origin}/#/jobs/playbook/${id}/output)`);
  console.table(rows);
  const retry = rows.filter(r => r.result !== "OK").map(r => r.host);
  if (retry.length) console.warn("Retry later (limit):", retry.join(","));
  return rows;
}
window.awxCheckJob = awxCheckJob;

/* ---------- main ---------- */
(async () => {
  const items = parseList(LIST);
  if (!items.length) return console.error("LIST is empty");
  const hosts = await allHosts();
  const byName = Object.fromEntries(hosts.map(h => [h.name.toUpperCase(), h]));
  const plan = [], skip = [], conflicts = [];
  for (const it of items) {
    const h = byName[it.spn];
    if (!h) { skip.push({ spn: it.spn, reason: "host not in AWX" }); continue; }
    if (!/^[0-9A-F]{6}$/.test(it.code)) { skip.push({ spn: it.spn, reason: `invalid S-MODE ${it.code}` }); continue; }
    if (it.tail.startsWith("N")) {
      const dec = icaoToN(it.code);
      if (dec !== it.tail) { skip.push({ spn: it.spn, reason: `S-MODE ${it.code}=${dec} != ${it.tail}` }); continue; }
    }
    const cur = getPlane(h.variables);
    const nv = cur === it.code ? h.variables : setPlane(h.variables || "", it.code);
    if (nv === null) { skip.push({ spn: it.spn, reason: "planeicao key not found in variables" }); continue; }
    plan.push({ spn: it.spn, id: h.id, tail: it.tail, from: cur || "-", to: it.code, change: cur !== it.code, nv });
    hosts.filter(o => o.id !== h.id && getPlane(o.variables) === it.code && !items.some(i => i.spn === o.name.toUpperCase()))
         .forEach(o => conflicts.push({ code: it.code, tail: it.tail, new_unit: it.spn, still_on: o.name }));
  }
  console.log("PLAN"); console.table(plan.map(({ nv, id, ...r }) => r));
  if (skip.length) { console.warn("SKIPPED"); console.table(skip); }
  if (conflicts.length) { console.warn("SAME CODE STILL ON OTHER UNITS (ask PIFE where they are now)"); console.table(conflicts); }
  if (DRY_RUN) return console.log("DRY_RUN=true -> nothing changed. Set DRY_RUN=false and run again to apply.");
  if (!plan.length) return console.log("Nothing to apply.");

  for (const p of plan.filter(p => p.change)) {
    await api(`/api/v2/hosts/${p.id}/`, "PATCH", { variables: p.nv });
    const back = getPlane((await api(`/api/v2/hosts/${p.id}/`)).variables);
    console.log(`${p.spn}: planeicao ${p.from} -> ${back} ${back === p.to ? "OK" : "MISMATCH!"}`);
  }
  const limit = plan.map(p => p.spn).join(",");
  const job = await api(`/api/v2/job_templates/${TEMPLATE_ID}/launch/`, "POST", { inventory: INVENTORY_ID, limit });
  const id = job.job || job.id;
  console.log(`Launched Set-Planeicao job ${id} limit=${limit}. Waiting up to ${POLL_MINUTES} min...`);
  const end = Date.now() + POLL_MINUTES * 60000;
  let st;
  while (Date.now() < end) {
    st = (await api(`/api/v2/jobs/${id}/`)).status;
    if (["successful", "failed", "error", "canceled"].includes(st)) break;
    await new Promise(r => setTimeout(r, 15000));
  }
  if (!["successful", "failed", "error", "canceled"].includes(st)) return console.warn(`Job ${id} still ${st}. Check later with: awxCheckJob(${id})`);
  await awxCheckJob(id);
})().catch(e => console.error("ERROR:", e.message));