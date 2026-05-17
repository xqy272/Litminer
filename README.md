# Litminer

中文 | [English](README.en.md)

> 2026-05 更新：本项目按 “AI Agent 使用的科研文献信息获取 skill” 继续收敛。新的主流程会额外生成
> `query_plan.json`、`artifacts_index.json`、`field_provenance.json`、`publisher_adapters.json`，支持
> `--time-budget-seconds`、`--stop-after-stage`、`--max-crossref-rows`、
> `--max-unpaywall-rows`、本地 cache 和 provider 失败恢复等可控运行参数；MCP 也提供
> `litminer_start_run` / `litminer_run_status` 这类后台任务工具，适合长检索。
> 首次或 Windows 环境不确定时，先运行 `python -m litminer.engine.bootstrap`。

Litminer 是一个面向 AI Agent 的科研文献信息获取 skill。它不是综述生成器，也不是 PDF 阅读器；它提供的是一套可复用、可追踪、可验证的文献发现和处理操作契约，让 Claude Code、Codex 等 Agent 不必只依赖通用 WebSearch/WebFetch 来处理科研检索任务。

一句话定位：Litminer 负责把“什么时候检索、怎么检索、如何核验、怎样记录失败、交付哪些证据文件”变成 Agent 可执行的 skill；最终科学判断仍由 Agent 和用户完成。

典型用途：

- 从 OpenAlex、Semantic Scholar、arXiv、Europe PMC 等专业渠道发现候选文献。
- 通过 Crossref 验证 DOI、题名、期刊、年份、文章类型等书目信息。
- 通过 Unpaywall 标注 OA 状态和结构化访问线索。
- 去重、合并、语义初筛、排序、汇总、生成处理报告。
- 构建 DOI/出版社页面证据队列，供 Agent 后续检查文章页面。
- 作为 Claude Code、Codex 或其他支持 skill/MCP 的工具的本地科研检索能力。

## Skill-first 设计

Litminer 首先是 skill，而不是单纯的命令行工具或 Python 包。项目里的 CLI、MCP server、配置文件和测试都是为了支撑 Agent 稳定执行这个 skill：

- `SKILL.md` 是 Agent 的主入口，规定触发条件、能力边界、运行档位、失败降级和交付规则。
- `CLAUDE.md` 是更详细的 Agent 操作指南，适合 Claude Code、Codex 或其他代码型 Agent 阅读。
- `litminer/engine/` 和 `litminer/sources/` 是确定性执行层，负责把检索、去重、核验、报告生成做成可复跑步骤。
- `litminer/sources/mcp/server.py` 是可选工具层，让 Agent 用结构化工具调用代替手写命令。

因此，评价本项目时不应只看“某个脚本能否搜到论文”，还要看 Agent 是否能判断何时使用、如何从失败中恢复、如何把候选和已验证证据分开交付。

## 能力概览

| 能力 | 主要模块/工具 | 说明 |
|------|---------------|------|
| API 候选发现 | `api_discovery.py`、OpenAlex、Semantic Scholar、arXiv、Europe PMC | 多来源统一输出，保留查询 trace 和 provider 状态。 |
| 元数据验证 | Crossref | 核验 DOI、标题、期刊、年份和文章类型；缺 DOI 时可做标题恢复。 |
| OA 线索 | Unpaywall | 标注 OA 状态、最佳 OA URL、PDF URL、host type 和 license。 |
| 去重与合并 | `dedupe_papers.py` | 优先按 DOI 去重；无 DOI 时避免仅凭孤立标题误合并。 |
| 语义初筛 | `semantic_triage.py` | 使用调用者提供的 required/optional/negative 概念打标签和排序。 |
| 期刊指标 | `journal_metrics.py` | 只使用本地已验证指标 CSV，不猜测 IF/JCR。 |
| 出版社页面队列 | `build_publisher_queue.py`、`publisher_probe.py` | 构建 DOI/出版社页面证据队列，可选轻量探测 PDF/SI 链接。 |
| Agent/MCP 接口 | `litminer.sources.mcp.server` | 把上述能力暴露为 stdio JSON-RPC 工具，并限制文件访问在工作区内。 |

## 先接入 Skill

Litminer 的根目录已经包含 `SKILL.md`，因此整个项目目录就是 skill 目录。最小安装就是把仓库 clone 到 Agent 会扫描的 skills 目录；这个步骤只会写入该目录下的项目文件，不会执行 `pip install`，也不会修改全局 Python 环境。只有当 Agent 真正运行 Litminer 脚本或 MCP 服务时，才需要本机有 Python 3.10+。

### 接入 Claude Code

Claude Code 的 skill 目录需要包含 `SKILL.md`。推荐把 Litminer 安装为用户级 skill，或者安装到某个目标项目的 `.claude/skills/` 下。

用户级安装：

```bash
# macOS / Linux
mkdir -p ~/.claude/skills
git clone https://github.com/xqy272/Litminer.git ~/.claude/skills/litminer
```

```powershell
# Windows PowerShell
New-Item -ItemType Directory -Force "$HOME\.claude\skills" | Out-Null
git clone https://github.com/xqy272/Litminer.git "$HOME\.claude\skills\litminer"
```

项目级安装：

