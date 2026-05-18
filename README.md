# Litminer

中文 | [English](README.en.md)

Litminer 是一个面向 AI Agent 的科研文献信息获取 skill。它帮助 Claude Code、Codex 等 Agent 从专业来源发现候选文献，验证 DOI 和书目信息，做语义初筛，生成可审计的报告和 publisher queue。

它不是综述生成器、PDF 阅读器或知识库。Litminer 负责把检索、核验、失败记录和证据交付做成可复跑流程；最终科学判断仍由 Agent 和用户完成。

## 适合什么

- 从 OpenAlex、Semantic Scholar、arXiv、Europe PMC 等来源发现候选文献。
- 用 Crossref 验证 DOI、标题、期刊、年份和文章类型。
- 用 Unpaywall 标注 OA 状态和结构化访问线索。
- 去重、合并、语义初筛、排序并生成处理报告。
- 构建 DOI/出版社页面证据队列，供 Agent 后续检查文章页面。

## 安装

当前分发方式很简单：**完整仓库就是 skill 包**。推荐通过 Git clone 使用；稳定版本用 GitHub release tag 固定。`pip install -e .` 只用于本地开发和 console scripts，不是主要安装方式。

### Codex

```bash
mkdir -p ~/.agents/skills
git clone https://github.com/xqy272/Litminer.git ~/.agents/skills/litminer
```

Windows PowerShell:

```powershell
New-Item -ItemType Directory -Force "$HOME\.agents\skills" | Out-Null
git clone https://github.com/xqy272/Litminer.git "$HOME\.agents\skills\litminer"
```

### Claude Code

```bash
mkdir -p ~/.claude/skills
git clone https://github.com/xqy272/Litminer.git ~/.claude/skills/litminer
```

项目级安装也可以，把仓库 clone 到目标项目的 `.agents/skills/litminer` 或 `.claude/skills/litminer`。

### 固定版本和更新

固定稳定版本：

```bash
git clone --branch <release-tag> --depth 1 https://github.com/xqy272/Litminer.git ~/.agents/skills/litminer
```

更新已有安装：

```bash
cd ~/.agents/skills/litminer
git pull --ff-only
python -m litminer.engine.bootstrap
python -m litminer.engine.offline_smoke
```

跨版本更新前先看 [CHANGELOG.md](CHANGELOG.md)。

## 首次检查

Litminer 运行脚本需要 Python 3.10+，运行时只依赖 Python 标准库。安装后在 Litminer 目录运行：

```bash
python -m litminer.engine.bootstrap
python -m litminer.engine.doctor
python -m litminer.engine.offline_smoke
```

这些命令不需要 API key。`offline_smoke` 不访问网络，会在 `.litminer/runs/offline_smoke/` 生成示例报告。

建议把用户项目里的运行产物加入 `.gitignore`：

```gitignore
.litminer/
```

## 快速运行

```bash
python -m litminer.engine.run_lit_search \
  --mode fast \
  --query "machine learning enzyme stability external validation" \
  --year-from 2026 \
  --required-concept "validation=external validation|prospective validation" \
  --optional-concept "benchmark=benchmark|dataset" \
  --negative-concept "review=review article|survey" \
  --output-dir .litminer/runs/litminer_run
```

首次建议用 `--mode fast` 跑通路径和语义概念；确认方向正确后，再用 `--mode balanced` 或 `--mode expanded` 扩大验证与召回。

`re:` 正则概念默认关闭。只有使用已审查的可信 profile 时，才通过 `--enable-regex-concepts` 或 MCP 的 `enable_regex_concepts` 显式开启。

## 主要输出

| 文件 | 用途 |
|------|------|
| `query_plan.json` | Agent 派生的查询、来源、概念和运行控制。 |
| `api_candidates.csv` | API 发现候选。 |
| `api_discovery_trace.csv` | 查询、来源、状态和失败原因追踪。 |
| `deduped_candidates.csv` | DOI/标题去重并合并后的候选。 |
| `verified_candidates.csv` | Crossref 验证结果。 |
| `triaged_candidates.csv` | 带语义标签、优先级和元数据状态的审查面。 |
| `publisher_queue.csv` | DOI/出版社页面证据队列。 |
| `processing_report.md` | 来源、元数据、triage、OA/access 和队列摘要。 |
| `agent_summary.json` | Agent 优先读取的机器可读摘要。 |
| `run_manifest.json` | 阶段状态、复用记录、行数、文件指纹和运行签名。 |

## 可选 MCP

MCP 不是必需项；它只是把 Litminer 的能力暴露成 Agent 可调用工具。先验证服务可启动：

```bash
python -m litminer.sources.mcp.test_server
```

MCP 运行时建议设置：

```bash
LITMINER_WORKSPACE_ROOT=/path/to/your/project
LITMINER_CONTACT_EMAIL=you@example.org
```

更完整的 MCP 配置见 [litminer/sources/mcp/README.md](litminer/sources/mcp/README.md) 和 [references/mcp-surface.md](references/mcp-surface.md)。

## 常用命令

| 任务 | 命令 |
|------|------|
| 环境检查 | `python -m litminer.engine.doctor` |
| 离线冒烟 | `python -m litminer.engine.offline_smoke` |
| 主流程 | `python -m litminer.engine.run_lit_search --help` |
| 语义初筛 | `python -m litminer.engine.semantic_triage --help` |
| MCP 自检 | `python -m litminer.sources.mcp.test_server` |
| 全量测试 | `python -m unittest discover -s test -p "test_*.py"` |

## 边界

Litminer 不做这些事：

- 不凭记忆回答实时论文、DOI、期刊指标或出版社页面证据问题。
- 不生成最终综述和最终纳入/排除判断。
- 不解析 PDF、OCR、补充材料或表格。
- 不绕过付费墙。
- 不猜测不可验证的 DOI、IF/JCR 指标或文章级事实。

## 更多文档

- [用户指南](references/user-guide.md)：安装细节、配置、MCP、运行和排错。
- [Artifact 契约](references/artifact-contracts.md)：Agent 可依赖的稳定输出契约。
- [CSV 字段字典](references/csv-fields.md)：字段含义、生成阶段和信任等级。
- [Agent 安全规则](references/agent-safety.md)：外部页面和 prompt injection 防误用规则。
- [Agent 工作流](references/agent-workflow.md)：Agent 应如何规划查询、选择来源和交付结果。
- [运行与恢复](references/runtime-recovery.md)：resume、budget、cache 和 provider 失败语义。
- [质量与证据规则](references/quality-and-evidence.md)：Trust Tiers、语义初筛和证据边界。
- [MCP 工具面](references/mcp-surface.md)：工具 profile、workspace 规则和 JSON-RPC 示例。
- [发布 checklist](references/release-checklist.md)：轻量 release tag 发布流程。
- [最小示例](examples/README.md)：本地 CSV 端到端示例。
- [Agent evals](evals/README.md)：小型 Agent 行为检查。
- [SKILL.md](SKILL.md)：Agent 读取的 skill 入口。
- [CLAUDE.md](CLAUDE.md)：更详细的 Agent 操作指南。

## 项目结构

```text
Litminer/
|-- README.md
|-- SKILL.md
|-- CLAUDE.md
|-- config/
|-- litminer/
|-- references/
|-- examples/
|-- evals/
`-- test/
```

`litminer/sources/api/` 是数据源 wrapper，`litminer/engine/` 是确定性处理流水线，`litminer/sources/mcp/` 是 MCP stdio 服务。

## 许可证

本项目使用 [MIT License](LICENSE)。
