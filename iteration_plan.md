# Litminer 迭代方案

> 日期：2026-06-20（v2，整合多轮讨论后的收敛版本）
> 定位：个人 + AI Agent 协作维护的轻量迭代路线
> 核心立场：在稳定的产品定位内做深，不通过扩张定位来增加功能

---

## 产品定位（不可移动）

Litminer 是 **Agent 原生的文献检索基础设施**。它做发现-验证-标注-队列，诚实报告失败和边界。

迭代准绳：**如果一个能力需要改变"Litminer 是什么"才能合理存在，它就不该做。** 所有迭代都在"Agent 原生的文献检索基础设施"这个定位内做深。`search_audit_report` 是诚实原则的延伸，PDF 元数据是 Hard Boundary 内的补全，引用扩展是发现能力的强化——它们都是同一座房子的补全，不是另盖一座房子。

---

## 设计原则

在进入具体方案之前，先固定几条从讨论中沉淀下来的原则。后续每个迭代项都应该通过这些原则的检验。

### 原则一：边界即产品

Litminer 的 Hard Boundaries（不做科学判断、不读 PDF 内容、不绕付费墙）不是缺陷，是产品定义。任何迭代都不应模糊这些边界。但"守住边界"不等于"在边界内可以静默失败"——`--min-if` 在空种子文件下静默 no-op 就是反例。

**检验标准：** 每个新功能上线前问一句——"如果这个功能在某种常见配置下静默无效，用户会困惑吗？"如果会，必须加 preflight 警告。

### 原则二：集合统计 ≠ 领域断言

- **可以输出：** "检索到的 187 篇中，23 篇发表在 Nature Energy" — 关于集合的描述性事实
- **不应输出：** "Nature Energy 是该领域的主要期刊" — 要求集合代表性，Litminer 无法保证
- **灰区处理：** 按 `cited_by_count` 排序是机械操作（可以做），但标注为"必读"是价值判断（不做）。输出"高引 Top 10"，不输出"必读 Top 10"

### 原则三：统计必须标注信任边界，不扁平化信任分层

这是原则二的延伸。Trust Tiers 的精神必须从行级延伸到统计级。任何统计产出（`result_profile`、`search_audit_report` 等）必须按 Trust Tier 分层，不能把未验证行和 Crossref 验证行混在一起算期刊分布——那会产出看起来干净但实际信任度参差不齐的统计。

### 原则四：检索过程完整性可报告，检索结果完整性不可声称

- **可以报告：** "Semantic Scholar 被断路了，3 个查询 429 跳过了"——检索过程的失败可知可报
- **不可声称：** "这个领域还有 50 篇相关论文 Litminer 没搜到"——文献的遗漏不可知，任何这种声称都是幻觉

`completeness_caveats` 必须严格限制在前者。`search_adequacy` 如果做，也必须明确：它报告的是检索过程的完整性（哪些源失败了、哪些查询触顶了），不是检索结果的完整性（领域覆盖度）。

### 原则五：Litminer 提供能力，Agent 做决策

迭代式检索的机械能力（合并 CSV、增量去重、保留已验证行）属于 Litminer。迭代的决策（何时补充检索、用什么新查询、要不要扩展引用）属于 Agent。不要把 Agent 的判断逻辑吸收进 Litminer。

### 原则六：零依赖是分发优势，不是限制

对于 `git clone` 安装、Agent 沙箱调用的 skill 来说，零运行时依赖意味着零环境冲突、零 venv 教程、MCP 启动可预测。Core 的所有迭代方案必须保持 stdlib-only 运行时。可选的 adapters 层（如未来的 PDF 元数据提取）可以引入可选依赖，但 core 的任何功能都不依赖 adapters 层。

### 原则七：小步验证，不预设抽象

不为"将来可能需要"的场景提前设计抽象。RunConfig dataclass 很好，但应该在接入第一个新 provider 时顺手做，用真实需求验证设计，而不是先花两天重构再期待它被用到。HTTP 客户端统一不是为未来抽象，是为当前 6 文件重复修改的实存疼痛。

### 原则八：Litminer 只使用合法访问通道

限速失败是要报告的事实，不是要绕过的障碍。Litminer 不通过 IP 池、多邮箱轮询、伪装 UA 或类似技术规避限速。这和 `completeness_caveats` 的精神一致——把"我没拿到"诚实说出来，而不是偷偷绕过去再假装拿全了。一个对上游不诚实的工具，不能要求下游信任它的输出。

### 原则九：Core 不持有访问凭证

Core 不持有 institutional credentials、cookies、proxies。机构访问、JavaScript 渲染、paywall 后内容提取属于外部适配器（`publisher_adapters.py` 中的 `external_optional`），由用户/Agent 持有自己的 session。Core 的可信度建立在"我只看公开可访问的东西"这个简单承诺上。

### 原则十：每条限制必须写理由

不是 governance 框架，是"为什么这条线在这里"的一句话。写下来的读者不是现在的项目，是未来某个没读过这几轮讨论的贡献者或 Agent 会话——他们要移动某条线时，必须先回答这个"因为"。解释权锚定在理由上，不锚定在"当时的 Agent 怎么说"。

---

## 第一轮：修真 Bug + 边界文档化 ✅ 已完成

> commit `170a44e` — Fix review-identified bugs, remove sys.path workarounds, add preflight metrics warning and citation signal to triage
> commit `6ed058f` — Add statistical output boundary, limits-as-product-definition to SKILL.md, and project review suite
>
> 每项都有确凿的代码证据或明确的文档责任，改动小，不触碰边界。
> SKILL.md 的三个新章节一起写，一次提交。

### 1.1 确认 Bug 修复

