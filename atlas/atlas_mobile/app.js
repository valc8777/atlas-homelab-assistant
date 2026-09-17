const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;

const state = {
  apiBase: new URL("../api/v1/", window.location.href),
  token: localStorage.getItem("atlas.token") || "",
  scope: localStorage.getItem("atlas.scope") || "personal",
  conversationId: localStorage.getItem("atlas.conversationId") || "",
  listening: false,
  recognition: null,
  currentAudioURL: null
};

const els = {
  token: document.querySelector("#token"),
  scope: document.querySelector("#scope"),
  saveSettings: document.querySelector("#save-settings"),
  openSettings: document.querySelector("#open-settings"),
  settingsView: document.querySelector("#settings-view"),
  settingsClose: document.querySelector("#settings-close"),
  status: document.querySelector("#status"),
  composer: document.querySelector("#composer"),
  prompt: document.querySelector("#prompt"),
  send: document.querySelector("#send"),
  clearChat: document.querySelector("#clear-chat"),
  messages: document.querySelector("#messages"),
  voiceStrip: document.querySelector("#voice-strip"),
  voiceOnce: document.querySelector("#voice-once"),
  voiceStop: document.querySelector("#voice-stop"),
  voiceState: document.querySelector("#voice-state"),
  openPanels: document.querySelector("#open-panels"),
  confirmationsBadge: document.querySelector("#confirmations-badge"),
  panelsView: document.querySelector("#panels-view"),
  panelsClose: document.querySelector("#panels-close"),
  panelsGroups: document.querySelector("#panels-groups"),
  panelsTabs: document.querySelector("#panels-tabs"),
  confirmationsFilter: document.querySelector("#confirmations-filter"),
  confirmationsFilterTabs: document.querySelectorAll(".status-filter-tab"),
  panelsAdd: document.querySelector("#panels-add"),
  panelsList: document.querySelector("#panels-list")
};

function setStatus(text, isError = false) {
  els.status.textContent = text;
  els.status.style.color = isError ? "var(--danger)" : "var(--muted)";
}

function setVoiceState(text, isError = false) {
  els.voiceState.textContent = text;
  els.voiceState.style.color = isError ? "var(--danger)" : "var(--muted)";
}

function jsonHeaders() {
  return {
    "Authorization": `Bearer ${state.token}`,
    "Content-Type": "application/json",
    "Accept": "application/json"
  };
}

function authHeaders(extra = {}) {
  return {
    "Authorization": `Bearer ${state.token}`,
    ...extra
  };
}

async function api(path, options = {}) {
  if (!state.token) {
    throw new Error("Set the ATLAS bearer token first.");
  }
  const url = new URL(path.replace(/^\/+/, ""), state.apiBase);
  const response = await fetch(url, {
    ...options,
    headers: { ...jsonHeaders(), ...(options.headers || {}) }
  });
  const data = await response.json().catch(() => ({}));
  if (response.status === 401) {
    throw new Error("Saved token was rejected. Open ⋯, paste the current ATLAS token, then tap Save on this device.");
  }
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || `HTTP ${response.status}`);
  }
  return data;
}

function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = value ?? "";
  return div.innerHTML;
}

