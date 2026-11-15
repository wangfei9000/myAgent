"""页面跳转后等加载完成并截图，启动时归档上一轮日志和截图。"""

from __future__ import annotations

import re
import time
from pathlib import Path

PAGE_IDLE_JS = """async () => {
  const isIdle = () => {
    const docs = [];
    const visit = (doc) => {
      if (!doc || docs.includes(doc)) return;
      docs.push(doc);
      let frames;
      try { frames = doc.querySelectorAll("iframe, frame"); } catch (e) { return; }
      for (const frame of frames) {
        try { visit(frame.contentDocument); } catch (e) {}
      }
    };
    visit(document);
    return docs.every((doc) => {
      if (doc.readyState !== "complete") return false;
      const view = doc.defaultView;
      if (!view || !view.performance) return true;
      return view.performance.getEntriesByType("resource").every((entry) => entry.responseEnd > 0);
    });
  };
  const start = Date.now();
  let stableSince = null;
  while (Date.now() - start < 15000) {
    if (isIdle()) {
      if (stableSince === null) stableSince = Date.now();
      if (Date.now() - start >= 2500 && Date.now() - stableSince >= 1500) {
        return { ok: true, waitedMs: Date.now() - start };
      }
    } else {
      stableSince = null;
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  return { ok: false, waitedMs: Date.now() - start };
}"""


def page_url_from_tool_result(text: str) -> str | None:
    match = re.search(r"Page URL:\s*(\S+)", text)
    if not match:
        return None
    url = match.group(1).rstrip(").,，。")
    if len(url) > 1 and url.endswith("/"):
        url = url[:-1]
    return url


def snapshot_fingerprint(text: str) -> str:
    """去掉每次都会变的 ref，用来判断页面内容是否还在变。"""
    text = re.sub(r"\[ref=[^\]]*\]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def screenshot_filename(seq: int, url: str, directory: str = "screenshots/model") -> str:
    path = url.split("?", 1)[0].split("://", 1)[-1]
    path = path.split("/", 1)[1] if "/" in path else "page"
    slug = re.sub(r"[^A-Za-z0-9]+", "-", path).strip("-").lower()[:40] or "page"
    return f"{directory}/{seq:03d}-{slug}.png"


def archive_previous_run(root: Path) -> Path | None:
    """启动时把上一份 logs/model.log 改名为 logs/N.log，截图目录改成 screenshots/N。"""
    logs = root / "logs"
    current = logs / "model.log"
    if not current.is_file():
        return None
    shot_root = root / "screenshots"
    n = 1
    while (logs / f"{n}.log").exists() or (shot_root / str(n)).exists():
        n += 1
    archived = logs / f"{n}.log"
    current.rename(archived)
    current_shots = shot_root / "model"
    dest = shot_root / str(n)
    if current_shots.is_dir():
        current_shots.rename(dest)
    if shot_root.is_dir():
        loose = [
            path
            for path in shot_root.iterdir()
            if path.is_file() and path.suffix.lower() == ".png"
        ]
        if loose:
            dest.mkdir(parents=True, exist_ok=True)
            for path in loose:
                path.rename(dest / path.name)
    return archived


def mcp_tool(agent, server: str, tool: str) -> str | None:
    return next(
        (
            name
            for name, (srv, tool_name) in agent.mcp.routes.items()
            if srv == server and tool_name == tool
        ),
        None,
    )

def wait_for_page_idle(agent) -> None:
    """等文档和同源 iframe 加载完，并安静一段时间。没有 evaluate 时直接返回。"""
    evaluate = mcp_tool(agent, "playwright", "browser_evaluate")
    if evaluate is None:
        return
    try:
        agent.mcp.call(evaluate, {"function": PAGE_IDLE_JS})
    except Exception as exc:
        agent.emit(f"等待页面加载失败：{exc}")

def wait_for_settled_snapshot(agent, initial: str) -> str:
    """页面内容连续两次不变后再把快照交给模型。"""
    snapshot = mcp_tool(agent, "playwright", "browser_snapshot")
    if snapshot is None:
        return initial
    wait_for_page_idle(agent)
    previous = None
    last = initial
    deadline = time.monotonic() + 15
    while True:
        try:
            last = agent.mcp.call(snapshot, {})
        except Exception as exc:
            agent.emit(f"加载后读取页面失败：{exc}")
            return last
        finger = snapshot_fingerprint(last)
        if previous is not None and finger == previous:
            return last
        if time.monotonic() >= deadline:
            return last
        previous = finger
        time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))

def capture_new_page(agent, exposed_name: str, result: str) -> str:
    """地址变化后先等页面加载完，再截图，并把加载完成后的快照交给下一步。"""
    server, tool = agent.mcp.routes.get(exposed_name, ("", ""))
    if server != "playwright" or tool in {"browser_take_screenshot", "browser_evaluate", "browser_snapshot"}:
        return result
    url = page_url_from_tool_result(result)
    if not url or url == agent._last_page_url:
        return result
    shot = mcp_tool(agent, "playwright", "browser_take_screenshot")
    if shot is None:
        return result
    settled = wait_for_settled_snapshot(agent, result)
    seq = agent._screenshot_seq + 1
    filename = screenshot_filename(seq, url, agent.screenshot_dir)
    (agent.root / filename).parent.mkdir(parents=True, exist_ok=True)
    try:
        agent.mcp.call(shot, {"filename": filename, "type": "png", "scale": "css"})
    except Exception as exc:
        return f"新页面截图失败：{exc}\n{settled}"
    agent._screenshot_seq = seq
    agent._last_page_url = url
    agent.emit(f"新页面已截图 {filename}")
    return (
        f"新页面截图：{filename}\n"
        "页面内容已稳定。请用下面这份快照里的 ref 继续，不要使用加载前的快照。\n"
        f"{settled}"
    )
