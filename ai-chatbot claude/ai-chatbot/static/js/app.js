/* app.js - Front-end logic: chat messages, popup panels and API calls */

// ---------- Element references ----------
const $ = (id) => document.getElementById(id);

const messagesEl = $("messages");
const form = $("chat-form");
const input = $("question");
const sendBtn = $("send-btn");
const statusEl = $("status");
const sidebar = $("sidebar");
const modal = $("modal");
const modalTitle = $("modal-title");
const modalBody = $("modal-body");
const welcomeTemplate = $("welcome-screen").cloneNode(true);

let busy = false;

// ---------- Helpers ----------

// Call the Flask API. Returns the JSON data or throws a readable Error.
async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": document.querySelector('meta[name="csrf-token"]').content,
    },
    ...options,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.ok) {
    throw new Error(data.error || `Request failed (${res.status}).`);
  }
  return data;
}

function scrollToBottom() {
  messagesEl.scrollTo({ top: messagesEl.scrollHeight, behavior: "smooth" });
}

// Add a chat bubble. textContent (not innerHTML) keeps the text safe from HTML injection.
function addMessage(role, text, meta = "") {
  const row = document.createElement("div");
  row.className = `message ${role}`;

  const avatar = document.createElement("div");
  avatar.className = "message-avatar";
  avatar.textContent = role === "user" ? "Y" : role === "error" ? "!" : "✦";
  avatar.setAttribute("aria-hidden", "true");
  row.appendChild(avatar);

  const content = document.createElement("div");
  content.className = "message-content";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  content.appendChild(bubble);

  if (meta) {
    const info = document.createElement("div");
    info.className = "meta";
    info.textContent = meta;
    content.appendChild(info);
  }

  row.appendChild(content);
  messagesEl.appendChild(row);
  scrollToBottom();
  return row;
}

// Show the bouncing-dots indicator while Gemini is answering
function showTyping() {
  const row = document.createElement("div");
  row.className = "message bot";

  const avatar = document.createElement("div");
  avatar.className = "message-avatar";
  avatar.textContent = "✦";
  avatar.setAttribute("aria-hidden", "true");
  row.appendChild(avatar);

  const content = document.createElement("div");
  content.className = "message-content";
  const bubble = document.createElement("div");
  bubble.className = "bubble typing";
  for (let i = 0; i < 3; i++) bubble.appendChild(document.createElement("span"));

  content.appendChild(bubble);
  row.appendChild(content);
  messagesEl.appendChild(row);
  scrollToBottom();
  return row;
}

// Disable the send button and update the status text while busy
function setBusy(isBusy) {
  busy = isBusy;
  sendBtn.disabled = isBusy;
  statusEl.textContent = isBusy ? "Thinking…" : "Online";
  statusEl.classList.toggle("busy", isBusy);
}

// Grow the text box as the user types (up to a maximum height)
function autoResize() {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 160) + "px";
}

// ---------- Sending a question ----------

async function sendQuestion(question) {
  $("welcome-screen")?.remove();
  addMessage("user", question);
  input.value = "";
  autoResize();

  const typing = showTyping();
  setBusy(true);

  try {
    const data = await api("/api/ask", {
      method: "POST",
      body: JSON.stringify({ question }),
    });
    typing.remove();
    addMessage("bot", data.answer, `Answered in ${data.seconds} sec`);
  } catch (err) {
    typing.remove();
    addMessage("error", err.message);
  } finally {
    setBusy(false);
    input.focus();
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const question = input.value.trim();
  if (!question || busy) return;
  sendQuestion(question);
});

// Enter sends the message; Shift + Enter adds a new line
input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});

input.addEventListener("input", autoResize);

// ---------- Popup window helpers ----------

function openModal(title) {
  modalTitle.textContent = title;
  modalBody.replaceChildren(makeText("Loading…"));
  modal.classList.remove("hidden");
}

function setModal(node) {
  modalBody.replaceChildren(node);
}

function closeModal() {
  modal.classList.add("hidden");
}

// Run a panel: open the popup, load data, show it (or show the error)
async function runPanel(title, loader) {
  openModal(title);
  try {
    setModal(await loader());
  } catch (err) {
    setModal(makeText(err.message, "panel-text error-text"));
  }
}

// Build small DOM elements (all text is set with textContent)
function makeText(text, className = "panel-text") {
  const p = document.createElement("p");
  p.className = className;
  p.textContent = text;
  return p;
}

