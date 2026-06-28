// ─── Core elements ────────────────────────────────────────────────────────────
const form = document.querySelector("#jobForm");
const runButton = document.querySelector("#runButton");
const statusPill = document.querySelector("#statusPill");
const jobIdEl = document.querySelector("#jobId");
const candidateCountEl = document.querySelector("#candidateCount");
const reelCountEl = document.querySelector("#reelCount");
const shortCountEl = document.querySelector("#shortCount");
const logOutput = document.querySelector("#logOutput");
const results = document.querySelector("#results");
const sourceRadios = document.querySelectorAll("input[name='source_type']");
const refreshJobsButton = document.querySelector("#refreshJobs");
const recentJobs = document.querySelector("#recentJobs");
const numClipsInput = document.querySelector("#numClips");
const templateInputs = [...document.querySelectorAll("input[name='template_ids']")];
const selectedTemplateCount = document.querySelector("#selectedTemplateCount");
const renderEstimate = document.querySelector("#renderEstimate");
const renderEstimateMirror = document.querySelector("#renderEstimateMirror");
const recommendedTemplates = document.querySelector("#recommendedTemplates");
const selectAllTemplates = document.querySelector("#selectAllTemplates");

let pollTimer = null;

// ─── Section navigation ───────────────────────────────────────────────────────
const SECTIONS = ["create", "calendar", "social", "insights", "compare"];
const SECTION_TITLES = {
  create: "AI clipping",
  calendar: "Content Calendar",
  social: "Social Connects",
  insights: "Post Insights",
  compare: "Post Comparison",
};

function showSection(name) {
  SECTIONS.forEach(s => {
    const el = document.getElementById("section-" + s);
    if (el) el.classList.toggle("hidden", s !== name);
  });
  document.querySelectorAll(".nav-link[data-section]").forEach(link => {
    link.classList.toggle("active", link.dataset.section === name);
  });
  const titleEl = document.getElementById("topbarTitle");
  if (titleEl) titleEl.textContent = SECTION_TITLES[name] || name;
  if (name === "calendar") initCalendar();
  if (name === "social") loadSocialConnections();
  if (name === "insights") loadPosts();
  if (name === "compare") loadCompareSelectors();
}

document.querySelectorAll(".nav-link[data-section]").forEach(link => {
  link.addEventListener("click", e => {
    const section = link.dataset.section;
    const hash = link.getAttribute("href") || "";
    const sectionHashes = ["#calendar","#social","#insights","#compare"];
    if (!sectionHashes.includes(hash)) {
      // In-page anchors on the Home/create view (Templates, Projects, Renders,
      // Run Log): show the create section, then smooth-scroll to the target.
      e.preventDefault();
      showSection("create");
      const targetId = hash.replace("#", "");
      const target = targetId ? document.getElementById(targetId) : null;
      if (target) {
        // Wait for the create section to become visible and reflow before
        // scrolling, otherwise the target's position isn't settled yet.
        setTimeout(() => target.scrollIntoView({ behavior: "smooth", block: "start" }), 60);
      } else {
        window.scrollTo({ top: 0, behavior: "smooth" });
      }
      // Reflect the active nav state.
      document.querySelectorAll(".nav-link").forEach(l => l.classList.remove("active"));
      link.classList.add("active");
      return;
    }
    e.preventDefault();
    showSection(section);
    history.pushState(null, "", hash);
  });
});

// ─── Utilities ────────────────────────────────────────────────────────────────
function setStatus(status) {
  const label = status ? status.charAt(0).toUpperCase() + status.slice(1) : "Idle";
  statusPill.textContent = label;
  statusPill.className = "status-pill " + (status || "");
}

function setEmpty(text) {
  results.innerHTML = "<div class=\"empty-state\">" + (text || "Ready") + "</div>";
}

function escapeHtml(value) {
  return String(value == null ? "" : value)
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#039;");
}

function formatTime(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(1) + "s" : "0.0s";
}

function formatSourceStamp(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "0:00.0";
  return Math.floor(n / 60) + ":" + (n % 60).toFixed(1).padStart(4, "0");
}

function fmtNum(n) {
  n = Number(n) || 0;
  if (n >= 1000000) return (n / 1000000).toFixed(1) + "M";
  if (n >= 1000) return (n / 1000).toFixed(1) + "K";
  return String(n);
}

// ─── Template summary ─────────────────────────────────────────────────────────
function updateTemplateSummary() {
  const selected = templateInputs.filter(i => i.checked).length;
  const reels = Math.max(1, Number.parseInt(numClipsInput ? numClipsInput.value : "1", 10) || 1);
  if (selectedTemplateCount) selectedTemplateCount.textContent = String(selected);
  if (renderEstimate) renderEstimate.textContent = String(reels * selected);
  if (renderEstimateMirror) renderEstimateMirror.textContent = String(reels * selected);
}