```bash
# 在目标项目根目录运行
mkdir -p .claude/skills
git clone https://github.com/xqy272/Litminer.git .claude/skills/litminer
```

如果你已经把 Litminer 克隆在其他位置，也可以用软链接或目录联接指向该目录。安装后重启或刷新 Claude Code 的 skills，然后用自然语言触发，例如：

```text
使用 Litminer 检索 2026 年以来关于 enzyme stability external validation 的论文，并生成 publisher queue。
```

Claude Code 的详细运行策略见本项目的 [CLAUDE.md](CLAUDE.md)。Skill 本身的机器可读说明见 [SKILL.md](SKILL.md)。

### 接入 Codex

Codex 会扫描用户级 `$HOME/.agents/skills`，也会扫描当前仓库里的 `.agents/skills`。把 Litminer clone 到这些目录后，Codex 就能发现包含 `SKILL.md` 的 skill。

用户级安装：

```bash
# macOS / Linux
mkdir -p ~/.agents/skills
git clone https://github.com/xqy272/Litminer.git ~/.agents/skills/litminer
```

```powershell
# Windows PowerShell
New-Item -ItemType Directory -Force "$HOME\.agents\skills" | Out-Null
git clone https://github.com/xqy272/Litminer.git "$HOME\.agents\skills\litminer"
```

项目级安装：

```bash
# 在目标项目根目录运行
mkdir -p .agents/skills
git clone https://github.com/xqy272/Litminer.git .agents/skills/litminer
```

安装后重启或刷新 Codex 的 skills。之后可以直接请求：

```text
用 Litminer 查找某主题的近年论文，要求验证 DOI、标注 OA 链接，并输出 publisher queue。
```

Codex 也提供 `$skill-installer`，可以从其他仓库下载 skill；但本项目文档推荐直接 `git clone`，因为路径明确、便于审查和更新。`[[skills.config]]` 主要用于对已发现的 skill 做启用/禁用覆盖，不是安装 Litminer 的必要步骤。后续如果希望提供真正的一键安装、MCP 配置打包或更完整的分发元数据，应把 Litminer 进一步打包为 Codex plugin。

### 安装后配置与检查

只使用 skill 时，通常不需要再写配置文件。用户只需要确认目录结构满足下面任意一种形式：

```text
~/.claude/skills/litminer/SKILL.md
~/.agents/skills/litminer/SKILL.md
目标项目/.claude/skills/litminer/SKILL.md
目标项目/.agents/skills/litminer/SKILL.md
```

然后重启或刷新 Claude Code / Codex 的 skills，直接用自然语言要求“使用 Litminer”即可。

安装成功后可以在 Litminer 目录运行一次本地检查和离线冒烟测试：

```bash
python -m litminer.engine.bootstrap
python -m litminer.engine.doctor
python -m litminer.engine.offline_smoke
```

这些命令不需要 API key。`offline_smoke` 不访问网络，会用内置样本在当前工作区的 `.litminer/runs/offline_smoke/` 下生成 `processing_report.md` 和 `publisher_queue.csv`。

### Windows 首次运行建议

Windows 上最常见的问题不是 Litminer 逻辑，而是 Python 命令、工作区路径、编码和 Agent 网络权限。

先确认 Python 3.10+ 可用：

```powershell
py -3 --version
python --version
```

如果 `python` 不可用但 `py -3` 可用，把命令中的 `python -m ...` 改成 `py -3 -m ...`；MCP 配置里的 `command` 也可以直接写完整解释器路径，例如 `C:/Users/you/AppData/Local/Programs/Python/Python312/python.exe`。

建议在 PowerShell 会话里先设置工作区、联系邮箱和 UTF-8 输出：

```powershell
$env:LITMINER_WORKSPACE_ROOT = "D:\Projects\YourProject"
$env:LITMINER_CONTACT_EMAIL = "you@example.org"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
```

然后运行自检：

```powershell
py -3 -m litminer.engine.doctor --workspace "D:\Projects\YourProject" --explain-path ".litminer\config.json"
py -3 -m litminer.engine.offline_smoke
```

真实 API 检索需要 Agent 环境允许访问 OpenAlex、Crossref、Semantic Scholar、Unpaywall 和 DOI/出版社页面；这类网络权限由 Codex/Claude Code/宿主环境控制，Litminer 只能在失败时把 provider 状态写入 trace 和 report。

如果 Codex 已发现该 skill 但你想显式启用或禁用它，可以在用户级 `~/.codex/config.toml` 或项目级 `.codex/config.toml` 中添加覆盖项：

```toml
[[skills.config]]
path = "C:/Users/your-name/.agents/skills/litminer"
enabled = true
```

正常安装不需要这段配置；只有排查 skill 启用状态或做团队项目覆盖时才建议添加。

### 可选：接入 MCP 工具

Skill 负责让 Agent 知道什么时候使用 Litminer；MCP 负责把 Litminer 的核心能力暴露为工具。二者可以同时使用，但 MCP 不是运行本项目的必要条件。

先验证本地 MCP 服务可启动：

```bash
python -m litminer.sources.mcp.test_server
```

MCP 有两个路径概念：

- Skill 安装目录：Litminer 代码所在位置，例如 `~/.claude/skills/litminer` 或 `~/.agents/skills/litminer`。
- 用户工作区：输入 CSV、输出报告和默认 `.litminer/` 运行产物所在位置。通过 `LITMINER_WORKSPACE_ROOT` 指定；未指定时使用 MCP 进程的 `cwd`。MCP 会拒绝访问工作区之外的路径。

