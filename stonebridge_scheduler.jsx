import { useEffect, useMemo, useState } from "react";

// Stonebridge Scheduler – Standalone Agent (enhanced)
// Implements pairing preferences, per-site/provider shift counts, export to Google Calendar CSV,
// timestamped file naming, and persistence of most recent run.
// Notes:
// - Upload a CSV roster (recommended columns below) or paste JSON.
// - We persist the latest generated CSV + inputs in localStorage so you can reload without rerunning.
// - Google Calendar CSV columns used: Subject, Start Date, End Date, All Day Event, Description, Location
//   (Start/End Time left empty for all‑day events). This imports as all‑day events with no further transforms.

// ==== Expected CSV headers for roster upload (flexible order, case-insensitive) ====
// site, date (YYYY-MM-DD), modality (Live|Telehealth), role (interviewer|tester|solo), provider
// language (English|Spanish) [optional], preferred_pair [optional], start [ignored], end [ignored]
// Only 9:00–4:00 blocks are scheduled per your rules; start/end can be ignored.

// ==== Hard rules & preferences encoded here (from Master Scheduling Rules 2025-08-12) ====
// Pairing Preferences (applied greedily, then fill):
// 1) Lakaii Jones → best with Virginia Parker (if V. Parker is available)
// 2) Lyn McDonald → best with Ed Howarth (if E. Howarth is available)
// 3) Spanish-speaking interviewers (e.g., Liliana Pizana) → best with Emma Thomae when possible
// Language matching preferred (Spanish↔Spanish) but not strictly required.
// Site/day/modality must match.

// ==== Utility helpers ====
const titleAbbrev = (site) => (site?.toLowerCase().includes("san antonio") || site?.toLowerCase().includes("san antonio behavioral") || site?.toLowerCase().includes("sa")) ? "SA" : site;

