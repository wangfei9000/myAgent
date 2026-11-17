const messageInput = document.querySelector("#message");
const taskForm = document.querySelector("#task-form");
const startButton = document.querySelector("#start");
const resetButton = document.querySelector("#reset");
const statusNode = document.querySelector("#status");
const logNode = document.querySelector("#log");
const filesNode = document.querySelector("#files");
const crumbsNode = document.querySelector("#crumbs");
const planHint = document.querySelector("#plan-hint");
const pageTitle = document.querySelector("#page-title");
const crumbPage = document.querySelector("#crumb-page");
const settingsForm = document.querySelector("#settings-form");
const providerSelect = document.querySelector("#provider");
const roundsInput = document.querySelector("#max-rounds");
const permissionSelect = document.querySelector("#permission");
const timeoutInput = document.querySelector("#timeout");
const saveButton = document.querySelector("#save-settings");
const settingsNote = document.querySelector("#settings-note");
const preview = document.querySelector("#preview");
const previewImage = document.querySelector("#preview-image");

const views = {
  database: document.querySelector("#view-database"),
  settings: document.querySelector("#view-settings"),
};
const titles = { database: "数据库", settings: "设置" };

let stream = null;
let stickToBottom = true;
let currentDir = "";
let settings = null;
let wasRunning = false;

function setStatus(text, kind) {
  statusNode.textContent = text;
  statusNode.className = kind ? "status " + kind : "status";
}

async function readJson(response) {
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "请求失败");
  return payload;
}

function showView(name) {
  const view = views[name] ? name : "database";
  for (const [key, node] of Object.entries(views)) {
    node.hidden = key !== view;
  }
  for (const button of document.querySelectorAll(".nav-item")) {
    const active = button.dataset.view === view;
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  }
  pageTitle.textContent = titles[view];
  crumbPage.textContent = titles[view];
  if (location.hash !== "#" + view) history.replaceState(null, "", "#" + view);
}

function icon(kind) {
  const paths = {
    dir: '<path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H10l2 2h6.5A2.5 2.5 0 0 1 21 9.5v8A2.5 2.5 0 0 1 18.5 20h-13A2.5 2.5 0 0 1 3 17.5v-10Z"/>',
    pdf: '<path d="M7 3h7l5 5v13a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Z"/><path d="M14 3v6h6"/><path d="M8 17v-4h2a1.5 1.5 0 0 1 0 3H8"/><path d="M13 13v4"/><path d="M16 13h2.2a1 1 0 0 1 0 2H16"/>',
    doc: '<path d="M7 3h7l5 5v13a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Z"/><path d="M14 3v6h6"/><path d="M8 13h8M8 17h6"/>',
    sheet: '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M4 9h16M4 15h16M10 3v18"/>',
    code: '<path d="m8 8-4 4 4 4"/><path d="m16 8 4 4-4 4"/><path d="m13 6-2 12"/>',
    sql: '<ellipse cx="12" cy="6" rx="7" ry="3"/><path d="M5 6v6c0 1.7 3.1 3 7 3s7-1.3 7-3V6"/><path d="M5 12v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6"/>',
    text: '<path d="M7 3h7l5 5v13a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Z"/><path d="M14 3v6h6M8 13h8M8 17h8"/>',
    archive: '<path d="M4 7h16v12a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7Z"/><path d="M4 7l2-3h12l2 3M12 11v6M10 14h4"/>',
    file: '<path d="M7 3h7l5 5v13a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Z"/><path d="M14 3v6h6"/>',
  };
  const colors = {
    dir: "#2563eb",
    pdf: "#dc2626",
    doc: "#2563eb",
    sheet: "#15803d",
    code: "#0f172a",
    sql: "#d97706",
    text: "#64748b",
    archive: "#b45309",
    file: "#d97706",
  };
  const wrap = document.createElement("span");
  const color = colors[kind] || colors.file;
  wrap.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="${color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${paths[kind] || paths.file}</svg>`;
  return wrap.firstChild;
}

function formatSize(size) {
  if (size == null) return "文件夹";
  if (size < 1024) return size + " B";
  if (size < 1024 * 1024) return (size / 1024).toFixed(1) + " KB";
  return (size / 1024 / 1024).toFixed(1) + " MB";
}

function renderCrumbs(path) {
  crumbsNode.replaceChildren();
  const root = document.createElement("button");
  root.type = "button";
  root.textContent = "exports";
  root.addEventListener("click", () => openDir(""));
  crumbsNode.append(root);
  if (!path) return;
  const parts = path.split("/");
  parts.forEach((part, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = part;
    const next = parts.slice(0, index + 1).join("/");
    button.addEventListener("click", () => openDir(next));
    crumbsNode.append(button);
  });
}