Claude Code 可用 JSON 方式添加 stdio MCP 服务。下面示例把代码放在 skill 安装目录，把输出写入目标项目目录：

```bash
claude mcp add-json litminer '{
  "type": "stdio",
  "command": "python",
  "args": ["C:/Users/your-name/.claude/skills/litminer/litminer/sources/mcp/server.py"],
  "cwd": "D:/path/to/your/project",
  "env": {
    "LITMINER_WORKSPACE_ROOT": "D:/path/to/your/project",
    "LITMINER_MCP_TOOL_PROFILE": "workflow",
    "LITMINER_CONTACT_EMAIL": "you@example.org"
  }
}'
```

Codex 可在 `config.toml` 中添加：

配置文件位置通常是用户级 `~/.codex/config.toml`，或目标项目内的 `.codex/config.toml`。把下面的路径替换成你自己的 skill 安装目录和用户工作区目录：

```toml
[mcp_servers.litminer]
command = "python"
args = ["C:/Users/your-name/.agents/skills/litminer/litminer/sources/mcp/server.py"]
cwd = "D:/path/to/your/project"
env = { LITMINER_WORKSPACE_ROOT = "D:/path/to/your/project", LITMINER_MCP_TOOL_PROFILE = "workflow" }
env_vars = [
  "OPENALEX_API_KEY",
  "OPENALEX_MAILTO",
  "CROSSREF_MAILTO",
  "UNPAYWALL_EMAIL",
  "LITMINER_CONTACT_EMAIL",
]
```

如果使用虚拟环境，也可以把 `command` 改成虚拟环境里的 Python，例如 Windows 的 `C:/Users/your-name/.agents/skills/litminer/.venv/Scripts/python.exe`。

可直接参考模板文件：

- Codex MCP: [config/mcp.codex.example.toml](config/mcp.codex.example.toml)
- Claude Code MCP: [config/mcp.claude.example.json](config/mcp.claude.example.json)

MCP 默认使用 `LITMINER_MCP_TOOL_PROFILE=workflow`，`tools/list` 只展示工作流工具，避免 Agent 被底层阶段工具干扰。默认可见工具包括：

- 环境与工作区：`litminer_workspace_doctor`、`litminer_bootstrap`
- 长任务：`litminer_start_run`、`litminer_run_status`、`litminer_resume_run`、`litminer_cancel_run`
- 主流程：`litminer_run_lit_search`、`litminer_discover_api`、`litminer_semantic_triage`、`litminer_build_publisher_queue`
- 摘要与读取：`litminer_processing_report`、`litminer_agent_summary`、`litminer_read_csv_summary`

需要单独调用 OpenAlex/Semantic Scholar/Crossref/Unpaywall、期刊指标校验、字段 provenance 或 publisher adapter 等底层工具时，把环境变量设为 `LITMINER_MCP_TOOL_PROFILE=all`。更详细的工具分层见 [references/mcp-surface.md](references/mcp-surface.md)。

如果只想通过命令行脚本使用 Litminer，可以跳过 MCP。

相关官方文档：

- Claude Code Skills: <https://docs.claude.com/en/docs/claude-code/skills>
- Claude Code MCP: <https://docs.claude.com/en/docs/claude-code/mcp>
- Codex Agent Skills: <https://developers.openai.com/codex/skills>
- Codex config reference: <https://developers.openai.com/codex/config-reference#configtoml>
- Codex plugins: <https://developers.openai.com/codex/plugins/build>

## 文件落点与边界

Litminer 把“安装目录”和“运行工作区”分开处理：

- 安装目录只放代码和文档。执行 `git clone https://github.com/xqy272/Litminer.git .../litminer` 只会写入该 `litminer` 目录，不会创建检索结果、虚拟环境或全局 Python 包。
- CLI 默认产物写入当前工作区的 `.litminer/`，例如 `.litminer/runs/litminer_run/`、`.litminer/runs/offline_smoke/` 和 `.litminer/screenshots/`。
- 如果设置了 `LITMINER_WORKSPACE_ROOT`，CLI 的默认相对输出路径会基于该目录解析；如果未设置，则基于当前命令的 `cwd` 解析。
- MCP 模式下，所有文件参数都必须位于 `LITMINER_WORKSPACE_ROOT` 内；未设置时必须位于 MCP 进程 `cwd` 内。路径逃逸会被拒绝。
- 用户显式传入 `--output-dir`、`--output`、MCP 工具参数或绝对路径时，Litminer 会尊重该显式选择；这类情况不属于默认落点承诺。

因此，推荐把 MCP 的 `LITMINER_WORKSPACE_ROOT` 指向目标项目根目录，并把目标项目的 `.litminer/` 加入 `.gitignore`。不建议把检索结果默认写进 skill 安装目录，否则多项目结果会和代码混在一起。

## Python 环境与污染控制

只安装或识别 skill 不需要运行 `pip install`。上面的 `git clone` 只会把 Litminer 仓库文件放到 skills 目录，不会向全局 `site-packages` 写入包，也不会修改用户的 Python 配置。

