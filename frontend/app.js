/* app.js — wires the Stripe Docs Assistant UI to the Flask API.
 *
 * Served same-origin by Flask, so API_BASE is "" by default. Override with
 *   ?api=http://localhost:5055   for opening the file against a remote API.
 */
"use strict";

const API_BASE = new URLSearchParams(location.search).get("api") || "";
const api = (p) => API_BASE + p;

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const STATES = ["empty", "loading", "answer", "nomatch", "error"];
let lastQuestion = "";       // for retry
let corpusCount = 0;         // links loaded (from /status)

// --------------------------------------------------------------- helpers ----
function showState(name) {
  STATES.forEach((s) => $("#state-" + s).classList.toggle("is-active", s === name));
  $("#content").scrollTop = 0;
}

function setQuestionText(q) {
  $$(".question-text").forEach((el) => (el.textContent = q));
  $("#breadcrumb").textContent = q.length > 48 ? q.slice(0, 48) + "…" : q;
}

// "https://docs.stripe.com/billing/subscriptions/cancel.md" -> "docs.stripe.com / billing / subscriptions / cancel"
function prettyPath(url) {
  try {
    const u = new URL(url);
    const path = u.pathname.replace(/\.md$/, "").replace(/^\/+|\/+$/g, "");
    return [u.host, ...path.split("/").filter(Boolean)].join(" / ");
  } catch {
    return url;
  }
}

// content = "Anchor title. Trailing description." -> {title, desc}
function splitContent(content) {
  const s = (content || "").trim();
  const i = s.indexOf(". ");
  if (i === -1) return { title: s.replace(/\.$/, ""), desc: "" };
  return { title: s.slice(0, i), desc: s.slice(i + 2).trim() };
}

function escapeHtml(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

// Render the model's plain-text answer into paragraphs.
function renderAnswer(text) {
  const paras = (text || "").trim().split(/\n\s*\n/);
  $("#answer-text").innerHTML = paras
    .map((p) => `<p style="margin:0 0 14px">${escapeHtml(p).replace(/\n/g, "<br>")}</p>`)
    .join("") || "<p style='margin:0'></p>";
}

// --------------------------------------------------------------- network ----
async function jsonOrThrow(resp) {
  let body = null;
  try { body = await resp.json(); } catch { /* ignore */ }
  if (!resp.ok) {
    const msg = (body && body.error) || `Request failed (HTTP ${resp.status})`;
    const err = new Error(msg);
    err.status = resp.status;
    throw err;
  }
  return body;
}

// --------------------------------------------------------------- actions ----
async function ask(question) {
  question = (question || "").trim();
  if (!question) return;
  lastQuestion = question;

  setQuestionText(question);
  $("#load-step-count").textContent = corpusCount
    ? `Searched ${corpusCount} Stripe documentation links`
    : "Searching the Stripe documentation";
  showState("loading");

  try {
    const data = await jsonOrThrow(
      await fetch(api("/ask"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question }),
      })
    );

    const links = data.links || [];
    if (!data.top_url || links.length === 0) {
      showState("nomatch");
    } else {
      populateAnswer(question, data.answer, links[0], data.retrieved_via, links.length);
      showState("answer");
    }
  } catch (e) {
    showError(e);
  } finally {
    loadHistory(); // refresh sidebar regardless
  }
}

function populateAnswer(question, answerText, top, via, nLinks) {
  setQuestionText(question);
  const { title, desc } = splitContent(top.content);
  $("#source-title").textContent = title || top.section || "Stripe documentation";
  $("#source-desc").textContent = desc;
  $("#source-path").textContent = prettyPath(top.url);
  $("#source-url").textContent = top.url;
  $("#source-link").href = top.url;
  $("#match-score").textContent = via ? `matched via ${via}` : "";
  $("#grounded-note").textContent =
    `Grounded in ${nLinks} official Stripe source${nLinks === 1 ? "" : "s"} · no outside knowledge used`;
  renderAnswer(answerText);
}

function showError(e) {
  $("#error-message").textContent =
    e.message || "Something went wrong reaching the assistant.";
  $("#error-code").textContent = e.status ? `HTTP ${e.status}` : "NETWORK_ERROR";
  showState("error");
}

