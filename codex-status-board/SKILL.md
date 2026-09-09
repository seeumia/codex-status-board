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

- 红色：本轮正在运行；绿色：收到明确的正常结束事件。绿色不表示项目验收完成，也不证明所有需求已实现。
- 黄色：可识别的同步输入等待、运行中断或明确失败。异步提问不会暂停任务，不因此标成等待。
- 灰色：读取失败或运行记录超过 5 分钟无变化；不能把无输出、失联或 idle 猜成成功。
- 打开服务时收录正在运行的本机顶层会话，此后加入新开始的会话，结束后保留。再次运行自动变红；历史空闲、归档和内部子代理不显示。浏览器刷新保留本次服务的列表，停止再启动则重新收录。
- 每 2 秒刷新；电脑睡眠时不会更新。自然语言中的追问、未出现在日志中的审批等待，第一版不能可靠判断。结束后请以会话正文确认下一步。

## 查看与停止

```sh
python3 "<本 Skill 绝对目录>/scripts/board.py" status
python3 "<本 Skill 绝对目录>/scripts/board.py" stop
```

关闭网页只关闭显示；用户明确说停止看板时才停止后台。停止只影响看板，Codex 开发会继续。

## 运行边界

启动器兼容 macOS 和 Windows；会话读取适配 Codex 0.153.4 的本地记录格式。Mac 已验证真实 Codex 数据；Windows 的启动/停止和记录解析由 GitHub Actions 检查，其他版本仍需首次启动确认。只读 `state_*.sqlite` 的元数据及其指向的会话生命周期记录；不向网页提供对话正文。仅监听 `127.0.0.1`。默认数据目录是当前用户主目录下的 `.codex`（Windows 通常为 `%USERPROFILE%\.codex`），设置 `CODEX_HOME` 时使用该目录；运行文件保存在其 `cache/status-board/` 子目录。不修改 Codex 配置、会话记录或系统防睡眠设置。

从 GitHub 安装时，复制仓库里的整个 `codex-status-board/` 文件夹，不能只复制本 SKILL.md。按 skill-installer 的 GitHub 路径安装方式即可，不需要第三方 Python 包。安装后先执行 `start` 并检查状态，再打开本机 URL；不以 GitHub 页面代替运行中的看板。

若启动失败，读取命令指定的 `server.log` 并检查错误；不要自动重启 Codex、启动另一套 Codex daemon 或安装第三方项目。端口冲突可用 `--port` 指定其他本地端口。Codex 升级后记录格式可能变化，读取异常必须如实报告，不能使用模拟数据冒充真实进度。