运行脚本或 MCP 服务需要 Python 3.10+。Litminer 运行时仅依赖 Python 标准库，所以在 Litminer 目录内可以直接运行：

```bash
python -m litminer.engine.run_lit_search --help
python -m litminer.sources.mcp.test_server
```

只有当你想使用 console scripts，或安装 Ruff、mypy 等开发/验证工具时，才需要 `pip install`。为避免污染用户电脑，推荐在 Litminer 目录内创建本地虚拟环境：

```bash
# macOS / Linux
cd ~/.claude/skills/litminer  # 或 ~/.agents/skills/litminer
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

```powershell
# Windows PowerShell
cd "$HOME\.claude\skills\litminer"  # 或 "$HOME\.agents\skills\litminer"
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

开发和验证工具只建议安装在已激活的 `.venv` 内：

```bash
python -m pip install -e ".[dev]"
```

安装为 editable 包后，会得到这些命令行入口：

| 命令 | 等价模块 |
|------|----------|
| `litminer-run` | `python -m litminer.engine.run_lit_search` |
| `litminer-discover-api` | `python -m litminer.engine.api_discovery` |
| `litminer-triage` | `python -m litminer.engine.semantic_triage` |
| `litminer-mcp` | `python -m litminer.sources.mcp.server` |
| `litminer-doctor` | `python -m litminer.engine.doctor` |
| `litminer-smoke` | `python -m litminer.engine.offline_smoke` |
| `litminer-agent-summary` | `python -m litminer.engine.agent_summary` |
| `litminer-bootstrap` | `python -m litminer.engine.bootstrap` |
| `litminer-query-plan` | `python -m litminer.engine.query_plan` |
| `litminer-field-provenance` | `python -m litminer.engine.provenance` |
| `litminer-publisher-adapters` | `python -m litminer.engine.publisher_adapters` |
| `litminer-journal-metrics` | `python -m litminer.engine.journal_metrics` |

`.venv/` 已在 `.gitignore` 中忽略。如果你配置 MCP，也可以把 MCP 的 `command` 指向 `.venv` 里的 Python，以保证运行环境稳定，例如 Windows 的 `.venv/Scripts/python.exe` 或 macOS/Linux 的 `.venv/bin/python`。

当前推荐 clone 完整仓库，而不是让用户手工只复制部分文件。Litminer 至少需要 `SKILL.md`、`litminer/`、`config/` 等文件共同工作；完整仓库也便于用户审查源码、运行测试和拉取更新。如果后续要做更干净的用户侧安装体验，应提供独立 release 包或 plugin，而不是依赖用户手工挑选文件。

当前发布边界是“clone-as-skill 优先”。`pip install -e .` 适合本地开发和 console scripts，但普通 wheel 安装还不等同于完整 skill 安装，因为 Agent 发现仍需要 `SKILL.md`、配置模板和文档目录。除非后续发布独立 plugin/release 包，否则不要把 wheel 安装描述为完整的 Agent 接入方式。

## API、来源与出版社配置

Litminer 的配置分两层：

- 环境变量：放 API 联系邮箱和可选 API key，适合写在 shell、Agent/MCP 配置或系统环境变量里。
- 运行配置 JSON：放检索来源开关、限额、输出目录、出版社探测策略，适合按项目保存并通过 `--config` 传入。

### API 联系信息与密钥

多数来源不要求 API key。OpenAlex、Crossref 和 Unpaywall 建议或要求提供联系邮箱；Litminer 会优先使用专用变量，找不到时回退到 `LITMINER_CONTACT_EMAIL`。

| 变量 | 用途 | 是否必需 |
|------|------|----------|
| `LITMINER_CONTACT_EMAIL` | 通用联系邮箱，作为 OpenAlex/Crossref/Unpaywall 的回退值。 | 推荐 |
| `OPENALEX_MAILTO` | OpenAlex polite pool 联系邮箱。 | 推荐 |
| `CROSSREF_MAILTO` | Crossref User-Agent 联系邮箱。 | 推荐 |
| `UNPAYWALL_EMAIL` | Unpaywall 查询邮箱。未设置时 Unpaywall 阶段会标记为 skipped。 | 使用 Unpaywall 时必需 |
| `OPENALEX_API_KEY` | OpenAlex API key。普通使用通常不需要；如你的访问策略要求 key 再设置。 | 可选 |

可以从 [.env.example](.env.example) 复制变量名，但不要把真实邮箱或 key 提交到仓库。

macOS / Linux：

```bash
export LITMINER_CONTACT_EMAIL="you@example.org"
export UNPAYWALL_EMAIL="you@example.org"
# 可选
export OPENALEX_API_KEY="your-openalex-api-key"
```

Windows PowerShell：

```powershell
$env:LITMINER_CONTACT_EMAIL = "you@example.org"
$env:UNPAYWALL_EMAIL = "you@example.org"
# 可选
$env:OPENALEX_API_KEY = "your-openalex-api-key"
```

如果通过 MCP 使用 Litminer，也可以把这些变量写到 MCP 配置的 `env` 或 `env_vars` 中。`env` 适合固定值，`env_vars` 适合让 Agent 从本机环境转发变量名。

### 来源、运行限额和证据队列策略

默认配置在 [config/default.json](config/default.json)。建议不要直接把个人邮箱、API key 或某个课题的检索词写入这个文件；如果需要项目级配置，可以在你的工作区创建例如 `.litminer/config.json`：