function makePre(text) {
  const pre = document.createElement("pre");
  pre.textContent = text;
  return pre;
}

function makeTable(headers, rows) {
  const wrap = document.createElement("div");
  wrap.className = "table-wrap";

  const table = document.createElement("table");
  const headRow = table.createTHead().insertRow();
  headers.forEach((h) => {
    const th = document.createElement("th");
    th.textContent = h;
    headRow.appendChild(th);
  });

  const body = table.createTBody();
  rows.forEach((cells) => {
    const tr = body.insertRow();
    cells.forEach((value) => {
      tr.insertCell().textContent = value;
    });
  });

  wrap.appendChild(table);
  return wrap;
}

// ---------- Menu actions ----------

function showHistory() {
  return runPanel("Last 5 chats", async () => {
    const { rows } = await api("/api/history");
    if (!rows.length) return makeText("No chats saved yet.");
    return makeTable(
      ["#", "Question", "Time (sec)"],
      rows.map((r) => [r.chat_no, r.question, r.response_time_sec])
    );
  });
}

function showStats() {
  return runPanel("Chat statistics", async () => {
    const { text } = await api("/api/stats");
    return makePre(text || "No chats yet.");
  });
}

function showCharts() {
  return runPanel("Charts", async () => {
    const { message, files } = await api("/api/charts", { method: "POST" });
    if (!files.length) return makeText(message || "Ask at least 2 questions first.");

    const wrap = document.createElement("div");
    wrap.className = "charts";
    files.forEach((file) => {
      const img = document.createElement("img");
      // Timestamp forces the browser to load the newest image
      img.src = `/charts/${encodeURIComponent(file)}?t=${Date.now()}`;
      img.alt = file.replace(/\.png$/, "").replace(/_/g, " ");
      wrap.appendChild(img);
    });
    return wrap;
  });
}

function showModels() {
  return runPanel("Available Gemini models", async () => {
    const { models } = await api("/api/models");
    if (!models.length) return makeText("No models found for your API key.");
    const list = document.createElement("ul");
    list.className = "model-list";
    models.forEach((name) => {
      const li = document.createElement("li");
      li.textContent = name;
      list.appendChild(li);
    });
    return list;
  });
}

function showSettings() {
  return runPanel("Settings", async () => {
    const panel = document.createElement("div");
    panel.className = "settings-info";

    const settings = [
      ["AI provider", "Google Gemini powers your conversations. Use Available models to view models accessible with your API key."],
      ["API key", "Your key is configured privately on the server and is never included in browser code or responses."],
      ["Conversation", "Use New chat to reset the current Gemini conversation. Chat history, statistics, and charts remain available from the sidebar."],
    ];

    settings.forEach(([title, description]) => {
      const row = document.createElement("section");
      row.className = "settings-row";
      const heading = document.createElement("strong");
      heading.textContent = title;
      const detail = document.createElement("p");
      detail.textContent = description;
      row.append(heading, detail);
      panel.appendChild(row);
    });
    return panel;
  });
}

async function resetChat() {
  if (!confirm("Start a fresh conversation? The chat on screen will be cleared.")) return;
  try {
    await api("/api/reset", { method: "POST" });
    messagesEl.replaceChildren(welcomeTemplate.cloneNode(true));
  } catch (err) {
    addMessage("error", err.message);
  }
}

// Map each menu button's data-action to its function
const actions = {
  history: showHistory,
  stats: showStats,
  charts: showCharts,
  models: showModels,
  settings: showSettings,
  reset: resetChat,
};

document.querySelectorAll("[data-action]").forEach((btn) => {
  btn.addEventListener("click", () => {
    actions[btn.dataset.action]();
    sidebar.classList.remove("open");   // close the menu on phones
  });
});

messagesEl.addEventListener("click", (event) => {
  const card = event.target.closest("[data-prompt]");
  if (!card || busy) return;
  sendQuestion(card.dataset.prompt);
});

// ---------- Popup and mobile menu controls ----------

$("modal-close").addEventListener("click", closeModal);

modal.addEventListener("click", (event) => {
  if (event.target === modal) closeModal();   // click outside the card
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeModal();
});

$("menu-toggle").addEventListener("click", () => {
  sidebar.classList.toggle("open");
});

// ---------- Start ----------
input.focus();