function renderFiles(payload) {
  currentDir = payload.path || "";
  renderCrumbs(currentDir);
  filesNode.replaceChildren();
  const entries = [];
  if (payload.parent !== null && payload.parent !== undefined && currentDir) {
    entries.push({ name: "上一级", path: payload.parent, kind: "dir", size: null, up: true });
  }
  entries.push(...(payload.items || []));
  if (!entries.length) {
    const empty = document.createElement("p");
    empty.className = "empty-files";
    empty.textContent = currentDir ? "这个目录是空的。" : "exports 里还没有文件。";
    filesNode.append(empty);
    return;
  }
  for (const item of entries) {
    const tile = item.kind === "image" || item.kind === "dir"
      ? document.createElement("button")
      : document.createElement("a");
    tile.className = "file-tile";
    if (tile.tagName === "BUTTON") tile.type = "button";
    else {
      tile.href = "/api/files/content?path=" + encodeURIComponent(item.path);
      tile.download = item.name;
    }
    const visual = document.createElement("span");
    visual.className = item.kind === "image" ? "file-thumb" : "file-icon";
    if (item.kind === "image") {
      const image = document.createElement("img");
      image.src = "/api/files/content?path=" + encodeURIComponent(item.path);
      image.alt = item.name;
      visual.append(image);
    } else {
      visual.append(icon(item.kind));
    }
    const name = document.createElement("span");
    name.className = "file-name";
    name.textContent = item.name;
    const meta = document.createElement("span");
    meta.className = "file-meta";
    meta.textContent = item.up ? "返回" : formatSize(item.size);
    tile.append(visual, name, meta);
    if (item.kind === "dir") {
      tile.addEventListener("click", () => openDir(item.path));
    } else if (item.kind === "image") {
      tile.addEventListener("click", () => {
        previewImage.src = "/api/files/content?path=" + encodeURIComponent(item.path);
        previewImage.alt = item.name;
        preview.showModal();
      });
    }
    filesNode.append(tile);
  }
}

async function openDir(path) {
  const query = path ? "?path=" + encodeURIComponent(path) : "";
  const payload = await readJson(await fetch("/api/files" + query));
  renderFiles(payload);
}

function renderRun(run) {
  const parts = [];
  if (run.logs.length) parts.push(run.logs.join("\n"));
  if (run.error) parts.push(run.error);
  else if (run.reply) parts.push(run.reply);
  const text = parts.length ? parts.join("\n\n") : (run.running ? "任务已开始…" : "还没有任务。");
  if (logNode.textContent !== text) {
    logNode.textContent = text;
    if (stickToBottom) logNode.scrollTop = logNode.scrollHeight;
  }
  const busy = run.running;
  startButton.disabled = busy;
  resetButton.disabled = busy;
  messageInput.disabled = busy;
  saveButton.disabled = busy;
  providerSelect.disabled = busy;
  roundsInput.disabled = busy;
  permissionSelect.disabled = busy;
  timeoutInput.disabled = busy;
  startButton.textContent = busy ? "执行中" : "运行";
  if (busy) setStatus("执行中", "running");
  else if (run.error) setStatus("失败", "error");
  else if (run.reply) setStatus("已完成");
  else setStatus(run.history ? "空闲 · 对话 " + run.history + " 条" : "空闲");
  if (wasRunning && !busy) openDir(currentDir).catch(() => {});
  wasRunning = busy;
}

function fillSettings(payload) {
  settings = payload;
  providerSelect.replaceChildren();
  for (const item of payload.providers || []) {
    providerSelect.append(new Option(item.label, item.id));
  }
  if ([...providerSelect.options].some((option) => option.value === payload.provider)) {
    providerSelect.value = payload.provider;
  }
  roundsInput.value = payload.max_rounds;
  permissionSelect.value = payload.permission;
  timeoutInput.value = payload.timeout;
  planHint.hidden = payload.permission !== "plan";
}

function watchRun() {
  if (stream) stream.close();
  stream = new EventSource("/api/run/stream");
  stream.addEventListener("run", (event) => {
    renderRun(JSON.parse(event.data));
  });
  stream.onerror = () => setStatus("正在重连进度通道", "error");
}

document.querySelectorAll(".nav-item").forEach((button) => {
  button.addEventListener("click", () => showView(button.dataset.view));
});
window.addEventListener("hashchange", () => showView(location.hash.slice(1)));

taskForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = messageInput.value.trim();
  if (!message) return;
  startButton.disabled = true;
  setStatus("正在开始", "running");
  try {
    await readJson(await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, skill: "pg_query" }),
    }));
    watchRun();
  } catch (error) {
    setStatus(error.message, "error");
    startButton.disabled = false;
  }
});

resetButton.addEventListener("click", async () => {
  try {
    await readJson(await fetch("/api/reset", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    }));
    const run = await readJson(await fetch("/api/run"));
    renderRun(run);
  } catch (error) {
    setStatus(error.message, "error");
  }
});

settingsForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  saveButton.disabled = true;
  settingsNote.textContent = "正在保存…";
  try {
    const payload = await readJson(await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        provider: providerSelect.value,
        permission: permissionSelect.value,
        max_rounds: Number(roundsInput.value),
        timeout: Number(timeoutInput.value),
      }),
    }));
    fillSettings(payload);
    settingsNote.textContent = "已保存到 web_settings.json";
  } catch (error) {
    settingsNote.textContent = error.message;
  } finally {
    saveButton.disabled = false;
  }
});

logNode.addEventListener("scroll", () => {
  stickToBottom = logNode.scrollHeight - logNode.scrollTop - logNode.clientHeight < 40;
});

async function boot() {
  showView(location.hash.slice(1) || "database");
  try {
    const [payload, run] = await Promise.all([
      readJson(await fetch("/api/settings")),
      readJson(await fetch("/api/run")),
      openDir(""),
    ]);
    fillSettings(payload);
    renderRun(run);
    watchRun();
  } catch (error) {
    setStatus(error.message, "error");
  }
}

boot();