这里的 `limits` 是运行预算和访问节流设置，不是语义检索条件。它限制的是“每个来源取多少条、哪些来源少跑一点、是否并发、页面探测跑多少、请求之间等多久”。真正控制主题语义的是运行时传入的 `--query`、`--query-file`、`--required-concept`、`--optional-concept`、`--negative-concept` 或 `--triage-profile`。

各类配置的边界：

- `channels` 决定启用哪些来源或处理阶段，例如 OpenAlex、Semantic Scholar、Crossref、Unpaywall、publisher probe。
- `limits` 决定运行成本和节流策略，例如每个 query 最多取多少候选、Semantic Scholar 最多处理几个 query、是否并行不同 provider、出版社页面探测间隔。
- `outputs` 决定默认输出目录和截图目录。
- `cache` 决定工作区本地缓存位置和 TTL；它只缓存 Crossref/Unpaywall 的正向 DOI 元数据，以及短期 provider 失败状态，不是证据库。
- `evidence` 决定证据队列策略，例如是否要求 DOI、哪些 triage 优先级进入 `publisher_queue.csv`、是否允许 metadata-blocked 行进入队列。
- `api` 决定 API key/contact email 的环境变量名，以及 OpenAlex 的 work type 过滤。

```json
{
  "channels": {
    "openalex": true,
    "semantic_scholar": true,
    "arxiv": true,
    "europe_pmc": false,
    "crossref": true,
    "unpaywall": true,
    "publisher_probe": true
  },
  "limits": {
    "max_results_per_query": 80,
    "semantic_query_limit": 3,
    "semantic_max_results": 50,
    "publisher_probe_limit": 20,
    "publisher_probe_sleep": 1.0,
    "strict_discovery": false,
    "parallel_providers": false,
    "provider_workers": null,
    "provider_failure_threshold": 2,
    "provider_rate_limit_cooldown_seconds": 60.0,
    "unpaywall_sleep": 0.1
  },
  "outputs": {
    "default_output_dir": ".litminer/runs/litminer_run",
    "screenshot_root": ".litminer/screenshots"
  },
  "cache": {
    "enabled": true,
    "cache_dir": ".litminer/cache",
    "ttl_days": 30.0,
    "provider_failure_ttl_seconds": 300.0
  },
  "evidence": {
    "require_doi_for_queue": true,
    "queue_priorities": "high,medium,needs_review",
    "include_metadata_blocked": false,
    "queue_strict_only": true
  }
}
```

也可以从 [config/example.user.json](config/example.user.json) 复制后按项目修改。

运行时传入：

```bash
python -m litminer.engine.run_lit_search \
  --mode balanced \
  --query "your literature query" \
  --year-from 2026 \
  --config .litminer/config.json
```

如果通过 MCP 调用，把工具参数里的 `config` 设为工作区内的 JSON 路径；该路径必须位于 `LITMINER_WORKSPACE_ROOT` 下，不能指向 skill 安装目录外的任意文件。

检查配置文件：

```bash
python -m litminer.engine.doctor --config .litminer/config.json
```

也可以不用 JSON，直接在单次运行中覆盖关键行为：

```bash
python -m litminer.engine.run_lit_search \
  --query "your literature query" \
  --year-from 2026 \
  --discovery-sources openalex,semantic_scholar,arxiv,europe_pmc \
  --max-results-per-query 80 \
  --openalex-work-types article \
  --enrich-unpaywall \
  --probe-publishers \
  --probe-limit 20 \
  --probe-sleep 1.0 \
  --fields-needed "dataset,external validation,benchmark"
```

关键运行参数的含义：