function chooseTemplates(ids) {
  const selected = new Set(ids);
  templateInputs.forEach(i => { i.checked = selected.has(i.value); });
  updateTemplateSummary();
}

function updateSourcePanels() {
  const checked = document.querySelector("input[name='source_type']:checked");
  if (!checked) return;
  const sel = checked.value;
  document.querySelectorAll("[data-source-panel]").forEach(p => {
    p.classList.toggle("hidden", p.dataset.sourcePanel !== sel);
  });
}

// ─── Job rendering ────────────────────────────────────────────────────────────
function renderResult(job) {
  const result = job.result || {};
  const shorts = result.shorts || [];
  const highlights = result.highlights || [];
  const plannedReels = result.reels || [];
  candidateCountEl.textContent = highlights.length;
  reelCountEl.textContent = plannedReels.length || new Set(shorts.map(s => s.reel_index)).size;
  shortCountEl.textContent = shorts.filter(s => s.clip_media_url).length;
  if (!shorts.length) { setEmpty(job.status === "failed" ? "Failed" : "Working"); return; }
  const resultJson = job.result_json_url
    ? "<a class=\"download-link\" href=\"" + job.result_json_url + "\" target=\"_blank\" rel=\"noreferrer\">JSON</a>" : "";
  results.innerHTML = "<div class=\"result-grid\">" + shorts.map(function(short, index) {
    const clipUrl = short.clip_media_url || short.clip_url;
    const posterUrl = short.poster_media_url || "";
    const editParts = short.edit_parts || [];
    const editSeq = editParts.length > 1 ? editParts.map(p => formatSourceStamp(p.start_time)).join(" → ") : "";
    return "<article class=\"result-card\">" +
      (!clipUrl
        ? "<div class=\"empty-state error-text\">Failed</div>"
        : "<video src=\"" + escapeHtml(clipUrl) + "\" " + (posterUrl ? "poster=\"" + escapeHtml(posterUrl) + "\" " : "") + "controls playsinline preload=\"metadata\"></video>") +
      "<div class=\"result-copy\">" +
        "<div class=\"result-meta\">" +
          "<span class=\"badge\">Reel " + escapeHtml(short.reel_index || index + 1) + "</span>" +
          "<span class=\"badge score\">" + escapeHtml(short.score != null ? short.score : "0") + "</span>" +
          "<span class=\"badge template\">" + escapeHtml(short.template_name || "Template") + "</span>" +
          (short.upscaled ? "<span class=\"badge quality\">" + escapeHtml(short.output_width + "x" + short.output_height) + "</span>" : "") +
          "<span class=\"badge\">" + formatTime(short.start_time) + " - " + formatTime(short.end_time) + "</span>" +
        "</div>" +
        "<h2>" + escapeHtml(short.title || "Untitled") + "</h2>" +
        (editSeq ? "<p class=\"edit-sequence\">" + escapeHtml(editSeq) + "</p>" : "") +
        "<p class=\"hook\">" + escapeHtml(short.hook_sentence || "") + "</p>" +
        "<p class=\"reason\">" + escapeHtml(short.virality_reason || short.error || "") + "</p>" +
        (clipUrl ? "<a class=\"download-link\" href=\"" + escapeHtml(clipUrl) + "\" download>Download</a>" : "") +
        (index === 0 ? resultJson : "") +
      "</div></article>";
  }).join("") + "</div>";
}

function renderJobShell(job) {
  setStatus(job.status);
  jobIdEl.textContent = job.id || "None";
  logOutput.textContent = (job.logs || []).join("\n") || "Waiting.";
  if (job.result) { renderResult(job); }
  else {
    candidateCountEl.textContent = "0";
    reelCountEl.textContent = "0";
    shortCountEl.textContent = "0";
    setEmpty(job.status === "failed" ? "Failed" : "No output");
  }
}

function renderRecentJobs(jobs) {
  if (!recentJobs) return;
  if (!jobs.length) {
    recentJobs.innerHTML = "<button class=\"recent-job\" type=\"button\"><span class=\"recent-thumb\">shorts//</span><span class=\"recent-copy\"><strong>No projects yet</strong><span>Generated reels appear here.</span></span></button>";
    return;
  }
  recentJobs.innerHTML = jobs.map(job =>
    "<button class=\"recent-job\" type=\"button\" data-job-id=\"" + escapeHtml(job.id) + "\">" +
    "<span class=\"recent-thumb\">" + (job.poster_media_url ? "<img src=\"" + escapeHtml(job.poster_media_url) + "\" alt=\"\" loading=\"lazy\">" : "shorts//") + "</span>" +
    "<span class=\"recent-copy\"><strong>" + escapeHtml(job.title || job.source_label || job.id) + "</strong>" +
    "<span>" + escapeHtml(job.status) + " · " + escapeHtml(job.reel_count || job.short_count || 0) + " reels · " + escapeHtml(job.short_count) + " renders</span></span></button>"
  ).join("");
}

