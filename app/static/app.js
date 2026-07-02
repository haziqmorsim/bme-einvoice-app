"use strict";

const $ = (sel) => document.querySelector(sel);
let selectedFiles = [];
let jobId = null;
let pollTimer = null;

// ----- Health check ------------------------------------------------------- //
fetch("/api/health").then((r) => r.json()).then((h) => {
  const el = $("#health");
  const label = `${h.backend || "?"}${h.model ? " · " + h.model : ""}`;
  if (h.ready) {
    el.textContent = "● " + label;
    el.className = "pill pill-ok";
    el.title = "Extraction backend ready";
  } else {
    el.textContent = "⚠ " + label;
    el.className = "pill pill-bad";
    el.title = h.message || "Backend not ready";
  }
}).catch(() => {
  $("#health").textContent = "offline";
  $("#health").className = "pill pill-bad";
});

// ----- File selection ----------------------------------------------------- //
const dropzone = $("#dropzone");
const fileInput = $("#fileInput");

$("#browseBtn").addEventListener("click", (e) => { e.stopPropagation(); fileInput.click(); });
dropzone.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", () => addFiles(fileInput.files));

["dragover", "dragenter"].forEach((ev) =>
  dropzone.addEventListener(ev, (e) => { e.preventDefault(); dropzone.classList.add("drag"); }));
["dragleave", "drop"].forEach((ev) =>
  dropzone.addEventListener(ev, (e) => { e.preventDefault(); dropzone.classList.remove("drag"); }));
dropzone.addEventListener("drop", (e) => addFiles(e.dataTransfer.files));

function addFiles(fileList) {
  for (const f of fileList) {
    if (f.type === "application/pdf" || f.name.toLowerCase().endsWith(".pdf")) {
      if (!selectedFiles.some((x) => x.name === f.name && x.size === f.size)) {
        selectedFiles.push(f);
      }
    }
  }
  renderFileList();
}

function renderFileList() {
  const ul = $("#fileList");
  ul.innerHTML = "";
  selectedFiles.forEach((f, i) => {
    const li = document.createElement("li");
    const kb = (f.size / 1024 / 1024).toFixed(1);
    li.innerHTML = `<span>📄 ${escapeHtml(f.name)} <small style="color:#6b7688">(${kb} MB)</small></span>`;
    const rm = document.createElement("button");
    rm.className = "rm"; rm.textContent = "✕"; rm.title = "Remove";
    rm.onclick = () => { selectedFiles.splice(i, 1); renderFileList(); };
    li.appendChild(rm);
    ul.appendChild(li);
  });
  $("#runBtn").disabled = selectedFiles.length === 0;
}

// ----- Run ---------------------------------------------------------------- //
$("#runBtn").addEventListener("click", startJob);

async function startJob() {
  const fd = new FormData();
  selectedFiles.forEach((f) => fd.append("files", f));

  show("#progressCard"); hide("#uploadCard"); hide("#resultCard");
  setProgress(0, "Uploading…");

  let res;
  try {
    res = await fetch("/api/upload", { method: "POST", body: fd });
  } catch {
    return fail("Could not reach the server.");
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    return fail(err.detail || "Upload failed.");
  }
  jobId = (await res.json()).job_id;
  pollTimer = setInterval(poll, 1500);
  poll();
}

async function poll() {
  if (!jobId) return;
  let s;
  try { s = await (await fetch(`/api/status/${jobId}`)).json(); }
  catch { return; }

  if (s.status === "processing" || s.status === "queued") {
    const pct = s.total_pages ? Math.round((s.done_pages / s.total_pages) * 100) : 5;
    setProgress(
      Math.max(pct, 5),
      s.total_pages
        ? `Reading page ${s.done_pages} of ${s.total_pages} — ${s.rows.length} invoice(s) found`
        : "Rendering & running OCR…"
    );
  } else if (s.status === "done") {
    clearInterval(pollTimer);
    setProgress(100, "Done");
    renderResults(s);
  } else if (s.status === "error") {
    clearInterval(pollTimer);
    fail(s.message || "Processing failed.");
  }
}

function renderResults(s) {
  hide("#progressCard"); show("#resultCard");
  $("#resultMsg").textContent = s.message;

  const w = $("#warnings");
  if (s.warnings && s.warnings.length) {
    w.innerHTML = "<strong>Some pages had issues:</strong><ul>" +
      s.warnings.map((x) => `<li>${escapeHtml(x)}</li>`).join("") + "</ul>";
    show("#warnings");
  } else { hide("#warnings"); }

  const tbody = $("#resultTable tbody");
  tbody.innerHTML = "";
  s.rows.forEach((r) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${escapeHtml(r.project_no)}</td>
      <td>${escapeHtml(r.vendor)}</td>
      <td>${escapeHtml(r.invoice_no)}</td>
      <td>${escapeHtml(r.pod)}</td>
      <td>${escapeHtml(r.type)}</td>
      <td class="num">${money(r.freight)}</td>
      <td class="num">${money(r.local_charges)}</td>
      <td class="num">${money(r.port_storage)}</td>
      <td class="num">${money(r.transport_charges)}</td>
      <td class="num">${money(r.reimbursement)}</td>
      <td class="num"><strong>${money(r.total)}</strong></td>
      `;
    tbody.appendChild(tr);
  });
}

$("#downloadBtn").addEventListener("click", () => {
  if (jobId) window.location = `/api/download/${jobId}`;
});
$("#resetBtn").addEventListener("click", () => {
  selectedFiles = []; jobId = null; renderFileList();
  show("#uploadCard"); hide("#progressCard"); hide("#resultCard");
});

// ----- Helpers ------------------------------------------------------------ //
function setProgress(pct, text) {
  $("#progressBar").style.width = pct + "%";
  $("#progressText").textContent = text;
}
function fail(msg) {
  hide("#progressCard"); show("#uploadCard");
  alert(msg);
}
function show(s) { $(s).classList.remove("hidden"); }
function hide(s) { $(s).classList.add("hidden"); }
function money(n) {
  return (n || 0).toLocaleString("en-MY", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function shortName(n) { return n.length > 18 ? n.slice(0, 15) + "…" : n; }
function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