- `--mode fast|balanced|expanded|full`：运行预设。`fast` 用于首次试跑，OpenAlex 小批量发现并跳过 Crossref、Unpaywall、Semantic Scholar 和出版社探测；`balanced` 是默认验证型路径；`expanded`/`full` 会启用 Semantic Scholar 和 provider 并发，但不会自动打开 arXiv 或 Europe PMC 这类领域特定来源。
- `--fields-needed` / `page_required_fields`：告诉 Agent 后续看出版社页面时要关注哪些字段，例如数据集、验证方式、外部基准、补充材料链接。
- `max_results_per_query` / `--max-results-per-query`：限制每个 provider、每个 query 最多返回多少候选；它影响召回量和运行时间，不表达主题偏好。
- `semantic_query_limit`：只限制发给 Semantic Scholar 的 query 数量，用于降低限流风险；这里的 `semantic` 指 Semantic Scholar 来源，不是语义筛选。
- `semantic_max_results`：限制 Semantic Scholar 每个 query 最多返回多少候选。
- `unpaywall_sleep` / `--unpaywall-sleep`：限制 Unpaywall 请求间隔。
- `queue_priorities` / `--queue-priorities`：决定哪些 triage 优先级进入 `publisher_queue.csv`。
- `require_doi_for_queue` / `--allow-missing-doi`：默认要求 DOI 才进入出版社队列；如果明确允许缺 DOI，可用 `--allow-missing-doi`。
- `strict_discovery` / `--strict-discovery`：当 API 来源报错导致候选集不可靠时让流程失败，而不是只生成空结果报告。
- `parallel_providers` / `--parallel-providers`：可选地并行执行同一 query 下的不同 API provider；实现使用标准库线程，同一 provider 在不同 query 间仍保持串行。
- `provider_workers` / `--provider-workers`：限制 provider 并发线程数；默认等于同一 query 下启用的 provider 数量。
- `provider_failure_threshold` / `--provider-failure-threshold`：同一次发现运行中，某个 provider 连续失败达到阈值后跳过剩余调用，避免 Semantic Scholar 429 或网络故障拖垮整轮任务。
- `cache.enabled` / `--no-cache`：默认启用工作区本地轻量缓存。缓存只用于减少重复正向 DOI 元数据查询和短期 provider 失败重试；失败、not found、mismatch 不会作为长期证据缓存。如果已经修复网络、代理或 API key 问题，可用 `--no-cache` 强制重新访问来源。
- `cache_dir` / `--cache-dir`：缓存默认写到工作区 `.litminer/cache`，不应视为最终交付证据，也不建议提交到项目仓库。
- `provider_failure_cache_ttl_seconds` / `--provider-failure-cache-ttl-seconds`：短期记住 rate limit、network 和明确标记为 transient 的 provider 失败，避免 Agent 续跑时立刻重复打同一个失败来源。`auth` 和普通 `error` 默认不写入失败缓存，因为这些问题通常应修复后马上重试。
- `--resume`：复用输出目录中已经存在的阶段 CSV，并把复用记录写入 `run_manifest.json`；适合超时后继续跑。Litminer 会用运行签名校验 query、概念、年份、来源和关键策略是否一致，不一致时会拒绝自动复用。
- `--time-budget-seconds`：设置单次运行的总时间预算；预算耗尽后在阶段边界停止，并写出 partial 报告。
- `--stop-after-stage`：显式停在 `query_plan`、`discovery`、`dedupe`、`crossref`、`triage`、`queue` 等阶段，适合 Agent 分步执行。
- `--max-crossref-rows` / `--max-unpaywall-rows`：限制 Crossref/Unpaywall 批处理行数，未处理行标记为 `skipped_budget`，不会被静默丢弃。
- `--max-publisher-probe-rows`：限制出版商页面探测行数；如果同时设置 `--probe-limit`，以后者为准。
- `openalex_work_types` / `--openalex-work-types`：控制 OpenAlex 的 work type 过滤，默认 `article`；可传 `all` 关闭类型过滤。
- `queue_strict_only` / `--queue-strict-only` / `--queue-all-metric-statuses`：使用 `--min-if` 时默认只把 metric-pass 行送入 queue；如只想标注不硬过滤，可显式选择 queue all metric statuses。
- `publisher_probe` / `--probe-publishers`：只做轻量页面可达性、PDF/SI 链接提示探测，不解析 PDF，也不绕过付费墙。
- `publisher_probe_limit` / `--probe-limit` 和 `publisher_probe_sleep` / `--probe-sleep`：控制探测数量和请求间隔，避免过快访问出版社页面。

## 快速开始

在 Litminer 项目根目录运行：

```bash
python -m litminer.engine.run_lit_search \
  --mode fast \
  --query "machine learning enzyme stability external validation" \
  --year-from 2026 \
  --required-concept "validation=external validation|prospective validation" \
  --optional-concept "benchmark=benchmark|dataset" \
  --negative-concept "review=review article|survey" \
  --config config/default.json \
  --output-dir .litminer/runs/litminer_run
```

Windows 辅助命令：

```bat
pipeline.bat "machine learning enzyme stability external validation" 2026
```

上面的 query 和 concept 只是示例。实际使用时，Agent 应根据当前用户请求生成检索式和语义概念。

首次建议先用 `--mode fast` 跑通路径和语义概念；确认候选方向正确后，再用 `--mode balanced` 或 `--mode expanded`（`full` 作为兼容别名）扩大验证与召回。需要 arXiv 或 Europe PMC 时由 Agent 按领域显式加 `--include-arxiv` 或 `--include-europe-pmc`。也可以从已有候选续跑：

```bash
python -m litminer.engine.run_lit_search \
  --mode balanced \
  --resume \
  --input-csv .litminer/runs/litminer_run/deduped_candidates.csv \
  --required-concept "validation=external validation|prospective validation" \
  --negative-concept "review=review article|survey" \
  --output-dir .litminer/runs/litminer_verify
```

## 默认工作流

`litminer.engine.run_lit_search` 会执行一条完整的文献处理链：

1. 生成 `query_plan.json`，记录 Agent 派生的查询、概念、来源和 advisory source strategy。
2. API 发现候选文献，默认使用 OpenAlex。
3. DOI/标题去重，并合并互补字段。
4. Crossref 验证书目信息，缺 DOI 时尝试标题恢复。
5. 按用户提供的 required/optional/negative 概念做语义初筛。
6. 通过 Unpaywall 标注 OA 和访问线索。
7. 可选：用本地已验证期刊指标表做标注或过滤。
8. 构建出版社页面证据队列。
9. 生成 `feasibility_report.md`、`processing_report.md`、`agent_summary.json` 和 `artifacts_index.json`。

默认配置位于 [config/default.json](config/default.json)。配置只放基础设施参数，例如渠道开关、API 环境变量名、限额、默认输出目录。检索主题、领域词表和纳入/排除概念不应写入全局配置。

