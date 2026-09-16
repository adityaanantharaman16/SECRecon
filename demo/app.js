(() => {
  "use strict";
  const data = window.SECReconDemo;
  const state = {
    company: 0,
    filter: "all",
    job: 0,
    jobs: structuredClone(data.jobs),
    search: "",
    tour: -1,
    replay: 0,
    replayRunning: false,
  };
  const content = document.querySelector("#content");
  const labels = {
    overview: "Overview",
    filings: "Filings",
    comparisons: "Reconciliation",
    jobs: "Processing jobs",
    archive: "Source archive",
    replay: "Replay & recovery",
  };
  const names = {
    changed: "Changed",
    unchanged: "Unchanged",
    only_in_original: "Only in original",
    only_in_amendment: "Only in amendment",
    ambiguous: "Ambiguous",
    succeeded: "Succeeded",
    retry_wait: "Retry scheduled",
    quarantined: "Quarantined",
    dead_letter: "Dead letter",
    queued: "Queued",
  };
  let replayTimer, toastTimer;
  const esc = (value) =>
    String(value ?? "").replace(
      /[&<>"']/g,
      (c) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[c],
    );
  const badge = (value) =>
    `<span class="badge ${esc(value.toLowerCase().replaceAll(" ", "-"))}">${esc(names[value] || value)}</span>`;
  const money = (value) =>
    value === null
      ? '<span class="meta">Not observed</span>'
      : /^-?\d+$/.test(value)
        ? BigInt(value).toLocaleString("en-US")
        : esc(value);
  const heading = (eyebrow, title, subtitle, actions = "") =>
    `<div class="heading"><div><p class="eyebrow">${eyebrow}</p><h1>${title}</h1><p>${subtitle}</p></div><div class="heading-actions">${actions}</div></div>`;
  const panel = (title, subtitle, body, action = "") =>
    `<section class="panel"><div class="panel-header"><div><h2>${title}</h2>${subtitle ? `<p>${subtitle}</p>` : ""}</div>${action}</div>${body}</section>`;
  const metric = (label, value, foot) =>
    `<article class="metric"><div class="metric-label">${label}<span>↗</span></div><div class="metric-value">${value}</div><div class="metric-foot">${foot}</div></article>`;
  const button = (text, action, cls = "") =>
    `<button class="button ${cls}" data-action="${action}">${text}</button>`;
  const route = () =>
    Object.hasOwn(labels, location.hash.slice(1))
      ? location.hash.slice(1)
      : "overview";
  const go = (page) => {
    if (route() === page) render();
    else location.hash = page;
  };
  function toast(message) {
    const el = document.querySelector("#toast");
    el.textContent = message;
    el.classList.add("visible");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove("visible"), 4200);
  }
  function overview() {
    return (
      heading(
        "PIPELINE OPERATIONS",
        "A clear view of every filing.",
        "Follow the data from arrival to a traceable financial assertion.",
        `<span class="time-chip">Sample day · 16 Sep 2026</span>${button("View comparisons →", "comparisons", "primary")}`,
      ) +
      `<div class="metrics">${metric("Filings preserved", "148", '<span class="green">↑ 12</span> in the last sample day')}${metric("Processing success", '98.6<span style="font-size:18px">%</span>', "146 of 148 completed processing jobs")}${metric("Ingestion lag", '42<span style="font-size:18px"> sec</span>', '<span class="green">Within</span> the 5-minute target')}${metric("Needs attention", "3", '<span class="amber">1 dead letter · 1 quarantine · 1 retry</span>')}</div>` +
      `<div class="grid-two"><div>${panel(
        "From source to evidence",
        "An auditable path, with the original bytes always retained.",
        `<div class="pipeline">${[
          ["↓", "Capture", "Raw SEC response"],
          ["◇", "Preserve", "Bytes + checksum"],
          ["≡", "Normalize", "Facts + provenance"],
          ["⇄", "Reconcile", "Changes + coverage"],
        ]
          .map(
            (x, i) =>
              `${i ? '<span class="pipeline-arrow">→</span>' : ""}<div class="pipeline-node"><span class="step-icon">${x[0]}</span><h3>${x[1]}</h3><p>${x[2]}</p></div>`,
          )
          .join("")}</div>`,
      )}
      ${panel("Recent filing activity", "Original filings and amendments remain separate.", `<div class="table-wrap"><table><thead><tr><th>Company / filing</th><th>Coverage</th><th>Received</th><th></th></tr></thead><tbody>${data.companies.map((c, i) => `<tr class="row-link" tabindex="0" role="button" data-company="${i}" aria-label="Compare ${esc(c.name)}"><td><strong>${c.name}</strong><small>${c.ticker} · 10-K/A · FY 2025</small></td><td>${badge(c.status)}</td><td class="meta">12:0${4 - i} UTC</td><td>↗</td></tr>`).join("")}</tbody></table></div>`, button("All filings →", "filings", "subtle small"))}</div><div>
      ${panel("Processing activity", "Sample events across the pipeline.", `<div class="activity-row"><span class="activity-symbol green">✓</span><div>Comparison completed<p>Northstar · 2 changed values<br>Evidence is ready to inspect.</p></div><time>2m ago</time></div><div class="activity-row"><span class="activity-symbol amber">!</span><div>Unexpected source shape<p>Atlas · Numeric type mismatch<br>Raw payload safely quarantined.</p></div><time>4m ago</time></div><div class="activity-row"><span class="activity-symbol blue">↺</span><div>Retry scheduled<p>Meridian · Upstream rate limit<br>Waiting for the requested interval.</p></div><time>5m ago</time></div>`, button("Inspect jobs →", "jobs", "subtle small"))}
      <div class="attention"><strong>One job needs an operator decision</strong><p>The retry budget is exhausted. Inspect the attempt history before creating a linked redrive.</p><div style="margin-top:13px">${button("Review dead letter →", "dead-letter", "small")}</div></div></div></div>`
    );
  }
  function filings() {
    const q = state.search.toLowerCase();
    const companies = data.companies
      .map((c, i) => ({ ...c, i }))
      .filter((c) =>
        (c.name + c.ticker + c.cik + c.original + c.amendment)
          .toLowerCase()
          .includes(q),
      );
    return (
      heading(
        "SOURCE INVENTORY",
        "Filings",
        "A filing is an immutable accession. An amendment adds history; it never replaces the original.",
      ) +
      panel(
        "Tracked filings",
        "Three fictional companies · annual reports and amendments",
        `<div class="filters"><input id="filing-search" aria-label="Search filings" placeholder="Search company, ticker or accession…" value="${esc(state.search)}"><span class="filter-count">${companies.length * 2} filings</span></div><div class="table-wrap"><table><thead><tr><th>Company</th><th>Form</th><th>Accession</th><th>Report period</th><th>Source</th></tr></thead><tbody>${companies.flatMap((c) => ["original", "amendment"].map((side) => `<tr class="row-link" tabindex="0" role="button" data-company="${c.i}" aria-label="Open ${esc(c.name)} ${side}"><td><strong>${c.name}</strong><small>${c.ticker} · ${c.cik}</small></td><td>${side === "original" ? "10-K" : "10-K/A"}</td><td class="mono">${c[side]}</td><td>${c.period}</td><td>${badge("Preserved")} ↗</td></tr>`)).join("")}</tbody></table>${companies.length ? "" : '<div class="empty">No sample filings match your search.</div>'}</div><div class="panel-footer"><span>Select a row to open its original/amendment comparison.</span><span>All results are fictional</span></div>`,
      )
    );
  }
  function comparisons() {
    const c = data.companies[state.company];
    const count = (status) => c.rows.filter((r) => r.status === status).length;
    const rows = c.rows
      .map((r, i) => ({ ...r, i }))
      .filter((r) => state.filter === "all" || r.status === state.filter);
    return (
      heading(
        "FINANCIAL RECONCILIATION",
        c.name,
        "Compare supported facts across two explicit filing accessions.",
        `<select id="scenario" aria-label="Comparison scenario">${data.companies.map((x, i) => `<option value="${i}" ${i === state.company ? "selected" : ""}>${x.ticker} · ${x.scenario}</option>`).join("")}</select>${button("Export JSON ↓", "export")}`,
      ) +
      `<div class="comparison-pair"><div class="filing-card"><span class="tag">ORIGINAL FILING</span><h3>10-K · Year ended ${c.period}</h3><span class="mono">${c.original}</span><small>Snapshot: demo-snapshot-original-${state.company + 1}</small></div><div class="compare-arrow">⇄</div><div class="filing-card"><span class="tag">AMENDED FILING</span><h3>10-K/A · Filed ${c.filed}</h3><span class="mono">${c.amendment}</span><small>Snapshot: demo-snapshot-amendment-${state.company + 1}</small></div></div>` +
      `<div class="notice"><strong>${state.company === 2 ? "Incomplete fact coverage." : "Scoped comparison."}</strong> ${state.company === 2 ? "No supported amendment facts were observed in this snapshot. Original values remain available." : "Only matching concepts, units and reporting dates are compared."} Missing facts are never treated as deleted or zero.</div>` +
      `<div class="summary-chips">${["changed", "unchanged", "only_in_original", "ambiguous"].map((s) => `<span><b>${count(s)}</b> ${names[s]}</span>`).join("")}</div>` +
      panel(
        "Fact comparison",
        "Values in USD · select a row to inspect the evidence",
        `<div class="filters"><select id="fact-filter" aria-label="Filter comparison results">${[["all", "All observations"], ...Object.entries(names).filter(([s]) => ["changed", "unchanged", "only_in_original", "ambiguous"].includes(s))].map(([v, l]) => `<option value="${v}" ${state.filter === v ? "selected" : ""}>${l}</option>`).join("")}</select><span class="filter-count">${rows.length} of ${c.rows.length} comparison keys</span></div><div class="table-wrap"><table><thead><tr><th>Concept / reporting period</th><th class="numeric">Original</th><th class="numeric">Amendment</th><th class="numeric">Delta</th><th>Result</th><th></th></tr></thead><tbody>${rows.map((r) => `<tr class="row-link" tabindex="0" role="button" data-fact="${r.i}" aria-label="Inspect ${r.concept} provenance"><td><strong>${r.concept}</strong><small>${r.period} · ${r.kind}</small></td><td class="numeric">${money(r.before)}</td><td class="numeric">${money(r.after)}</td><td class="numeric ${r.status === "changed" ? "blue" : ""}">${r.delta === null ? "—" : r.delta}</td><td>${badge(r.status)}</td><td>↗</td></tr>`).join("")}</tbody></table>${rows.length ? "" : '<div class="empty">No observations have this status in the selected scenario.</div>'}</div><div class="panel-footer"><span>Parser: sec-json-v1 · Comparison: accession-pair-v1</span><span>Entity-wide US-GAAP coverage</span></div>`,
      ) +
      `<p class="key-legend">An accession identifies the filing. A snapshot identifies what the upstream API returned at capture time.<br>A changed snapshot for the same accession is an upstream revision; it is not automatically another legal amendment.</p>`
    );
  }
  function jobs() {
    const selected = state.jobs[state.job];
    return (
      heading(
        "DURABLE PROCESSING",
        "Nothing disappears into a log.",
        "Inspect attempts, ownership and recovery decisions for each accepted job.",
      ) +
      `<div class="job-layout">${panel("Processing jobs", "PostgreSQL is authoritative; Redis delivers work notifications.", `<div class="table-wrap"><table><thead><tr><th>Job / company</th><th>Status</th><th>Attempts</th></tr></thead><tbody>${state.jobs.map((j, i) => `<tr class="row-link" tabindex="0" role="button" data-job="${i}" aria-label="Inspect ${j.id}" ${state.job === i ? 'style="background:#f0f5fc"' : ""}><td><strong>${j.kind}</strong><small>${j.company}<br>${j.id}</small></td><td>${badge(j.status)}</td><td>${j.attempts}</td></tr>`).join("")}</tbody></table></div>`)}${panel("Attempt history", `${selected.id} · ${selected.kind}`, `<div class="panel-body">${badge(selected.status)}<ol class="timeline">${selected.events.map((e) => `<li>${esc(e)}</li>`).join("")}</ol>${selected.status === "dead_letter" ? `${button("Create linked redrive →", "redrive", "primary")}<p class="callout">The failed job stays intact. A new job references its predecessor.</p>` : selected.status === "quarantined" ? `<div class="notice"><strong>Schema diagnostic</strong><br><span class="mono">$/facts/us-gaap/Assets/units/USD/0/val</span><br>Expected number; received object. Investigate the adapter before reprocessing.</div>` : '<p class="callout">Attempt history is retained independently of queue messages.</p>'}<p class="meta">Demo actions affect only this browser tab.</p></div>`)}</div>`
    );
  }
  function archive() {
    return (
      heading(
        "IMMUTABLE INPUTS",
        "The evidence stays intact.",
        "Preserved responses let you explain a value today and reproduce it later.",
      ) +
      `<div class="metrics">${metric("Source events", "296", "Each capture keeps its own identity")}${metric("Unique bodies", "241", "Identical bytes share a content hash")}${metric("Integrity checks", '100<span style="font-size:18px">%</span>', "Sample archive checksums verified")}${metric("Stored bytes", '84.2<span style="font-size:18px"> MB</span>', "Fictional archive size")}</div>` +
      panel(
        "Source event inventory",
        "Selected sample entries · bytes are preserved before normalization.",
        `<div class="table-wrap"><table><thead><tr><th>Source / company</th><th>Event ID</th><th>Content checksum</th><th>Captured (UTC)</th><th></th></tr></thead><tbody>${data.companies.flatMap((c, i) => ["Company Facts JSON", "Primary filing HTML"].map((kind, k) => `<tr class="row-link" tabindex="0" role="button" data-archive="${i}" aria-label="Inspect sample source for ${c.name}"><td><strong>${kind}</strong><small>${c.name}</small></td><td class="mono">demo-event-${i + 1}-${k + 1}</td><td class="mono">demo:${i + 1}f7a…${k + 1}b92</td><td>2026-09-16 12:0${i}:18</td><td>↗</td></tr>`)).join("")}</tbody></table></div><div class="panel-footer"><span>Checksums shown here are illustrative, not verifiable source hashes.</span><span>No live source access</span></div>`,
      ) +
      `<div class="notice"><strong>Two identities, two purposes.</strong> A content hash identifies bytes. An event ID identifies a capture with a URL and fetch time. Re-fetching identical content can create another event without duplicating the financial assertion.</div>`
    );
  }
  function replay() {
    const stages = [
      "Freeze the source inventory",
      "Validate all content checksums",
      "Rebuild into a new generation",
      "Compare canonical output digests",
    ];
    return (
      heading(
        "RECOVERY WORKSPACE",
        "Rebuild with confidence.",
        "Regenerate derived facts from preserved sources. Keep the current generation available.",
        button(
          state.replayRunning
            ? "Replaying…"
            : state.replay === 4
              ? "Run again ↺"
              : "Run simulated replay →",
          "replay-run",
          "primary",
        ),
      ) +
      `<div class="run-grid">${panel("Replay progress", "Offline simulation · no network or database changes", `<div class="panel-body"><div class="progress-track"><div class="progress-bar" style="width:${state.replay * 25}%"></div></div>${stages.map((s, i) => `<div class="replay-step"><span>${String(i + 1).padStart(2, "0")} &nbsp; ${s}</span>${i < state.replay ? badge("Verified") : `<span class="meta">${i === state.replay && state.replayRunning ? "In progress…" : "Waiting"}</span>`}</div>`).join("")}${state.replay === 4 ? '<div class="notice" style="margin:20px 0 0"><strong>Canonical output matches.</strong><br>The new generation is ready for review. The active generation has not been replaced.</div>' : ""}</div>`)}${panel("Projection generations", "A generation is a versioned view of the same preserved evidence.", `<div class="panel-body"><div class="generation"><div>live-v1<small>sec-json-v1 · existing financial view</small></div>${badge("Active")}</div>${state.replay === 4 ? `<div class="generation"><div>demo-rebuild-v1<small>sec-json-v1 · identical financial digest</small></div>${badge("Ready")}</div>` : '<p class="callout">A separate candidate generation will appear after the simulated replay.</p>'}<p class="callout"><strong>Replay is not a full backup restore.</strong><br>Source events can reconstruct financial facts and provenance. Recovering historical job attempts requires a database backup.</p><p class="meta">This demo intentionally has no real promotion control.</p></div>`)}</div>`
    );
  }
  function render() {
    const page = route();
    document.querySelector("#breadcrumb").textContent = labels[page];
    document.querySelectorAll("[data-nav]").forEach((a) => {
      a.classList.toggle("active", a.dataset.nav === page);
      if (a.dataset.nav === page) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    });
    content.innerHTML = {
      overview,
      filings,
      comparisons,
      jobs,
      archive,
      replay,
    }[page]();
  }
  function openSource(index) {
    const c = data.companies[state.company],
      r = c.rows[index];
    const raw = {
      end: "2025-12-31",
      ...(r.kind === "duration" ? { start: "2025-01-01" } : {}),
      val: r.before,
      accn: c.original,
      form: "10-K",
      unit: r.unit,
    };
    document.querySelector("#source-content").innerHTML =
      `<div class="drawer-head"><div><p class="eyebrow">ASSERTION PROVENANCE · SAMPLE</p><h2 id="source-title">${r.concept}</h2><span class="meta">${c.name}</span></div><button class="close-button" data-action="close-source" aria-label="Close source details">×</button></div><div class="drawer-body">${badge(r.status)}<p class="callout">${r.note}</p><dl class="definition"><dt>Original value</dt><dd>${money(r.before)} ${r.unit}</dd><dt>Amendment value</dt><dd>${money(r.after)} ${r.after === null ? "" : r.unit}</dd><dt>Concept</dt><dd class="mono">us-gaap:${r.key}</dd><dt>Reporting period</dt><dd>${r.period} · ${r.kind}</dd><dt>Original accession</dt><dd class="mono">${c.original}</dd><dt>Source event</dt><dd class="mono">demo-snapshot-original-${state.company + 1}</dd><dt>Content checksum</dt><dd class="mono">demo:7f4a…9c21 (illustrative)</dd><dt>Captured at</dt><dd>2026-09-16 12:04:16 UTC</dd><dt>Parser</dt><dd class="mono">sec-json-v1</dd><dt>JSON locator</dt><dd class="mono">/facts/us-gaap/${r.key}/units/USD/0</dd></dl><h3>Illustrative source observation</h3><pre>${esc(JSON.stringify(raw, null, 2))}</pre><div class="notice">This snippet and its identifiers are fictional. The displayed value is a string to preserve precision. Real archived SEC JSON retains the source's original numeric representation and exact bytes.</div></div>`;
    document.querySelector("#source-dialog").showModal();
  }
  function showTour() {
    const box = document.querySelector("#tour");
    box.hidden = state.tour < 0;
    if (state.tour < 0) return;
    const step = data.steps[state.tour];
    go(step.page);
    document.querySelector("#tour-position").textContent =
      `Step ${state.tour + 1} of ${data.steps.length}`;
    document.querySelector("#tour-title").textContent = step.title;
    document.querySelector("#tour-description").textContent = step.text;
    document.querySelector("#tour-back").disabled = state.tour === 0;
    document.querySelector("#tour-next").textContent =
      state.tour === data.steps.length - 1 ? "Finish walkthrough ✓" : "Next →";
  }
  document.addEventListener("click", (event) => {
    const target = event.target.closest(
      "[data-company],[data-fact],[data-job],[data-archive],[data-action]",
    );
    if (!target) return;
    if (target.dataset.company !== undefined) {
      state.company = Number(target.dataset.company);
      state.filter = "all";
      go("comparisons");
    }
    if (target.dataset.fact !== undefined)
      openSource(Number(target.dataset.fact));
    if (target.dataset.job !== undefined) {
      state.job = Number(target.dataset.job);
      render();
    }
    if (target.dataset.archive !== undefined) {
      state.company = Number(target.dataset.archive);
      openSource(0);
    }
    const action = target.dataset.action;
    if (Object.hasOwn(labels, action)) go(action);
    if (action === "dead-letter") {
      state.job = 3;
      go("jobs");
    }
    if (action === "close-source")
      document.querySelector("#source-dialog").close();
    if (action === "redrive") {
      const previous = state.jobs[state.job];
      if (previous.status !== "dead_letter") return;
      const id = `job-demo-${state.jobs.length + 1}`;
      state.jobs.push({
        id,
        company: previous.company,
        kind: previous.kind,
        status: "queued",
        attempts: 0,
        events: [
          `Demo · Created as a redrive of ${previous.id}`,
          "Demo · Queued; no real work is executed",
        ],
      });
      state.job = state.jobs.length - 1;
      render();
      toast(`Created ${id}; ${previous.id} remains in dead letter.`);
    }
    if (action === "replay-run" && !state.replayRunning) {
      state.replay = 0;
      state.replayRunning = true;
      render();
      replayTimer = setInterval(() => {
        state.replay++;
        if (state.replay === 4) {
          clearInterval(replayTimer);
          state.replayRunning = false;
          toast("Simulated replay verified. Active generation unchanged.");
        }
        if (route() === "replay") render();
      }, 850);
    }
    if (action === "export") {
      const blob = new Blob(
        [
          JSON.stringify(
            { demo: true, fictional: true, ...data.companies[state.company] },
            null,
            2,
          ),
        ],
        { type: "application/json" },
      );
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "secrecon-fictional-comparison.json";
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      toast("Exported a clearly labeled fictional comparison.");
    }
  });
  document.addEventListener("keydown", (event) => {
    if (
      (event.key === "Enter" || event.key === " ") &&
      event.target.matches('tr[role="button"]')
    ) {
      event.preventDefault();
      event.target.click();
    }
  });
  document.addEventListener("change", (event) => {
    if (event.target.id === "scenario") {
      state.company = Number(event.target.value);
      state.filter = "all";
      render();
    }
    if (event.target.id === "fact-filter") {
      state.filter = event.target.value;
      render();
    }
  });
  document.addEventListener("input", (event) => {
    if (event.target.id === "filing-search") {
      const position = event.target.selectionStart;
      state.search = event.target.value;
      render();
      const input = document.querySelector("#filing-search");
      input.focus();
      input.setSelectionRange(position, position);
    }
  });
  document.querySelector("#tour-start").onclick = () => {
    state.tour = 0;
    showTour();
  };
  document.querySelector("#tour-close").onclick = () => {
    state.tour = -1;
    showTour();
  };
  document.querySelector("#tour-back").onclick = () => {
    if (state.tour > 0) state.tour--;
    showTour();
  };
  document.querySelector("#tour-next").onclick = () => {
    state.tour++;
    if (state.tour === data.steps.length) state.tour = -1;
    showTour();
  };
  document.querySelector("#reset").onclick = () => {
    clearInterval(replayTimer);
    Object.assign(state, {
      company: 0,
      filter: "all",
      job: 0,
      jobs: structuredClone(data.jobs),
      search: "",
      tour: -1,
      replay: 0,
      replayRunning: false,
    });
    document.querySelector("#source-dialog").close();
    showTour();
    go("overview");
    render();
    toast("Demo reset. All sample state restored.");
  };
  window.addEventListener("hashchange", () => {
    render();
    content.focus({ preventScroll: true });
  });
  render();
})();
