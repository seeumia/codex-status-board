---
name: codex-status-board
description: Use when the user wants to open, inspect, or stop a local Codex session status board, or wants large red and green session tiles visible from a distance while away from the computer. 适用于“打开开发看板”“我要离开电脑，看开发状态”“停止状态看板”。
---

# Codex 离席状态看板

打开本机持续刷新的大字看板。后台由 Python 进程读取状态，不需要模型保持运行或定时发送消息。

## 打开

从本 Skill 所在目录解析 `scripts/board.py` 的绝对路径，用当前可用的 Python 3.9+ 执行：

```sh
python3 "<本 Skill 绝对目录>/scripts/board.py" start
```

Mac 通常使用 `python3`；Windows 通常使用 `py -3` 或 `python`，先确认实际可用命令和版本，再以同一解释器执行全部命令。路径始终加引号，支持中文和空格。Windows 没有 Python 时先说明缺少运行环境，不要宣称看板已安装可用。

命令会启动或复用本机实例，输出 JSON。确认 `running: true`、`error: null` 后，用当前环境的浏览器打开工具打开返回的 `url`。在 Codex 中可用 `open_in_codex` 打开该 URL；有 Computer Use 时可检查实际页面并保留为交付标签页。没有浏览器打开工具时，用同一命令增加 `--open`，并提供可点击链接。

用户离开座位前切换到该页面，点击“进入全屏”。只在实际观察到页面后声称已打开；仅有启动成功时说“看板已启动”，附链接。

## 状态与范围

- 看板只显示两种任务：红色为本轮正在开发；绿色为收到明确正常结束事件，且 Codex 仍标为未读，或刚完成时已被自动标为已读而暂时保留。绿色不表示项目验收完成，也不证明所有需求已实现。
- 普通未读任务在用户打开、由 Codex 标为已读后移除。看板观察到任务刚完成时已被 Codex 自动标为已读，则暂时保留至下一轮开发或其他明确生命周期状态，避免用户一直开着任务却漏看结果。只读桌面应用保存的未读任务列表，不修改已读标记。
- 尚未实现：对上述暂时保留任务识别“离开再进入后移除”。现有只读文件没有可靠的重新进入记录，不得宣称已完整实现。保护状态仅保存在本次后台进程内；停止再启动会重新依据未读列表收录。
- 等待输入、中断、失败、读取失败或运行记录超过 5 分钟无变化的任务不显示色块；不能把无输出、失联或 idle 猜成成功。异步提问不会暂停任务，不因此标成等待。
- 打开服务时收录状态可确认的运行中本机顶层会话，以及已完成且仍未读的会话（包括启动前完成的历史任务）。此后持续更新，再次运行自动变红；自动化任务（无论开发中、未读或已读）、归档和内部子代理不显示。浏览器刷新不清空列表，停止再启动则重新收录。
- 会话列表或已读状态读取异常时显示页顶提示，并暂不显示任务色块；恢复后自动更新。
- 每 2 秒刷新；电脑睡眠时不会更新。自然语言中的追问、未出现在日志中的审批等待，第一版不能可靠判断。结束后请以会话正文确认下一步。

## 查看与停止

```sh
python3 "<本 Skill 绝对目录>/scripts/board.py" status
python3 "<本 Skill 绝对目录>/scripts/board.py" stop
```

关闭网页只关闭显示；用户明确说停止看板时才停止后台。停止只影响看板，Codex 开发会继续。

## 运行边界

启动器兼容 macOS 和 Windows；会话读取适配 Codex 0.153.4 的本地记录格式。已读状态接入依据本机 0.153.3 随附桌面应用代码及实际持久化数据，Windows 新已读联动仍需真实桌面验证。只读 `state_*.sqlite` 元数据及其指向的会话生命周期记录，并读取 `.codex-global-state.json` 中 `electron-persisted-atom-state.unread-thread-ids-by-host-v1.local` 的未读任务 ID；不向网页提供对话正文或其他配置。仅监听 `127.0.0.1`。默认数据目录是当前用户主目录下的 `.codex`（Windows 通常为 `%USERPROFILE%\.codex`），设置 `CODEX_HOME` 时使用该目录；运行文件保存在其 `cache/status-board/` 子目录。不修改 Codex 配置、会话记录或系统防睡眠设置。

从 GitHub 安装时，复制仓库里的整个 `codex-status-board/` 文件夹，不能只复制本 SKILL.md。按 skill-installer 的 GitHub 路径安装方式即可，不需要第三方 Python 包。安装后先执行 `start` 并检查状态，再打开本机 URL；不以 GitHub 页面代替运行中的看板。

若启动失败，读取命令指定的 `server.log` 并检查错误；不要自动重启 Codex、启动另一套 Codex daemon 或安装第三方项目。端口冲突可用 `--port` 指定其他本地端口。Codex 升级后记录格式可能变化，读取异常必须如实报告，不能使用模拟数据冒充真实进度。