async function loadRecentJobs() {
  if (!recentJobs) return;
  const r = await fetch("/api/jobs");
  const p = await r.json();
  if (!r.ok) throw new Error(p.error || "Could not load jobs");
  renderRecentJobs(p.jobs || []);
}

async function loadJob(jobId) {
  const r = await fetch("/api/jobs/" + encodeURIComponent(jobId));
  const job = await r.json();
  if (!r.ok) throw new Error(job.error || "Job not found");
  renderJobShell(job);
}

async function pollJob(statusUrl) {
  const r = await fetch(statusUrl);
  const job = await r.json();
  if (!r.ok) throw new Error(job.error || "Job not found");
  setStatus(job.status);
  jobIdEl.textContent = job.id || "None";
  logOutput.textContent = (job.logs || []).join("\n") || "Waiting.";
  if (job.status === "complete" || job.status === "failed") {
    runButton.disabled = false;
    clearInterval(pollTimer); pollTimer = null;
    loadRecentJobs().catch(() => {});
  }
  if (job.status === "failed") {
    candidateCountEl.textContent = "0";
    shortCountEl.textContent = "0";
    setEmpty("Failed");
    if (job.error) logOutput.textContent += "\n" + job.error;
    return;
  }
  if (job.result) renderResult(job);
}

// ─── Event listeners (create) ─────────────────────────────────────────────────
sourceRadios.forEach(function(r) { r.addEventListener("change", updateSourcePanels); });
templateInputs.forEach(function(i) { i.addEventListener("change", updateTemplateSummary); });
if (numClipsInput) numClipsInput.addEventListener("input", updateTemplateSummary);

if (recommendedTemplates) {
  recommendedTemplates.addEventListener("click", function() {
    chooseTemplates(["yellow-pop", "red-alert", "clean-authority", "fanpage-gold"]);
  });
}
if (selectAllTemplates) {
  selectAllTemplates.addEventListener("click", function() {
    chooseTemplates(templateInputs.map(function(i) { return i.value; }));
    if (numClipsInput) numClipsInput.value = "1";
    updateTemplateSummary();
  });
}
if (refreshJobsButton) {
  refreshJobsButton.addEventListener("click", function() {
    loadRecentJobs().catch(function(e) { if (logOutput) logOutput.textContent = e.message; });
  });
}
if (recentJobs) {
  recentJobs.addEventListener("click", function(e) {
    const btn = e.target.closest("[data-job-id]");
    if (!btn) return;
    clearInterval(pollTimer); pollTimer = null;
    runButton.disabled = false;
    loadJob(btn.dataset.jobId).catch(function(e) { if (logOutput) logOutput.textContent = e.message; });
  });
}

if (form) {
  form.addEventListener("submit", async function(e) {
    e.preventDefault();
    clearInterval(pollTimer); pollTimer = null;
    runButton.disabled = true;
    setStatus("queued");
    jobIdEl.textContent = "Queued";
    candidateCountEl.textContent = "0";
    reelCountEl.textContent = "0";
    shortCountEl.textContent = "0";
    logOutput.textContent = "Queued.";
    setEmpty("Running");
    try {
      if (!templateInputs.some(function(i) { return i.checked; })) chooseTemplates(["fanpage-gold"]);
      const data = new FormData(form);
      const r = await fetch("/jobs", { method: "POST", body: data });
      const p = await r.json();
      if (!r.ok) throw new Error(p.error || "Could not start job");
      jobIdEl.textContent = p.job_id;
      await pollJob(p.status_url);
      pollTimer = setInterval(function() {
        pollJob(p.status_url).catch(function(e) {
          clearInterval(pollTimer); pollTimer = null;
          runButton.disabled = false;
          setStatus("failed");
          logOutput.textContent = e.message;
          setEmpty("Failed");
        });
      }, 1600);
    } catch(e) {
      runButton.disabled = false;
      setStatus("failed");
      logOutput.textContent = e.message;
      setEmpty("Failed");
    }
  });
}

// ─── CALENDAR ─────────────────────────────────────────────────────────────────
let calYear, calMonth, calEvents = [];

async function initCalendar() {
  const now = new Date();
  if (!calYear) { calYear = now.getFullYear(); calMonth = now.getMonth(); }
  const r = await fetch("/api/calendar");
  const p = await r.json();
  calEvents = p.events || [];
  renderCalendar();
}