| 编号 | 问题 | 文件 | 改动量 |
|------|------|------|--------|
| B1 | `doctor.py` OpenAlex 检查 `"ok" if ... else "ok"` | `doctor.py:247` | 1 行 |
| B2 | `EXPECTED_CONFIG` 含无意义 `unknown_value` 键 | `doctor.py:75` | 1 行 |
| O2 | `deduped or api` 空列表短路报错数 | `agent_summary.py:181`, `processing_report.py:100` | 2 处各 3 行 |
| P5 | `artifacts.write_index()` 双调用 | `run_lit_search.py:894-896` | 删 1 行 |
| O1 | `processing_report.md` 缺尾部换行 | `processing_report.py:341` | 1 行 |
| O4 | `empty_result` 误入 "Non-OK Provider Calls" | `processing_report.py:244` | 1 行 |
| B4 | `skip_unpaywall` 哨兵检查不严格（`is True` vs 真值检查） | `run_lit_search.py:298` | 1 行 |
| P4 | `sys.path.insert` 在生产代码中残留 | `run_lit_search.py`, `api_discovery.py`, `server.py` | 移除生产代码中的 hack，仅保留测试 workaround |

### 1.2 Preflight 防静默失败

当用户启用了 `--min-if` 或 `--metrics` 但种子文件为空（仅表头）时，在管道启动前发出明确警告：

```
WARNING: --min-if is set but the metrics CSV contains no data rows.
All papers will receive metric_filter_status="unverified".
Provide a populated metrics CSV via --metrics, or remove --min-if.
```

同理，当 `skip_unpaywall=False` 但无可用 email 时，也应在 preflight 而非运行中途静默 skip。

这不是新功能，是工程质量——不让用户在无效配置下静默运行。原则一的应用。

### 1.3 `cited_by_count` 进入 triage 评分

**改动范围：** `semantic_triage.py` 的 `_score_row` 函数。

**设计：**
```python
cited = int(row.get("cited_by_count") or 0)
citation_bonus = min(math.log2(cited + 1) * 0.3, 2.0) if cited > 0 else 0.0
```

- 0 引用 → 0 分加成
- 10 引用 → +1.0
- 100 引用 → +2.0（封顶）
- 不改变 priority 的判定逻辑（仍然由概念匹配决定），只影响同 priority 内的排序
- 在 `processing_report.md` 的 triage 段落中注明"评分包含引用计数信号"
- SKILL.md 中标注："引用计数是机械排序信号，不代表科学重要性判断"

**为什么这一步优先：** `cited_by_count` 已经被采集但未被使用，是最纯粹的"低成本高收益"。它是机械排序信号（引用数是事实），不是科学判断（不说"高引=重要"）。

### 1.4 SKILL.md 新增三个章节

同一批 SKILL.md 更新，一次提交。详见文末"SKILL.md 更新清单"。

**章节一：Statistical Output Boundary**

明确集合统计与领域断言的区分、Trust Tier 分层统计的要求、过程完整性 vs 结果完整性的边界、不做查询对比词频提示。

**章节二：Limits as Product Definition**

三层限制分类（self-imposed / compliance / delegated），每条限制写理由。不是 governance 框架，是"为什么这条线在这里"的一句话。

**章节三：合规措辞（两段式）**

先承认 Litminer 输出中包含的个人数据性质（ORCID、机构、资助信息）以及 Litminer 自己做了什么、没做什么（不做个体画像），再分配合规责任给用户。不写一句式免责声明——那会削弱 Litminer 诚实定位，和标准法律免责声明没区别。

---

## 第二轮：让产出物更有用 + 撤稿检查 ✅ 已完成

> commit `6bf3b04` — Add stratified result_profile, Crossref retraction status with triage demotion, and OpenAlex affiliation/ORCID extraction
>
> 不增加新数据源、不改管道结构，让现有管道的输出对 Agent 和人类更有价值。
> `result_profile` 的设计必须在 HTML meta 提取（第三轮）之前稳定——新字段进来时有现成的地方放。

### 2.1 结果集合统计（result_profile，分层 + 完整性告诫）

**新增文件：** `litminer/engine/result_profile.py`（独立模块，不塞进 `run_lit_search.py`）

**输入：** 任何管道阶段的 CSV（通常是 `triaged_candidates.csv`）+ manifest + trace

**核心设计约束：**

1. **分层统计**——至少分 `all_rows` 和 `crossref_verified` 两层。未验证行的 `journal_name` 可能是 API 返回的错误值，把它和 Crossref 验证行一起算期刊分布，产出的是看起来干净但实际信任度参差不齐的统计。Agent 汇报时应该说"187 篇中 142 篇已验证，验证行中 Top 期刊是 Nature Energy (12 篇)"，而不是把信任度差异糊掉。

2. **`completeness_caveats` 字段**——从 manifest 和 trace 中提取检索过程的失败信息：

```json
"completeness_caveats": {
  "circuit_broken_providers": ["semantic_scholar"],
  "rate_limited_queries": 3,
  "provider_failure_counts": {"semantic_scholar": 2},
  "caveat_text": "Semantic Scholar was circuit-broken after 2 failures; coverage likely underestimates S2-indexed literature."
}
```

这是 Trust Tiers 精神从行级延伸到统计级的体现。干净的统计数字会暗示"检索是完整的"——必须同时报告"哪些源失败了、哪些查询被限速了"，否则就是在用友好包装掩盖不完整的检索过程。

3. **0 结果时退化为 `failure_summary`**——0 结果或大面积失败时不输出统计（没有东西可统计），改为从 manifest/trace 提取失败摘要：哪些 provider 失败了、失败类别是什么、建议的下一步是什么（`status_policy.py` 已有 `provider_next_action`）。

4. **缺列降级**——每个统计在其源列缺失时返回 `None`，不报错、不返回 0、不跳过整个 profile 生成。这条写进 `result_profile.py` 的 docstring。

**数据结构：**

