# Litminer 用户指南

这份文档承接 README 中不适合放在首页的细节：安装位置、更新、Python 环境、配置、MCP、运行模式和常见排错。

## 分发方式

Litminer 当前按“完整仓库就是 skill 包”的方式分发。推荐直接 clone 仓库，不建议只复制部分文件。

需要的关键文件包括：

- `SKILL.md`
- `CLAUDE.md`
- `litminer/`
- `config/`
- `references/`

`pip install -e .` 只适合开发和 console scripts。普通 wheel 安装不等同于 Agent skill 安装，因为 Agent 仍需要发现 `SKILL.md` 和相关文档。

## 安装位置

Codex 用户级：

```bash
mkdir -p ~/.agents/skills
git clone https://github.com/xqy272/Litminer.git ~/.agents/skills/litminer
```

Claude Code 用户级：

```bash
mkdir -p ~/.claude/skills
git clone https://github.com/xqy272/Litminer.git ~/.claude/skills/litminer
```

项目级安装：

```bash
mkdir -p .agents/skills
git clone https://github.com/xqy272/Litminer.git .agents/skills/litminer
```

或：

```bash
mkdir -p .claude/skills
git clone https://github.com/xqy272/Litminer.git .claude/skills/litminer
```

安装后重启或刷新 Agent 的 skills，然后用自然语言要求“使用 Litminer”即可。

## 版本和更新

跟随默认分支：

```bash
git clone https://github.com/xqy272/Litminer.git ~/.agents/skills/litminer
```

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

跨版本更新前先读 [CHANGELOG.md](../CHANGELOG.md)。

## Python 环境

运行 Litminer 脚本或 MCP 服务需要 Python 3.10+。只安装或识别 skill 不需要 `pip install`。

检查 Python：

```bash
python --version
```

Windows 上也可以用：

```powershell
py -3 --version
```

如果要使用 console scripts 或开发工具，建议在 Litminer 目录内创建本地虚拟环境：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

Windows PowerShell：

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

## 工作区和输出

Litminer 区分“安装目录”和“运行工作区”：

- 安装目录放代码和文档。
- 运行工作区放输入 CSV、配置文件和 `.litminer/` 输出。
- MCP 模式下，文件参数必须位于 `LITMINER_WORKSPACE_ROOT` 内。

建议在用户项目 `.gitignore` 中加入：

```gitignore
.litminer/
```

常用环境变量：

```bash
export LITMINER_WORKSPACE_ROOT="/path/to/project"
export LITMINER_CONTACT_EMAIL="you@example.org"
```

Windows PowerShell：

```powershell
$env:LITMINER_WORKSPACE_ROOT = "D:\Projects\YourProject"
$env:LITMINER_CONTACT_EMAIL = "you@example.org"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
```

## API 联系信息

多数来源不要求 API key，但 OpenAlex、Crossref 和 Unpaywall 推荐或要求联系邮箱。

| 变量 | 用途 |
|------|------|
| `LITMINER_CONTACT_EMAIL` | 通用联系邮箱回退。 |
| `OPENALEX_MAILTO` | OpenAlex polite-pool 邮箱。 |
| `CROSSREF_MAILTO` | Crossref User-Agent 联系邮箱。 |
| `UNPAYWALL_EMAIL` | Unpaywall 查询邮箱。 |
| `OPENALEX_API_KEY` | 可选 OpenAlex API key。 |
| `SEMANTIC_SCHOLAR_API_KEY` / `S2_API_KEY` | 可选 Semantic Scholar API key。 |

## 运行配置

默认配置在 [config/default.json](../config/default.json)。不要把个人邮箱、API key 或某个课题的检索词写入默认配置。

项目级配置可以放在 `.litminer/config.json`，并通过 `--config` 传入：

```bash
python -m litminer.engine.run_lit_search \
  --query "your literature query" \
  --year-from 2026 \
  --config .litminer/config.json
```

检查配置：

```bash
python -m litminer.engine.doctor --config .litminer/config.json
```

配置适合放来源开关、限额、输出目录、publisher probe 策略。检索主题、领域词表和纳入/排除概念应在运行时通过 `--query`、`--required-concept`、`--optional-concept`、`--negative-concept` 或 `--triage-profile` 传入。

## 运行模式

| 模式 | 用途 |
|------|------|
| `fast` | 首次试跑；小批量发现，默认跳过 Crossref、Unpaywall、Semantic Scholar 和 publisher probe。 |
| `balanced` | 默认验证型路径。 |
| `expanded` / `full` | 更高召回；会启用 Semantic Scholar 和 provider 并发，但不会自动打开 arXiv 或 Europe PMC。 |

常用控制参数：

- `--resume`：复用同一输出目录里的已完成阶段。
- `--stop-after-stage STAGE`：停在指定阶段并写出 partial artifacts。
- `--time-budget-seconds N`：到达阶段边界后按时间预算停止。
- `--max-crossref-rows N` / `--max-unpaywall-rows N`：限制逐行 API 补充。
- `--no-cache`：修复网络、代理或 API key 后强制重新访问来源。

## 语义概念

概念参数用于打标签和排序，不是最终删除规则：

```bash
--required-concept "validation=external validation|prospective validation"
--optional-concept "benchmark=benchmark|dataset"
--negative-concept "review=review article|survey"
```

`re:` 正则概念默认关闭。只有在使用已审查的可信 profile 时，才显式开启：

```bash
--enable-regex-concepts
```

## MCP

MCP 是可选工具层。先验证服务：

```bash
python -m litminer.sources.mcp.test_server
```

Codex MCP 示例：

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

模板文件：

- [config/mcp.codex.example.toml](../config/mcp.codex.example.toml)
- [config/mcp.claude.example.json](../config/mcp.claude.example.json)

更多工具说明见 [MCP surface reference](mcp-surface.md) 和 [MCP README](../litminer/sources/mcp/README.md)。

## 常见问题

- API 请求失败或限流：查看 `api_discovery_trace.csv` 的 `status_class`、`http_status`、`transient_error` 和 `next_action`。
- `skipped_cached_provider_failure`：命中了短期 provider 失败缓存。等待 TTL，或确认环境修复后用 `--no-cache`。
- Unpaywall 显示 `skipped_missing_email`：设置 `UNPAYWALL_EMAIL` 或 `LITMINER_CONTACT_EMAIL` 后重跑。
- Crossref `lookup_failed` 或 `mismatch`：不要手工伪造 DOI；扩大检索或人工核验。
- 候选数量过少：增加 query、启用 Semantic Scholar/arXiv/Europe PMC，或放宽 required concepts。
- MCP 路径报错：确认输入、输出和 `config` 都在 `LITMINER_WORKSPACE_ROOT` 下。

更完整的恢复语义见 [runtime-recovery.md](runtime-recovery.md)。

## 验证命令

```bash
python -m compileall litminer -q
python -m unittest discover -s test -p "test_*.py"
python -m litminer.sources.mcp.test_server
python -m litminer.engine.bootstrap --output-dir .litminer/bootstrap
python -m litminer.engine.doctor
python -m litminer.engine.offline_smoke
python -m litminer.engine.journal_metrics --validate --metrics references/journal_metrics_seed.csv
```

开发环境安装了 dev 依赖后，也可以运行：

```bash
python -m ruff check litminer test
python -m mypy litminer
```