## 主要输出

| 文件 | 用途 |
|------|------|
| `api_candidates.csv` | API 发现候选。 |
| `api_discovery_trace.csv` | 查询、来源和状态追踪。 |
| `api_discovery_report.md` | API 发现报告。 |
| `deduped_candidates.csv` | DOI/标题去重并合并后的候选。 |
| `verified_candidates.csv` | Crossref 验证输出，失败或不匹配行会显式标记，不会默认晋级。 |
| `triaged_candidates.csv` | 带语义标签、优先级和元数据状态的审查面。 |
| `selected_candidates.csv` | 按优先级进入后续处理的候选。 |
| `oa_annotated_candidates.csv` | Unpaywall OA 状态和结构化访问线索。 |
| `metrics_annotated_candidates.csv` | 期刊指标标注结果。 |
| `publisher_queue.csv` | DOI/出版社页面证据队列。 |
| `publisher_queue_probed.csv` | 可选页面探测结果。 |
| `feasibility_report.md` | 可行性、数量和阻塞原因。 |
| `processing_report.md` | 来源、元数据健康、Crossref、triage、OA/access 和队列摘要。 |
| `agent_summary.json` | Agent 优先读取的机器可读状态摘要，包含 Trust Tiers、provider 健康、primary artifact 路径、告警、source strategy 和下一步建议。 |
| `artifacts_index.json` | 按 primary/supporting/debug 分层列出本次运行产物，帮助 Agent 先读关键入口、再按需进入大表或 debug 文件。 |
| `query_plan.json` | Agent 派生的查询、来源、概念表达式、运行预算和 advisory source strategy。 |
| `field_provenance.json` | 队列或探测结果中关键字段的来源与信任等级。 |
| `publisher_adapters.json` | 内置/外部出版商页面检查适配器及边界说明。 |
| `run_manifest.json` | 阶段状态、复用/跳过记录、行数、文件指纹、运行签名和参数摘要；用于安全续跑、超时恢复和审计。 |

如果通过 MCP 查看较大的 CSV，优先用 `litminer_read_csv_summary` 分页读取，而不是把完整 CSV 一次性塞进 Agent 上下文。

`query_plan.json` 中的 `source_strategy` 只提供检索策略提示：例如当前查询可能更适合补充 Europe PMC、arXiv 或 Semantic Scholar，或者当前只有单个 query、缺少 required concepts、年份过新导致元数据滞后风险较高。它不会自动改变检索范围；是否扩展来源仍由 Agent 结合用户任务决定。

## 常用命令

只运行 API 发现：

```bash
python -m litminer.engine.api_discovery \
  --query "user topic query" \
  --sources openalex,semantic_scholar,arxiv,europe_pmc \
  --year-from 2026 \
  --parallel-providers \
  --output .litminer/runs/litminer_run/api_candidates.csv \
  --trace-output .litminer/runs/litminer_run/api_discovery_trace.csv \
  --report-output .litminer/runs/litminer_run/api_discovery_report.md
```

对已有 CSV 做语义初筛：

```bash
python -m litminer.engine.semantic_triage \
  --input .litminer/runs/litminer_run/deduped_candidates.csv \
  --output .litminer/runs/litminer_run/triaged_candidates.csv \
  --required-concept "main=term1|term2" \
  --optional-concept "secondary=term3|term4" \
  --negative-concept "negative=term5|term6" \
  --year-from 2026 \
  --require-doi
```

构建出版社页面证据队列：

```bash
python -m litminer.engine.build_publisher_queue \
  --input .litminer/runs/litminer_run/oa_annotated_candidates.csv \
  --output .litminer/runs/litminer_run/publisher_queue.csv \
  --priorities high,medium,needs_review \
  --fields-needed "field_from_user_request"
```

如果跳过 Unpaywall，则把 `--input` 改成 `.litminer/runs/litminer_run/triaged_candidates.csv` 或 `.litminer/runs/litminer_run/selected_candidates.csv`。不要直接用只有 Crossref 字段的 `verified_candidates.csv` 配合 `--priorities`，因为优先级字段来自语义初筛阶段。

生成处理摘要：

```bash
python -m litminer.engine.processing_report \
  --output-dir .litminer/runs/litminer_run
```

## 内测限制、失败和重试

第一版内测建议先跑 `python -m litminer.engine.doctor` 和 `python -m litminer.engine.offline_smoke`。如果这两步失败，优先修复本地 Python、目录或配置问题，再运行真实检索。

### 耗时和超时

耗时主要来自四类因素：`query 数量 × provider 数量 × max_results_per_query` 的发现调用、Semantic Scholar 限流、Crossref/Unpaywall 对 DOI 逐条补充、以及可选的出版社页面探测。首次使用不要直接打开所有来源和页面探测，先用 `--mode fast` 验证工作区和语义筛选，再逐步放大。

如果 Agent 在固定时间窗口内超时，Litminer 仍会尽量保留阶段文件。当前工作流会在关键阶段刷新 `processing_report.md` 和 `agent_summary.json`，并持续写入带文件指纹的 `run_manifest.json`；完成或中途停止时还会写 `artifacts_index.json`。Crossref 和 Unpaywall 批处理会定期写 checkpoint，并通过 `.litminer/cache` 复用已稳定查询过的 DOI 元数据。如果只看到 `api_candidates.csv` 或 `deduped_candidates.csv`，可以手动重建摘要：