```python
@dataclass
class ResultProfile:
    # 分层统计
    all_rows: LayerStats
    crossref_verified: LayerStats
    
    # 完整性告诫
    completeness_caveats: CompletenessCaveats
    
    # 失败路径退化（0 结果或大面积失败时）
    failure_summary: FailureSummary | None

@dataclass
class LayerStats:
    total_rows: int
    year_distribution: dict[str, int]
    top_journals: list[tuple[str, int]]     # 前 15
    top_authors: list[tuple[str, int]]      # 前 15
    high_cited: list[dict]                  # cited_by_count Top 10，含 title/doi/count
    article_type_distribution: dict[str, int]
    oa_rate: float | None                   # 有 is_oa 列时计算，否则 None
    abstract_coverage: float
    doi_coverage: float
    triage_priority_distribution: dict[str, int]
```

**集成方式：**
- 在 `run_lit_search.py` 的 triage 阶段后自动调用
- `run_lit_search.py` 只增加编排调用（10 行以内），实现逻辑在 `result_profile.py`
- 结果写入 `result_profile.json`
- 在 `agent_summary.json` 中嵌入摘要版本（`result_profile` 字段）
- 在 `processing_report.md` 末尾添加文本版本

**不做的事：**
- 不做关键词提取（需要 NLP，超出 stdlib）
- 不做"趋势"判断（"增长 40%"是推断，不是事实）
- 不做"主要期刊"标注（集合不代表领域）
- 不标注"必读"（"高引 Top 10"是事实排序，"必读"是价值判断）
- 不做 `frequent_terms_not_in_query`（把"高频"和"不在查询中"组合成字段是在做 Agent 应该做的对比判断，越界）

**为什么这一步值得做：** Agent 读完 `result_profile.json` 后可以直接汇报"检索到 187 篇论文，高优先级 42 篇，Top 期刊为 Nature Energy (12 篇) 和 ACS Catalysis (8 篇)，OA 比例 45%，高引 Top 论文是 XXX (342 次引用)。注意：S2 被断路，CS 方向覆盖可能低估"。不需要扫描 CSV，不需要自己做统计，且诚实报告了检索过程的限制。

### 2.2 撤稿检查（上移到第二轮）

**改动范围：** `litminer/sources/api/crossref_verify.py` 的 `verify_csv` 阶段。

**设计：**
- Crossref 的 `update-to` 关系中包含撤稿信息。在 Crossref 验证阶段顺手检查，多读一个字段，几乎零成本。
- 在 CSV 中新增 `retraction_status` 列：`active` / `retracted` / `update_to` / `unknown`
- 在 triage 中对已撤稿论文自动降级（`triage_priority` 降一级或标 `needs_review`）
- 在 `agent_summary.json` 中报告撤稿行数
- 在 `result_profile` 的分层统计中排除 `retraction_status=retracted` 的行（或单独标注）

**为什么上移到第二轮：** 这是学术基础设施的最低义务——不标注撤稿论文的文献发现工具，在产品诚信上是有缺陷的。撤稿信息的不传播会损害科学共同体利益（Retraction Watch 研究表明被撤论文在撤稿后多年仍被新论文引用）。不增加新 API（Crossref 已经在调用），是已有调用多读一个字段。

### 2.3 补全 OpenAlex 字段提取

在 `OPENALEX_SELECT_FIELDS` 中添加 `authorships`，从中提取：

| 新字段 | 来源 | 格式 |
|--------|------|------|
| `affiliations` | `authorships[].institutions[].display_name` | 分号分隔，去重 |
| `orcids` | `authorships[].author.orcid` | 分号分隔，去重 |

**不做的事：**
- 不添加 `grants`（资助信息），留到第三轮出版商 HTML meta 提取时一起补全（`citation_funder_name` 是更可靠的来源）
- 不给其他 provider 补字段（S2 和 Europe PMC 的字段扩展各有各的 API 结构，不一起做）

**为什么只做 OpenAlex：** OpenAlex 是默认启用的唯一发现源，补全它的字段提取覆盖面最大。其他 provider 是 opt-in 的，可以后续按需补全。一次只改一个 provider 的字段映射，更容易测试和验证。

---

## 第三轮：核心能力增强 + 审计性 ✅ 已完成

> commit `802580f` — Unify HTTP client across 6 providers, add citation expansion, search audit report, publisher HTML meta extraction, and consolidate utc_now
> commit `ef676c3` — Wire citation_expand and publisher_html_extract into pipeline, exclude retracted rows from result_profile stats, sync SKILL.md and CLAUDE.md, clean dead imports
> commit `f22f397` — Add citation_expand trace output, processing_report sections for expansion and HTML meta, and MCP tools for result_profile, search_audit_report, and citation_expand
>
> 这轮开始触碰管道结构，但每项仍然是独立可交付的。
> 每个新阶段必须是独立模块，`run_lit_search.py` 只增加编排调用（10 行以内），不增加实现逻辑。

### 3.1 HTTP 客户端统一

**新增文件：** `litminer/sources/api/http_client.py`

**范围：** 将 6 个文件中重复的重试/退避/429 处理提取为共享模块。

**边界（写进 docstring）：**
`http_client.py` 只负责**单次请求**的重试/退避/429/超时。**不吸收**跨请求的断路器、失败计数、失败缓存——那些是编排层逻辑，留在 `api_discovery.py`。混淆两层会让后续要拆开变得困难。

**接口设计：**

```python
@dataclass
class RetryPolicy:
    max_retries: int = 3
    max_wait_seconds: float = 120.0
    backoff_base: float = 2.0
    polite_pause_interval: float = 0.0  # 0 表示不做额外暂停

def fetch_json(url: str, *, headers: dict | None = None,
               retry: RetryPolicy = RetryPolicy(),
               timeout: float = 30.0) -> dict: ...

def fetch_xml(url: str, *, retry: RetryPolicy = RetryPolicy(),
              timeout: float = 30.0) -> bytes: ...

def retry_after_seconds(response: http.client.HTTPResponse) -> float | None: ...

def status_for_exception(exc: Exception) -> str: ...
```

**迁移策略：**