function renderCalendar() {
  const monthNames = ["January","February","March","April","May","June","July","August","September","October","November","December"];
  const label = document.getElementById("calMonthLabel");
  if (label) label.textContent = monthNames[calMonth] + " " + calYear;
  const grid = document.getElementById("calGrid");
  if (!grid) return;
  grid.innerHTML = "";
  const firstDay = new Date(calYear, calMonth, 1).getDay();
  const daysInMonth = new Date(calYear, calMonth + 1, 0).getDate();
  const today = new Date();
  for (let i = 0; i < firstDay; i++) {
    grid.insertAdjacentHTML("beforeend", "<div class=\"cal-cell empty\"></div>");
  }
  for (let d = 1; d <= daysInMonth; d++) {
    const dateStr = calYear + "-" + String(calMonth + 1).padStart(2,"0") + "-" + String(d).padStart(2,"0");
    const dayEvents = calEvents.filter(function(e) { return e.date === dateStr; });
    const isToday = today.getFullYear() === calYear && today.getMonth() === calMonth && today.getDate() === d;
    const dotsHtml = dayEvents.slice(0, 4).map(function(ev) {
      const cls = ev.type === "render" ? "render"
        : ev.type === "scheduled" ? "scheduled"
        : ev.verdict === "viral" ? "viral"
        : ev.verdict === "growing" ? "growing"
        : "underperforming";
      return "<span class=\"cal-dot " + cls + "\"></span>";
    }).join("");
    grid.insertAdjacentHTML("beforeend",
      "<div class=\"cal-cell" + (isToday ? " today" : "") + (dayEvents.length ? " has-events" : "") + "\" data-date=\"" + dateStr + "\" onclick=\"showCalEvents('" + dateStr + "')\">" +
      "<span class=\"cal-day\">" + d + "</span>" +
      "<div class=\"cal-dots\">" + dotsHtml + "</div></div>"
    );
  }
}

window.showCalEvents = function(dateStr) {
  const list = document.getElementById("calEventList");
  if (!list) return;
  const dayEvents = calEvents.filter(function(e) { return e.date === dateStr; });
  document.querySelectorAll(".cal-cell").forEach(function(c) { c.classList.remove("selected"); });
  const cell = document.querySelector(".cal-cell[data-date=\"" + dateStr + "\"]");
  if (cell) cell.classList.add("selected");
  if (!dayEvents.length) {
    list.innerHTML = "<p class=\"cal-event-empty\">No events on " + dateStr + "</p>";
    return;
  }
  list.innerHTML = "<div class=\"cal-event-date\">" + dateStr + "</div>" +
    dayEvents.map(function(ev) {
      if (ev.type === "render") {
        return "<div class=\"cal-event-item render\"><span class=\"cal-event-icon\">▶</span><div><strong>" + escapeHtml(ev.title) + "</strong><span>Render · " + escapeHtml(ev.status) + "</span></div></div>";
      }
      if (ev.type === "scheduled") {
        const plats = (ev.platforms || []).join(", ");
        const st = ev.status || "scheduled";
        return "<div class=\"cal-event-item scheduled\"><span class=\"cal-event-icon\">◷</span><div><strong>" + escapeHtml(ev.title) + "</strong><span>" + (ev.time ? escapeHtml(ev.time) + " · " : "") + escapeHtml(plats) + " · <em class=\"verdict-tag " + (st === 'failed' ? 'underperforming' : st === 'published' ? 'viral' : 'growing') + "\">" + escapeHtml(st) + "</em></span></div><button class=\"mini-del\" onclick=\"cancelScheduled('" + ev.id + "')\">✕</button></div>";
      }
      const vc = ev.verdict === "viral" ? "viral" : ev.verdict === "growing" ? "growing" : "underperforming";
      const icon = ev.platform === "youtube" ? "▶" : ev.platform === "instagram" ? "◆" : "●";
      return "<div class=\"cal-event-item " + vc + "\"><span class=\"cal-event-icon\">" + icon + "</span><div><strong>" + escapeHtml(ev.title) + "</strong><span>" + escapeHtml(ev.platform) + " · " + fmtNum(ev.views || 0) + " views · <em class=\"verdict-tag " + vc + "\">" + escapeHtml(ev.verdict) + "</em></span></div></div>";
    }).join("");
};

const calPrev = document.getElementById("calPrev");
const calNext = document.getElementById("calNext");
if (calPrev) calPrev.addEventListener("click", function() {
  calMonth--; if (calMonth < 0) { calMonth = 11; calYear--; } renderCalendar();
});
if (calNext) calNext.addEventListener("click", function() {
  calMonth++; if (calMonth > 11) { calMonth = 0; calYear++; } renderCalendar();
});

// ─── SCHEDULING ───────────────────────────────────────────────────────────────
const scheduleModal = document.getElementById("scheduleModal");
const scheduleBtn = document.getElementById("scheduleBtn");
const scheduleClose = document.getElementById("scheduleClose");
const schedSubmit = document.getElementById("schedSubmit");

