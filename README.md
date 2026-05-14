# Litminer

中文 | [English](README.en.md)

Litminer 是一个面向 AI Agent 的科研文献信息获取 skill。它不是综述生成器，也不是 PDF 阅读器；它提供的是一层可复用、可追踪、可验证的文献发现和处理底座，让 Claude Code、Codex 等 Agent 不必只依赖通用 WebSearch/WebFetch 来处理科研检索任务。

一句话定位：Litminer 负责把“找文献、核元数据、打标签、排队列”做成可复查的本地工作流；最终科学判断仍由 Agent 和用户完成。

典型用途：

- 从 OpenAlex、Semantic Scholar、arXiv、Europe PMC 等专业渠道发现候选文献。
- 通过 Crossref 验证 DOI、题名、期刊、年份、文章类型等书目信息。
- 通过 Unpaywall 标注 OA 状态和结构化访问线索。
- 去重、合并、语义初筛、排序、汇总、生成处理报告。
- 构建 DOI/出版社页面证据队列，供 Agent 后续检查文章页面。
- 作为 Claude Code、Codex 或其他支持 skill/MCP 的工具的本地科研检索能力。

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
python -m litminer.engine.doctor
python -m litminer.engine.offline_smoke
```

这两个命令不需要 API key。`offline_smoke` 不访问网络，会用内置样本在当前工作区的 `.litminer/runs/offline_smoke/` 下生成 `processing_report.md` 和 `publisher_queue.csv`。

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
env = { LITMINER_WORKSPACE_ROOT = "D:/path/to/your/project" }
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

主要 MCP 工具：

| 工具 | 用途 |
|------|------|
| `litminer_search_openalex` | 搜索 OpenAlex 候选文献。 |
| `litminer_search_semantic_scholar` | 搜索 Semantic Scholar，或从 DOI 做一跳 citation/reference expansion。 |
| `litminer_search_arxiv` | 通过 arXiv Atom API 搜索预印本。 |
| `litminer_search_europe_pmc` | 搜索 Europe PMC 生物医学/生命科学元数据。 |
| `litminer_discover_api` | 多 query、多 provider API 发现，并写出候选、trace 和报告。 |
| `litminer_verify_crossref` | 验证单个 DOI。 |
| `litminer_batch_verify_crossref` | 批量验证 DOI。 |
| `litminer_search_crossref_title` | 按标题搜索 Crossref，辅助恢复 DOI。 |
| `litminer_batch_crossref_title_search` | 批量按标题搜索 Crossref。 |
| `litminer_lookup_unpaywall` | 查询单个 DOI 的 OA 线索。 |
| `litminer_dedupe` | 对候选 CSV 去重。 |
| `litminer_semantic_triage` | 对候选 CSV 做语义标签和排序。 |
| `litminer_filter_journal_metrics` | 用本地 verified metrics CSV 标注或过滤期刊指标。 |
| `litminer_build_publisher_queue` | 生成出版社页面证据队列。 |
| `litminer_probe_publishers` | 轻量探测 DOI/出版社页面可达性和 PDF/SI 链接。 |
| `litminer_import_websearch` | 把 WebSearch 结果正规化为未验证候选。 |
| `litminer_processing_report` | 生成处理摘要报告。 |
| `litminer_read_csv_summary` | 分页读取 CSV 摘要，支持按优先级/状态筛选，适合 Agent 查看大表。 |
| `litminer_run_lit_search` | 运行完整工作流。 |

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

### 来源、限额和出版社策略

默认配置在 [config/default.json](config/default.json)。建议不要直接把个人邮箱、API key 或某个课题的检索词写入这个文件；如果需要项目级配置，可以在你的工作区创建例如 `.litminer/config.json`：

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
    "unpaywall_sleep": 0.1
  },
  "outputs": {
    "default_output_dir": ".litminer/runs/litminer_run",
    "screenshot_root": ".litminer/screenshots"
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

- `--fields-needed` / `page_required_fields`：告诉 Agent 后续看出版社页面时要关注哪些字段，例如数据集、验证方式、外部基准、补充材料链接。
- `queue_priorities` / `--queue-priorities`：决定哪些 triage 优先级进入 `publisher_queue.csv`。
- `require_doi_for_queue` / `--allow-missing-doi`：默认要求 DOI 才进入出版社队列；如果明确允许缺 DOI，可用 `--allow-missing-doi`。
- `strict_discovery` / `--strict-discovery`：当 API 来源报错导致候选集不可靠时让流程失败，而不是只生成空结果报告。
- `parallel_providers` / `--parallel-providers`：可选地并行执行同一 query 下的不同 API provider；实现使用标准库线程，同一 provider 在不同 query 间仍保持串行。
- `provider_workers` / `--provider-workers`：限制 provider 并发线程数；默认等于同一 query 下启用的 provider 数量。
- `openalex_work_types` / `--openalex-work-types`：控制 OpenAlex 的 work type 过滤，默认 `article`；可传 `all` 关闭类型过滤。
- `queue_strict_only` / `--queue-strict-only` / `--queue-all-metric-statuses`：使用 `--min-if` 时默认只把 metric-pass 行送入 queue；如只想标注不硬过滤，可显式选择 queue all metric statuses。
- `publisher_probe` / `--probe-publishers`：只做轻量页面可达性、PDF/SI 链接提示探测，不解析 PDF，也不绕过付费墙。
- `publisher_probe_limit` / `--probe-limit` 和 `publisher_probe_sleep` / `--probe-sleep`：控制探测数量和请求间隔，避免过快访问出版社页面。

## 快速开始

在 Litminer 项目根目录运行：

```bash
python -m litminer.engine.run_lit_search \
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

## 默认工作流

`litminer.engine.run_lit_search` 会执行一条完整的文献处理链：

1. API 发现候选文献，默认使用 OpenAlex。
2. DOI/标题去重，并合并互补字段。
3. Crossref 验证书目信息，缺 DOI 时尝试标题恢复。
4. 按用户提供的 required/optional/negative 概念做语义初筛。
5. 通过 Unpaywall 标注 OA 和访问线索。
6. 可选：用本地已验证期刊指标表做标注或过滤。
7. 构建出版社页面证据队列。
8. 生成 `feasibility_report.md` 和 `processing_report.md`。

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

如果通过 MCP 查看较大的 CSV，优先用 `litminer_read_csv_summary` 分页读取，而不是把完整 CSV 一次性塞进 Agent 上下文。

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

常见情况：

- API 请求失败或限流：减少 `max_results_per_query`，减少来源数量，稍后重试；不要把失败来源的空结果当作最终事实。
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
python -m litminer.engine.doctor
python -m litminer.engine.offline_smoke
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