1. 迁移每个 provider 前，先列出旧行为对照表，确认无行为漂移：

| Provider | max_wait | backoff | 特殊处理 |
|----------|----------|---------|----------|
| OpenAlex | 120s | 2^attempt | 403/409 不重试 |
| S2 | 60s(env 可配) | 10s(env 可配) | 自有 RateLimitError |
| arXiv | 120s | max(3s, 2^attempt) | 固定 3s 间隔 |
| Crossref | 60s | 2^attempt | polite_pause 每 10 请求 |
| Europe PMC | 120s | 2^attempt | — |
| Unpaywall | 120s | 2^attempt | — |

2. 迁移后各 provider 的现有测试保留断言意图，mock 目标从 `urlopen` 改为 `http_client.fetch_json`
3. `http_client.py` 自身新增独立的 retry/backoff 单元测试
4. 一次迁移一个 provider，每迁移一个就运行该 provider 的测试 + offline smoke

**迁移顺序：** `openalex_search.py`（最常用，最先验证）→ `crossref_verify.py`（最复杂，验证覆盖面最大）→ 其余 4 个。

**为什么这一步是前置条件：** 6x 重复是当前疼痛（每次改动都要同步 6 个文件），不是未来疼痛。后续任何新 provider 接入、引用扩展集成、或重试策略调整都受益于统一的 HTTP 客户端。

### 3.2 引用扩展作为可选管道阶段

**改动范围：**
- `semantic_scholar_search.py` 的 `get_citations()` / `get_references()` 已实现
- 新增 `litminer/engine/citation_expand.py`（独立模块）作为阶段协调器
- `run_lit_search.py` 只增加编排调用（10 行以内）

**设计：**
- 新 CLI 参数：`--expand-citations`（默认关闭）
- **种子选择（机械默认 + Agent 覆盖）：**
  - 默认：按 `triage_priority=high` + `triage_score` 降序取 Top N（默认 5，可配 `--expand-top-n`）
  - 覆盖：`--expand-seeds doi:10.xxx,doi:10.yyy`（Agent 或用户显式指定）
  - SKILL.md 标注："默认种子选择是机械规则，不代表科学重要性判断"
- 每个种子论文最多扩展 M 篇（默认 30，可配）
- 扩展结果进入正常的去重 → 验证 → 筛选管道（不跳过任何阶段）
- `discovery_source` 标记为 `"semantic_scholar_citation"` / `"semantic_scholar_reference"`
- `source_note` 记录 `"cites doi:10.xxx"` 或 `"cited_by doi:10.xxx"`
- 在 `agent_summary.json` 的 `result_profile` 中报告扩展新增的论文数量
- 在 `processing_report.md` 中添加 "Citation Expansion" 段落

**边界问题的回答：**
- 扩展来的论文 **必须** 经 Crossref 验证（跟正常发现行一样的信任路径）
- trace 按 `(provider=semantic_scholar, query_type=citation_expand, seed_doi=xxx)` 记录
- 如果 S2 限速，按正常的 provider cooldown 和 circuit breaker 处理
- 扩展结果计入 `completeness_caveats` 的失败报告

**不做的事：**
- 不做多跳扩展（1 跳足够，多跳指数膨胀）
- 不自动选择种子的"科学重要性"（机械规则选 Top N，Agent 覆盖）
- 不在 fast 模式下启用（fast 模式的意义是快速验证方向）

**为什么值得做：** 引用扩展是关键词检索最强的补充——它能发现用了完全不同术语但科学上高度相关的论文。代码已经存在，主要工作是管道集成和 trace/报告适配。

### 3.3 search_audit_report.md（诚实原则的延伸）

**新增文件：** `litminer/engine/search_audit_report.py`（独立模块）

**定位：** 诚实原则从 Agent 端延伸到人类可读端。**不是产品定位扩张**——不是"用户群从 Agent 扩展到 Agent + 背后的研究者"，是同一原则（诚实报告）在检索过程维度的延伸。Litminer 已经在 `agent_summary.json` 里对 Agent 诚实，audit report 是对人类研究者也诚实。

**输入：** `result_profile.json` + `query_plan.json` + `agent_summary.json` + `api_discovery_trace.csv` + `run_manifest.json`

**输出：** `search_audit_report.md`——给人类读的自然语言 Markdown 文件，记录：

- 用了哪些查询词、为什么（来自 `query_plan.json`，转成人类可读）
- 配置了哪些概念（required/optional/negative）、为什么
- 每个数据源的成功/失败/限速情况
- 排除了多少行、为什么（DOI 缺失、Crossref 失配、metric 未过、撤稿等）
- 如果有 `--merge-into`，报告每轮查询和 `delta_profile` 中的新增数量
- Trust Tiers 各层的行数和含义
- `completeness_caveats` 的人类可读版本

**设计约束：**
- audit report 的信息必须和 Agent 拿到的信息**一致**——不能有"Agent 知道但研究者不知道"的信息差
- 格式必须是研究者能读的自然语言（不是 JSON）
- 这是给研究者向同事证明"我的检索策略是合理的"的产品文件——补上 Trust Tiers 的最外层：研究过程的可审计性

**为什么放在第三轮：** 依赖 `result_profile`（第二轮）作为输入。`result_profile` 稳定后再做。

### 3.4 出版商 HTML meta 提取

**新增文件：** `litminer/engine/publisher_html_extract.py`（独立模块）

**定位：** Hard Boundary 内允许但还没做的事的补全。Hard Boundary 写的是"Do not parse PDFs, OCR files, extract PDF tables, or inspect supplementary information"——**没有禁止 HTML 解析**。当前 `publisher_probe.py` 已经在解析 HTML（用正则找 PDF/SI 链接）。扩展 HTML 解析到提取结构化元数据，是同一类操作的延伸。