async function openScheduleModal() {
  if (!scheduleModal) return;
  // Populate the render dropdown from recent jobs.
  const sel = document.getElementById("schedJob");
  if (sel) {
    try {
      const r = await fetch("/api/jobs");
      const p = await r.json();
      sel.innerHTML = "<option value=\"\">— pick a render —</option>" +
        (p.jobs || []).filter(function(j){ return j.status === "complete"; }).map(function(j) {
          return "<option value=\"" + escapeHtml(j.id) + "\">" + escapeHtml(j.title || j.source_label || j.id) + "</option>";
        }).join("");
    } catch (e) {}
  }
  scheduleModal.classList.remove("hidden");
}
if (scheduleBtn) scheduleBtn.addEventListener("click", openScheduleModal);
if (scheduleClose) scheduleClose.addEventListener("click", function() { scheduleModal.classList.add("hidden"); });
if (scheduleModal) scheduleModal.addEventListener("click", function(e) { if (e.target === scheduleModal) scheduleModal.classList.add("hidden"); });

if (schedSubmit) schedSubmit.addEventListener("click", async function() {
  const hint = document.getElementById("schedHint");
  const platforms = Array.from(document.querySelectorAll(".platform-picker input:checked")).map(function(c){ return c.value; });
  const whenLocal = document.getElementById("schedWhen").value;
  const payload = {
    job_id: document.getElementById("schedJob").value,
    short_index: 1,
    title: document.getElementById("schedTitle").value,
    caption: document.getElementById("schedCaption").value,
    platforms: platforms,
    publish_at: whenLocal ? new Date(whenLocal).toISOString() : "",
  };
  if (!platforms.length) { hint.textContent = "Pick at least one platform."; return; }
  if (!whenLocal) { hint.textContent = "Pick a publish date/time."; return; }
  schedSubmit.disabled = true; hint.textContent = "Scheduling…";
  const r = await fetch("/api/schedule", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
  });
  const data = await r.json();
  schedSubmit.disabled = false;
  if (data.ok) {
    hint.textContent = "";
    scheduleModal.classList.add("hidden");
    initCalendar();
  } else {
    hint.textContent = data.error || "Could not schedule.";
  }
});

window.cancelScheduled = async function(id) {
  await fetch("/api/schedule/" + encodeURIComponent(id), { method: "DELETE" });
  initCalendar();
};

// ─── SOCIAL CONNECTS ──────────────────────────────────────────────────────────
const PLATFORM_PREFIXES = { youtube: "yt", instagram: "ig", facebook: "fb" };

let socialState = {};

async function loadSocialConnections() {
  const r = await fetch("/api/social/connections");
  const data = await r.json();
  socialState = data;
  ["youtube", "instagram", "facebook"].forEach(function(p) {
    renderSocialCard(p, (data[p] || {}).connection, (data[p] || {}).configured);
  });
}

function renderSocialCard(platform, conn, configured) {
  const pfx = PLATFORM_PREFIXES[platform];
  const statusEl = document.getElementById(pfx + "-status");
  const badgeEl = document.getElementById(pfx + "-badge");
  const bodyEl = document.getElementById(pfx + "-body");
  const connectedEl = document.getElementById(pfx + "-connected");
  const handleDisp = document.getElementById(pfx + "-handle-display");
  const timeDisp = document.getElementById(pfx + "-time-display");
  const setupEl = document.getElementById(pfx + "-setup");
  const connectBtn = bodyEl ? bodyEl.querySelector(".connect-btn") : null;
  if (!statusEl) return;
  if (conn) {
    statusEl.textContent = "Connected";
    statusEl.className = "social-status connected";
    if (badgeEl) badgeEl.innerHTML = "<span class=\"connected-dot\"></span>";
    if (bodyEl) bodyEl.classList.add("hidden");
    if (connectedEl) connectedEl.classList.remove("hidden");
    if (handleDisp) handleDisp.textContent = conn.handle || platform;
    if (timeDisp) timeDisp.textContent = conn.connected_at ? "Connected " + new Date(conn.connected_at).toLocaleDateString() : "Connected";
  } else {
    statusEl.textContent = configured ? "Not connected" : "Setup required";
    statusEl.className = "social-status" + (configured ? "" : " setup");
    if (badgeEl) badgeEl.innerHTML = "";
    if (bodyEl) bodyEl.classList.remove("hidden");
    if (connectedEl) connectedEl.classList.add("hidden");
    if (setupEl) setupEl.classList.toggle("hidden", !!configured);
    if (connectBtn) connectBtn.disabled = !configured;
  }
}

window.connectSocial = async function(platform) {
  const r = await fetch("/api/social/connect", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ platform: platform }),
  });
  const data = await r.json();
  if (data.ok && data.auth_url) {
    // Hand off to the platform's real OAuth consent screen.
    window.location.href = data.auth_url;
  } else if (data.error === "not_configured") {
    alert(data.message || "This platform needs server-side API credentials. See SOCIAL_SETUP.md.");
  } else {
    alert(data.message || data.error || "Could not start sign-in.");
  }
};

window.disconnectSocial = async function(platform) {
  const r = await fetch("/api/social/disconnect", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ platform: platform }),
  });
  const data = await r.json();
  if (data.ok) loadSocialConnections();
};