function escapeAttr(value) {
  return escapeHtml(value).replace(/"/g, "&quot;");
}

function renderMarkdown(text) {
  const codeBlocks = [];
  let working = text.replace(/```([\s\S]*?)```/g, (_, code) => {
    codeBlocks.push(code.replace(/^\n/, "").replace(/\n$/, ""));
    return `%%%CODEBLOCK${codeBlocks.length - 1}%%%`;
  });

  working = escapeHtml(working);
  working = working.replace(/`([^`\n]+)`/g, (_, code) => `<code>${code}</code>`);
  working = working.replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, (_, label, url) => `<a href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>`);
  working = working.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  working = working.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>");

  const lines = working.split("\n");
  const out = [];
  let list = null;

  const closeList = () => {
    if (list) {
      out.push(`<${list.type}>${list.items.join("")}</${list.type}>`);
      list = null;
    }
  };

  for (const line of lines) {
    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    const ul = line.match(/^[-*]\s+(.+)$/);
    const ol = line.match(/^\d+\.\s+(.+)$/);
    const quote = line.match(/^&gt;\s?(.*)$/);
    if (heading) {
      closeList();
      const level = heading[1].length;
      out.push(`<h${level}>${heading[2]}</h${level}>`);
    } else if (ul) {
      if (!list || list.type !== "ul") { closeList(); list = { type: "ul", items: [] }; }
      list.items.push(`<li>${ul[1]}</li>`);
    } else if (ol) {
      if (!list || list.type !== "ol") { closeList(); list = { type: "ol", items: [] }; }
      list.items.push(`<li>${ol[1]}</li>`);
    } else if (quote) {
      closeList();
      out.push(`<blockquote>${quote[1]}</blockquote>`);
    } else {
      closeList();
      out.push(line === "" ? "" : `${line}<br>`);
    }
  }
  closeList();

  let html = out.join("");
  html = html.replace(/%%%CODEBLOCK(\d+)%%%(?:<br>)?/g, (_, idx) => {
    const code = escapeHtml(codeBlocks[Number(idx)]);
    return `<div class="code-block"><button type="button" class="code-copy" aria-label="Copy code">Copy</button><pre><code>${code}</code></pre></div>`;
  });
  return html;
}

async function copyMessageText(text, button) {
  try {
    await navigator.clipboard.writeText(text);
    const original = button.textContent;
    button.textContent = "Copied";
    setTimeout(() => { button.textContent = original; }, 1200);
  } catch (err) {
    setStatus("Couldn't copy to clipboard.", true);
  }
}

async function sendFeedback(messageId, rating, goodBtn, badBtn) {
  try {
    await api("feedback", {
      method: "POST",
      body: JSON.stringify({
        rating,
        message_id: messageId,
        conversation_id: state.conversationId,
        client: "atlas-mobile-pwa"
      })
    });
    goodBtn.disabled = true;
    badBtn.disabled = true;
    (rating === "good" ? goodBtn : badBtn).classList.add("voted");
  } catch (err) {
    setStatus(err.message, true);
  }
}

function addMessage(role, text, meta = "", messageId = null) {
  const empty = els.messages.querySelector(".empty-state");
  if (empty) {
    empty.remove();
  }
  const div = document.createElement("div");
  div.className = `message ${role}`;
  if (messageId) {
    div.dataset.messageId = messageId;
  }
  const content = document.createElement("div");
  content.className = "message-content";
  if (role === "assistant") {
    content.innerHTML = renderMarkdown(text);
  } else {
    content.textContent = text;
  }
  div.appendChild(content);
  const footer = document.createElement("div");
  footer.className = "message-footer";
  if (meta) {
    const small = document.createElement("small");
    small.className = "meta";
    small.textContent = meta;
    footer.appendChild(small);
  }
  const copyBtn = document.createElement("button");
  copyBtn.type = "button";
  copyBtn.className = "copy-message";
  copyBtn.setAttribute("aria-label", "Copy message");
  copyBtn.textContent = "Copy";
  copyBtn.addEventListener("click", () => copyMessageText(text, copyBtn));
  footer.appendChild(copyBtn);
  if (role === "assistant" && messageId) {
    const goodBtn = document.createElement("button");
    goodBtn.type = "button";
    goodBtn.className = "feedback-button";
    goodBtn.setAttribute("aria-label", "Good response");
    goodBtn.textContent = "👍";
    const badBtn = document.createElement("button");
    badBtn.type = "button";
    badBtn.className = "feedback-button";
    badBtn.setAttribute("aria-label", "Bad response");
    badBtn.textContent = "👎";
    goodBtn.addEventListener("click", () => sendFeedback(messageId, "good", goodBtn, badBtn));
    badBtn.addEventListener("click", () => sendFeedback(messageId, "bad", goodBtn, badBtn));
    footer.appendChild(goodBtn);
    footer.appendChild(badBtn);
  }
  div.appendChild(footer);
  els.messages.appendChild(div);
  div.scrollIntoView({ block: "end", behavior: "smooth" });
}

els.messages.addEventListener("click", async (event) => {
  const button = event.target.closest(".code-copy");
  if (!button) return;
  const codeEl = button.closest(".code-block")?.querySelector("pre code");
  if (!codeEl) return;
  await copyMessageText(codeEl.textContent, button);
});

function addTypingIndicator() {
  const empty = els.messages.querySelector(".empty-state");
  if (empty) empty.remove();
  const div = document.createElement("div");
  div.className = "message assistant typing";
  div.id = "typing-indicator";
  div.innerHTML = "<span class=\"typing-dots\"><span></span><span></span><span></span></span>";
  els.messages.appendChild(div);
  div.scrollIntoView({ block: "end", behavior: "smooth" });
}

function removeTypingIndicator() {
  const el = document.querySelector("#typing-indicator");
  if (el) el.remove();
}

async function speakWithPiper(text) {
  if (!state.token || !text.trim()) return;
  setVoiceState("Generating Piper voice…");
  const url = new URL("speech", state.apiBase);
  const response = await fetch(url, {
    method: "POST",
    headers: authHeaders({
      "Content-Type": "application/json",
      "Accept": "audio/*"
    }),
    body: JSON.stringify({
      text,
      voice: "alloy",
      response_format: "mp3"
    })
  });
  if (!response.ok) {
    const error = await response.text().catch(() => "");
    throw new Error(error || `Piper returned HTTP ${response.status}`);
  }
  const blob = await response.blob();
  if (state.currentAudioURL) {
    URL.revokeObjectURL(state.currentAudioURL);
  }
  state.currentAudioURL = URL.createObjectURL(blob);
  const audio = new Audio(state.currentAudioURL);
  await audio.play();
  await new Promise((resolve) => {
    audio.addEventListener("ended", resolve, { once: true });
    audio.addEventListener("error", resolve, { once: true });
  });
}

async function sendText(text, { speak = false } = {}) {
  const trimmed = text.trim();
  if (!trimmed) return;
  addMessage("user", trimmed);
  setStatus("Sending…");
  if (speak) {
    setVoiceState("ATLAS is thinking…");
  }
  addTypingIndicator();
  let data;
  try {
    data = await api("messages", {
      method: "POST",
      body: JSON.stringify({
        text: trimmed,
        conversation_id: state.conversationId || undefined,
        input_mode: speak ? "voice-pwa" : "pwa",
        client: "atlas-mobile-pwa",
        context_scope: state.scope
      })
    });
  } finally {
    removeTypingIndicator();
  }
  state.conversationId = data.conversation_id || data.assistant_message?.conversation_id || state.conversationId;
  if (state.conversationId) {
    localStorage.setItem("atlas.conversationId", state.conversationId);
  }
  const answer = data.assistant_message?.speak_text || data.assistant_message?.text || "No response text.";
  const route = data.assistant_message?.route || "unknown";
  const source = data.assistant_message?.source || "";
  addMessage("assistant", answer, source ? `${route} · ${source}` : route, data.assistant_message?.id);
  setStatus("Ready.");
  if (speak) {
    await speakWithPiper(answer);
    setVoiceState("Tap 🎙 to talk again.");
  }
  return data;
}

async function sendPrompt() {
  const text = els.prompt.value.trim();
  if (!text) return;
  els.prompt.value = "";
  try {
    await sendText(text);
    refreshConfirmationsBadge();
  } catch (err) {
    setStatus(err.message, true);
    setVoiceState(err.message, true);
  }
}

function ensureRecognition() {
  if (!SpeechRecognition) {
    throw new Error("Speech recognition is not available in this browser. Try Safari on iPhone or use typed chat.");
  }
  if (state.recognition) {
    return state.recognition;
  }
  const recognition = new SpeechRecognition();
  recognition.lang = "en-US";
  recognition.interimResults = false;
  recognition.continuous = false;

  recognition.addEventListener("start", () => {
    state.listening = true;
    els.voiceStop.hidden = false;
    setVoiceState("Listening…");
  });

  recognition.addEventListener("end", () => {
    state.listening = false;
    els.voiceStop.hidden = true;
  });

  recognition.addEventListener("error", (event) => {
    state.listening = false;
    els.voiceStop.hidden = true;
    setVoiceState(event.error ? `Mic error: ${event.error}` : "Mic error.", true);
  });

  recognition.addEventListener("result", async (event) => {
    const result = event.results[event.results.length - 1];
    const transcript = result?.[0]?.transcript || "";
    if (!transcript.trim()) {
      setVoiceState("No speech recognized. Tap 🎙 to try again.", true);
      return;
    }
    // Deliberately do not auto-restart recognition here. iOS Safari requires
    // SpeechRecognition.start() to be tied to a direct user gesture; calling
    // it programmatically from this async callback can silently "succeed"
    // (fires the start event) without actually re-arming the microphone,
    // leaving voice mode looking active while never hearing anything again.
    // Requiring a tap on the mic button between turns is reliable on iOS.
    try {
      await sendText(transcript, { speak: true });
      refreshConfirmationsBadge();
    } catch (err) {
      setStatus(err.message, true);
      setVoiceState(err.message, true);
    }
  });

  state.recognition = recognition;
  return recognition;
}

function startListening() {
  try {
    if (!state.token) {
      throw new Error("Set the ATLAS bearer token first.");
    }
    const recognition = ensureRecognition();
    if (!state.listening) {
      recognition.start();
    }
  } catch (err) {
    setVoiceState(err.message, true);
  }
}

function stopListening() {
  if (state.recognition && state.listening) {
    state.recognition.stop();
  }
  setVoiceState("Stopped listening.");
}

async function saveSettings() {
  state.token = els.token.value.trim();
  state.scope = els.scope.value;
  localStorage.setItem("atlas.token", state.token);
  localStorage.setItem("atlas.scope", state.scope);
  if (!state.token) {
    setStatus("Token cleared. Paste the current ATLAS token to connect.", true);
    return;
  }
  setStatus("Checking token…");
  try {
    await api("version");
    setStatus("Token verified and saved on this device.");
    setVoiceState("Tap 🎙 to talk.");
    refreshConfirmationsBadge();
  } catch (err) {
    setStatus(err.message, true);
  }
}

function clearVisibleChat() {
  els.messages.replaceChildren();
  const empty = document.createElement("div");
  empty.className = "empty-state";
  empty.innerHTML = "<div class=\"logo-mark\">✦</div><h2>How can I help?</h2><p>Ask a question, create a reminder, add a task, or tap the mic for a spoken turn.</p>";
  els.messages.appendChild(empty);
  setStatus("Visible chat cleared. Backend history was not deleted.");
}

async function archiveVisibleChat() {
  if (!state.conversationId) {
    clearVisibleChat();
    setStatus("Started a new chat.");
    return;
  }
  try {
    await api(`conversations/${state.conversationId}/archive`, { method: "POST", body: "{}" });
    state.conversationId = "";
    localStorage.removeItem("atlas.conversationId");
    clearVisibleChat();
    setStatus("Started a new chat. Find the previous one in Logs.");
  } catch (err) {
    setStatus(err.message, true);
  }
}

const PANEL_CONFIG = {
  confirmations: { key: "confirmations", addable: false },
  tasks: { endpoint: "tasks", key: "tasks", addable: true, placeholder: "New task…" },
  notes: { endpoint: "notes", key: "notes", addable: true, placeholder: "New note…" },
  logs: { endpoint: "conversations?limit=50", key: "conversations", addable: false },
  reminders: { endpoint: "reminders", key: "reminders", addable: true, placeholder: "Remind me to…" },
  shopping: { endpoint: "shopping", key: "items", addable: true, placeholder: "Add to shopping list…" },
  preferences: { endpoint: "preferences", key: "preferences", addable: true, placeholder: null },
  rules: { endpoint: "rules", key: "rules", addable: true, placeholder: null },
  notifications: { endpoint: "notifications?limit=50", key: "notifications", addable: false },
  audit: { endpoint: "audit?limit=100", key: "events", addable: false },
  "media-comics": { endpoint: "media-requests/comics", key: "requests", addable: true, placeholder: "What comic/issue…" }
};

const PANEL_LABELS = {
  confirmations: "Confirmations",
  tasks: "Tasks",
  notes: "Notes",
  logs: "Logs",
  reminders: "Reminders",
  shopping: "Shopping",
  preferences: "Preferences",
  rules: "Rules",
  notifications: "Notifications",
  audit: "Audit",
  status: "Status",
  home: "Home",
  "nextcloud-calendar": "Calendar",
  "nextcloud-files": "Files",
  "nextcloud-contacts": "Contacts",
  "media-comics": "Comics"
};

const PANEL_GROUPS = {
  organizer: ["reminders", "tasks", "notes", "shopping", "preferences"],
  automation: ["rules", "notifications", "confirmations"],
  system: ["status", "home", "nextcloud-calendar", "nextcloud-files", "nextcloud-contacts", "audit", "logs"],
  media: ["media-comics"]
};

const CUSTOM_PANELS = new Set(["status", "home", "nextcloud-calendar", "nextcloud-files", "nextcloud-contacts"]);

const HOME_QUICK_ACTIONS = [
  { label: "Turn on bedside lamp", text: "Turn on the bedside lamp" },
  { label: "Turn off bedside lamp", text: "Turn off the bedside lamp" },
  { label: "Is the lamp on?", text: "Is the bedside lamp on?" }
];

let activeGroup = "automation";
let activePanel = "confirmations";
let confirmationStatus = "pending";

function panelEndpoint(panel) {
  if (panel === "confirmations") {
    return `confirmations?status=${confirmationStatus}`;
  }
  return PANEL_CONFIG[panel].endpoint;
}

async function searchMessages(query) {
  els.panelsList.innerHTML = "";
  const loading = document.createElement("p");
  loading.className = "panels-empty";
  loading.textContent = "Searching…";
  els.panelsList.appendChild(loading);
  try {
    const data = await api(`messages?query=${encodeURIComponent(query)}`);
    const results = data.messages || [];
    els.panelsList.innerHTML = "";
    if (!results.length) {
      const empty = document.createElement("p");
      empty.className = "panels-empty";
      empty.textContent = "No matches.";
      els.panelsList.appendChild(empty);
      return;
    }
    for (const msg of results) {
      els.panelsList.appendChild(renderSearchResultRow(msg));
    }
  } catch (err) {
    els.panelsList.innerHTML = "";
    const errorEl = document.createElement("p");
    errorEl.className = "panels-empty";
    errorEl.textContent = err.message;
    els.panelsList.appendChild(errorEl);
  }
}

function renderSearchResultRow(msg) {
  const row = document.createElement("div");
  row.className = "panel-row";
  row.innerHTML = `
    <div class="panel-row-main">
      <strong>${escapeHtml(msg.conversation_title || "Conversation")}</strong>
      <small>${escapeHtml(msg.role)} · ${escapeHtml(msg.created_at)}</small>
      <p class="panel-row-snippet">${escapeHtml((msg.text || "").slice(0, 140))}</p>
    </div>
    <div class="panel-row-actions">
      <button type="button" class="resume">Open</button>
    </div>`;
  row.querySelector(".resume").addEventListener("click", () => resumeConversation(msg.conversation_id));
  return row;
}

function renderPanelAddForm(panel) {
  els.panelsAdd.innerHTML = "";
  if (CUSTOM_PANELS.has(panel)) return;
  if (panel === "logs") {
    const form = document.createElement("form");
    form.className = "panels-add-form";
    form.innerHTML = "<input type=\"text\" placeholder=\"Search past messages…\"><button type=\"submit\">Search</button>";
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const query = form.querySelector("input").value.trim();
      if (!query) {
        loadPanel("logs");
        return;
      }
      searchMessages(query);
    });
    els.panelsAdd.appendChild(form);
    return;
  }
  if (panel === "preferences") {
    const form = document.createElement("form");
    form.className = "panels-add-form panels-add-form-wide";
    form.innerHTML = `<input type="text" name="key" placeholder="Key (e.g. favorite color)" required><input type="text" name="value" placeholder="Value" required><button type="submit">Save</button>`;
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const key = form.key.value.trim();
      const value = form.value.value.trim();
      if (!key || !value) return;
      try {
        await api("preferences", { method: "POST", body: JSON.stringify({ key, value }) });
        form.reset();
        loadPanel("preferences");
      } catch (err) {
        setStatus(err.message, true);
      }
    });
    els.panelsAdd.appendChild(form);
    return;
  }
  if (panel === "rules") {
    const form = document.createElement("form");
    form.className = "panels-add-form panels-add-form-wide";
    form.innerHTML = `<input type="text" name="condition" placeholder="If… (condition)" required><input type="text" name="action" placeholder="Then… (action)" required><button type="submit">Add</button>`;
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const condition = form.condition.value.trim();
      const action = form.action.value.trim();
      if (!condition || !action) return;
      try {
        await api("rules", { method: "POST", body: JSON.stringify({ condition, action }) });
        form.reset();
        loadPanel("rules");
      } catch (err) {
        setStatus(err.message, true);
      }
    });
    els.panelsAdd.appendChild(form);
    return;
  }
  if (panel === "media-comics") {
    const form = document.createElement("form");
    form.className = "panels-add-form";
    form.innerHTML = `<input type="text" placeholder="${PANEL_CONFIG[panel].placeholder}" required><button type="submit">Send</button>`;
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const input = form.querySelector("input");
      const text = input.value.trim();
      if (!text) return;
      try {
        await api("media-requests/comics", {
          method: "POST",
          body: JSON.stringify({ text, context_scope: state.scope })
        });
        input.value = "";
        setStatus("Sent for approval — check Automation > Confirmations.");
        loadPanel("media-comics");
      } catch (err) {
        setStatus(err.message, true);
      }
    });
    els.panelsAdd.appendChild(form);
    return;
  }
  const config = PANEL_CONFIG[panel];
  if (!config.addable) return;
  const form = document.createElement("form");
  form.className = "panels-add-form";
  form.innerHTML = `<input type="text" placeholder="${config.placeholder}" required><button type="submit">Add</button>`;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = form.querySelector("input");
    const text = input.value.trim();
    if (!text) return;
    try {
      await api(panel, {
        method: "POST",
        body: JSON.stringify({ text, context_scope: state.scope })
      });
      input.value = "";
      loadPanel(panel);
    } catch (err) {
      setStatus(err.message, true);
    }
  });
  els.panelsAdd.appendChild(form);
}

async function decideConfirmation(id, decision) {
  try {
    await api(`confirmations/${id}/${decision}`, { method: "POST", body: "{}" });
    loadPanel("confirmations");
    refreshConfirmationsBadge();
  } catch (err) {
    setStatus(err.message, true);
  }
}

async function completeItem(panel, id) {
  try {
    await api(`${panel}/${id}/complete`, { method: "POST", body: "{}" });
    loadPanel(panel);
  } catch (err) {
    setStatus(err.message, true);
  }
}

async function deleteNote(id) {
  try {
    await api(`notes/${id}`, { method: "DELETE" });
    loadPanel("notes");
  } catch (err) {
    setStatus(err.message, true);
  }
}

function startNoteEdit(row, item) {
  row.innerHTML = `
    <form class="panel-row-edit-form">
      <input type="text" name="title" placeholder="Title (optional)" value="${escapeAttr(item.title || "")}">
      <input type="text" name="text" placeholder="Note" value="${escapeAttr(item.text || "")}" required>
      <div class="panel-row-edit-actions">
        <button type="button" class="cancel">Cancel</button>
        <button type="submit" class="save">Save</button>
      </div>
    </form>`;
  row.querySelector(".cancel").addEventListener("click", () => loadPanel("notes"));
  row.querySelector("form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.target;
    const title = form.title.value.trim();
    const text = form.text.value.trim();
    if (!text) return;
    try {
      await api(`notes/${item.id}`, {
        method: "PATCH",
        body: JSON.stringify({ title, text })
      });
      loadPanel("notes");
    } catch (err) {
      setStatus(err.message, true);
    }
  });
}

function startTaskEdit(row, item) {
  row.innerHTML = `
    <form class="panel-row-edit-form">
      <input type="text" name="text" value="${escapeAttr(item.text || "")}" required>
      <select name="priority">
        <option value="low">low</option>
        <option value="normal">normal</option>
        <option value="high">high</option>
      </select>
      <div class="panel-row-edit-actions">
        <button type="button" class="cancel">Cancel</button>
        <button type="submit" class="save">Save</button>
      </div>
    </form>`;
  row.querySelector('select[name="priority"]').value = item.priority || "normal";
  row.querySelector(".cancel").addEventListener("click", () => loadPanel("tasks"));
  row.querySelector("form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.target;
    const text = form.text.value.trim();
    const priority = form.priority.value;
    if (!text) return;
    try {
      await api(`tasks/${item.id}`, {
        method: "PATCH",
        body: JSON.stringify({ text, priority })
      });
      loadPanel("tasks");
    } catch (err) {
      setStatus(err.message, true);
    }
  });
}

async function snoozeReminder(id) {
  try {
    await api(`reminders/${id}/snooze?minutes=15`, { method: "POST", body: "{}" });
    loadPanel("reminders");
  } catch (err) {
    setStatus(err.message, true);
  }
}

function startReminderEdit(row, item) {
  row.innerHTML = `
    <form class="panel-row-edit-form">
      <input type="text" name="text" value="${escapeAttr(item.text || "")}" required>
      <input type="text" name="due_at" placeholder="Due (e.g. tomorrow at 9am)" value="${escapeAttr(item.due_at || "")}">
      <div class="panel-row-edit-actions">
        <button type="button" class="cancel">Cancel</button>
        <button type="submit" class="save">Save</button>
      </div>
    </form>`;
  row.querySelector(".cancel").addEventListener("click", () => loadPanel("reminders"));
  row.querySelector("form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.target;
    const text = form.text.value.trim();
    const dueAt = form.due_at.value.trim();
    if (!text) return;
    try {
      await api(`reminders/${item.id}`, {
        method: "PATCH",
        body: JSON.stringify({ text, due_at: dueAt || undefined })
      });
      loadPanel("reminders");
    } catch (err) {
      setStatus(err.message, true);
    }
  });
}

function startShoppingEdit(row, item) {
  row.innerHTML = `
    <form class="panel-row-edit-form">
      <input type="text" name="text" value="${escapeAttr(item.text || "")}" required>
      <input type="text" name="quantity" placeholder="Quantity (optional)" value="${escapeAttr(item.quantity || "")}">
      <input type="text" name="store" placeholder="Store (optional)" value="${escapeAttr(item.store || "")}">
      <div class="panel-row-edit-actions">
        <button type="button" class="cancel">Cancel</button>
        <button type="submit" class="save">Save</button>
      </div>
    </form>`;
  row.querySelector(".cancel").addEventListener("click", () => loadPanel("shopping"));
  row.querySelector("form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.target;
    const text = form.text.value.trim();
    if (!text) return;
    try {
      await api(`shopping/${item.id}`, {
        method: "PATCH",
        body: JSON.stringify({ text, quantity: form.quantity.value.trim(), store: form.store.value.trim() })
      });
      loadPanel("shopping");
    } catch (err) {
      setStatus(err.message, true);
    }
  });
}

async function deletePreference(key) {
  try {
    await api(`preferences/${encodeURIComponent(key)}`, { method: "DELETE" });
    loadPanel("preferences");
  } catch (err) {
    setStatus(err.message, true);
  }
}

async function toggleRule(id, enabled) {
  try {
    await api(`rules/${id}`, { method: "PATCH", body: JSON.stringify({ enabled }) });
    loadPanel("rules");
  } catch (err) {
    setStatus(err.message, true);
  }
}

async function deleteRule(id) {
  try {
    await api(`rules/${id}`, { method: "DELETE" });
    loadPanel("rules");
  } catch (err) {
    setStatus(err.message, true);
  }
}

function renderPanelRow(panel, item) {
  const row = document.createElement("div");
  row.className = "panel-row";
  if (panel === "confirmations") {
    const decided = Boolean(item.status) && item.status !== "pending";
    const metaBits = [item.action_type, item.risk_level ? `${item.risk_level} risk` : "", item.requested_at];
    if (decided) {
      metaBits.push(item.decided_at ? `${item.status} · ${item.decided_at}` : item.status);
    }
    row.innerHTML = `
      <div class="panel-row-main">
        <strong>${escapeHtml(item.summary)}</strong>
        <small>${escapeHtml(metaBits.filter(Boolean).join(" · "))}</small>
      </div>
      ${decided ? "" : `<div class="panel-row-actions">
        <button type="button" class="approve">Approve</button>
        <button type="button" class="deny">Deny</button>
      </div>`}`;
    if (!decided) {
      row.querySelector(".approve").addEventListener("click", () => decideConfirmation(item.id, "approve"));
      row.querySelector(".deny").addEventListener("click", () => decideConfirmation(item.id, "deny"));
    }
    return row;
  }
  if (panel === "media-comics") {
    const statusBits = [item.status];
    if (item.execution_status && item.execution_status !== "not_applicable") statusBits.push(item.execution_status);
    row.innerHTML = `
      <div class="panel-row-main">
        <strong>${escapeHtml((item.details || {}).text || item.summary)}</strong>
        <small>${escapeHtml(statusBits.filter(Boolean).join(" · "))} · ${escapeHtml(item.requested_at)}</small>
      </div>`;
    return row;
  }
  if (panel === "tasks") {
    const meta = item.completed_at ? "Completed" : `${item.priority || "normal"} priority`;
    row.innerHTML = `
      <div class="panel-row-main">
        <strong>${escapeHtml(item.text)}</strong>
        <small>${escapeHtml(meta)}</small>
      </div>
      <div class="panel-row-actions"></div>`;
    const actions = row.querySelector(".panel-row-actions");
    if (!item.completed_at) {
      const done = document.createElement("button");
      done.type = "button";
      done.textContent = "Done";
      done.addEventListener("click", () => completeItem("tasks", item.id));
      actions.append(done);
      const edit = document.createElement("button");
      edit.type = "button";
      edit.className = "edit";
      edit.textContent = "Edit";
      edit.addEventListener("click", () => startTaskEdit(row, item));
      actions.append(edit);
    } else {
      actions.remove();
    }
    return row;
  }
  if (panel === "logs") {
    const label = item.title || (item.last_message ? item.last_message.slice(0, 80) : "Conversation");
    row.innerHTML = `
      <div class="panel-row-main">
        <strong>${escapeHtml(label)}</strong>
        <small>${escapeHtml(item.context_scope)} · updated ${escapeHtml(item.updated_at)}</small>
      </div>
      <div class="panel-row-actions">
        <button type="button" class="resume">Resume</button>
      </div>`;
    row.querySelector(".resume").addEventListener("click", () => resumeConversation(item.id));
    return row;
  }
  if (panel === "notes") {
    row.innerHTML = `
      <div class="panel-row-main">
        <strong>${escapeHtml(item.title || item.text)}</strong>
        <small>${escapeHtml(item.text)}</small>
      </div>
      <div class="panel-row-actions">
        <button type="button" class="edit">Edit</button>
        <button type="button" class="delete">Delete</button>
      </div>`;
    row.querySelector(".edit").addEventListener("click", () => startNoteEdit(row, item));
    row.querySelector(".delete").addEventListener("click", () => deleteNote(item.id));
    return row;
  }
  if (panel === "reminders") {
    const metaBits = [item.due_at, item.recurrence].filter(Boolean);
    const meta = item.completed_at ? "Completed" : (metaBits.join(" · ") || "No due time set");
    row.innerHTML = `
      <div class="panel-row-main">
        <strong>${escapeHtml(item.text)}</strong>
        <small>${escapeHtml(meta)}</small>
      </div>
      <div class="panel-row-actions"></div>`;
    const actions = row.querySelector(".panel-row-actions");
    if (!item.completed_at) {
      const done = document.createElement("button");
      done.type = "button";
      done.textContent = "Done";
      done.addEventListener("click", () => completeItem("reminders", item.id));
      actions.append(done);
      const snooze = document.createElement("button");
      snooze.type = "button";
      snooze.textContent = "Snooze 15m";
      snooze.addEventListener("click", () => snoozeReminder(item.id));
      actions.append(snooze);
      const edit = document.createElement("button");
      edit.type = "button";
      edit.className = "edit";
      edit.textContent = "Edit";
      edit.addEventListener("click", () => startReminderEdit(row, item));
      actions.append(edit);
    } else {
      actions.remove();
    }
    return row;
  }
  if (panel === "shopping") {
    const metaBits = [item.quantity, item.store].filter(Boolean);
    const meta = item.completed_at ? "Completed" : (metaBits.join(" · ") || "No details set");
    row.innerHTML = `
      <div class="panel-row-main">
        <strong>${escapeHtml(item.text)}</strong>
        <small>${escapeHtml(meta)}</small>
      </div>
      <div class="panel-row-actions"></div>`;
    const actions = row.querySelector(".panel-row-actions");
    if (!item.completed_at) {
      const done = document.createElement("button");
      done.type = "button";
      done.textContent = "Done";
      done.addEventListener("click", () => completeItem("shopping", item.id));
      actions.append(done);
      const edit = document.createElement("button");
      edit.type = "button";
      edit.className = "edit";
      edit.textContent = "Edit";
      edit.addEventListener("click", () => startShoppingEdit(row, item));
      actions.append(edit);
    } else {
      actions.remove();
    }
    return row;
  }
  if (panel === "preferences") {
    row.innerHTML = `
      <div class="panel-row-main">
        <strong>${escapeHtml(item.key)}</strong>
        <small>${escapeHtml(item.value)}</small>
      </div>
      <div class="panel-row-actions">
        <button type="button" class="delete">Delete</button>
      </div>`;
    row.querySelector(".delete").addEventListener("click", () => deletePreference(item.key));
    return row;
  }
  if (panel === "rules") {
    row.innerHTML = `
      <div class="panel-row-main">
        <strong>${escapeHtml(item.action_text)}</strong>
        <small>If: ${escapeHtml(item.condition_text)} · ${item.enabled ? "Enabled" : "Disabled"}</small>
      </div>
      <div class="panel-row-actions">
        <button type="button" class="toggle">${item.enabled ? "Disable" : "Enable"}</button>
        <button type="button" class="delete">Delete</button>
      </div>`;
    row.querySelector(".toggle").addEventListener("click", () => toggleRule(item.id, !item.enabled));
    row.querySelector(".delete").addEventListener("click", () => deleteRule(item.id));
    return row;
  }
  if (panel === "notifications") {
    row.innerHTML = `
      <div class="panel-row-main">
        <strong>${escapeHtml(item.title || "Notification")}</strong>
        <small>${escapeHtml(item.status)} · ${escapeHtml(item.created_at)}</small>
        <p class="panel-row-snippet">${escapeHtml(item.body || "")}</p>
      </div>`;
    return row;
  }
  if (panel === "audit") {
    row.innerHTML = `
      <div class="panel-row-main">
        <strong>${escapeHtml(item.event_type)}</strong>
        <small>${escapeHtml(item.created_at)}</small>
        <p class="panel-row-snippet">${escapeHtml(item.summary || "")}</p>
      </div>`;
    return row;
  }
  return row;
}

async function resumeConversation(id) {
  try {
    const data = await api(`conversations/${id}`);
    const conversation = data.conversation;
    state.conversationId = conversation.id;
    localStorage.setItem("atlas.conversationId", conversation.id);
    clearVisibleChat();
    for (const msg of conversation.messages || []) {
      if (msg.role !== "user" && msg.role !== "assistant") continue;
      const meta = msg.role === "assistant" ? (msg.source ? `${msg.route || "unknown"} · ${msg.source}` : (msg.route || "")) : "";
      addMessage(msg.role, msg.text, meta, msg.id);
    }
    closePanelsView();
    setStatus("Resumed a previous conversation.");
  } catch (err) {
    setStatus(err.message, true);
  }
}

function renderGroupTabs(group) {
  els.panelsTabs.innerHTML = "";
  for (const panel of PANEL_GROUPS[group]) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "panel-tab";
    btn.dataset.panel = panel;
    btn.textContent = PANEL_LABELS[panel] || panel;
    els.panelsTabs.appendChild(btn);
  }
}

function switchGroup(group) {
  activeGroup = group;
  els.panelsGroups.querySelectorAll(".panel-group").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.group === group);
  });
  renderGroupTabs(group);
  loadPanel(PANEL_GROUPS[group][0]);
}

async function loadStatusPanel() {
  try {
    const [diagnostics, permissions] = await Promise.all([api("diagnostics"), api("permissions")]);
    if (activePanel !== "status") return;
    els.panelsList.innerHTML = "";
    const checksHtml = (diagnostics.checks || []).map((check) => `
      <div class="status-check">
        <span class="status-dot ${check.ok ? "ok" : "fail"}"></span>
        <span>${escapeHtml(check.name)}</span>
        <small>${escapeHtml(check.detail || "")}</small>
      </div>`).join("");
    const block = document.createElement("div");
    block.className = "panel-row";
    block.innerHTML = `
      <div class="panel-row-main">
        <strong>ATLAS ${escapeHtml(diagnostics.service || "backend")} · v${escapeHtml(diagnostics.api_version || "?")}</strong>
        <small>${diagnostics.ok ? "All checks passing" : "Some checks failing"}</small>
      </div>
      <div class="status-checks">${checksHtml}</div>
      <p class="panel-row-snippet">${escapeHtml(permissions.summary || "")}</p>`;
    els.panelsList.appendChild(block);
  } catch (err) {
    if (activePanel !== "status") return;
    els.panelsList.innerHTML = "";
    const errorEl = document.createElement("p");
    errorEl.className = "panels-empty";
    errorEl.textContent = err.message;
    els.panelsList.appendChild(errorEl);
  }
}

function loadHomePanel() {
  els.panelsAdd.innerHTML = "";
  els.panelsList.innerHTML = "";
  for (const action of HOME_QUICK_ACTIONS) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "home-action";
    button.textContent = action.label;
    button.addEventListener("click", async () => {
      closePanelsView();
      try {
        await sendText(action.text);
        refreshConfirmationsBadge();
      } catch (err) {
        setStatus(err.message, true);
      }
    });
    els.panelsList.appendChild(button);
  }
}

async function loadNextcloudCalendarPanel() {
  els.panelsAdd.innerHTML = "";
  els.panelsList.innerHTML = "";
  const form = document.createElement("form");
  form.className = "panels-add-form panels-add-form-wide";
  form.innerHTML = `<select name="calendar"><option value="">Loading calendars…</option></select><button type="submit">Load events</button>`;
  els.panelsAdd.appendChild(form);
  const select = form.querySelector("select");

  async function loadEvents(calendar) {
    els.panelsList.innerHTML = "";
    if (!calendar) return;
    const loading = document.createElement("p");
    loading.className = "panels-empty";
    loading.textContent = "Loading…";
    els.panelsList.appendChild(loading);
    try {
      const data = await api(`nextcloud/calendar-events?calendar=${encodeURIComponent(calendar)}`);
      if (activePanel !== "nextcloud-calendar") return;
      const events = data.events || [];
      els.panelsList.innerHTML = "";
      if (!events.length) {
        const empty = document.createElement("p");
        empty.className = "panels-empty";
        empty.textContent = "No upcoming events.";
        els.panelsList.appendChild(empty);
        return;
      }
      for (const eventItem of events) {
        const row = document.createElement("div");
        row.className = "panel-row";
        const when = [eventItem.dtstart, eventItem.dtend].filter(Boolean).join(" – ");
        row.innerHTML = `
          <div class="panel-row-main">
            <strong>${escapeHtml(eventItem.summary || "Event")}</strong>
            <small>${escapeHtml(when)}${eventItem.location ? " · " + escapeHtml(eventItem.location) : ""}</small>
          </div>`;
        els.panelsList.appendChild(row);
      }
    } catch (err) {
      if (activePanel !== "nextcloud-calendar") return;
      els.panelsList.innerHTML = "";
      const errorEl = document.createElement("p");
      errorEl.className = "panels-empty";
      errorEl.textContent = err.message;
      els.panelsList.appendChild(errorEl);
    }
  }

  try {
    const data = await api("nextcloud/calendars");
    if (activePanel !== "nextcloud-calendar") return;
    const calendars = data.calendars || [];
    select.innerHTML = calendars.length
      ? calendars.map((cal) => `<option value="${escapeAttr(cal.display_name || "")}">${escapeHtml(cal.display_name || "Untitled")}</option>`).join("")
      : `<option value="">No calendars available</option>`;
    if (calendars.length) {
      loadEvents(select.value);
    }
  } catch (err) {
    if (activePanel !== "nextcloud-calendar") return;
    select.innerHTML = `<option value="">Unavailable</option>`;
    const errorEl = document.createElement("p");
    errorEl.className = "panels-empty";
    errorEl.textContent = err.message;
    els.panelsList.appendChild(errorEl);
  }
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    loadEvents(select.value);
  });
}

async function loadNextcloudFilesPanel() {
  els.panelsAdd.innerHTML = "";
  els.panelsList.innerHTML = "";
  const form = document.createElement("form");
  form.className = "panels-add-form";
  form.innerHTML = `<input type="text" name="path" placeholder="Path (blank for root)"><button type="submit">Browse</button>`;
  els.panelsAdd.appendChild(form);

  async function loadFiles(path) {
    els.panelsList.innerHTML = "";
    const loading = document.createElement("p");
    loading.className = "panels-empty";
    loading.textContent = "Loading…";
    els.panelsList.appendChild(loading);
    try {
      const data = await api(`nextcloud/files?path=${encodeURIComponent(path || "")}`);
      if (activePanel !== "nextcloud-files") return;
      const records = (data.records || []).filter((record) => record.display_name);
      els.panelsList.innerHTML = "";
      if (!records.length) {
        const empty = document.createElement("p");
        empty.className = "panels-empty";
        empty.textContent = "Nothing here.";
        els.panelsList.appendChild(empty);
        return;
      }
      for (const record of records) {
        const row = document.createElement("div");
        row.className = "panel-row";
        const meta = record.is_collection ? "Folder" : `${record.content_length || "?"} bytes · ${record.last_modified || ""}`;
        row.innerHTML = `
          <div class="panel-row-main">
            <strong>${escapeHtml(record.display_name)}</strong>
            <small>${escapeHtml(meta)}</small>
          </div>`;
        els.panelsList.appendChild(row);
      }
    } catch (err) {
      if (activePanel !== "nextcloud-files") return;
      els.panelsList.innerHTML = "";
      const errorEl = document.createElement("p");
      errorEl.className = "panels-empty";
      errorEl.textContent = err.message;
      els.panelsList.appendChild(errorEl);
    }
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    loadFiles(form.path.value.trim());
  });
  loadFiles("");
}

async function loadNextcloudContactsPanel() {
  els.panelsAdd.innerHTML = "";
  els.panelsList.innerHTML = "";
  const form = document.createElement("form");
  form.className = "panels-add-form panels-add-form-wide";
  form.innerHTML = `<select name="addressbook"><option value="">Loading address books…</option></select><button type="submit">Load contacts</button>`;
  els.panelsAdd.appendChild(form);
  const select = form.querySelector("select");

  async function loadContacts(addressbook) {
    els.panelsList.innerHTML = "";
    if (!addressbook) return;
    const loading = document.createElement("p");
    loading.className = "panels-empty";
    loading.textContent = "Loading…";
    els.panelsList.appendChild(loading);
    try {
      const data = await api(`nextcloud/contact-cards?addressbook=${encodeURIComponent(addressbook)}`);
      if (activePanel !== "nextcloud-contacts") return;
      const contacts = data.contacts || [];
      els.panelsList.innerHTML = "";
      if (!contacts.length) {
        const empty = document.createElement("p");
        empty.className = "panels-empty";
        empty.textContent = "No contacts found.";
        els.panelsList.appendChild(empty);
        return;
      }
      for (const contact of contacts) {
        const row = document.createElement("div");
        row.className = "panel-row";
        const meta = [(contact.emails || [])[0], (contact.phones || [])[0], contact.organization].filter(Boolean).join(" · ");
        row.innerHTML = `
          <div class="panel-row-main">
            <strong>${escapeHtml(contact.display_name)}</strong>
            <small>${escapeHtml(meta)}</small>
          </div>`;
        els.panelsList.appendChild(row);
      }
    } catch (err) {
      if (activePanel !== "nextcloud-contacts") return;
      els.panelsList.innerHTML = "";
      const errorEl = document.createElement("p");
      errorEl.className = "panels-empty";
      errorEl.textContent = err.message;
      els.panelsList.appendChild(errorEl);
    }
  }

  try {
    const data = await api("nextcloud/addressbooks");
    if (activePanel !== "nextcloud-contacts") return;
    const addressbooks = data.addressbooks || [];
    select.innerHTML = addressbooks.length
      ? addressbooks.map((book) => `<option value="${escapeAttr(book.display_name || "")}">${escapeHtml(book.display_name || "Untitled")}</option>`).join("")
      : `<option value="">No address books available</option>`;
    if (addressbooks.length) {
      loadContacts(select.value);
    }
  } catch (err) {
    if (activePanel !== "nextcloud-contacts") return;
    select.innerHTML = `<option value="">Unavailable</option>`;
    const errorEl = document.createElement("p");
    errorEl.className = "panels-empty";
    errorEl.textContent = err.message;
    els.panelsList.appendChild(errorEl);
  }
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    loadContacts(select.value);
  });
}

async function loadPanel(panel) {
  activePanel = panel;
  els.panelsTabs.querySelectorAll(".panel-tab").forEach((btn) => btn.classList.toggle("active", btn.dataset.panel === panel));
  els.confirmationsFilter.hidden = panel !== "confirmations";
  renderPanelAddForm(panel);
  els.panelsList.innerHTML = "";
  const loading = document.createElement("p");
  loading.className = "panels-empty";
  loading.textContent = "Loading…";
  els.panelsList.appendChild(loading);
  if (panel === "status") {
    await loadStatusPanel();
    return;
  }
  if (panel === "home") {
    loadHomePanel();
    return;
  }
  if (panel === "nextcloud-calendar") {
    await loadNextcloudCalendarPanel();
    return;
  }
  if (panel === "nextcloud-files") {
    await loadNextcloudFilesPanel();
    return;
  }
  if (panel === "nextcloud-contacts") {
    await loadNextcloudContactsPanel();
    return;
  }
  const config = PANEL_CONFIG[panel];
  try {
    const data = await api(panelEndpoint(panel));
    if (activePanel !== panel) return;
    const items = data[config.key] || [];
    els.panelsList.innerHTML = "";
    if (!items.length) {
      const empty = document.createElement("p");
      empty.className = "panels-empty";
      empty.textContent = "Nothing here.";
      els.panelsList.appendChild(empty);
      return;
    }
    for (const item of items) {
      els.panelsList.appendChild(renderPanelRow(panel, item));
    }
  } catch (err) {
    if (activePanel !== panel) return;
    els.panelsList.innerHTML = "";
    const errorEl = document.createElement("p");
    errorEl.className = "panels-empty";
    errorEl.textContent = err.message;
    els.panelsList.appendChild(errorEl);
  }
}

let panelsInitialized = false;

function openPanelsView() {
  els.panelsView.classList.add("open");
  els.messages.hidden = true;
  els.voiceStrip.hidden = true;
  els.composer.hidden = true;
  if (!panelsInitialized) {
    panelsInitialized = true;
    renderGroupTabs(activeGroup);
  }
  loadPanel(activePanel);
}

function closePanelsView() {
  els.panelsView.classList.remove("open");
  els.messages.hidden = false;
  els.voiceStrip.hidden = false;
  els.composer.hidden = false;
}

async function refreshConfirmationsBadge() {
  if (!state.token) {
    els.confirmationsBadge.hidden = true;
    return;
  }
  try {
    const data = await api("confirmations?status=pending&limit=100");
    const count = (data.confirmations || []).length;
    if (count > 0) {
      els.confirmationsBadge.hidden = false;
      els.confirmationsBadge.textContent = count > 99 ? "99+" : String(count);
    } else {
      els.confirmationsBadge.hidden = true;
    }
  } catch (err) {
    // Badge is best-effort; ignore transient failures.
  }
}

els.openPanels.addEventListener("click", () => {
  if (els.panelsView.classList.contains("open")) {
    closePanelsView();
  } else {
    openPanelsView();
  }
});
els.panelsClose.addEventListener("click", closePanelsView);
els.panelsGroups.addEventListener("click", (event) => {
  const btn = event.target.closest(".panel-group");
  if (!btn) return;
  switchGroup(btn.dataset.group);
});
els.panelsTabs.addEventListener("click", (event) => {
  const btn = event.target.closest(".panel-tab");
  if (!btn) return;
  loadPanel(btn.dataset.panel);
});
els.confirmationsFilterTabs.forEach((btn) => btn.addEventListener("click", () => {
  confirmationStatus = btn.dataset.status;
  els.confirmationsFilterTabs.forEach((b) => b.classList.toggle("active", b === btn));
  loadPanel("confirmations");
}));

els.token.value = state.token;
els.scope.value = state.scope;
els.openSettings.addEventListener("click", () => els.settingsView.classList.add("open"));
els.settingsClose.addEventListener("click", () => els.settingsView.classList.remove("open"));
els.saveSettings.addEventListener("click", () => saveSettings());
els.composer.addEventListener("submit", (event) => {
  event.preventDefault();
  sendPrompt();
});
els.clearChat.addEventListener("click", archiveVisibleChat);
els.prompt.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
    sendPrompt();
  }
});
els.voiceOnce.addEventListener("click", () => startListening());
els.voiceStop.addEventListener("click", stopListening);

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/atlas/mobile/sw.js?v=20260917.1", { updateViaCache: "none" }).then((registration) => {
    const checkForUpdate = () => registration.update().catch(() => {});
    checkForUpdate();
    window.addEventListener("focus", checkForUpdate);
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") checkForUpdate();
    });
    window.setInterval(checkForUpdate, 60000);
  }).catch(() => {});
  navigator.serviceWorker.addEventListener("controllerchange", () => {
    if (window.__atlasRefreshing) return;
    window.__atlasRefreshing = true;
    window.location.reload();
  });
}

setStatus(state.token ? "Ready." : "Set the ATLAS bearer token to connect.");
setVoiceState(SpeechRecognition ? "Tap 🎙 to talk." : "Speech recognition unavailable in this browser.");

refreshConfirmationsBadge();
window.setInterval(refreshConfirmationsBadge, 30000);
window.addEventListener("focus", refreshConfirmationsBadge);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") refreshConfirmationsBadge();
});
