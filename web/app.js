const providerSelect = document.querySelector("#provider");
const messageInput = document.querySelector("#message");
const form = document.querySelector("#task-form");
const startButton = document.querySelector("#start");
const resetButton = document.querySelector("#reset");
const statusNode = document.querySelector("#status");
const logNode = document.querySelector("#log");
const replyNode = document.querySelector("#reply");
const shotsNode = document.querySelector("#shots");

const PERMISSION = "default";
const MAX_ROUNDS = 100;

let polling = null;
let stickToBottom = true;

function setStatus(text, kind) {
  statusNode.textContent = text;
  statusNode.className = "status" + (kind ? " " + kind : "");
}

const providers = new Map();

function providerLabel(id) {
  const item = providers.get(id);
  return item ? item.label : "";
}

function renderProviders(items, current) {
  const selected = providerSelect.value || current;
  providerSelect.replaceChildren();
  providers.clear();
  for (const item of items || []) {
    if (!item || !item.id || providers.has(item.id)) continue;
    providers.set(item.id, item);
    providerSelect.append(new Option(item.label, item.id));
  }
  if (providers.has(selected)) providerSelect.value = selected;
  else if (current && providers.has(current)) providerSelect.value = current;
}

function renderRun(run) {
  const text = run.logs.length ? run.logs.join("\n") : (run.running ? "任务已开始…" : "还没有任务。");
  if (logNode.textContent !== text) {
    logNode.textContent = text;
    if (stickToBottom) logNode.scrollTop = logNode.scrollHeight;
  }
  if (run.error) {
    replyNode.textContent = run.error;
    replyNode.classList.add("error");
  } else if (run.reply) {
    replyNode.textContent = run.reply;
    replyNode.classList.remove("error");
  } else if (run.running) {
    replyNode.textContent = "还在执行。";
    replyNode.classList.remove("error");
  }
  shotsNode.replaceChildren();
  if (!run.screenshots.length) {
    const empty = document.createElement("p");
    empty.textContent = "还没有截图。";
    shotsNode.append(empty);
  } else {
    for (const shot of run.screenshots) {
      const link = document.createElement("a");
      link.href = shot.url;
      link.target = "_blank";
      link.rel = "noopener";
      const image = document.createElement("img");
      image.src = shot.url;
      image.alt = shot.name;
      const caption = document.createElement("span");
      caption.textContent = shot.name;
      link.append(image, caption);
      shotsNode.append(link);
    }
  }
  const busy = run.running;
  startButton.disabled = busy;
  resetButton.disabled = busy;
  providerSelect.disabled = busy;
  messageInput.disabled = busy;
  startButton.textContent = busy ? "执行中" : "开始执行";
  if (busy) setStatus("执行中", "running");
  else if (run.error) setStatus("失败", "error");
  else if (run.reply) setStatus("已完成");
  else setStatus(run.history ? `空闲 · 对话 ${run.history} 条` : "空闲");
}

async function readJson(response) {
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "请求失败");
  return payload;
}

async function refreshRun() {
  const run = await readJson(await fetch("/api/run"));
  renderRun(run);
  if (!run.running && polling) {
    clearInterval(polling);
    polling = null;
  }
}

function watchRun() {
  if (polling) clearInterval(polling);
  polling = setInterval(() => {
    refreshRun().catch((error) => setStatus(error.message, "error"));
  }, 1000);
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = messageInput.value.trim();
  if (!message || !providerSelect.value) return;
  startButton.disabled = true;
  setStatus("正在开始", "running");
  try {
    await readJson(await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message,
        provider: providerSelect.value,
        permission: PERMISSION,
        max_rounds: MAX_ROUNDS,
      }),
    }));
    await refreshRun();
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
    replyNode.textContent = "结果会显示在这里。";
    replyNode.classList.remove("error");
    await refreshRun();
  } catch (error) {
    setStatus(error.message, "error");
  }
});

logNode.addEventListener("scroll", () => {
  stickToBottom = logNode.scrollHeight - logNode.scrollTop - logNode.clientHeight < 40;
});

async function boot() {
  try {
    const [status, run] = await Promise.all([
      readJson(await fetch("/api/status")),
      readJson(await fetch("/api/run")),
    ]);
    renderProviders(status.providers, status.provider);
    renderRun(run);
    if (!run.running && !run.error && !run.reply) {
      const label = providerLabel(status.provider);
      setStatus(label ? `空闲 · ${label}` : "空闲");
    }
    if (run.running) watchRun();
  } catch (error) {
    setStatus(error.message, "error");
  }
}

boot();
