# Codex 开发状态看板

离开座位前打开看板，通过大色块查看本机 Codex 会话：红色正在开发，绿色本轮完成待阅读。

本分支保留 Windows 本机优化，基于原安装提交 `9572bcf`，供统一合并评审；尚未合入主版本。完整差异、验证与兼容性限制见 [本机优化与合并说明](docs/windows-local-2026-09-09.md)。下面的 `main` 安装链接仍指向主版本；检查本分支请在当前检出目录运行 `board.py`。

## 在另一台电脑安装

支持 Mac 和 Windows。电脑上需要已安装并使用过 Codex，以及 Python 3.9+（建议 3.11 或以上）。只使用 Python 标准库，没有额外运行依赖。每台电脑监看自己的本机会话，不会把原电脑的会话同步过去。

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
| 红色 | 本轮正在运行 |
| 绿色 | 本轮正常结束且待阅读，不代表整个项目验收通过 |

只显示开发中和完成待阅读的顶层任务。一般完成项跟随 Codex 本机未读标记；已归档、内部子代理、等待输入、中断、失败及未知状态不显示色块。

当看板已观察过一个任务，又观察到它完成，且任务仍被选中或 Codex 已清除未读标记时，看板单独保留绿色。一直停留和页面刷新不算已读；开始新一轮时转红，明确切换到其他会话再回来时移出。该特殊状态按轮次保存在看板自己的缓存，重启后继续使用。

回访识别目前仅实现 Windows UIAutomation；标题不唯一、侧栏不可读、窗口最小化或快速切换未被轮询捕获时，会保留待阅读。非 Windows 环境不能通过回访清除此类特殊保留，须等待后续任务状态变化。没有观察到完成过程、且 Codex 已自动清除未读标记的历史任务不会补录。

## 实现与限制

- macOS / Windows，Python 3.9+；使用标准库，无第三方运行依赖。
- 生命周期读取沿用 Codex CLI 0.153.4 格式；一般未读标记只读桌面端 `.codex-global-state.json`。新增 Windows 选择识别使用系统 UIAutomation，不改 Codex 配置、未读标记或会话，不添加额外运行依赖。
- 本机服务默认 `http://127.0.0.1:17329`。实例信息及日志在当前用户的 `.codex/cache/status-board/`；Windows 默认位于 `%USERPROFILE%\.codex`，也尊重 `CODEX_HOME`。仓库不包含本机运行数据或凭证。
- 内部存储格式和桌面控件可能随 Codex 升级变化。全局读取失败时清空色块并提示；一般未读读取失败时仅显示运行项和本机已记住的特殊待阅读项。没有把 idle 直接当完成。
- 自然语言追问、未写入日志的审批等待无法可靠识别；绿色仅指本轮正常结束。
- 电脑睡眠时不会刷新；运行记录超过 5 分钟没有更新时隐藏该任务，后续记录恢复后重新判断。此分支不提供主线新增的逐项关注提示。

## 验证

```sh
python3 -m unittest discover -s tests -v
```

测试使用临时数据库和日志，不修改真实 Codex 会话。除动态状态、启停、并发启动、中文/空格路径及安装目录迁移外，仓库入口还包含 Skill 自带的 16 项未读与回访状态回归测试。UIA 选择信号在单元测试中注入，不自动操作真实桌面。

[GitHub Actions 测试记录](https://github.com/seeumia/codex-status-board/actions)：配置在 macOS 和 Windows 上运行 Python 3.11 / 3.13 测试；CI 测试不等于真实桌面回访验证。本机分支已在 Windows/Python 3.12.14 验证，原生 UIA 曾成功读取当前会话选择。新的 macOS 回访实现及跨版本桌面兼容性仍需统一合并时处理。
