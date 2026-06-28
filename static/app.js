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

function setStatus(status) {
  const label = status ? status.charAt(0).toUpperCase() + status.slice(1) : "Idle";
  statusPill.textContent = label;
  statusPill.className = `status-pill ${status || ""}`.trim();
}

function setEmpty(text = "Ready") {
  results.innerHTML = `<div class="empty-state">${text}</div>`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatTime(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "0.0s";
  return `${number.toFixed(1)}s`;
}

function formatSourceStamp(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "0:00.0";
  const minutes = Math.floor(number / 60);
  const seconds = (number % 60).toFixed(1).padStart(4, "0");
  return `${minutes}:${seconds}`;
}

function updateTemplateSummary() {
  const selected = templateInputs.filter((input) => input.checked).length;
  const reels = Math.max(1, Number.parseInt(numClipsInput?.value || "1", 10) || 1);
  selectedTemplateCount.textContent = String(selected);
  renderEstimate.textContent = String(reels * selected);
  renderEstimateMirror.textContent = String(reels * selected);
}

function chooseTemplates(ids) {
  const selected = new Set(ids);
  templateInputs.forEach((input) => {
    input.checked = selected.has(input.value);
  });
  updateTemplateSummary();
}

function activeSourceType() {
  return document.querySelector("input[name='source_type']:checked").value;
}

function updateSourcePanels() {
  const selected = activeSourceType();
  document.querySelectorAll("[data-source-panel]").forEach((panel) => {
    panel.classList.toggle("hidden", panel.dataset.sourcePanel !== selected);
  });
}

function renderResult(job) {
  const result = job.result;
  const shorts = result?.shorts || [];
  const highlights = result?.highlights || [];
  const plannedReels = result?.reels || [];

  candidateCountEl.textContent = highlights.length;
  reelCountEl.textContent = plannedReels.length || new Set(shorts.map((short) => short.reel_index)).size;
  shortCountEl.textContent = shorts.filter((short) => short.clip_media_url).length;

  if (!shorts.length) {
    setEmpty(job.status === "failed" ? "Failed" : "Working");
    return;
  }

  const resultJson = job.result_json_url
    ? `<a class="download-link" href="${job.result_json_url}" target="_blank" rel="noreferrer">JSON</a>`
    : "";

  results.innerHTML = `
    <div class="result-grid">
      ${shorts.map((short, index) => {
        const clipUrl = short.clip_media_url || short.clip_url;
        const posterUrl = short.poster_media_url || "";
        const failed = !clipUrl;
        const editParts = short.edit_parts || [];
        const editSequence = editParts.length > 1
          ? editParts.map((part) => formatSourceStamp(part.start_time)).join(" → ")
          : "";
        return `
          <article class="result-card">
            ${failed
              ? `<div class="empty-state error-text">Failed</div>`
              : `<video src="${escapeHtml(clipUrl)}" ${posterUrl ? `poster="${escapeHtml(posterUrl)}"` : ""} controls playsinline preload="metadata"></video>`}
            <div class="result-copy">
              <div class="result-meta">
                <span class="badge">Reel ${escapeHtml(short.reel_index || index + 1)}</span>
                <span class="badge score">${escapeHtml(short.score ?? "0")}</span>
                <span class="badge template">${escapeHtml(short.template_name || "Template")}</span>
                ${short.upscaled ? `<span class="badge quality">${escapeHtml(`${short.output_width}x${short.output_height}`)}</span>` : ""}
                <span class="badge">${formatTime(short.start_time)} - ${formatTime(short.end_time)}</span>
              </div>
              <h2>${escapeHtml(short.title || "Untitled")}</h2>
              ${editSequence ? `<p class="edit-sequence">${escapeHtml(editSequence)}</p>` : ""}
              <p class="hook">${escapeHtml(short.hook_sentence || "")}</p>
              <p class="reason">${escapeHtml(short.virality_reason || short.error || "")}</p>
              ${clipUrl ? `<a class="download-link" href="${escapeHtml(clipUrl)}" download>Download</a>` : ""}
              ${index === 0 ? resultJson : ""}
            </div>
          </article>
        `;
      }).join("")}
    </div>
  `;
}

function renderJobShell(job) {
  setStatus(job.status);
  jobIdEl.textContent = job.id || "None";
  logOutput.textContent = (job.logs || []).join("\n") || "Waiting.";
  if (job.result) {
    renderResult(job);
  } else {
    candidateCountEl.textContent = "0";
    reelCountEl.textContent = "0";
    shortCountEl.textContent = "0";
    setEmpty(job.status === "failed" ? "Failed" : "No output");
  }
}

function renderRecentJobs(jobs) {
  if (!recentJobs) return;
  if (!jobs.length) {
    recentJobs.innerHTML = `
      <button class="recent-job" type="button">
        <span class="recent-thumb">shorts//</span>
        <span class="recent-copy"><strong>No projects yet</strong><span>Your generated reels will appear here.</span></span>
      </button>`;
    return;
  }
  recentJobs.innerHTML = jobs.map((job) => `
    <button class="recent-job" type="button" data-job-id="${escapeHtml(job.id)}">
      <span class="recent-thumb">
        ${job.poster_media_url
          ? `<img src="${escapeHtml(job.poster_media_url)}" alt="" loading="lazy">`
          : "shorts//"}
      </span>
      <span class="recent-copy">
        <strong>${escapeHtml(job.title || job.source_label || job.id)}</strong>
        <span>${escapeHtml(job.status)} · ${escapeHtml(job.reel_count || job.short_count || 0)} reels · ${escapeHtml(job.short_count)} renders</span>
      </span>
    </button>
  `).join("");
}

async function loadRecentJobs() {
  if (!recentJobs) return;
  const response = await fetch("/api/jobs");
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Could not load jobs");
  renderRecentJobs(payload.jobs || []);
}

async function loadJob(jobId) {
  const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`);
  const job = await response.json();
  if (!response.ok) throw new Error(job.error || "Job not found");
  renderJobShell(job);
}

async function pollJob(statusUrl) {
  const response = await fetch(statusUrl);
  const job = await response.json();
  if (!response.ok) throw new Error(job.error || "Job not found");

  setStatus(job.status);
  jobIdEl.textContent = job.id || "None";
  logOutput.textContent = (job.logs || []).join("\n") || "Waiting.";

  if (job.status === "complete" || job.status === "failed") {
    runButton.disabled = false;
    clearInterval(pollTimer);
    pollTimer = null;
    loadRecentJobs().catch(() => {});
  }

  if (job.status === "failed") {
    candidateCountEl.textContent = "0";
    shortCountEl.textContent = "0";
    setEmpty("Failed");
    if (job.error) {
      logOutput.textContent = `${logOutput.textContent}\n${job.error}`;
    }
    return;
  }

  if (job.result) {
    renderResult(job);
  }
}

sourceRadios.forEach((radio) => radio.addEventListener("change", updateSourcePanels));
templateInputs.forEach((input) => input.addEventListener("change", updateTemplateSummary));
numClipsInput?.addEventListener("input", updateTemplateSummary);
recommendedTemplates?.addEventListener("click", () => {
  chooseTemplates(["yellow-pop", "red-alert", "clean-authority", "fanpage-gold"]);
});
selectAllTemplates?.addEventListener("click", () => {
  chooseTemplates(templateInputs.map((input) => input.value));
  numClipsInput.value = "1";
  updateTemplateSummary();
});
refreshJobsButton?.addEventListener("click", () => {
  loadRecentJobs().catch((error) => {
    logOutput.textContent = error.message;
  });
});
recentJobs?.addEventListener("click", (event) => {
  const button = event.target.closest("[data-job-id]");
  if (!button) return;
  clearInterval(pollTimer);
  pollTimer = null;
  runButton.disabled = false;
  loadJob(button.dataset.jobId).catch((error) => {
    logOutput.textContent = error.message;
  });
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearInterval(pollTimer);
  pollTimer = null;

  runButton.disabled = true;
  setStatus("queued");
  jobIdEl.textContent = "Queued";
  candidateCountEl.textContent = "0";
  reelCountEl.textContent = "0";
  shortCountEl.textContent = "0";
  logOutput.textContent = "Queued.";
  setEmpty("Running");

  try {
    if (!templateInputs.some((input) => input.checked)) {
      chooseTemplates(["fanpage-gold"]);
    }
    const data = new FormData(form);
    const response = await fetch("/jobs", { method: "POST", body: data });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Could not start job");

    jobIdEl.textContent = payload.job_id;
    await pollJob(payload.status_url);
    pollTimer = setInterval(() => {
      pollJob(payload.status_url).catch((error) => {
        clearInterval(pollTimer);
        pollTimer = null;
        runButton.disabled = false;
        setStatus("failed");
        logOutput.textContent = error.message;
        setEmpty("Failed");
      });
    }, 1600);
  } catch (error) {
    runButton.disabled = false;
    setStatus("failed");
    logOutput.textContent = error.message;
    setEmpty("Failed");
  }
});

updateSourcePanels();
updateTemplateSummary();
setEmpty();
loadRecentJobs().catch(() => {});

// Register Service Worker for PWA
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js')
      .then((reg) => console.log('[Service Worker] Registered successfully with scope:', reg.scope))
      .catch((err) => console.error('[Service Worker] Registration failed:', err));
  });
}