// ------------------------------------------------------------- sidebar ------
async function loadHistory() {
  let items = [];
  try {
    const data = await jsonOrThrow(await fetch(api("/history?limit=20")));
    items = (data && data.history) || [];
  } catch { /* leave empty */ }

  const list = $("#history-list");
  list.innerHTML = "";
  $("#history-empty").style.display = items.length ? "none" : "block";

  for (const h of items) {
    const li = document.createElement("li");
    li.className = "history-item";
    li.textContent = h.question;
    li.title = h.question;
    li.style.cssText =
      "padding:9px 10px;border-radius:8px;cursor:pointer;font-size:12.5px;color:#5b6573;line-height:1.35;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;border:1px solid transparent";
    li.addEventListener("click", () => renderFromHistory(h, li));
    list.appendChild(li);
  }
}

// Show a stored Q&A without re-querying the model.
function renderFromHistory(h, li) {
  $$(".history-item").forEach((el) => el.classList.remove("is-current"));
  if (li) li.classList.add("is-current");

  lastQuestion = h.question;
  if (h.top_url) {
    populateAnswer(
      h.question,
      h.answer,
      { url: h.top_url, content: "", section: "" },
      h.retrieved_via,
      1
    );
    showState("answer");
  } else {
    setQuestionText(h.question);
    showState("nomatch");
  }
}

async function refreshStatus() {
  const footer = $("#status-footer");
  try {
    const s = await jsonOrThrow(await fetch(api("/status")));
    corpusCount = s.docs_lines || 0;
    if (corpusCount > 0) {
      footer.innerHTML =
        `<span style="width:7px;height:7px;border-radius:50%;background:#11a06a;box-shadow:0 0 8px rgba(17,160,106,.5)"></span>` +
        `${corpusCount} docs loaded`;
    } else {
      // No corpus yet — offer a one-click load (the only way to populate the DB).
      footer.innerHTML =
        `<button class="load-btn" style="width:100%;display:flex;align-items:center;justify-content:center;gap:7px;padding:8px 10px;border-radius:8px;border:1px solid rgba(216,150,74,.4);background:rgba(216,150,74,.1);color:#b4751f;font-family:'IBM Plex Mono',monospace;font-size:11px;font-weight:600;cursor:pointer">Load Stripe docs</button>`;
      footer.querySelector(".load-btn").addEventListener("click", loadDocs);
    }
  } catch {
    footer.innerHTML =
      `<span style="width:7px;height:7px;border-radius:50%;background:#d84a4a"></span>API offline`;
  }
}

async function loadDocs() {
  const footer = $("#status-footer");
  footer.innerHTML =
    `<span style="width:14px;height:14px;border-radius:50%;border:1.5px solid #11a06a;border-top-color:transparent;animation:spin .7s linear infinite"></span>` +
    `<span style="margin-left:8px">loading corpus…</span>`;
  try {
    await jsonOrThrow(await fetch(api("/load"), { method: "POST" }));
  } catch (e) {
    footer.innerHTML =
      `<span style="width:7px;height:7px;border-radius:50%;background:#d84a4a"></span>${escapeHtml(e.message)}`;
    return;
  }
  refreshStatus();
}

// --------------------------------------------------------------- wiring -----
function submitFromInput() {
  const input = $("#question-input");
  const q = input.value;
  input.value = "";
  input.style.height = "auto";
  ask(q);
}

function init() {
  const input = $("#question-input");

  $("#send-btn").addEventListener("click", submitFromInput);

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submitFromInput();
    }
  });
  // auto-grow textarea
  input.addEventListener("input", () => {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 140) + "px";
  });

  // example chips (present in empty + nomatch states)
  document.addEventListener("click", (e) => {
    const chip = e.target.closest(".example-chip");
    if (chip) ask(chip.textContent.trim());
  });

  $("#new-question-btn").addEventListener("click", () => {
    $$(".history-item").forEach((el) => el.classList.remove("is-current"));
    $("#breadcrumb").textContent = "Stripe documentation";
    input.value = "";
    input.focus();
    showState("empty");
  });

  $("#retry-btn").addEventListener("click", () => ask(lastQuestion));

  showState("empty");
  refreshStatus();
  loadHistory();
}

document.addEventListener("DOMContentLoaded", init);