// Surface OAuth callback results (e.g. #social?connected=youtube or ?error=...).
(function handleOAuthReturn() {
  const hash = window.location.hash || "";
  if (hash.indexOf("connected=") > -1) {
    const plat = hash.split("connected=")[1].split("&")[0];
    setTimeout(function() {
      showSection("social");
      loadSocialConnections();
    }, 50);
  } else if (hash.indexOf("error=") > -1) {
    const err = decodeURIComponent(hash.split("error=")[1].split("&")[0]);
    setTimeout(function() { showSection("social"); alert("Connection failed: " + err); }, 50);
  }
})();

// ─── INSIGHTS ─────────────────────────────────────────────────────────────────
let allPosts = [];

const addPostBtn = document.getElementById("addPostBtn");
if (addPostBtn) {
  addPostBtn.addEventListener("click", function() {
    const modal = document.getElementById("addPostModal");
    if (modal) modal.classList.remove("hidden");
    const dateEl = document.getElementById("ap-date");
    if (dateEl) dateEl.value = new Date().toISOString().slice(0, 10);
  });
}

window.closeAddPost = function() {
  const modal = document.getElementById("addPostModal");
  if (modal) modal.classList.add("hidden");
};

const syncBtn = document.getElementById("syncBtn");
if (syncBtn) {
  syncBtn.addEventListener("click", async function() {
    syncBtn.disabled = true;
    const original = syncBtn.textContent;
    syncBtn.textContent = "⟳ Syncing…";
    try {
      const r = await fetch("/api/analytics/sync", { method: "POST" });
      const data = await r.json();
      if (data.ok) {
        await loadPosts();
        syncBtn.textContent = data.updated ? "✓ Synced " + data.updated : "No connected posts";
        if (data.errors && data.errors.length) console.warn("Sync issues:", data.errors);
      } else {
        syncBtn.textContent = "Sync failed";
      }
    } catch (e) {
      syncBtn.textContent = "Sync failed";
    }
    setTimeout(function() { syncBtn.textContent = original; syncBtn.disabled = false; }, 2500);
  });
}

window.submitPost = async function() {
  const r = await fetch("/api/analytics/posts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      title: document.getElementById("ap-title").value,
      platform: document.getElementById("ap-platform").value,
      posted_at: document.getElementById("ap-date").value,
      template: document.getElementById("ap-template").value,
      views: document.getElementById("ap-views").value || 0,
      likes: document.getElementById("ap-likes").value || 0,
      comments: document.getElementById("ap-comments").value || 0,
      shares: document.getElementById("ap-shares").value || 0,
      notes: document.getElementById("ap-notes").value,
    }),
  });
  const data = await r.json();
  if (data.ok) {
    closeAddPost();
    loadPosts();
    ["ap-title","ap-views","ap-likes","ap-comments","ap-shares","ap-notes","ap-template"].forEach(function(id) {
      const el = document.getElementById(id);
      if (el) el.value = "";
    });
  }
};

async function loadPosts() {
  const r = await fetch("/api/analytics/posts");
  const data = await r.json();
  allPosts = data.posts || [];
  renderInsightsSummary();
  renderPostsList();
}

function renderInsightsSummary() {
  const el = document.getElementById("insightsSummary");
  if (!el) return;
  if (!allPosts.length) { el.innerHTML = ""; return; }
  const totalViews = allPosts.reduce(function(s, p) { return s + (p.views || 0); }, 0);
  const viral = allPosts.filter(function(p) { return (p.analysis && p.analysis.verdict) === "viral"; }).length;
  const avgEng = allPosts.reduce(function(s, p) { return s + ((p.analysis && p.analysis.engagement_rate) || 0); }, 0) / allPosts.length;
  const best = allPosts.reduce(function(a, b) { return (b.views || 0) > (a.views || 0) ? b : a; }, allPosts[0]);
  el.innerHTML =
    "<div class=\"insights-cards\">" +
      "<div class=\"insight-card\"><span class=\"insight-label\">Total Views</span><strong class=\"insight-value\">" + fmtNum(totalViews) + "</strong></div>" +
      "<div class=\"insight-card\"><span class=\"insight-label\">Posts Tracked</span><strong class=\"insight-value\">" + allPosts.length + "</strong></div>" +
      "<div class=\"insight-card viral\"><span class=\"insight-label\">Viral Posts</span><strong class=\"insight-value\">" + viral + "</strong></div>" +
      "<div class=\"insight-card\"><span class=\"insight-label\">Avg Engagement</span><strong class=\"insight-value\">" + avgEng.toFixed(1) + "%</strong></div>" +
    "</div>" +
    (best ? "<div class=\"best-post-banner\"><span class=\"best-label\">⭐ Best performing</span><strong>" + escapeHtml(best.title) + "</strong><span>" + fmtNum(best.views) + " views · " + best.platform + "</span></div>" : "");
}

