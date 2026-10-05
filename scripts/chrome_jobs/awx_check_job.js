/* AWX Set-Planeicao job status per host — paste in DevTools console with AWX open. Read-only.
 * Put one or more job ids in JOB_IDS. */
{
const JOB_IDS = [18851];
(async () => {
  const get = async p => { const r = await fetch(p, { credentials: "same-origin" }); if (!r.ok) throw new Error(`${p} -> HTTP ${r.status}`); return r.json(); };
  const rows = [];
  for (const id of JOB_IDS) {
    const job = await get(`/api/v2/jobs/${id}/`);
    const hosts = (job.limit || "").split(",").map(x => x.trim()).filter(Boolean);
    const s = job.finished ? (await get(`/api/v2/jobs/${id}/job_host_summaries/?page_size=200`)).results : [];
    for (const h of hosts) {
      let result;
      if (!job.finished) {
        const ev = (await get(`/api/v2/jobs/${id}/job_events/?host_name=${h}&order_by=-counter&page_size=1`)).results[0];
        result = ev ? `RUNNING: ${ev.task} (${ev.event})` : "WAITING (not started for this host)";
      } else {
        const x = s.find(r => r.host_name === h);
        result = !x ? "NOT REACHED" : x.dark ? "UNREACHABLE" : (x.failures || x.failed) ? "FAILED" : "OK";
      }
      rows.push({ job: id, job_status: job.status, host: h, result, started: (job.started || "").slice(0, 16), finished: (job.finished || "-").slice(0, 16) });
    }
  }
  console.table(rows);
  const retry = rows.filter(r => r.job_status !== "running" && r.job_status !== "pending" && r.result !== "OK").map(r => r.host);
  console.log(retry.length ? `Retry (finished, not OK): ${retry.join(",")}` : "No finished failures.");
})().catch(e => console.error("ERROR:", e.message));
}