```bash
python -m litminer.engine.processing_report --output-dir .litminer/runs/litminer_run
python -m litminer.engine.agent_summary --output-dir .litminer/runs/litminer_run
```

然后用 `--resume` 或 `--input-csv` 从已有 CSV 继续后续阶段，避免重复访问发现 API。`--resume` 会检查 `run_manifest.json` 里的运行签名；如果本次用户条件已经改变，应使用新的 `--output-dir`，不要强行复用旧结果。

缓存和 resume 的边界不同：`--resume` 复用某次运行的阶段产物，适合同一用户条件下继续跑；cache 只是工作区本地查询加速和短期失败抑制，不代表论文证据已经核验。如果网络、代理、API key 或联系邮箱刚被修复，可以加 `--no-cache` 重新访问来源。

`processing_report.md` 里的 Trust Tiers 会把结果分成 `discovered_or_deduped`、`crossref_trusted`、`metric_pass`、`publisher_queue` 和 `publisher_probe_checked`。不要把发现候选数等同于已核验论文数。

### Workspace isn't working

这类问题通常分三种：

- Agent 自带的隔离 Linux workspace 无法启动：这是宿主 Agent 环境问题，Litminer 无法启动外部 workspace；可改用本机 Python/MCP，或让 Agent 直接调用 Windows 可见路径下的 Litminer。
- MCP 工作区根目录不对：设置 `LITMINER_WORKSPACE_ROOT`，并确保输入 CSV、配置文件和输出目录都在这个根目录下。
- 路径映射混用：Windows 用户应优先使用原生路径或工作区相对路径，不要把 Linux VM 映射路径传给 Windows MCP 进程。

排查命令：

```bash
python -m litminer.engine.doctor --workspace "D:/path/to/project" --explain-path "input.csv"
```

把 `input.csv` 替换成实际报错路径。通过 MCP 时调用 `litminer_workspace_doctor`，它会返回 `workspace_root`、默认输出目录、写入权限和每个路径是否位于工作区内。

常见情况：

- API 请求失败或限流：查看 `api_discovery_trace.csv` 的 `status_class`、`http_status`、`transient_error` 和 `next_action`。`rate_limited` 适合稍后续跑或减少请求量；`network` / `auth` 通常是 Agent 网络权限、代理/证书或 API key/contact email 问题；不要把失败来源的空结果当作最终事实。
- `api_discovery_trace.csv` 显示 `skipped_cached_provider_failure`：Litminer 命中了短期 provider 失败缓存，通常表示上一次同源同 query 刚遇到 rate limit、network 或明确 transient 的 provider 错误；等待 TTL 后续跑，或在确认环境已修复后用 `--no-cache`。`auth` 和普通 `error` 默认不会写入该失败缓存。
- Unpaywall 显示 `skipped_missing_email`：设置 `UNPAYWALL_EMAIL` 或 `LITMINER_CONTACT_EMAIL` 后重跑。
- Crossref `lookup_failed` 或 `mismatch`：保留阻塞状态，不要手工伪造 DOI；必要时扩大检索或人工核验。
- 出版社页面不可达：降低 `--probe-limit`，提高 `--probe-sleep`，或跳过自动探测后人工检查 `publisher_queue.csv`。
- 候选数量过少：增加查询词、启用 Semantic Scholar/arXiv/Europe PMC，或放宽 required concepts；不要为了凑数生成不存在的论文。
- MCP 路径报错：确认输入、输出和 `config` 都在 `LITMINER_WORKSPACE_ROOT` 下。

## 项目边界

Litminer 不做这些事：

- 不凭记忆回答实时论文、DOI、期刊指标或出版社页面证据问题。
- 不生成最终综述和最终纳入/排除判断。
- 不解析 PDF、OCR、补充材料或表格。
- 不绕过付费墙。
- 不猜测不可验证的 DOI、IF/JCR 指标或文章级事实。

这些规则和 Agent 操作细节写在 [CLAUDE.md](CLAUDE.md) 和 [SKILL.md](SKILL.md)，不是 README 的主要职责。

## 验证

```bash
python -m compileall litminer -q
python -m ruff check litminer test
python -m mypy litminer
python -m unittest discover -s test -p "test_*.py"
python -m litminer.sources.mcp.test_server
python -m litminer.engine.bootstrap --output-dir .litminer/bootstrap
python -m litminer.engine.doctor
python -m litminer.engine.offline_smoke
python -m litminer.engine.agent_summary --output-dir .litminer/runs/offline_smoke
python -m litminer.engine.journal_metrics --validate --metrics references/journal_metrics_seed.csv
```

## 项目结构

```text
Litminer/
|-- README.md
|-- README.en.md
|-- README.zh-CN.md
|-- LICENSE
|-- SKILL.md
|-- CLAUDE.md
|-- config/
|-- agents/
|-- litminer/
|-- references/
`-- test/
```

其中 `litminer/sources/api/` 是数据源 wrapper，`litminer/engine/` 是确定性处理流水线，`litminer/sources/mcp/` 是 MCP stdio 服务。

## 许可证

本项目使用 [MIT License](LICENSE)。
