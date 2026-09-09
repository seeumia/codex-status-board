# Codex 开发状态看板

离开座位前打开看板，通过大色块查看本机 Codex 任务：红色开发中，绿色已开发完未读。已读状态自动跟随 Codex，看板无需操作。

## 在另一台电脑安装

支持 Mac 和 Windows。电脑上需要已安装并使用过 Codex 桌面端，以及 Python 3.9+（建议 3.11 或以上）。只使用 Python 标准库，没有额外运行依赖。每台电脑监看自己的本机会话，不会把原电脑的会话同步过去。

把下面这句话复制给另一台电脑上的 Codex：

> 请从 https://github.com/seeumia/codex-status-board/tree/main/codex-status-board 安装 codex-status-board Skill。先检查当前系统、Python 和本机 Codex 记录是否兼容，安装整个 Skill 文件夹，然后启动并打开开发状态看板。

仓库中的 `codex-status-board/` 才是 Skill 目录，里面包括 `SKILL.md`、`scripts/`、`assets/` 和 `agents/`。Codex 的 skill-installer 可直接安装此 GitHub 子目录链接。不要只下载 SKILL.md；安装后在下一轮对话中说“打开开发状态看板”。如果客户端尚未刷新技能列表，也可直接执行下面的命令。

如果安装器的直接下载遇到 Python 证书验证错误，可让 Codex 改用 skill-installer 的 `--method git` 安装方式；不需要关闭证书验证。

Mac（默认技能目录）：

```sh
python3 "${CODEX_HOME:-$HOME/.codex}/skills/codex-status-board/scripts/board.py" start --open
```

Windows PowerShell（默认技能目录，若有自定义 CODEX_HOME，请使用对应路径）：

```powershell
py -3 "$env:USERPROFILE\.codex\skills\codex-status-board\scripts\board.py" start --open
```

Windows 如果没有 `py` 而有 Python，可把 `py -3` 换成 `python`。未安装 Python 时需先安装后再启动。看板运行后默认地址是 http://127.0.0.1:17329 ，GitHub 链接用于安装，并不是在线看板地址。

## 使用

从项目目录运行：

```sh
python3 codex-status-board/scripts/board.py start --open
```

点击页面“进入全屏”。后台每两秒更新，不需要 Codex 持续回复。重复启动复用实例。以后可以对 Codex 说“用 $codex-status-board 打开开发状态看板”。新安装 Skill 是否被当前任务自动发现取决于客户端刷新；直接命令不受影响。

布局主要面向 1–10 个会话：色块根据窗口大小自动铺满一屏，最后一行自动加宽，不留空格，不需要滚动。字号和间距随色块调整；会话数量不设硬性上限。

```sh
python3 codex-status-board/scripts/board.py status
python3 codex-status-board/scripts/board.py stop
```

关闭网页不停止后台；停止命令只停止看板。第一版监看本机，未连接远程主机。

## 颜色说明

| 颜色 | 含义 |
| --- | --- |
| 红色 | 开发中，本轮正在运行 |
| 绿色 | 已开发完未读：本轮正常结束，并且 Codex 仍标记未读 |

只显示开发中和正常结束但未读的任务，启动前已经结束且未读的任务也会出现。Codex 标记已读后，完成任务自动移出；继续发消息开始新一轮时自动变红。已归档任务和内部子代理不显示。

看板不提供“标记已读”按钮或点击清除动作，不改变 Codex 的记录。当前打开的任务也遵循 Codex：如果 Codex 自动将它算作已读，结束后就会移出；看板不另设“必须离开后重新进入”的规则。刷新和重启看板仍按当前 Codex 状态筛选。

等待输入、中断、失败及相关任务状态不明时，在顶部显示提示，色块仍只有两种。记录读取失败或连接中断时明确提示，不把异常当成完成。绿色代表本轮正常结束，不代表项目验收通过。

## 实现与限制

- macOS / Windows，Python 3.9+；使用标准库，无第三方运行依赖。
- 当前验证版本：Codex CLI 0.153.4。只读本地 SQLite 元数据和 JSONL 生命周期事件，不改 Codex 配置和会话。
- 已读判断来自桌面端 `.codex-global-state.json` 中的 `electron-persisted-atom-state.unread-thread-ids-by-host-v1.local`。缺失或格式异常时，顶部说明已读状态不可用，已完成任务暂不显示；可确认仍在开发的任务继续显示。仅使用 CLI 而没有桌面端记录时，不能完整提供未读筛选。
- 本机服务默认 `http://127.0.0.1:17329`。实例信息及日志在当前用户的 `.codex/cache/status-board/`；Windows 默认位于 `%USERPROFILE%\.codex`，也尊重 `CODEX_HOME`。仓库不包含本机运行数据或凭证。
- 内部存储格式可能随 Codex 升级变化，异常时显示待确认。没有把 idle 直接当完成。
- 自然语言追问、未写入日志的审批等待无法可靠识别；绿色仅指本轮正常结束。
- 电脑睡眠时不会刷新；长时间推理但没有日志时会在顶部提示待确认，后续记录恢复后自动更新。

## 验证

```sh
python3 -m unittest discover -s tests -v
```

测试使用临时数据库、日志和桌面端已读记录，不修改真实 Codex 会话。覆盖动态状态、已读移出、再次开发、未读标记变化、异常提示、启停、重复和并发启动、中文/空格路径及安装目录迁移。

[GitHub Actions 测试记录](https://github.com/seeumia/codex-status-board/actions)：在 macOS 和 Windows 上运行 Python 3.11 / 3.13 测试。Mac 已验证真实 Codex 会话；Windows 自动测试使用模拟记录，仍需在目标电脑确认其 Codex 版本的本地记录可读取。Codex 内部格式可能变化，不能保证所有未来版本无须适配。
