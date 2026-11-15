const skillSelect = document.querySelector("#skill");
const skillHint = document.querySelector("#skill-hint");
const permissionSelect = document.querySelector("#permission");
const roundsInput = document.querySelector("#rounds");
const messageInput = document.querySelector("#message");
const form = document.querySelector("#task-form");
const startButton = document.querySelector("#start");
const resetButton = document.querySelector("#reset");
const statusNode = document.querySelector("#status");
const logNode = document.querySelector("#log");
const replyNode = document.querySelector("#reply");
const shotsNode = document.querySelector("#shots");

const skills = new Map();
let polling = null;
let stickToBottom = true;

function setStatus(text, kind) {
  statusNode.textContent = text;
  statusNode.className = "status" + (kind ? " " + kind : "");
}

function renderSkills(items) {
  const current = skillSelect.value;
  skillSelect.replaceChildren(new Option("不指定，由模型自己选", ""));
  skills.clear();
  for (const skill of items) {
    skills.set(skill.name, skill);
    const option = new Option(skill.name, skill.name);
    skillSelect.append(option);
  }
  if (skills.has(current)) skillSelect.value = current;
  showSkillHint();
}

function showSkillHint() {
  const skill = skills.get(skillSelect.value);
  skillHint.textContent = skill ? (skill.description || skill.when || "") : "不指定技能时，运行时会按任务内容检索。";
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
  startButton.disabled = run.running;
  resetButton.disabled = run.running;
  startButton.textContent = run.running ? "执行中" : "开始执行";
  if (run.running) setStatus("执行中", "running");
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
  startButton.disabled = true;
  setStatus("正在开始", "running");
  try {
    await readJson(await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: messageInput.value.trim(),
        skill: skillSelect.value,
        permission: permissionSelect.value,
        max_rounds: Number(roundsInput.value),
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

skillSelect.addEventListener("change", showSkillHint);
logNode.addEventListener("scroll", () => {
  stickToBottom = logNode.scrollHeight - logNode.scrollTop - logNode.clientHeight < 40;
});

async function boot() {
  try {
    const [status, skillPayload, run] = await Promise.all([
      readJson(await fetch("/api/status")),
      readJson(await fetch("/api/skills")),
      readJson(await fetch("/api/run")),
    ]);
    permissionSelect.value = status.permission;
    roundsInput.value = String(status.max_rounds);
    renderSkills(skillPayload.skills);
    renderRun(run);
    if (!run.running && !run.error && !run.reply) {
      setStatus(`${status.model} · ${status.permission}`);
    }
    if (run.running) watchRun();
  } catch (error) {
    setStatus(error.message, "error");
  }
}

boot();