function renderPostsList() {
  const el = document.getElementById("postsList");
  if (!el) return;
  if (!allPosts.length) {
    el.innerHTML = "<div class=\"posts-empty\">No posts yet. Click <strong>+ Add Post</strong> to track your first post.</div>";
    return;
  }
  el.innerHTML = allPosts.map(function(post) {
    const a = post.analysis || {};
    const vc = a.verdict || "underperforming";
    const score = a.virality_score || 0;
    const learn = (a.learnings || []).slice(0, 1)[0] || "";
    const flag = (a.flags || []).slice(0, 1)[0] || "";
    return "<div class=\"post-card\">" +
      "<div class=\"post-card-top\">" +
        "<div class=\"post-card-meta\">" +
          "<span class=\"platform-tag " + post.platform + "\">" + post.platform + "</span>" +
          "<span class=\"verdict-tag " + vc + "\">" + vc + "</span>" +
          "<span class=\"post-date\">" + (post.posted_at || "").slice(0,10) + "</span>" +
        "</div>" +
        "<button class=\"post-delete\" onclick=\"deletePost('" + post.id + "')\" title=\"Delete\">✕</button>" +
      "</div>" +
      "<h3 class=\"post-title\">" + escapeHtml(post.title) + "</h3>" +
      "<div class=\"post-stats\">" +
        "<div class=\"post-stat\"><span>Views</span><strong>" + fmtNum(post.views||0) + "</strong></div>" +
        "<div class=\"post-stat\"><span>Likes</span><strong>" + fmtNum(post.likes||0) + "</strong></div>" +
        "<div class=\"post-stat\"><span>Comments</span><strong>" + fmtNum(post.comments||0) + "</strong></div>" +
        "<div class=\"post-stat\"><span>Shares</span><strong>" + fmtNum(post.shares||0) + "</strong></div>" +
        "<div class=\"post-stat\"><span>Engagement</span><strong>" + (a.engagement_rate||0) + "%</strong></div>" +
      "</div>" +
      "<div class=\"virality-bar-wrap\">" +
        "<span class=\"virality-label\">Virality Score</span>" +
        "<div class=\"virality-bar\"><div class=\"virality-fill " + vc + "\" style=\"width:" + score + "%\"></div></div>" +
        "<span class=\"virality-score\">" + score + "/100</span>" +
      "</div>" +
      (learn ? "<div class=\"post-insight learning\">✦ " + escapeHtml(learn) + "</div>" : "") +
      (flag ? "<div class=\"post-insight flag\">⚠ " + escapeHtml(flag) + "</div>" : "") +
    "</div>";
  }).join("");
}

window.deletePost = async function(id) {
  await fetch("/api/analytics/posts/" + id, { method: "DELETE" });
  loadPosts();
};

// ─── COMPARE ──────────────────────────────────────────────────────────────────
function loadCompareSelectors() {
  const selA = document.getElementById("compareA");
  const selB = document.getElementById("compareB");
  if (!selA || !selB) return;
  const opts = allPosts.length
    ? allPosts.map(function(p) { return "<option value=\"" + p.id + "\">" + escapeHtml(p.title) + " (" + fmtNum(p.views||0) + " views)</option>"; }).join("")
    : "<option value=\"\">No posts yet</option>";
  selA.innerHTML = opts;
  selB.innerHTML = opts;
  if (allPosts.length >= 2) selB.selectedIndex = 1;
  const empty = document.getElementById("compareEmpty");
  const result = document.getElementById("compareResult");
  if (empty) empty.classList.toggle("hidden", allPosts.length >= 2);
  if (result) result.classList.add("hidden");
}

const runCompareBtn = document.getElementById("runCompare");
if (runCompareBtn) {
  runCompareBtn.addEventListener("click", async function() {
    const a = document.getElementById("compareA").value;
    const b = document.getElementById("compareB").value;
    if (!a || !b || a === b) return;
    const r = await fetch("/api/analytics/compare?a=" + a + "&b=" + b);
    const data = await r.json();
    if (!r.ok) return;
    renderCompare(data);
  });
}

