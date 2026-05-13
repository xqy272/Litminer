# Litminer

中文 | [English](README.en.md)

Litminer 是一个面向 AI Agent 的科研文献信息获取 skill。它不是综述生成器，也不是 PDF 阅读器；它提供的是一层可复用、可追踪、可验证的文献发现和处理底座，让 Claude Code、Codex 等 Agent 不必只依赖通用 WebSearch/WebFetch 来处理科研检索任务。

典型用途：

- 从 OpenAlex、Semantic Scholar、arXiv、Europe PMC 等专业渠道发现候选文献。
- 通过 Crossref 验证 DOI、题名、期刊、年份、文章类型等书目信息。
- 通过 Unpaywall 标注 OA 状态和结构化访问线索。
- 去重、合并、语义初筛、排序、汇总、生成处理报告。
- 构建 DOI/出版社页面证据队列，供 Agent 后续检查文章页面。
- 作为 Claude Code、Codex 或其他支持 skill/MCP 的工具的本地科研检索能力。

## 先接入 Skill

Litminer 的根目录已经包含 `SKILL.md`，因此整个项目目录就是 skill 目录。把本目录注册给你的 Agent 后，Agent 才能自动读到 skill 描述并在合适任务中调用它。

### 接入 Claude Code

Claude Code 的 skill 目录需要包含 `SKILL.md`。推荐把 Litminer 安装为用户级 skill，或者安装到某个目标项目的 `.claude/skills/` 下。

用户级安装：

```bash
# macOS / Linux
mkdir -p ~/.claude/skills
git clone <this-repo-url> ~/.claude/skills/litminer
```

```powershell
# Windows PowerShell
New-Item -ItemType Directory -Force "$HOME\.claude\skills" | Out-Null
git clone <this-repo-url> "$HOME\.claude\skills\litminer"
```

项目级安装：

```bash
# 在目标项目根目录运行
mkdir -p .claude/skills
git clone <this-repo-url> .claude/skills/litminer
```

如果你已经把 Litminer 克隆在其他位置，也可以用软链接或目录联接指向该目录。安装后重启或刷新 Claude Code 的 skills，然后用自然语言触发，例如：

```text
使用 Litminer 检索 2026 年以来关于 enzyme stability external validation 的论文，并生成 publisher queue。
```

Claude Code 的详细运行策略见本项目的 [CLAUDE.md](CLAUDE.md)。Skill 本身的机器可读说明见 [SKILL.md](SKILL.md)。

### 接入 Codex

Codex 支持在 `config.toml` 中注册包含 `SKILL.md` 的 skill 目录。可使用用户级 `~/.codex/config.toml`，也可以在可信项目中使用项目级 `.codex/config.toml`。

```toml
[[skills.config]]
path = "D:/Projects/Litminer"
enabled = true
```

Windows 路径可以使用正斜杠。macOS / Linux 示例：

```toml
[[skills.config]]
path = "/home/me/projects/Litminer"
enabled = true
```

保存后重启 Codex 或执行技能重载。之后可以直接请求：

```text
用 Litminer 查找某主题的近年论文，要求验证 DOI、标注 OA 链接，并输出 publisher queue。
```

Codex 的配置参考见官方文档中的 `skills.config`、`skills.config.<index>.path` 和 `skills.config.<index>.enabled`。

### 可选：接入 MCP 工具

Skill 负责让 Agent 知道什么时候使用 Litminer；MCP 负责把 Litminer 的核心能力暴露为工具。二者可以同时使用，但 MCP 不是运行本项目的必要条件。

先验证本地 MCP 服务可启动：

```bash
python sources/mcp/test_server.py
```

Claude Code 可用 JSON 方式添加 stdio MCP 服务：

```bash
claude mcp add-json litminer '{
  "type": "stdio",
  "command": "python",
  "args": ["D:/Projects/Litminer/sources/mcp/server.py"],
  "env": {
    "LITMINER_CONTACT_EMAIL": "you@example.org"
  }
}'
```

Codex 可在 `config.toml` 中添加：

```toml
[mcp_servers.litminer]
command = "python"
args = ["D:/Projects/Litminer/sources/mcp/server.py"]
cwd = "D:/Projects/Litminer"
env_vars = [
  "OPENALEX_API_KEY",
  "OPENALEX_MAILTO",
  "CROSSREF_MAILTO",
  "UNPAYWALL_EMAIL",
  "LITMINER_CONTACT_EMAIL",
]
```

如果只想通过命令行脚本使用 Litminer，可以跳过 MCP。

相关官方文档：

- Claude Code Skills: <https://docs.claude.com/en/docs/claude-code/skills>
- Claude Code MCP: <https://docs.claude.com/en/docs/claude-code/mcp>
- Codex config reference: <https://developers.openai.com/codex/config-reference#configtoml>

## 安装运行环境

Litminer 运行时仅依赖 Python 标准库。推荐 Python 3.10 或更高版本。

```bash
python -m pip install -e .
```

开发和验证工具：

```bash
python -m pip install -e ".[dev]"
```

可选 API 联系信息：

- `OPENALEX_MAILTO` 或 `LITMINER_CONTACT_EMAIL`
- `CROSSREF_MAILTO` 或 `LITMINER_CONTACT_EMAIL`
- `UNPAYWALL_EMAIL` 或 `LITMINER_CONTACT_EMAIL`
- `OPENALEX_API_KEY`，如果你有 OpenAlex API key

## 快速开始

在 Litminer 项目根目录运行：

```bash
python engine/run_lit_search.py \
  --query "machine learning enzyme stability external validation" \
  --year-from 2026 \
  --required-concept "validation=external validation|prospective validation" \
  --optional-concept "benchmark=benchmark|dataset" \
  --negative-concept "review=review article|survey" \
  --config config/default.json \
  --output-dir check/litminer_run
```

Windows 辅助命令：

```bat
pipeline.bat "machine learning enzyme stability external validation" 2026
```

上面的 query 和 concept 只是示例。实际使用时，Agent 应根据当前用户请求生成检索式和语义概念。

## 默认工作流

`engine/run_lit_search.py` 会执行一条完整的文献处理链：

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

## 常用命令

只运行 API 发现：

```bash
python engine/api_discovery.py \
  --query "user topic query" \
  --sources openalex,semantic_scholar,arxiv,europe_pmc \
  --year-from 2026 \
  --output check/api_candidates.csv \
  --trace-output check/api_discovery_trace.csv \
  --report-output check/api_discovery_report.md
```

对已有 CSV 做语义初筛：

```bash
python engine/semantic_triage.py \
  --input check/deduped_candidates.csv \
  --output check/triaged_candidates.csv \
  --required-concept "main=term1|term2" \
  --optional-concept "secondary=term3|term4" \
  --negative-concept "negative=term5|term6" \
  --year-from 2026 \
  --require-doi
```

构建出版社页面证据队列：

```bash
python engine/build_publisher_queue.py \
  --input check/verified_candidates.csv \
  --output check/publisher_queue.csv \
  --priorities high,medium,needs_review \
  --fields-needed "field_from_user_request"
```

生成处理摘要：

```bash
python engine/processing_report.py \
  --output-dir check/litminer_run
```

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
python -m compileall engine sources -q
python -m ruff check engine sources test
python -m mypy engine sources
python -m unittest discover -s test -p "test_*.py"
python sources/mcp/test_server.py
```

## 项目结构

```text
Litminer/
|-- README.md
|-- README.en.md
|-- README.zh-CN.md
|-- SKILL.md
|-- CLAUDE.md
|-- config/
|-- agents/
|-- engine/
|-- sources/
|-- references/
`-- test/
```