const timestamp = () => {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}_${pad(d.getHours())}-${pad(d.getMinutes())}-${pad(d.getSeconds())}`;
};

const STORAGE_KEY = "stonebridge_scheduler_last_run_v2";

function download(filename, text) {
  const blob = new Blob([text], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.setAttribute("href", url);
  link.setAttribute("download", filename);
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

function csvEscape(value) {
  if (value == null) return "";
  const s = String(value);
  if (/[",
]/.test(s)) return '"' + s.replace(/"/g, '""') + '"';
  return s;
}

function toGoogleCSV(rows) {
  // Columns: Subject, Start Date, Start Time, End Date, End Time, All Day Event, Description, Location
  const header = [
    "Subject","Start Date","Start Time","End Date","End Time","All Day Event","Description","Location"
  ];
  const lines = [header.join(",")];
  for (const r of rows) {
    const line = [
      csvEscape(r.Subject),
      csvEscape(r.StartDate),
      "",
      csvEscape(r.EndDate),
      "",
      "True",
      csvEscape(r.Description || ""),
      csvEscape(r.Location || "")
    ].join(",");
    lines.push(line);
  }
  return lines.join("
");
}

// Basic CSV parser (no external libs) – handles commas and quotes
function parseCSV(text) {
  const lines = text.split(/?
/).filter(Boolean);
  if (!lines.length) return [];
  const headers = splitCSVLine(lines[0]).map(h=>h.trim().toLowerCase());
  return lines.slice(1).map(line => {
    const cells = splitCSVLine(line);
    const obj = {};
    headers.forEach((h,i)=> obj[h] = (cells[i] ?? "").trim());
    return obj;
  });
}
function splitCSVLine(line) {
  const out = [];
  let cur = ""; let inQ = false;
  for (let i=0;i<line.length;i++) {
    const ch = line[i];
    if (inQ) {
      if (ch === '"') {
        if (line[i+1] === '"') { cur += '"'; i++; }
        else { inQ = false; }
      } else cur += ch;
    } else {
      if (ch === ',') { out.push(cur); cur = ""; }
      else if (ch === '"') inQ = true;
      else cur += ch;
    }
  }
  out.push(cur);
  return out;
}

// Normalize roster rows
function normalizeRow(r) {
  const norm = (k) => (r[k] ?? r[k?.toLowerCase?.()] ?? "").toString().trim();
  const site = norm("site");
  const date = norm("date");
  const modality = norm("modality");
  const role = norm("role").toLowerCase();
  const provider = norm("provider");
  const language = (norm("language") || "English").toLowerCase();
  return { site, date, modality, role, provider, language };
}

// Preference checks
function isSpanish(name) {
  // lightweight heuristic; real flag should come from data. We stash a small map for key providers.
  const spanishSet = new Set([
    "cintia martinez","liliana pizana","emma thomae","ben aguilar","cesar villarreal","teresa castano","dr. alvarez-sanders","alvarez-sanders","belinda castillo","noemi martinez"
  ]);
  return spanishSet.has((name||"").toLowerCase());
}

function preferredMatch(interviewer, tester) {
  const i = (interviewer||"").toLowerCase();
  const t = (tester||"").toLowerCase();
  if (i.includes("lakaii jones") && t.includes("virginia parker")) return 3;
  if (i.includes("lyn mcdonald") && t.includes("ed howarth")) return 3;
  // Spanish interviewer best with Emma Thomae if possible
  if (isSpanish(i) && t.includes("emma thomae")) return 2;
  // Language match preference
  const langI = isSpanish(i) ? "spanish" : "english";
  const langT = isSpanish(t) ? "spanish" : "english";
  if (langI === langT) return 1;
  return 0;
}

// Core pairing algorithm
function generatePairings(rows) {
  // Group by (site, date, modality)
  const keyBy = (r) => `${r.site}|${r.date}|${r.modality}`;
  const groups = new Map();
  rows.forEach(r=>{
    const k = keyBy(r);
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(r);
  });

  const results = [];
  const violations = [];
  const gaps = [];

  const providerShiftCounts = new Map();
  const siteShiftCounts = new Map();

  for (const [k, arr] of groups.entries()) {
    const [site, date, modality] = k.split("|");
    const interviewers = arr.filter(a=>a.role === "interviewer").map(a=>a.provider);
    const testers = arr.filter(a=>a.role === "tester").map(a=>a.provider);
    const solos = arr.filter(a=>a.role === "solo").map(a=>a.provider);

    // Count shifts per site
    siteShiftCounts.set(site, (siteShiftCounts.get(site)||0) + arr.length);
    // Count per provider
    arr.forEach(a => providerShiftCounts.set(a.provider, (providerShiftCounts.get(a.provider)||0) + 1));

    // Make a copy of testers availability
    const availableTesters = new Set(testers);

    // Greedy: go through interviewers and pick best tester
    for (const iName of interviewers) {
      let bestTester = null; let bestScore = -1;
      for (const tName of availableTesters) {
        const score = preferredMatch(iName, tName);
        if (score > bestScore) { bestScore = score; bestTester = tName; }
      }
      if (bestTester) {
        availableTesters.delete(bestTester);
        // Create pairing
        const subjSite = titleAbbrev(site);
        const subject = `${subjSite} | Pairing: ${iName} + ${bestTester}`;
        const description = `Dyad pairing for ${date} ${modality}.` + (bestScore>=2 ? " Preference satisfied." : bestScore===1 ? " Language-matched." : "");
        results.push({
          Subject: subject,
          StartDate: date,
          EndDate: date,
          Description: description,
          Location: site
        });
        // Flag preference not met
        if (bestScore < 2 && (iName.toLowerCase().includes("lakaii jones") || iName.toLowerCase().includes("lyn mcdonald") || isSpanish(iName))) {
          violations.push({ site, date, modality, type: "Preference Not Met", interviewer: iName, tester: bestTester || "(none)" });
        }
      } else {
        // Gap: interviewer without tester
        const subjSite = titleAbbrev(site);
        const subject = `${subjSite} | GAP: ${iName} (no tester)`;
        gaps.push({ site, date, modality, interviewer: iName });
        results.push({
          Subject: subject,
          StartDate: date,
          EndDate: date,
          Description: `Unpaired interviewer. Needs tester for ${modality}.`,
          Location: site
        });
        violations.push({ site, date, modality, type: "Unpaired Interviewer", interviewer: iName });
      }
    }

    // Any leftover testers imply underutilization – optional note
    for (const tName of availableTesters) {
      const subjSite = titleAbbrev(site);
      const subject = `${subjSite} | GAP: ${tName} (tester unassigned)`;
      gaps.push({ site, date, modality, tester: tName });
      results.push({
        Subject: subject,
        StartDate: date,
        EndDate: date,
        Description: `Tester not assigned.`,
        Location: site
      });
    }

    // Solo providers become their own all‑day events
    for (const sName of solos) {
      const subjSite = titleAbbrev(site);
      const subject = `${subjSite} | SOLO: ${sName}`;
      results.push({
        Subject: subject,
        StartDate: date,
        EndDate: date,
        Description: `Solo provider working ${modality}.`,
        Location: site
      });
    }
  }

  return { results, violations, gaps, providerShiftCounts, siteShiftCounts };
}

export default function SchedulerApp() {
  const [csvText, setCsvText] = useState("");
  const [rows, setRows] = useState([]);
  const [output, setOutput] = useState(null); // { results, violations, gaps, providerShiftCounts, siteShiftCounts }
  const [autoDownload, setAutoDownload] = useState(true);

  const stats = useMemo(() => {
    if (!output) return { perSite: [], perProvider: [] };
    const perSite = Array.from(output.siteShiftCounts.entries()).map(([site,count])=>({site,count}));
    const perProvider = Array.from(output.providerShiftCounts.entries()).map(([provider,count])=>({provider,count})).sort((a,b)=>b.count-a.count);
    return { perSite, perProvider };
  }, [output]);

  function handleFile(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => setCsvText(String(reader.result||""));
    reader.readAsText(file);
  }

  function ingest() {
    const raw = parseCSV(csvText);
    const normalized = raw.map(normalizeRow).filter(r=>r.site && r.date && r.modality && r.role && r.provider);
    setRows(normalized);
    // Ingest complete: roster normalized and ready for pairing
  }

  function run() {
    const { results, violations, gaps, providerShiftCounts, siteShiftCounts } = generatePairings(rows);
    const csvRows = results.map(r=>({
      Subject: r.Subject,
      StartDate: r.StartDate, // YYYY-MM-DD expected
      EndDate: r.EndDate,
      Description: r.Description,
      Location: r.Location
    }));
    const csv = toGoogleCSV(csvRows);
    const name = `Stonebridge_Pairings_${timestamp()}.csv`;

    const payload = { name, csv, results, violations, gaps, inputs: rows };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
    setOutput({ results, violations, gaps, providerShiftCounts, siteShiftCounts });

    if (autoDownload) download(name, csv);
  }

  function loadLast() {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return;
    try {
      const payload = JSON.parse(raw);
      setOutput({
        results: payload.results || [],
        violations: payload.violations || [],
        gaps: payload.gaps || [],
        providerShiftCounts: new Map(),
        siteShiftCounts: new Map()
      });
      // Rehydrate counts if original inputs exist
      if (payload.inputs?.length) {
        const { providerShiftCounts, siteShiftCounts } = generatePairings(payload.inputs);
        setOutput(o=>o && ({...o, providerShiftCounts, siteShiftCounts}));
      }
    } catch {}
  }

  useEffect(()=>{
    // Auto-restore lightweight view of last run on mount
    loadLast();
  },[]);

  const lastFileName = useMemo(()=>{
    try { return JSON.parse(localStorage.getItem(STORAGE_KEY)||"{}")?.name || ""; } catch { return ""; }
  }, [output]);

  return (
    <div className="min-h-screen bg-gray-50 p-6">
      <div className="max-w-7xl mx-auto space-y-6">
        <header className="flex items-center justify-between">
          <h1 className="text-2xl md:text-3xl font-bold">Stonebridge Scheduler – Standalone Agent</h1>
          <div className="flex items-center gap-3">
            <label className="inline-flex items-center gap-2 text-sm">
              <input type="checkbox" checked={autoDownload} onChange={e=>setAutoDownload(e.target.checked)} />
              Auto‑download CSV on run
            </label>
            <button className="px-3 py-2 rounded-2xl bg-gray-900 text-white shadow" onClick={loadLast}>Load last run</button>
          </div>
        </header>

        <section className="bg-white rounded-2xl shadow p-4">
          <h2 className="text-lg font-semibold mb-3">1) Upload roster CSV</h2>
          <input type="file" accept=".csv" onChange={handleFile} className="block mb-2" />
          <textarea className="w-full border rounded-lg p-2 h-32" placeholder="...or paste CSV here" value={csvText} onChange={e=>setCsvText(e.target.value)} />
          <div className="flex gap-3 mt-3">
            <button className="px-3 py-2 rounded-2xl bg-blue-600 text-white" onClick={ingest}>Parse roster</button>
            <button className="px-3 py-2 rounded-2xl bg-emerald-600 text-white" onClick={run} disabled={!rows.length}>Run pairings</button>
          </div>
          <p className="text-xs text-gray-500 mt-2">Parsed rows: {rows.length}</p>
          {lastFileName && (
            <p className="text-xs text-gray-600 mt-1">Most recent file: <span className="font-mono">{lastFileName}</span></p>
          )}
        </section>

        {output && (
          <>
            <section className="bg-white rounded-2xl shadow p-4">
              <h2 className="text-lg font-semibold mb-3">2) Export-ready Google Calendar CSV</h2>
              <p className="text-sm text-gray-600">Includes pairings, gaps, and violations as all‑day events. “San Antonio” is abbreviated to “SA” in titles.</p>
              <button className="mt-2 px-3 py-2 rounded-2xl bg-indigo-600 text-white" onClick={()=>{
                const last = JSON.parse(localStorage.getItem(STORAGE_KEY)||"{}");
                if (last?.csv && last?.name) download(last.name, last.csv);
              }}>Download last generated CSV</button>
            </section>

            <section className="bg-white rounded-2xl shadow p-4">
              <h2 className="text-lg font-semibold mb-4">3) Summary: shifts per site & provider</h2>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                <div>
                  <h3 className="font-semibold mb-2">Per Site</h3>
                  <ul className="space-y-1 text-sm">
                    {stats.perSite.map((s,i)=> (
                      <li key={i} className="flex justify-between border-b py-1"><span>{s.site}</span><span className="font-mono">{s.count}</span></li>
                    ))}
                  </ul>
                </div>
                <div>
                  <h3 className="font-semibold mb-2">Per Provider</h3>
                  <ul className="max-h-64 overflow-auto space-y-1 text-sm">
                    {stats.perProvider.map((p,i)=> (
                      <li key={i} className="flex justify-between border-b py-1"><span>{p.provider}</span><span className="font-mono">{p.count}</span></li>
                    ))}
                  </ul>
                </div>
              </div>
            </section>

            <section className="bg-white rounded-2xl shadow p-4">
              <h2 className="text-lg font-semibold mb-3">4) Detected Violations</h2>
              {output.violations.length ? (
                <ul className="text-sm list-disc pl-6">
                  {output.violations.map((v,i)=> (
                    <li key={i}>{v.date} | {titleAbbrev(v.site)} | {v.modality} — <span className="font-semibold">{v.type}</span> — {v.interviewer}{v.tester?` + ${v.tester}`:""}</li>
                  ))}
                </ul>
              ) : <p className="text-sm text-emerald-700">No violations detected.</p>}
            </section>
          </>
        )}

        <footer className="text-xs text-gray-500 text-center">All output is formatted for Google Calendar all‑day .csv import. Titles show pairings, gaps, and violations. SA abbreviation applied.</footer>
      </div>
    </div>
  );
}