function renderCompare(data) {
  const result = document.getElementById("compareResult");
  const empty = document.getElementById("compareEmpty");
  if (result) result.classList.remove("hidden");
  if (empty) empty.classList.add("hidden");
  renderCompareCol("compareColA", data.a, data.analysis_a);
  renderCompareCol("compareColB", data.b, data.analysis_b);

  const winner = (data.a.views||0) >= (data.b.views||0) ? data.a : data.b;
  const loser  = winner === data.a ? data.b : data.a;
  const winAnalysis = winner === data.a ? data.analysis_a : data.analysis_b;
  const loseAnalysis = winner === data.a ? data.analysis_b : data.analysis_a;

  const learnings = (winAnalysis.learnings||[]).map(function(l) { return "<li class=\"learning-item\">✦ " + escapeHtml(l) + "</li>"; }).join("");
  const flags = (loseAnalysis.flags||[]).map(function(f) { return "<li class=\"flag-item\">⚠ " + escapeHtml(f) + "</li>"; }).join("");
  const skills = generateSkillUpdates(data.a, data.b, winAnalysis, loseAnalysis);

  const learningsEl = document.getElementById("compareLearnings");
  if (learningsEl) {
    learningsEl.innerHTML =
      "<div class=\"learnings-block\">" +
        "<div class=\"learnings-section\">" +
          "<div class=\"learnings-header viral\">" +
            "<strong>🚀 Why &quot;" + escapeHtml(winner.title) + "&quot; performed better</strong>" +
            "<span>" + fmtNum(winner.views||0) + " views · Virality " + winAnalysis.virality_score + "/100</span>" +
          "</div>" +
          "<ul class=\"learnings-list\">" + (learnings || "<li>Not enough data</li>") + "</ul>" +
        "</div>" +
        "<div class=\"learnings-section\">" +
          "<div class=\"learnings-header underperforming\">" +
            "<strong>🔍 What went wrong with &quot;" + escapeHtml(loser.title) + "&quot;</strong>" +
            "<span>" + fmtNum(loser.views||0) + " views · Virality " + loseAnalysis.virality_score + "/100</span>" +
          "</div>" +
          "<ul class=\"learnings-list\">" + (flags || "<li>No specific issues detected</li>") + "</ul>" +
        "</div>" +
        "<div class=\"skill-update-box\">" +
          "<div class=\"skill-title\">📚 Skills updated from this comparison</div>" +
          "<div class=\"skill-items\">" + skills.map(function(s) {
            return "<div class=\"skill-item\"><span class=\"skill-icon\">◎</span>" + escapeHtml(s) + "</div>";
          }).join("") + "</div>" +
        "</div>" +
      "</div>";
  }
}

function renderCompareCol(colId, post, analysis) {
  const el = document.getElementById(colId);
  if (!el) return;
  const vc = analysis.verdict || "underperforming";
  el.innerHTML =
    "<div class=\"compare-card " + vc + "\">" +
      "<div class=\"compare-card-top\">" +
        "<span class=\"platform-tag " + post.platform + "\">" + post.platform + "</span>" +
        "<span class=\"verdict-tag " + vc + "\">" + vc + "</span>" +
      "</div>" +
      "<h3 class=\"compare-title\">" + escapeHtml(post.title) + "</h3>" +
      "<div class=\"compare-stats\">" +
        "<div class=\"cstat\"><span>Views</span><strong>" + fmtNum(post.views||0) + "</strong></div>" +
        "<div class=\"cstat\"><span>Likes</span><strong>" + fmtNum(post.likes||0) + "</strong></div>" +
        "<div class=\"cstat\"><span>Comments</span><strong>" + fmtNum(post.comments||0) + "</strong></div>" +
        "<div class=\"cstat\"><span>Shares</span><strong>" + fmtNum(post.shares||0) + "</strong></div>" +
      "</div>" +
      "<div class=\"virality-bar-wrap\">" +
        "<span class=\"virality-label\">Virality Score</span>" +
        "<div class=\"virality-bar\"><div class=\"virality-fill " + vc + "\" style=\"width:" + analysis.virality_score + "%\"></div></div>" +
        "<span class=\"virality-score\">" + analysis.virality_score + "/100</span>" +
      "</div>" +
      "<div class=\"cstat-eng\"><span>Engagement Rate</span><strong>" + analysis.engagement_rate + "%</strong></div>" +
    "</div>";
}

function generateSkillUpdates(postA, postB, analysisA, analysisB) {
  const skills = [];
  const views = Math.max(postA.views||0, postB.views||0);
  if (views >= 100000) skills.push("Hook quality matters most — viral content hooks in the first 2 seconds.");
  if (views >= 10000) skills.push("Trending audio correlation: posts with viral sounds outperform 3x on average.");
  const maxEng = Math.max(analysisA.engagement_rate||0, analysisB.engagement_rate||0);
  if (maxEng >= 5) skills.push("High engagement (" + maxEng + "%) tied to strong CTA or question in caption.");
  const maxShares = Math.max(postA.shares||0, postB.shares||0);
  if (maxShares > 0) skills.push("Identity-shareable content: viewers share when they see themselves in the story.");
  if (postA.template || postB.template) {
    skills.push("Template comparison: \"" + (postA.template||"default") + "\" vs \"" + (postB.template||"default") + "\" — pick the one with higher engagement.");
  }
  if (!skills.length) skills.push("Add more posts with real metrics to surface deeper learning patterns.");
  return skills;
}

// ─── Init ─────────────────────────────────────────────────────────────────────
updateSourcePanels();
updateTemplateSummary();
setEmpty();
setStatus("");
loadRecentJobs().catch(function() {});
loadPosts();

if ('serviceWorker' in navigator) {
  window.addEventListener('load', function() {
    navigator.serviceWorker.register('/sw.js')
      .then(function(reg) { console.log('[SW] Registered', reg.scope); })
      .catch(function(err) { console.error('[SW] Failed', err); });
  });
}