**范围（严格）：**
- 只提取 `<meta name="citation_*">` 和等价的 JSON-LD `schema.org/ScholarlyArticle` 结构
- 严格不做：JavaScript 执行、PDF 解析、paywall 后内容、SI 内容、任何需要凭证的字段
- 未提取到 meta 的行 **显式标 `html_meta_status="not_present_on_page"`**，不静默留空——否则下游会分不清"这篇论文没关键词"和"出版商页面没给 meta 标签"（同 `journal_metrics_seed.csv` 空文件的"静默 no-op"失败模式）

**字段级 provenance（不新增 Trust Tier）：**
- 行级信任保持四层不变（由 Crossref 验证状态决定）
- 字段级用 `field_provenance` 标 `source="publisher_html_meta"`
- Agent 可以说"这篇论文的关键词来自出版商页面"，不需要引入新的行级信任层
- **不新增 `publisher_visible` Trust Tier**——Trust Tiers 的产品力来自简单清晰，多一层增加 Agent 向用户解释的复杂度

**字段优先级（按研究者最缺什么）：**

| 批次 | 字段 | 价值 | API 替代 |
|------|------|------|---------|
| 第一批 | `citation_keywords` | API 几乎都不提供 | 无 |
| 第一批 | `citation_online_date` | "提前在线"论文识别 | API 给 `publication_year`，精度不够 |
| 第一批 | `citation_funder_name` | 资助分析的唯一来源 | OpenAlex/Crossref 经常缺失 |
| 第二批 | `citation_author_institution` | 机构分析 | OpenAlex 机构字段经常缺失 |
| 第二批 | `citation_author_orcid` | 作者消歧 | 同上 |
| 第二批 | `citation_abstract` | API 摘要缺失时补全 | 65-75% 覆盖，但出版商版更完整 |
| 第三批 | `citation_reference` | 参考文献列表 | Crossref 的 reference 经常被隐藏 |

**为什么 `citation_abstract` 不在第一批：** API 摘要覆盖率已经 65-75%，缺摘要的行往往也缺出版商页面（OA 期刊和预印本对得上，付费期刊页面有 abstract 但有时被 paywall 拦）。真正高价值且 API 几乎全缺的是 `citation_keywords`、`citation_online_date`、`citation_funder_name`。

**集成方式：**
- 在 `publisher_probe.py` 之后作为可选阶段
- 输入：`publisher_queue.csv` 中 `access_status in ("html_possible", "abstract_only_or_landing")` 的行
- 输出：独立的 `publisher_queue_html_meta.csv` 文件（含 `html_meta_*` 列）
- 在 `processing_report.md` 增加段落报告 HTML meta 覆盖率（"142 篇中 98 篇有 citation_keywords，67 篇有 citation_author_institution"）

**为什么放在第三轮：** `result_profile` 的分层统计设计必须在 HTML meta 字段进来之前稳定（第二轮完成）。如果 HTML meta 字段和 `result_profile` 同时开发，会出现"统计框架还没稳定就有新字段要纳入"的问题。

### 3.5 小型重复代码统一

顺手将 5 处 `utc_now()` 统一到 `common.py`。同时统一 `_year_ok`、`_clean_text`、`_row_identity` 等跨 provider 的小型重复函数。这些都是几行代码的改动，但消除了"改一处忘改其他五处"的维护风险。

---

## 第四轮：可用性补全 ✅ 已完成

> 4.1—4.4 均已按后续 Canonical Evidence/Contract Layer 设计落地。

### 4.1 RIS/BibTeX 导出 ✅ 已完成

**实际落点：** `litminer/exporters/`、`litminer-export`、
`litminer_export` MCP 工具和 finalize 的 `--export`。

原设想“从任何阶段 CSV 直接套文本模板”已被更严格的实现取代：
导出只消费 canonical bibliography，默认排除未验证、撤稿和缺标题记录。
显式包含未验证记录时不会提升其信任等级，并由
`export_manifest.json` 记录风险、排除原因、冲突和输出哈希。

- RIS/BibTeX 字段映射、Unicode/ASCII-LaTeX 模式和稳定 cite key 已实现
- CLI、runner finalize 与 MCP 共用同一 exporter
- 默认资格由书目信任和撤稿状态决定，不用科学 priority 替代书目可信度
- fixture、冲突稳定性、路径安全和导出审计已有自动化测试

**设计结论：** 这是从“能看结果”到“能交付可信书目”的桥梁，但不是
通用文献管理器，也不扩张 Litminer 的科学判断边界。

### 4.2 增量合并能力（`--merge-into`）✅ 已完成

**前置条件（已完成）：** `merge_csv.py` 已覆盖 union-schema 合并、空/缺失输入和重复表头；重复表头会明确失败，避免字段被静默覆盖。

**设计：** 不是"迭代式会话"（那是 Agent 的职责），而是一个简单的机械能力——将新一轮运行的发现结果合并到已有的输出目录中。

```bash
# 第 1 轮：正常运行
python -m litminer.engine.run_lit_search --query "photocatalytic H2" --output-dir run1/

# 第 2 轮：Agent 决定补充检索，将新结果合并到同一目录
python -m litminer.engine.run_lit_search --query "water splitting catalyst" \
    --merge-into run1/
```

**已实现行为（关键）：**
- 从已有目录选择 `deduped_candidates.csv`、`merged_candidates.csv` 或 `api_candidates.csv` 作为既有候选池，并保存 `merge_base_candidates.csv` 快照
- 新发现与既有候选池合并后重新执行去重
- **全量重跑 triage（用新概念配置）**——第二轮补充检索的典型场景就是改了概念配置（加同义词、加 negative concept），这种情况下旧行的 `triage_score` 是过期的，混在一起排序会得到错误结果。不重跑 triage 会在概念配置变更时静默产出错误排序，违反原则一
- 重新执行 pretriage、验证队列、Crossref 和最终 triage；已有可信 Crossref 结果可复用且不消耗当前行预算
- `research_session_manifest.json` 记录每轮查询、概念、时间、运行状态和增量摘要
- `result_profile` 重新计算（含新的 `completeness_caveats`）

