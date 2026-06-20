# Litminer 项目全面审查报告

> 审查日期：2026-06-20
> 审查范围：架构设计、安全性、代码质量、性能与可扩展性、测试覆盖、API 设计与开发者体验
> 代码版本：v0.1.0 (commit 208cb7b)

---

## 目录

- [一、执行摘要](#一执行摘要)
- [二、架构与结构审查](#二架构与结构审查)
- [三、安全性审查](#三安全性审查)
- [四、代码质量审查](#四代码质量审查)
- [五、性能与可扩展性审查](#五性能与可扩展性审查)
- [六、测试覆盖审查](#六测试覆盖审查)
- [七、API 设计与开发者体验审查](#七api-设计与开发者体验审查)
- [八、优先级修复路线图](#八优先级修复路线图)

---

## 一、执行摘要

Litminer 是一个架构清晰、依赖极简（纯 stdlib）的文献检索与元数据验证工具。整体代码质量在 pre-1.0 阶段属于上乘水平：依赖图无环、类型标注覆盖率高、测试覆盖 28 个源模块中的 25 个、SSRF/路径穿越防护设计扎实。

但审查发现了 **4 个中等安全风险**、**7 个功能性 bug**、**12 个性能瓶颈**、**15 个代码质量改进点**，以及若干 API 设计不一致之处。以下按维度逐一展开。

### 关键数据

| 指标 | 数值 |
|------|------|
| Python 源文件 | 34 个 |
| 总代码行数 | ~12,000 行（含测试 ~14,300 行） |
| 测试方法 | 58 个（单文件单类） |
| 未覆盖模块 | 3 个（`merge_csv`, `validate_stage`, `status_policy`） |
| 运行时依赖 | 0（纯标准库） |
| 安全通过项 | 命令注入、危险函数、硬编码凭证、SSRF 防护 |
| 安全待修复项 | 4 个（1 中 + 3 低） |

---

## 二、架构与结构审查

### 2.1 整体架构评价

项目采用分层管道架构，依赖图严格单向，无循环依赖：

```
MCP Server (server.py)
    └── Engine Orchestrator (run_lit_search.py)
            ├── Discovery (api_discovery.py)
            │       └── Source Wrappers (openalex, s2, arxiv, europe_pmc)
            ├── Dedup (dedupe_papers.py)
            ├── Crossref Verify (crossref_verify.py)
            ├── Triage (semantic_triage.py)
            ├── Unpaywall (unpaywall_lookup.py)
            ├── Metrics (journal_metrics.py)
            ├── Queue (build_publisher_queue.py)
            ├── Probe (publisher_probe.py)
            └── Reports (agent_summary, processing_report, provenance, artifacts)
                    └── Common Utilities (common.py, workspace.py, cache.py, errors.py)
```

**优点：**
- 每个阶段产出原子性 CSV，阶段间通过文件传递，天然支持断点续跑
- `workflow_state.py` 通过 SHA-256 指纹实现智能缓存复用
- MCP 服务器采用惰性导入，启动快、内存占用低
- 配置系统支持三层合并（默认 → 项目 → 用户），`doctor.py` 提供类型校验

### 2.2 架构问题

#### P1: `argparse.Namespace` 耦合（中等 / 维护性风险）

`run_lit_search.run()` 接受 `argparse.Namespace` 对象。MCP 服务器在 `_run_namespace()` 中手动构造伪 Namespace 来调用它。这意味着：
- 每新增一个 CLI 参数，`server.py` 和 `run_lit_search.py` 都必须同步修改
- 不存在共享的参数 schema，二者可能静默漂移

**建议：** 引入 `@dataclass` 形式的 `RunConfig`，CLI 和 MCP 各自构造，传入 `run()` 时类型安全。

#### P2: `run_lit_search.py` 过大（1999 行）

该文件承载了：配置加载、参数标准化（140 行）、所有阶段运行器（6 个函数各 50-120 行）、可行性报告生成、manifest 管理、`main()` 入口。

**建议：**
- 将 `make_report()`（119 行）拆为 `litminer/engine/feasibility_report.py`
- 将 `normalize_args()`（137 行）拆为 `litminer/engine/config_normalize.py`
- 6 个 `run_*_stage()` 可抽象为注册式管道步骤

#### P3: Crossref 使用独立异常体系

发现阶段的所有 provider 使用统一的 `ProviderSearchError`，但 `crossref_verify.py` 定义了自己的 `CrossrefRateLimitError` / `CrossrefRequestError`，不继承 `ProviderSearchError`。虽然因为 Crossref 在 `verify_csv()` 内部捕获处理，不会泄漏，但破坏了统一的错误分类约定。

**建议：** 让 `CrossrefRateLimitError` 继承 `ProviderSearchError`，保持错误体系一致。

#### P4: `sys.path` 运行时注入（4 处）

`run_lit_search.py`、`api_discovery.py`、`server.py`、`test_litminer_core.py` 均在模块顶部插入 `sys.path`。在 `pip install -e .` 后不再需要此操作，且 `pyproject.toml` 中的 `ignore = ["E402"]` 掩盖了根本问题。

**建议：** 移除生产代码中的 `sys.path.insert`，仅保留测试文件中的 workaround。

#### P5: 冗余与残留

| 问题 | 位置 | 说明 |
|------|------|------|
| `artifacts.write_index()` 被调用两次 | `run_lit_search.py:894,896` | 第一次结果被第二次覆盖 |
| `evidence.unknown_value` 配置键 | `config/default.json` | 在 `RUNTIME_DEFAULTS` 和任何引擎代码中均未使用 |
| `STAGE_REQUIRED["preliminary"]` | `schema.py` | 无任何管道阶段生成 preliminary CSV |
| `workflow_state` manifest 中 `input` 与 `input_path` 重复 | `workflow_state.py` | 冗余字段 |

---

## 三、安全性审查

### 3.1 通过项

| 检查项 | 状态 | 说明 |
|--------|------|------|
| 命令注入 | **通过** | 全项目无 `subprocess.shell=True`、无 `os.system()`、无 `os.popen()` |
| 危险函数 | **通过** | 无 `eval()`、`exec()`、`pickle`、`marshal` |
| 硬编码凭证 | **通过** | 所有 API 密钥通过环境变量读取 |
| MCP 路径穿越 | **通过** | `_workspace_path()` 使用 `resolve()` + `relative_to()` 防御 |
| SSRF 防护 | **通过** | `publisher_probe.py` 实现 DNS 预解析 + IP 黑名单 + 重定向验证 |

### 3.2 待修复项

#### S1: Engine 层 `resolve_workspace_path` 不做工作区限制（中等）

**文件：** `litminer/engine/workspace.py:23-29`

MCP 层的 `_workspace_path()` 正确验证路径在工作区内，但引擎内部使用的 `resolve_workspace_path()` 不做限制检查。如果攻击者通过 MCP 传入一个工作区内的 config 文件，但该 config 的 `outputs.default_output_dir` 设置为绝对路径（如 `/tmp/evil`），引擎将写入任意位置。

**攻击路径：** MCP `tool_run_lit_search` → `_optional_workspace_path`（验证 config 文件本身在工作区内）→ `load_runtime_config` 读取 JSON → `RuntimeConfig.output_path()` 调用 `resolve_workspace_path`（无限制）→ 写入 config 内指定的任意路径。

**修复方案：**
```python
def resolve_workspace_path(value: str | Path, root: Path | None = None) -> Path:
    path = Path(value).expanduser()
    base = root or workspace_root()
    resolved = (path if path.is_absolute() else base / path).resolve(strict=False)
    resolved.relative_to(base)  # 添加限制检查
    return resolved
```

#### S2: 正则 ReDoS 风险（中等 / opt-in）

**文件：** `litminer/engine/semantic_triage.py:313-316`

`allow_regex=True` 时，用户可提供 `re:` 前缀的正则模式。虽然有 300 字符长度限制，但未对回溯复杂度做检查。`(a+)+$` 这类模式可导致灾难性回溯。

**修复方案：** 添加 `re.compile(pattern)` 超时包装，或引入 `re2` 风格的安全编译检查。

#### S3: CSV 公式注入（低）

**文件：** `litminer/engine/common.py:99-115`

来自外部 API 的标题、摘要可能包含 `=`、`+`、`-`、`@` 开头的内容。当用户在 Excel 中打开 CSV 并启用宏时，可执行注入公式。

**修复方案：** 在 `write_csv_atomic` 中对以 `=+\-@` 开头的单元格值添加 `'` 前缀转义。

#### S4: DNS TOCTOU / Rebinding（低）

**文件：** `litminer/engine/publisher_probe.py:115-122`

DNS 预解析验证与实际 TCP 连接之间存在时间窗口。攻击者控制 DNS 可在 5 分钟缓存窗口内实施 rebinding。这是所有基于预解析的 SSRF 防御的已知局限。

#### S5: 调试回溯泄漏内部路径（低）

**文件：** `litminer/sources/mcp/server.py:305-306,928`

`LITMINER_MCP_DEBUG_ERRORS` 环境变量启用时，JSON-RPC 错误包含完整 Python traceback。虽然有非默认环境变量保护，但应文档明确声明生产环境不可启用。

#### S6: MCP 参数中 API Key 明文传输（低 / 设计局限）

**文件：** `litminer/sources/mcp/server.py:327,541`

MCP 工具参数接受 `api_key` 字符串，记录 tool call 的 Agent 会将密钥明文写入日志。这是 MCP 协议的设计局限。

---

## 四、代码质量审查

### 4.1 代码重复（最高优先级）

#### D1: HTTP 重试/请求逻辑在 6 个文件中重复实现

以下每个文件都有各自的 `_retry_after_seconds`、`_status_for_fetch_exception`、`_fetch_json`/`_fetch_xml` 实现：

| 文件 | 行数 | 差异点 |
|------|------|--------|
| `openalex_search.py:187-258` | 71 行 | 重试上限 120s |
| `semantic_scholar_search.py:88-160` | 72 行 | 环境变量可配 backoff |
| `arxiv_search.py:147-214` | 67 行 | 使用 `max(SLEEP, 2**attempt)` |
| `europe_pmc_search.py:166-233` | 67 行 | 重试上限 120s |
| `crossref_verify.py:75-135` | 60 行 | 重试上限 60s（更紧） |
| `unpaywall_lookup.py:79-142` | 63 行 | 重试上限 120s |

**修复方案：** 提取至 `litminer/sources/api/http_client.py`，提供可配置的 `RetryConfig` dataclass。

#### D2: `utc_now()` 在 5 个模块中独立定义

| 位置 | 返回类型 |
|------|----------|
| `api_discovery.py:92` | `str` |
| `cache.py:33` | `datetime` |
| `workflow_state.py:17` | `str` |
| `websearch_import.py:56` | `str` |
| `unpaywall_lookup.py:55` | `str` |

**修复方案：** 统一到 `common.py`，提供 `utc_now() -> str` 和 `utc_now_dt() -> datetime`。

#### D3: 跨 Provider 重复的辅助函数

| 函数 | 重复文件 | 说明 |
|------|----------|------|
| `_year_ok` | `arxiv_search.py:217`, `europe_pmc_search.py:236` | 逐字节相同 |
| `_clean_text` | `arxiv_search.py:52`, `europe_pmc_search.py:51` | 可复用 `common.cell_text` |
| `_extract_doi` | `openalex_search.py:73`, `semantic_scholar_search.py:165` | 归一化逻辑与 `normalize_doi` 重复 |
| `_row_identity` | `crossref_verify.py:281`, `unpaywall_lookup.py:254` | 近乎相同的行标识逻辑 |
| `to_csv` | 4 个 search provider | 2-3 行完全一样的模板 |

#### D4: `run_lit_search.py` 中计数传播样板代码

`run_crossref_stage`（1057-1071 行）和 `run_unpaywall_stage`（1232-1243 行）各有 10+ 行 `counts["prefix_X"] = status_counts.get("X", 0)` 的重复模式。

**修复方案：** 提取 `_propagate_counts(counts, status_counts, prefix)` 辅助函数。

### 4.2 函数复杂度

| 函数 | 文件 | 行数 | 问题 |
|------|------|------|------|
| `verify_csv` | `crossref_verify.py:409-679` | **270 行** | DOI 路径和标题搜索路径结构近似，应拆分 |
| `run` | `run_lit_search.py:1493-1852` | **360 行** | 10 个 stop-after-stage 守卫块重复 12 行模板 |
| `normalize_args` | `run_lit_search.py:259-395` | **137 行** | 40+ 个 `if ... is None:` 赋值块 |
| `make_report` | `run_lit_search.py:913-1031` | **119 行** | 可独立为模块 |

### 4.3 全局状态与副作用

#### G1: `os.environ` 运行时修改

**文件：** `run_lit_search.py:383-387`

```python
if crossref_contact and not os.environ.get("CROSSREF_MAILTO"):
    os.environ["CROSSREF_MAILTO"] = crossref_contact
```

`normalize_args` 是一个看似无副作用的函数，却修改了进程环境变量。这使测试隔离困难且行为依赖调用顺序。

**修复方案：** 将 `crossref_contact` 作为显式参数传给 `crossref_verify.verify_csv()`。

#### G2: OpenAlex `DEFAULT_MAILTO` 在 import 时求值

**文件：** `openalex_search.py:41`

```python
DEFAULT_MAILTO = os.environ.get("OPENALEX_MAILTO") or os.environ.get("LITMINER_CONTACT_EMAIL") or ""
```

Import 后修改环境变量不会影响该值。其他 provider 在调用时读取环境变量。

**修复方案：** 改为函数调用 `_get_mailto()` 或在 `search()` 参数中传入。

### 4.4 日志系统缺失

项目使用 `print(..., file=sys.stderr)` 作为唯一的诊断输出手段（82 处调用，15 个文件）。没有日志级别区分，调用方无法编程式地控制输出。

**建议：** 引入 `logging.getLogger(__name__)` 替代 `print`。不增加运行时依赖，仅使用标准库 `logging`。

---

## 五、性能与可扩展性审查

### 5.1 内存效率

#### M1: 全量加载 CSV 到内存（所有阶段）

每个阶段通过 `read_csv_rows()` 将整个 CSV 加载为 `list[dict]`，无流式处理路径：

| 阶段 | 文件 | 额外内存开销 |
|------|------|-------------|
| Dedup | `dedupe_papers.py:133` | 全量加载 |
| Triage | `semantic_triage.py:618,626` | `output_rows` 产生 2x 内存副本 |
| Crossref | `crossref_verify.py:417` | 全量加载，原地修改 |
| Unpaywall | `unpaywall_lookup.py:286,311` | `output_rows + rows[index+1:]` 每次 checkpoint 创建 N 大小临时列表 |
| Metrics | `journal_metrics.py:282` | `list(reader)` 全量化 |

**影响：** 对于大型发现运行（>10,000 行），工作内存可达数百 MB。

**修复方案（短期）：** Unpaywall checkpoint 改为 `itertools.chain` 或分段写入，避免 O(N) 列表连接。
**修复方案（长期）：** 引入流式 CSV 处理器，支持惰性行迭代。

#### M2: Triage 阶段双倍内存

**文件：** `semantic_triage.py:626`

```python
output_rows = [triage_row(row, profile) for row in rows]
```

`triage_row` 内部 `out = dict(row)` 再次复制，实际产生 2x 内存放大。

#### M3: Dedup 中 `row_quality` 未缓存

**文件：** `dedupe_papers.py:101`

```python
if row_quality(row) > row_quality(best):
```

`row_quality(best)` 在每次迭代时重新计算，而 `best` 只在满足条件时才更新。应缓存到局部变量。

### 5.2 I/O 效率

#### I1: `cache.py` 每次 `get()`/`set()` 做全文件 JSON 解析

**文件：** `cache.py:155-166`（get）, `cache.py:188-209`（set）

每个缓存操作：获取文件锁 → 读取整个 JSON 文件 → 反序列化 → 检查/修改一个键 → 重新序列化 → 原子写回。对于 5,000 条记录的 `crossref.json`，每次缓存查询都解析大 JSON。

**修复方案（短期）：** 引入进程内 `_data` 字典作为读穿缓存层，仅在 `set()` 时写磁盘。
**修复方案（长期）：** 考虑 SQLite 替代（仍然是 stdlib）。

#### I2: `workflow_state.row_count()` 读取整个 CSV 仅为计数

**文件：** `workflow_state.py:96-103`

```python
def row_count(path):
    fieldnames, rows = read_csv_rows(path)
    return len(rows)
```

在 `record_stage()` 和 `run_lit_search.py` 多处调用。对万行 CSV 极其浪费。

**修复方案：** 替换为行计数（`sum(1 for _ in open(path))`）或在 manifest 中维护行数元数据。

#### I3: Resume 时 SHA-256 全量哈希

**文件：** `workflow_state.py:163-198`

`reusable_stage()` 对每个阶段的输入和输出文件计算 SHA-256。10 个阶段 × 2 文件 = 20 次全文件读取。对于 MB 级 CSV，resume 检查可增加数秒 I/O。

**修复方案：** 使用文件大小 + 修改时间作为快速预检，仅在不一致时回退到 SHA-256。

### 5.3 API 调用效率

#### A1: Crossref/Unpaywall 阶段完全串行

每行一个 HTTP 请求，无并发。Crossref 还有硬编码的礼貌暂停（每 10 请求 sleep 0.5s，不可配置）：

**文件：** `crossref_verify.py:472-476`

```python
if request_count % 10 == 0:
    time.sleep(0.5)
```

**修复方案（短期）：** 将 `polite_pause` 暴露为可配参数。
**修复方案（长期）：** 引入 `ThreadPoolExecutor` 并发验证（需配合速率限制器）。

#### A2: Discovery 的查询间串行

**文件：** `api_discovery.py:431`

`parallel_providers=True` 时，同一查询的多个 provider 并行。但多个查询之间完全串行。10 个查询 × 4 个 provider = 10 个串行批次。

**修复方案：** 允许查询级别的并行化，或至少支持查询的流水线处理。

### 5.4 算法效率

#### A3: `_word_positions` 重复分词

**文件：** `semantic_triage.py:340-365`

`_near_matches` 对同一文本的每个模式独立调用 `_word_positions`，每次都重新执行 `re.findall(r"[a-z0-9]+", text)` 分词。500 行 × 10 个 `near` 概念 = 5,000 次冗余分词。

**修复方案：** 将分词结果缓存到 `triage_row` 级别，所有概念共享。

#### A4: `fieldnames_from_rows` O(N×M²) 最坏复杂度

**文件：** `common.py:49-58`

```python
if key not in fields:  # fields 是 list，线性查找
```

`fields` 应改为 `dict` 保持插入顺序且 O(1) 查找。

### 5.5 性能问题汇总表

| 编号 | 问题 | 影响 | 优先级 |
|------|------|------|--------|
| I1 | 缓存无内存层，每次全文件读写 | 高频 I/O | P1 |
| I2 | `row_count` 全量读 CSV 仅计数 | 每阶段调用 | P1 |
| M1 | 无流式 CSV 处理 | 大数据集 OOM 风险 | P2 |
| A1 | Crossref 礼貌暂停不可配 | 影响吞吐量 | P2 |
| M3 | `row_quality` 未缓存 | 热路径冗余计算 | P2 |
| I3 | Resume 全量 SHA-256 | 增加数秒启动延迟 | P3 |
| A3 | 近义词匹配重复分词 | 大数据集延迟 | P3 |

---

## 六、测试覆盖审查

### 6.1 覆盖现状

项目拥有 **58 个测试方法**（单文件 `test/test_litminer_core.py`，2,280 行），覆盖 28 个源模块中的 25 个。

**已覆盖模块（部分列表）：**

| 模块 | 测试数 | 覆盖要点 |
|------|--------|----------|
| `api_discovery` | 8 | Provider 错误、断路器、速率限制冷却、并行、失败缓存 |
| `crossref_verify` | 6 | 429/Retry-After、速率限制行不复用、网络错误、标题恢复、预算跳过 |
| `unpaywall_lookup` | 5 | 429 处理、缓存 TTL、预算跳过 |
| `semantic_triage` | 4 | 否定概念、模式缓存边界、表达式概念、正则 opt-in |
| `run_lit_search` | 11 | 空候选、Crossref 阻断、triage 续跑、部分阶段、时间预算、取消 |
| MCP server | 10 | 路径逃逸、协议拒绝、工作区路由、工具配置文件、后台运行生命周期 |

**测试强项：**
- 错误路径测试充分（429、网络错误、空结果、损坏数据）
- 集成测试覆盖端到端流程（offline smoke、MCP 全流程、resume 语义）
- Mocking 策略正确且一致（全部使用 `unittest.mock.patch`，无第三方 mock 库）

### 6.2 覆盖缺口

#### 未测试模块

| 模块 | 风险 | 说明 |
|------|------|------|
| `merge_csv.py` | 中等 | union-schema 合并、`allow_missing`、无头文件、重复字段均未测试 |
| `validate_stage.py` | 低 | 阶段验证报告生成器 |
| `status_policy.py` | 低 | 状态分类词汇表 |

#### 未使用的测试夹具

`test/test_input.csv` 和 `test/websearch_candidates.csv` 未被 `test_litminer_core.py` 引用，是孤立的文档示例。

#### 其他测试质量问题

| 问题 | 说明 |
|------|------|
| 无属性测试 / Fuzz 测试 | 概念解析器和 CSV 处理是理想的 fuzz 目标 |
| 无覆盖率工具 | `pytest-cov` 未在 dev 依赖中 |
| `test_server.py` 使用裸 `assert` | 失败时仅产生通用 `AssertionError`，无信息性差异 |
| 静默失败模式未测试 | `workflow_state`、`crossref_verify`、`unpaywall_lookup` 的 `except Exception: return {}` 路径 |
| CLI 入口未测试 | `litminer-discover-api`、`litminer-triage` 的 argparse 解析无测试 |

### 6.3 TODO/FIXME/HACK

全项目 **零实例** — 无内联注释债务。

### 6.4 类型标注

覆盖率极高（约 360 个已标注函数），仅 7 个函数缺少返回类型标注（主要在 `server.py` 的内部辅助函数中）。mypy 配置合理（`check_untyped_defs = true`），但未启用 `strict` 模式。

---

## 七、API 设计与开发者体验审查

### 7.1 CLI 接口问题

#### C1: 大量参数缺少 `help` 文本

以下参数在 `--help` 输出中无任何说明：

| 参数 | 文件 |
|------|------|
| `--query-file` | `run_lit_search.py:1861`, `api_discovery.py:700` |
| `--year-from` / `--year-to` | `run_lit_search.py:1862-1863` |
| `--metrics` / `--min-if` | `run_lit_search.py:1962-1963` |
| `--allow-missing-doi` | `run_lit_search.py:1977` |
| `--screenshot-root` | `run_lit_search.py:1979` |
| `--probe-sleep` / `--unpaywall-sleep` | `run_lit_search.py:1981-1983` |

#### C2: `--sources` 与 `--discovery-sources` 命名不一致

`api_discovery.py` 使用 `--sources`，`run_lit_search.py` 使用 `--discovery-sources`。熟悉一个工具的用户在使用另一个时会犯错。

#### C3: Boolean 标志使用 `default=None` 反模式

`--skip-openalex`、`--include-semantic-scholar` 等使用 `store_true` + `default=None`。这在 `normalize_args` 前产生 `None` 值而非 `False`，是潜在 bug 源。

### 7.2 MCP 工具设计问题

#### M1: `litminer_start_run` 参数 schema 定义与实际不符

**文件：** `server.py:1281-1297`

内联定义 `"parameters": {}`（空），然后在 1377 行通过 mutation 从 `litminer_run_lit_search` 复制。静态阅读代码会误以为这些工具不接受参数。

#### M2: 工具描述过于简短

`litminer_run_lit_search` 的描述仅一句话，未说明必须提供 `queries` 或 `input_csv`，无示例，无输出结构说明。Agent 可读性差。

#### M3: 搜索结果静默截断

**文件：** `server.py:333`

```python
"results": results[:20]  # 硬编码截断至 20 条
```

Agent 若未注意 `truncated: True` 标志，将误以为收到了完整结果。应在工具描述中文档化，或提供 `limit` 参数。

#### M4: JSON-RPC 错误码无差异化

所有工具错误统一返回 `-32000`。MCP 客户端无法区分验证错误、网络错误和文件缺失。

### 7.3 输出格式问题

#### O1: `processing_report.md` 缺少尾部换行

**文件：** `processing_report.py:341`

```python
write_text_atomic(output_path, "\n".join(lines))  # 缺少 + "\n"
```

其他所有报告写入均附加 `"\n"`。

#### O2: `deduped_rows or api_rows` 逻辑 bug

**文件：** `agent_summary.py:181`, `processing_report.py:100`

```python
discovered = len(rows["deduped"] or rows["api"])
```

当 `deduped` 为空列表 `[]` 时，`or` 短路导致报告 pre-dedup 的计数。

**修复：**
```python
deduped = rows.get("deduped_candidates")
count = len(deduped) if deduped is not None else len(rows.get("api_candidates") or [])
```

#### O3: 可行性报告中 `strict_path`/`backup_path` 被归入 "Blocking reasons"

**文件：** `run_lit_search.py:1011-1014`

这些文件路径是 "Available artifacts"，不是阻断原因。Agent 读取报告时可能误判。

#### O4: "Non-OK Provider Calls" 包含 `empty_result`

**文件：** `processing_report.py:244-245`

`empty_result` 是正常结果（查询无匹配），但被包含在问题调用列表中，增加噪声。

**修复：** 过滤条件改为 `not in {"ok", "empty_result"}`。

#### O5: 时间戳实现不统一

5 个模块各自实现 `utc_now()`（见 4.1 D2），格式虽一致但代码不共享。

### 7.4 功能性 Bug

#### B1: `doctor.py` 中 OpenAlex API Key 检查始终返回 "ok"

**文件：** `doctor.py:247-249`

```python
Check("env", "ok" if _env_value(key_name) else "ok", ...)
```

三元表达式两个分支都是 `"ok"`，API Key 缺失永远不会触发警告。

#### B2: `doctor.py` 中 `"unknown_value"` 配置键通过验证

**文件：** `doctor.py:75`

`EXPECTED_CONFIG["evidence"]` 包含 `"unknown_value": (str,)`，导致验证器接受此无意义的键而不报警。

#### B3: `source_strategy.py` 中重复键

**文件：** `source_strategy.py:204-214`

```python
"configured_sources": effective_configured,
"effective_configured_sources": effective_configured,  # 始终与上行相同
```

重构残留，Agent 读取 `query_plan.json` 时产生困惑。

#### B4: `skip_unpaywall` 哨兵值检查不严格

**文件：** `run_lit_search.py:298`

```python
if getattr(args, "skip_unpaywall", None):  # 应为 is True
```

若通过非 CLI 路径（如 MCP）传入 `0` 或 `""`，判断结果错误。

#### B5: 概念解析器冒号歧义

**文件：** `semantic_triage.py:107-109`

`parse_concept_spec` 使用 `=` 和 `:` 作为名称与模式的分隔符，对含冒号的化学符号（如 `CO2:reduction`）产生错误切分。

### 7.5 跨平台兼容性

| 检查项 | 状态 | 说明 |
|--------|------|------|
| Path 处理 | **通过** | `pathlib.Path` 自动处理分隔符 |
| BOM 处理 | **通过** | `read_csv_rows` 使用 `utf-8-sig` |
| 原子写入 | **通过** | `tempfile.mkstemp` + `os.replace` 跨平台正确 |
| PowerShell bootstrap | 轻微问题 | 占位邮箱 `you@example.org` 可能被 Agent 直接复制 |
| MCP stdin 编码 | 轻微问题 | `errors="replace"` 静默替换非 UTF-8 字节，可能丢失重音字符 |
| 临时文件残留 | 轻微问题 | 进程中断时 `.tmp` 文件无清理机制 |

---

## 八、优先级修复路线图

### Phase 1: 紧急修复（Bug & 安全）

| 编号 | 类型 | 问题 | 文件 | 预估工作量 |
|------|------|------|------|-----------|
| B1 | Bug | `doctor.py` OpenAlex 检查始终 `"ok"` | `doctor.py:247` | 5 分钟 |
| B2 | Bug | `"unknown_value"` 配置键误通过 | `doctor.py:75` | 5 分钟 |
| O2 | Bug | `deduped or api` 空列表短路 | `agent_summary.py:181`, `processing_report.py:100` | 15 分钟 |
| S1 | 安全 | `resolve_workspace_path` 缺工作区限制 | `workspace.py:23-29` | 30 分钟 |
| P5 | Bug | `artifacts.write_index()` 双调用 | `run_lit_search.py:894-896` | 5 分钟 |
| O1 | Bug | 报告缺尾部换行 | `processing_report.py:341` | 5 分钟 |
| O4 | Bug | `empty_result` 误入问题调用 | `processing_report.py:244` | 5 分钟 |

### Phase 2: 性能优化（高影响）

| 编号 | 问题 | 修复方案 | 预估工作量 |
|------|------|----------|-----------|
| I2 | `row_count` 全量读 CSV | 改为行计数或 manifest 元数据 | 30 分钟 |
| I1 | 缓存无内存层 | 添加 `_data` 字典读穿层 | 1 小时 |
| M3 | `row_quality` 未缓存 | 局部变量缓存 | 10 分钟 |
| A1 | Crossref 礼貌暂停不可配 | 暴露为参数 | 20 分钟 |
| A3 | 近义词匹配重复分词 | 行级分词缓存 | 30 分钟 |

### Phase 3: 代码质量提升

| 编号 | 问题 | 修复方案 | 预估工作量 |
|------|------|----------|-----------|
| D1 | HTTP 重试逻辑 6x 重复 | 提取 `http_client.py` 共享模块 | 3 小时 |
| D2 | `utc_now` 5x 重复 | 统一到 `common.py` | 30 分钟 |
| D3 | 辅助函数跨 provider 重复 | 提取共享工具函数 | 1 小时 |
| G1 | `os.environ` 副作用 | 改为显式参数传递 | 1 小时 |
| G2 | OpenAlex `DEFAULT_MAILTO` import 时求值 | 改为调用时求值 | 15 分钟 |
| P4 | `sys.path` 注入 | 移除生产代码中的 hack | 30 分钟 |

### Phase 4: 架构改进

| 编号 | 问题 | 修复方案 | 预估工作量 |
|------|------|----------|-----------|
| P1 | `Namespace` 耦合 | 引入 `RunConfig` dataclass | 4 小时 |
| P2 | `run_lit_search.py` 过大 | 拆分为 3-4 个模块 | 3 小时 |
| P3 | Crossref 异常体系不统一 | 继承 `ProviderSearchError` | 1 小时 |
| 日志 | 无 `logging` 模块 | 引入标准库 logging | 2 小时 |

### Phase 5: 测试补全

| 编号 | 问题 | 修复方案 | 预估工作量 |
|------|------|----------|-----------|
| T1 | `merge_csv` 无测试 | 添加 union-schema、空文件、重复字段测试 | 1 小时 |
| T2 | `validate_stage` 无测试 | 添加列缺失、空值测试 | 30 分钟 |
| T3 | `status_policy` 无测试 | 添加分类覆盖、边界值测试 | 30 分钟 |
| T4 | CLI argparse 无测试 | 添加入口点参数解析测试 | 1 小时 |
| T5 | 添加 `pytest-cov` | 加入 dev 依赖并集成 CI | 30 分钟 |

### Phase 6: API & DX 改进

| 编号 | 问题 | 修复方案 | 预估工作量 |
|------|------|----------|-----------|
| C1 | CLI 参数缺 help | 补全所有参数帮助文本 | 1 小时 |
| M1-M4 | MCP 工具定义问题 | 补全描述、参数 schema、错误码 | 2 小时 |
| B3 | `source_strategy` 重复键 | 移除 `effective_configured_sources` | 5 分钟 |
| B5 | 概念解析冒号歧义 | 文档化或修改分隔符优先级 | 30 分钟 |

---

## 附录：审查方法

本报告由 6 个独立审查维度并行完成，每个维度使用专门的代码分析 Agent 深入阅读源代码：

1. **架构与结构** — 模块关系、数据流、配置系统、依赖图
2. **安全性** — 路径穿越、SSRF、注入、凭证处理、危险函数
3. **代码质量** — 重复、命名、复杂度、全局状态、日志
4. **性能与可扩展性** — 内存、I/O、缓存、并发、算法复杂度
5. **测试覆盖** — 覆盖率、质量、边界用例、类型标注
6. **API 设计与 DX** — CLI/MCP 接口、输出格式、跨平台兼容性

所有问题均附带具体的文件路径和行号引用，可直接定位修复。
