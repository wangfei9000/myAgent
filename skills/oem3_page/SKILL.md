---
name: oem3_page
description: 在 OEM3 按菜单名配置页面。用 layoutCode 找到 Layout，再查看 Panel、Component 和 Button。
when-to-use: 用户要求配置 OEM3 菜单或页面、查看某个 layoutCode 的 Layout、Panel、组件代码，或按现有页面做一套新页面时。
---

站点是 https://oem3.yunfangdata.com。只使用以 `mcp__playwright__` 开头的浏览器工具。每次点击或输入前先 `browser_snapshot`，只用快照里的 ref。页面地址变化时，运行时会先等页面和其中的框架加载完，再截图，并把加载完成后的快照放进工具结果。用这份快照里的 ref 继续，不要为了留档再调用截图。

`plan` 模式不能调用这些工具。配置页面用 `default` 权限。

标识要分清，不能混用：

- 菜单上的业务地址用 `layoutCode` 决定加载哪套配置。同一个 Code 就是同一个 Layout。
- `/layout/show/{数字ID}` 的数字 ID 不是 layoutCode。`layoutType` 决定它是 Layout、Panel 还是 Component。
- 示例「建筑物列表」也可以叫「小区列表」。业务地址是 `/flexObject/index?layoutCode=F5E61DEC401C4008AF3F5F38A7A2FEEA`，Layout 详情是 `/layout/show/299542?layoutType=Layout`，Panel 详情是 `/layout/show/299568?layoutType=LayoutPanelDetail`，第一个组件是 `/layout/show/299562?layoutType=ComponentDetail`。这些 ID 只属于这个例子。

配置前先只读，把链路记下来。用户没有明确说「确认创建」或「确认修改」时，不点任何保存。

1. 打开 https://oem3.yunfangdata.com。先确定登录账号：任务原文里写了用户名和密码，`browser_type` 的 `text` 必须原样使用任务里的那两个字符串。任务没写时，用户名用 `OEM3开发`，密码用 `Qwe123!@`。Territory 是房价网。换账号只改任务或本技能里的这两个字符串。
2. 页面上有 User Name、Password 和 Login 时，先 `browser_snapshot`。看到表单后必须发出工具调用，`text` 就用第 1 步确定的用户名和密码，不要改成 admin 或 admin123456。`browser_type` 和 `browser_click` 的元素参数名是 `target`，值用快照里的 ref，例如 `e13`。顺序是：向 User Name 的 `target` 输入第 1 步的用户名，向 Password 的 `target` 输入第 1 步的密码，再 `browser_click` Login 的 `target`。登录后的地址应离开登录页，页头能看到这个用户名和房价网。页头不一致就停止。已经登录且页头正确时，不要重复登录。
3. 按用户给的菜单名在侧栏找到菜单，读取它的 URL 和 `layoutCode`。菜单名、页面标题、Panel 名可以不同，以 URL 里的 `layoutCode` 为准。
4. 打开 `/layout/index?layoutType=Layout`，用这个 `layoutCode` 搜索。点开搜索结果进入 Layout 详情。不要凭示例猜测数字 ID。
5. 在 Layout 详情记下 Code、名称、标题，以及每个 Panel 的名称、模板、Pane Type、顺序。点 Panel 行打开详情，地址应是 `/layout/show/{id}?layoutType=LayoutPanelDetail`。打不开这一行就停止，不要改用页面容器上的 id。
6. 在 Panel 详情记下每个 Component 的槽位和 name，以及每个 Button 的文字、类型、link 或组件名。逐个点开组件，地址应是 `/layout/show/{id}?layoutType=ComponentDetail`，读取脚本。
7. 用简体中文输出清单：菜单名、业务 URL、layoutCode、Layout 数字 ID、Panel、组件槽位和脚本、按钮。然后停止，等用户确认。

用户确认创建一套新页面时，沿用刚读到的模板和槽位，不复用原来的 layoutCode、Panel ID、Component name。先建对象，保存后从详情页读回新的 Code 和 ID，再写入脚本、按钮和菜单 URL。业务地址的动作必须和 Panel 模板一致：搜索列表用 `/flexObject/index?layoutCode=新Code`。不要修改示例那条 `F5E61DEC401C4008AF3F5F38A7A2FEEA`，除非用户点名修改它。

不要点业务页上的保存、批量新增、价格补充、识别或生成报告。
