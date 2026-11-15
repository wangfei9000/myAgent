---
name: browser
description: 用 Playwright MCP 打开网页、查看页面、点击和填写表单。
when-to-use: 用户要求打开网页、看页面内容、点击按钮、填写输入框、检查页面是否能打开时。
---

浏览器操作只走 Playwright MCP，工具名以 `mcp__playwright__` 开头。不要用 Shell 启动浏览器。

顺序：

1. 调用 `mcp__playwright__browser_navigate` 打开用户给出的网址。
2. 调用 `mcp__playwright__browser_snapshot` 读取页面。标题和可见内容以快照为准，不要凭记忆编造。
3. 需要点击或输入时，再用 `browser_click`、`browser_type`、`browser_press_key`。
4. 用简体中文说明打开了哪个地址、页面标题是什么。

页面地址变化时，运行时会先等页面和其中的框架加载完，再自动截图，并把加载完成后的快照放进工具结果。用这份快照里的 ref 继续。同一地址上再点击、输入或查看快照，不再截图。不要为了留档重复调用截图；只有用户明确要求再留一张时才自己调用。

`plan` 模式不能调用 MCP。需要操作浏览器时，权限必须是 `default` 或 `accept-edits`。