**不做的事：**
- 不自动判断"需不需要补充检索"（Agent 的判断）
- 不自动生成新查询词（Agent 的判断）
- 不跟踪每篇论文的完整跨轮版本历史；只保留机械候选池快照和轮次级谱系

**为什么是 `--merge-into` 而不是 `--append-to`：** 语义更准确。不是在已有运行上追加阶段，而是将新检索的原始发现合并到已有的候选池中，然后重跑管道。

### 4.3 delta_profile（`--merge-into` 的增量可见性）✅ 已完成

**已实现：** 每轮 finalize 都写 `delta_profile.json`；合并轮次会相对 `merge_base_candidates.csv` 计算新增行数、新增书目已验证数、新增优先级分布、来源分布和 Top 期刊。`agent_summary.json` 嵌入当前轮 delta；若发现旧轮遗留 delta，会明确标记 stale，而不是静默误报。

**为什么必须做：** 没有 delta，`--merge-into` 在产品层是黑盒。研究者做迭代检索的真实场景是问 Agent"第二轮新找到了什么"——不是"现在总共有多少论文"。Agent 如果只能说"现在总共有 240 篇"而说不出"新增了 53 篇，其中 12 篇高优先级"，那迭代检索的价值就打了折扣。差集计算是机械操作（manifest 里已有每轮的时间戳和查询），不增加新 API 调用。

### 4.4 MCP 工具描述补全 ✅ 已完成

该项已升级为 Contract Layer，而不是只润色 description：

- 默认 MCP 面收敛为九个高层工具，低层工具进入 advanced profile
- CLI/MCP 共享 `RunSpec` 与严格 JSON Schema
- 客户端声明 Schema 与服务端严格 Schema 分离，兼容 Claude Code，同时
  不放宽运行时输入约束
- 支持当前 Codex/Claude 所需的 MCP 协议版本矩阵
- provider 分页、截断、workspace、安全和错误字段均在描述/Schema 中公开
- 工具失败返回 `isError=true` 与结构化 `ErrorEnvelope`
- 确定性及真实 Codex/Claude MCP acceptance 防止“文档存在但工具不可用”的假绿

---

## 按需扩展（不预排期，等触发条件）

以下是候选的扩展方向，但不预排进路线图。当出现明确的触发条件时再启动。

### 5.1 RunConfig dataclass

**触发条件：** 当接入第一个新 provider 或重构 CLI 参数时。
**不提前做的原因：** 影响面大（CLI + MCP + 所有测试），没有真实需求驱动时容易过度设计。

### 5.2 新数据源（PubMed E-utilities 等）

**触发条件：** 当有明确的生医系统综述使用场景时。
**不提前做的原因：** 生医不是唯一场景，PubMed 的 E-utilities API 有自己的复杂性（MeSH 检索、结果格式），不是 1-2 天能做好的。DBLP、BASE、CORE 同理——按领域需求逐个评估。

### 5.3 Retraction Watch 扩展接入

**触发条件：** 当 Crossref `update-to` 的覆盖度不够、需要更全面的撤稿数据时。
**做法：** 第二轮已经做了 Crossref `update-to` 的基础版，Retraction Watch 的公开数据作为补充源按需接入。

### 5.4 source_strategy 领域扩展

**触发条件：** 当有非 biomedical/chemistry/materials/environmental 领域的实际使用时。
**做法：** 扩充 `DOMAIN_HINTS` 词表，添加 economics、social science、CS 等领域。

### 5.5 缓存内存层

**触发条件：** 当实际运行中缓存 I/O 成为可测量的瓶颈时（目前是理论推测）。
**做法：** 在 `JsonCache` 中添加 `_data` dict 作为读穿层，仅在 `set()` 时写磁盘。

### 5.6 PDF 元数据提取（DOI/标题）

**定位：** Hard Boundary 内的补全（读信封不读信），**不是"研究者论文管理助手"的一部分**。Hard Boundary 禁止的是读 PDF 内容/表格/SI，不禁止读 PDF 元数据（XMP/Dublin Core 或首页正则匹配 DOI）。

**工程设计：** 可选 adapters 层，不破坏 core 的 stdlib-only 约束。

```
litminer/
├── engine/          # core, stdlib-only
├── sources/         # core, stdlib-only
└── adapters/        # optional, may have dependencies
    └── pdf_meta.py  # requires: pip install litminer[pdf]
```

- 没装 PDF 依赖时：core 的功能正常工作，遇到 PDF 路径输入报明确错误 "PDF input requires `pip install litminer[pdf]`"
- 装了 PDF 依赖时：自动从 PDF 提取 DOI，走正常验证管道
- **Core 的任何功能都不依赖 adapters 层**——adapters 是 core 的消费者，不是 core 的依赖

**范围限定：** 读信封（DOI/标题/作者元数据），不读信（内容/表格/SI/摘要）。

**触发条件：** verify-papers 入口实现后、且有真实用户反馈需要 PDF 输入时。先做不需要 PDF 的 verify-papers（DOI/标题列表输入），验证这条路径有人用，然后再考虑加 PDF 支持。

### 5.7 verify-papers 入口

**定位：** 已有管道的第二入口，**不是"研究者论文管理助手"**。复用已有管道（Crossref 验证 → OA 标注 → triage → 报告），只是开了第二个入口。

**设计：**
- 输入：DOI 列表或标题列表（不涉及 PDF，PDF 支持见 5.6）
- 跳过发现阶段，直接走验证管道
- 输出和正向检索一致（`agent_summary.json`、`result_profile.json`、`processing_report.md` 等）

**触发条件：** 当有真实场景需要反向验证已有论文集合时（如研究者拿到一篇被推荐的论文想知道"这篇是不是这个方向的核心"、或拿到一组论文想验证 Crossref 元数据准不准）。

**不做的事：**
- 不和 PDF、RIS/BibTeX 组合成"研究者论文管理助手"叙事——这是定位扩张，会让项目变得更大更乱
- verify-papers 就是 CLI 的第二个入口，不是一个"面"

### 5.8 search_coverage 字段

**触发条件：** 当 `completeness_caveats` 不足以覆盖检索过程描述时。
**做法：** `completeness_caveats` 处理真正的失败和限速；`search_coverage` 处理过程描述（成功的源数、触顶的查询数）。两个都是机械事实，但语气不同——一个是"出了问题"，一个是"这是搜索过程的参数"。可作为 `result_profile` 的补充字段按需添加。

### 5.9 high_priority_abstracts_top_terms

**触发条件：** 当 Agent 需要"集合内高频术语"作为汇报素材时。
**做法：** 纯词频统计（不做 stopword 过滤——stopword 列表本身就是领域假设），输出 `high_priority_abstracts_top_terms: [("water splitting", 23), ("HER", 18)]` 带 raw 频次。**不做查询对比**——不输出 `frequent_terms_not_in_query`，把"这些词是否应该加进查询"的判断完全留给 Agent。

### 5.10 日志系统

**触发条件：** 当 `run_lit_search.py` 超过 2000 行后 debug 越来越痛时。
**做法：** 引入标准库 `logging.getLogger(__name__)` 替代 82 处 `print(..., file=sys.stderr)`。不增加运行时依赖。

---

## 不做清单

以下是明确不做的事项，以及不做的原因。每条限制都写明理由（原则十）。

| 不做 | 原因 | 限制类型 |
|------|------|---------|
| IP 池 / 多邮箱 / 任何限速规避 | 对 OpenAlex/Crossref/Unpaywall 等 donated infrastructure 的 freeloading；破坏 Litminer 诚实定位；现有 6 源里 4 个根本不需要绕，2 个有官方高限速通道且已支持 | Self-imposed |
| Core 持有 institutional credentials | 持有凭证 = core 在不同用户合法访问权间无法区分；凭证泄漏的安全责任；破坏"我只看公开信息"承诺 | Self-imposed |
| Core 执行 JavaScript | 需要重量级依赖；JS 渲染页面可能含 paywall 后内容；属于外部 `browser_page` 适配器 | Delegated |
| Core 绕 paywall（即使用户有合法访问权） | Hard Boundary；机构访问走外部适配器，由用户持有 session | Self-imposed + Compliance |
| PDF 内容/表格/SI 提取 | Hard Boundary；需要非 stdlib 依赖 | Self-imposed |
| `frequent_terms_not_in_query` | 把"高频"和"不在查询中"组合成字段是在做 Agent 应该做的对比判断，越界（原则二） | Self-imposed |
| 新增 `publisher_visible` 行级 Trust Tier | Trust Tiers 的产品力来自简单清晰；用 `field_provenance` 字段级标注替代 | Self-imposed |
| "Agent + 背后的研究者"作为产品定位扩张 | 研究可审计性是能力，不是新用户群；定位扩张会让项目变得更大更乱 | Self-imposed |
| verify-papers + PDF + RIS/BibTeX 组合成"研究者论文管理助手"叙事 | 定位扩张；verify-papers 是第二入口，不是助手 | Self-imposed |
| LLM 驱动的语义理解 | 违反"不做科学判断"原则；引入外部依赖 | Self-imposed |
| 自动检索策略优化 | Agent 的职责，不是 Litminer 的职责（原则五） | Self-imposed |
| Web UI / 前端界面 | 太重，超出个人 + Agent 的维护能力；违反零依赖分发优势 | Self-imposed |
| PRISMA 流程自动化 | 领域特化太深，不符合"领域中立"定位 | Self-imposed |
| 多人协作/权限系统 | 太重，个人项目不需要 | Self-imposed |
| PyPI 发布 | 当前 `git clone` 分发模式对 Agent skill 更合适 | Self-imposed |
| 关键词/NLP 提取作为 core 功能 | 需要非 stdlib 依赖（分词器）；容易越过统计/断言边界 | Self-imposed |
| 一句式法律免责声明 | 会削弱 Litminer 诚实定位；应写两段式（先承认数据性质，再分配责任） | Self-imposed |

---

## 元规则

这些是关于"怎么做"的规则，不是"做什么"的规则。

### M1：新阶段必须是独立模块

`run_lit_search.py` 只增加编排调用（10 行以内），不增加实现逻辑。

- 引用扩展 → `citation_expand.py`
- 合并 → `merge_run.py`（或 `merge_csv.py` 增强）
- 结果统计 → `result_profile.py`
- 审计报告 → `search_audit_report.py`
- 出版商 HTML 提取 → `publisher_html_extract.py`
- 导出 → `export.py`

这条规则比"以后拆 `run_lit_search.py`"更可执行。当前该文件已 1999 行，不让它继续膨胀。

### M2：新功能必须带测试；旧模块仅在修改时补测试

不专门扫测试窟窿，但新功能上线必须带测试。修改旧模块时如果触及无测试代码（如 `merge_csv.py`），先补测试再改。

### M3：每条限制必须写理由

不是 governance 框架，是"为什么这条线在这里"的一句话。写下来的读者不是现在的项目，是未来某个没读过这几轮讨论的贡献者或 Agent 会话。详见原则十。

### M4：迭代检验标准（30 秒测试）

不是"功能是否完整"，而是：

> 一个 Agent 拿到 Litminer 的输出后，能否在 30 秒内向用户给出一个准确、有信息量的回答——而不是"管道跑完了，请看 CSV"？

**成功路径和失败路径都要测：**
- 成功路径：Agent 能否在 30 秒内汇报"检索到 187 篇，高优 42 篇，Top 期刊是 Nature Energy (12 篇)，最高引论文是 XXX (342 次引用)，OA 比例 45%。注意 S2 被断路，CS 方向覆盖可能低估"
- 失败路径：Agent 能否在 30 秒内解释"为什么一次运行产出了 0 结果或 50% 限速行"——`result_profile` 在 0 结果时退化为 `failure_summary`

每一轮都让这个问题的答案好一点，这就是对的方向。

---

## SKILL.md 更新清单（第一轮 1.4 节交付）

第一轮需要新增的三个章节，一次提交。

### 章节一：Statistical Output Boundary

放在现有 "Core Boundary" 章节之后。

```markdown
## Statistical Output Boundary

Litminer may output statistics about the retrieved collection, but must not
output assertions about the research field. The collection is shaped by the
search strategy, not by the field itself.

### Allowed (collection statistics)

- Year distribution, journal distribution, author frequency within the collection
- High-cited ranking (by `cited_by_count`, mechanical sort)
- OA rate, abstract coverage, DOI coverage within the collection
- Triage priority distribution (high/medium/needs_review/low counts)

### Not allowed (field assertions)

- "X is the leading journal in this area" (requires representativeness)
- "The field is trending toward Y" (requires causal inference)
- "These are must-read papers" (requires value judgment)

### Tier stratification

Statistics must be stratified by Trust Tier, not flattened. Unverified rows
and Crossref-verified rows have different trust levels; mixing them in a
single journal distribution produces statistics that look clean but have
uneven trust. Report `all_rows` and `crossref_verified` as separate layers.

### Process completeness vs result completeness

Litminer can report search process completeness (failures, rate limits,
circuit breaks, query caps) but must never claim result completeness (field
coverage). "Semantic Scholar was circuit-broken" is a reportable fact;
"there are 50 more relevant papers Litminer missed" is a hallucination.

`completeness_caveats` is strictly limited to the former.

### No query-comparison term hints

Litminer may output raw term frequencies in high-priority abstracts
(`high_priority_abstracts_top_terms`). It must not output
`frequent_terms_not_in_query` — combining "frequent" and "not in query"
into a single field does the Agent's job of recommending search strategy
adjustments. Providing the facts is Litminer's role; deciding what to do
with them is the Agent's role.
```

### 章节二：Limits as Product Definition

放在 "Statistical Output Boundary" 之后。

```markdown
## Limits as Product Definition

Litminer's limits are not unfinished work. They are product definition.

### Three kinds of limits

1. **Self-imposed (product identity)**: rate-limit circumvention, paywall
   bypass, credential holding, LLM-driven scientific judgment. Litminer
   chose not to do these because doing them would change what Litminer
   *is*, not just what it does.

2. **Compliance (legal/ToS exposure)**: bulk redistribution of provider
   metadata, automated access against publisher ToS, database rights in
   some jurisdictions. Litminer cannot do these regardless of technical
   feasibility.

3. **Delegated (external adapter only)**: institutional-access full text,
   JavaScript-rendered pages, PDF extraction. These belong to
   user-controlled external adapters; Litminer core never holds
   credentials for them.

### Why each line is here

- "No rate-limit circumvention": OpenAlex/Crossref/Unpaywall are donated
  infrastructure. Freeloading harms the ecosystem Litminer depends on.
  Honest failure reporting (`completeness_caveats`) is the product feature,
  not an obstacle to work around.
- "No credentials in core": core's trustworthiness rests on "I only look
  at publicly accessible things." Holding credentials breaks this promise
  and creates security/liability exposure.
- "No PDF content/SI parsing": Hard Boundary. Reading the envelope (DOI,
  title, author metadata) is allowed; reading the letter (content,
  tables, SI) is not.
- "No scientific judgment": Litminer tags, ranks, queues, reports. Final
  inclusion/exclusion decisions belong to the Agent and the researcher.

### Moving a limit requires answering

- Does moving it change Litminer's product identity?
- Does moving it create compliance exposure for users?
- Does moving it require core to hold credentials or make access decisions
  on behalf of users?

If the answer to any is yes, the limit is not movable by code change
alone. The reason for the line must be re-read and re-judged first.
```

### 章节三：合规措辞（两段式）

放在 "Limits as Product Definition" 之后，或合并入其 "Compliance" 子章节。

```markdown
### Data protection and redistribution

Litminer outputs contain author metadata (ORCID, affiliations, funding
information) that may be considered personal data in some jurisdictions.
These fields are extracted from public publisher metadata and aggregated
for research discovery purposes. Litminer does not perform profiling of
individuals across multiple works — it aggregates "which papers are in
this collection", not "what is a specific researcher's complete activity
trajectory".

If you redistribute Litminer outputs to third parties or use them for
non-research purposes, you are responsible for compliance with applicable
data protection regulations (GDPR, CCPA, and others). Litminer's outputs
are intended for research use; commercial profiling use requires your own
compliance assessment.
```

---

## 总结

整个迭代路线的核心逻辑：

```
第一轮：修真 bug + 边界文档化        ✅ → 让现有功能可靠 + 产品定义写下来
第二轮：分层统计 + 完整性告诫 + 撤稿  ✅ → 让现有产出有用且诚实
第三轮：HTTP 统一 + 引用扩展 + 审计性 + HTML meta ✅ → 强化核心检索能力 + 补全边界内缺失
第四轮：可信导出 + 增量合并/delta + MCP Contract ✅ → 4.1—4.4 全部完成
按需：不预设，等触发                  ⏳ → 不为未来抽象，用真实需求验证
```

每一轮都是独立可交付的。第一轮做完就有价值，不依赖后续轮次。每一轮的工作量都在"1 人 + AI Agent 几天内可完成"的范围内。Core 不引入新的运行时依赖，不越过已定义的产品边界。

**对的方向的检验：** 每一轮都让"Agent 能否在 30 秒内给出准确有信息量的回答"这个问题的答案好一点。每一轮都强化 Litminer 的诚实定位（Trust Tiers 纪律、失败透明、边界明确），不稀释它。
