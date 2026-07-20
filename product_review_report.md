# Litminer 产品审查与演进方案

> **历史快照：** 本文记录 2026-06-20 的产品视角，不是当前功能清单或
> 未完成 backlog。后续已落地的 canonical evidence、可信导出、共享
> Contract Layer、SQLite runtime 和九工具 MCP，以
> `litminer_next_architecture_design.md` 与 `CHANGELOG.md` 为准。
>
> 审查日期：2026-06-20
> 视角：产品定位、用户价值、能力边界、演进方向
> 性质：如果我来做这个产品，我会怎么想、怎么做

---

## 目录

- [一、产品本质判断](#一产品本质判断)
- [二、用户价值审计](#二用户价值审计)
- [三、能力边界与诚实度评估](#三能力边界与诚实度评估)
- [四、数据源能力深度分析](#四数据源能力深度分析)
- [五、产出物价值评估](#五产出物价值评估)
- [六、用户旅程断点分析](#六用户旅程断点分析)
- [七、竞争定位与差异化](#七竞争定位与差异化)
- [八、如果我来做这个产品](#八如果我来做这个产品)
- [九、具体演进方案](#九具体演进方案)
- [十、优先级与节奏建议](#十优先级与节奏建议)

---

## 一、产品本质判断

### 1.1 Litminer 是什么

Litminer 是一个 **Agent 原生的学术文献检索基础设施**。它不是面向人类的搜索引擎，不是综述写作工具，不是 PDF 阅读器。它的核心设计意图是：让 AI Agent 能够以可复现、可审计、可中断恢复、对失败诚实的方式执行文献检索的机械性工作。

这个定位非常独特。市面上的文献工具（Elicit、Consensus、ResearchRabbit、Connected Papers）都是面向人类终端用户的产品，Litminer 则把自己定位为 Agent 的"技能"——一个被调用的能力模块，而非被使用的应用。

### 1.2 这个定位对不对

**对，但不够。**

Agent 原生的定位是一个正确的差异化选择，原因有三：

1. **AI Agent 正在成为研究工作流的核心**。Claude Code、Codex、未来的各种 Agent 框架都需要结构化的文献检索能力，而不是把一串搜索结果塞进 prompt。
2. **机械性文献检索确实是 Agent 的最佳工作**。查询 API、去重、校验 DOI、标注 OA 状态——这些工作重复、规则明确、不需要科学判断。
3. **信任分层是关键洞见**。Litminer 最好的设计决策是 Trust Tiers 模型：发现行（未验证）→ Crossref 验证行 → 指标通过行 → 出版商队列行。这种递进式信任而非扁平结果列表，正是 Agent 系统需要的。

**不够的地方在于**：纯粹的"基础设施"定位会让产品陷入"有用但不被需要"的困境。文献检索是手段，不是目的。研究者不是想要一个 CSV 列表，而是想要回答"这个领域的现状是什么"、"哪些方法被验证过"、"谁在做这个方向"等问题。Litminer 在"发现文献"这一步做得很好，但在"文献发现之后怎么办"这一步完全停住了。

### 1.3 本质问题

Litminer 目前是一个 **半成品的正确产品**。架构设计精良、失败处理诚实、代码质量上乘——但它停在了价值链的中间位置。它生产的是半成品（验证过的候选文献列表），而不是终端价值（研究问题的答案）。

---

## 二、用户价值审计

### 2.1 三类用户的真实需求

| 用户类型 | 表面需求 | 深层需求 | Litminer 满足度 |
|----------|----------|----------|----------------|
| AI Agent（技能调用方）| 结构化文献数据 | 做出研究决策的证据基础 | **70%** — 数据结构好，但缺乏摘要/引用分析 |
| 人类研究者 | 找到相关论文 | 理解领域现状、发现研究空白 | **30%** — 给了列表，但无分析、无可视化 |
| 开发者（扩展方）| 添加新数据源 | 低摩擦地贡献代码 | **60%** — 架构清晰，但 HTTP 重试等样板代码多 |

### 2.2 价值漏斗分析

```
研究者的完整工作流：

   提出研究问题
        ↓
   确定检索策略（查询词、年份、数据源选择）
        ↓
   执行检索 ←── Litminer 覆盖了这里
        ↓
   去重与元数据验证 ←── Litminer 覆盖了这里
        ↓
   相关性筛选 ←── Litminer 部分覆盖（关键词匹配，非语义理解）
        ↓
   获取全文 ←── Litminer 仅提供 OA 链接提示
        ↓
   阅读与笔记 ←── Litminer 不覆盖
        ↓
   综合分析（主题聚类、趋势、空白识别）←── Litminer 不覆盖
        ↓
   撰写综述 / 做出决策 ←── Litminer 不覆盖
```

Litminer 覆盖了价值链的前 2.5 步（检索 + 验证 + 粗筛），但研究者的核心痛点在后半程。这不是说前半程不重要——恰恰相反，如果前半程不可靠，后半程全部建立在沙子上。但仅做前半程意味着 Litminer 的价值必须通过 Agent 的后续处理来体现，它自己无法独立交付完整价值。

### 2.3 Agent 作为用户的特殊性

对于 Agent 调用方而言，Litminer 的价值等式是：

```
Agent 独立完成文献检索的能力 = Litminer 技能 + Agent 自身的推理能力
```

当前的问题是，Litminer 交给 Agent 的数据太"原始"了。`agent_summary.json` 提供了信任层级计数和下一步动作建议，但这些建议是运维性质的（"重试被限速的 provider"、"添加同义词查询"），不是研究性质的（"这 30 篇论文主要分为三个方向"、"2023 年后该领域转向了 X 方法"）。

Agent 拿到 Litminer 的输出后，还需要自己：
1. 扫描数百行 CSV 中的标题和摘要
2. 识别主题聚类
3. 判断哪些论文真正重要（不仅仅是关键词匹配）
4. 合成发现
5. 向用户报告

这些工作 Litminer 完全不参与，全靠 Agent 自己的上下文窗口和推理能力。

---

## 三、能力边界与诚实度评估

### 3.1 诚实度：项目最大的优点

Litminer 在"不做什么"这件事上，做得比绝大多数同类工具都好：

- **每个信任层级都有明确声明**："发现行是候选，不是验证过的文章事实"
- **可行性报告直接禁止幻觉**："不要捏造行"
- **空值显式标记**：`Unknown`、`Not verified`、空队列字段、失败状态说明
- **出版商队列每一行的 notes 字段都声明**："这是任务队列，不是提取的证据"
- **provenance 注释声明**："溯源说明字段来源，不证明文章级科学主张"

这种系统性的认识论诚实是一个深思熟虑的设计决策，而非偶然。它使 Litminer 成为一个可信任的中间层——Agent 可以依赖它不会静默地将猜测伪装成事实。

### 3.2 能力边界的真实画像

| 能力 | 状态 | 说明 |
|------|------|------|
| 多源 API 检索 | **强** | 4 个发现源 + 2 个验证源，统一追踪 |
| DOI 验证 | **强** | Crossref 双路径（DOI + 标题恢复），失配检测 |
| OA 状态标注 | **强** | Unpaywall 完整字段，gold/hybrid/bronze/green 分类 |
| 去重 | **中等** | DOI 去重准确，标题去重保守（避免误合并），预印本→正式版关联缺失 |
| 语义筛选 | **中等偏弱** | 纯关键词/正则匹配，无语义理解，质量完全依赖调用方的概念规格质量 |
| 引用网络 | **弱** | Semantic Scholar 引用/引文扩展已实现但未集成到主流程 |
| 内容提取 | **不存在** | 不读 PDF、不解析 HTML、不提取表格 |
| 综合分析 | **不存在** | 无主题聚类、无趋势分析、无研究空白识别 |
| 作者/机构分析 | **不存在** | 不提取 ORCID、隶属机构、资助信息（API 有但未请求） |
| 撤稿检测 | **不存在** | 未接入 Retraction Watch 或类似服务 |

### 3.3 关键缺失：文献检索到知识的鸿沟

Litminer 给出一个排好序的论文列表，但研究者需要的是：

1. **"这个领域的主要方向是什么？"** — 需要主题聚类，Litminer 不提供
2. **"哪些是必读论文？"** — 需要引用计数 + 影响力分析，Litminer 采集了 `cited_by_count` 但未用于排序
3. **"近年有什么新趋势？"** — 需要年份分布分析，Litminer 不输出
4. **"谁是这个领域的关键研究者？"** — 需要作者分析，Litminer 不提取隶属机构和 ORCID
5. **"哪些结论有争议？"** — 需要全文阅读和交叉比对，超出范围
6. **"还有什么没被覆盖？"** — 需要检索完整度评估，source_strategy 做了初步尝试但太浅

---

## 四、数据源能力深度分析

### 4.1 各源实际表现

| 维度 | OpenAlex | Semantic Scholar | arXiv | Europe PMC |
|------|----------|-----------------|-------|------------|
| 覆盖面 | ~2.5 亿篇，最广 | ~2.2 亿篇，CS/生医强 | 预印本为主 | 生医为主 |
| 摘要覆盖 | ~65% | ~70% | ~100% | ~75% |
| DOI 覆盖 | 高 | 高 | 部分（预印本无） | 高 |
| 独特价值 | 广度 + OA 信息 | 引用网络 | PDF 直链 | PMID/PMCID |
| 速率限制 | 宽松 | 严格（无 key 1req/s） | 慢（3s/请求） | 宽松 |
| 默认启用 | 是 | 否（opt-in） | 否（opt-in） | 否（opt-in） |

### 4.2 字段提取的系统性缺口

以下字段在多个 API 中可用，但 Litminer **未提取**：

| 字段 | OpenAlex | S2 | Crossref | Europe PMC | 价值 |
|------|---------|-----|---------|------------|------|
| 作者隶属机构 | `authorships[].institutions` | `authors[].affiliations` | 有 | 有 | 机构分析、合作网络 |
| ORCID | `authorships[].author.orcid` | — | 有 | 有 | 作者消歧 |
| 资助信息 | `grants` | — | `funder` | 有 | 资助来源分析 |
| 参考文献列表 | — | `references` | `reference` | — | 引文网络 |
| 主题词/MeSH | `concepts` | — | — | MeSH terms | 主题分类 |
| 撤稿状态 | — | — | — | — | 质量控制 |

**这不是代码 bug，而是产品决策**。当前的字段集足够支撑"找到论文并验证元数据"的核心流程，但不足以支撑"理解论文集合"的进阶需求。

### 4.3 数据源生态的结构性问题

1. **Semantic Scholar 引用扩展未集成**。`get_citations()` 和 `get_references()` 已实现但不在 `api_discovery.py` 的统一编排中。雪球采样（从种子论文向外扩展）是文献检索最有力的补充策略之一，但当前只能手动调用。

2. **预印本→正式版关联缺失**。一篇 arXiv 预印本和同一篇论文的 Nature 正式版会作为两条独立记录存在。去重模块不做跨版本关联，因为预印本通常没有 DOI，标题也可能不同。

3. **领域特化源缺失**。对于系统综述级别的检索，还需要：
   - **PubMed/NCBI E-utilities**：MeSH 词检索、精确的作者/机构筛选
   - **临床试验注册库**（ClinicalTrials.gov）：生医系统综述的必要源
   - **CORE/OpenAIRE**：机构仓库中的灰色文献
   - **Retraction Watch**：撤稿检测

4. **source_strategy 的领域检测太浅**。只覆盖 4 个领域（生医、预印本密集型、化学材料、环境），通过硬编码关键词匹配。经济学、社科、法学、教育学等领域完全无覆盖。

---

## 五、产出物价值评估

### 5.1 对 Agent 的实际可用性

| 产出物 | Agent 能做什么 | Agent 不能做什么 |
|--------|---------------|-----------------|
| `agent_summary.json` | 判断管道状态、决定下一步运维动作 | 理解检索结果的科学含义 |
| `triaged_candidates.csv` | 按优先级排序阅读列表 | 判断一篇论文是否真正相关（仅关键词匹配） |
| `processing_report.md` | 了解管道运行概况 | 获得任何文献综述性质的洞见 |
| `query_plan.json` | 审计检索策略、发现检索盲区 | 自动优化检索策略 |
| `publisher_queue.csv` | 生成出版商页面访问任务列表 | 实际访问和提取内容 |
| `field_provenance.json` | 审计每个字段的来源和信任级别 | 判断 Crossref 本身的元数据是否正确 |

**核心缺失**：Agent 拿到这些输出后，缺少一个 **"结果摘要"层**。当前 Agent 必须自己扫描 CSV 中的标题和摘要来理解检索到了什么——这正是 Agent 上下文窗口最不擅长做的事情（大量结构化数据的模式识别）。

### 5.2 对人类研究者的实际可用性

**缺失项清单：**

| 需要 | 现状 | 影响 |
|------|------|------|
| 结果概览（年份分布、期刊分布、主题分布）| 无 | 研究者打开 CSV 后面对一堆行，无法快速掌握全貌 |
| 必读论文推荐 | 无 | `cited_by_count` 已采集但未用于排序或标注 |
| 交互式筛选界面 | 无 | 纯 CSV 输出，需要 Excel/Sheets 手动操作 |
| 一键导出到文献管理器 | 无 | 无 RIS/BibTeX 导出 |
| 检索策略可视化 | 无 | 查询计划以 JSON 提供，人类难以直观理解 |

### 5.3 journal_metrics_seed.csv 是空的

这是一个产品层面的问题，不仅是技术问题。Litminer 宣称支持期刊指标过滤（IF、JCR 分区），但开箱即用时，种子文件只有表头，无数据。用户必须自己提供经过验证的指标 CSV。

这是正确的设计决策（不分发可能过期或有版权问题的指标数据），但产品体验上是一个断崖。用户看到有"期刊指标过滤"功能，尝试使用，发现什么都没过滤——因为没有指标数据。

---

## 六、用户旅程断点分析

### 6.1 Agent 技能用户旅程

```
✅ 发现 SKILL.md → 理解能力边界
✅ 运行 bootstrap / doctor / offline_smoke → 环境验证
✅ 执行 --mode fast 首次运行 → 验证查询和概念
✅ 读取 agent_summary.json → 了解管道状态
⚠️ 读取 CSV → 上下文窗口可能不够，缺少摘要层
❌ 执行引用扩展 → 未集成到主流程，需手动调用
❌ 理解结果含义 → Litminer 不提供
❌ 生成文献综述 → 完全不覆盖
```

**关键断点**：`agent_summary.json` → CSV 之间缺少一个 **"结果消化"层**。

### 6.2 人类研究者旅程

```
✅ 阅读 README → 理解工具定位
✅ 安装（git clone，无 pip 依赖）→ 极低摩擦
⚠️ 首次运行 pipeline.bat → 无概念参数示例，不知道怎么配
⚠️ 查看结果 → 打开 CSV，数百行，无法快速判断质量
❌ 期刊指标过滤 → 种子文件为空，功能无法使用
❌ 结果可视化 → 无任何图表或统计输出
❌ 导出到 Zotero/EndNote → 无 RIS/BibTeX 支持
❌ 与团队共享 → 无 Web 界面，仅本地文件
```

**关键断点**：CSV 产出 → 实际使用之间缺少 **结果理解和导出** 能力。

### 6.3 开发者扩展旅程

```
✅ 理解架构 → CLAUDE.md 清晰映射模块关系
✅ 添加新数据源 → registry.py 提供清晰的注册模式
⚠️ 实现 HTTP 逻辑 → 需要复制粘贴 6 个文件中的重试逻辑
⚠️ 运行测试 → 单文件 58 个测试，但无覆盖率报告
⚠️ MCP 工具同步 → 新增 CLI 参数需手动同步到 server.py
```

**关键断点**：HTTP 重试逻辑的 6 倍重复是新 provider 开发者的最大摩擦。

---

## 七、竞争定位与差异化

### 7.1 竞品格局

| 工具 | 定位 | 核心差异 |
|------|------|----------|
| **Elicit** | AI 研究助手 | 面向人类，全文阅读 + 数据提取 + 综述写作 |
| **Consensus** | 科学声明搜索引擎 | 面向人类，搜索科学共识而非论文列表 |
| **ResearchRabbit** | 文献发现网络 | 面向人类，引用网络可视化 |
| **Connected Papers** | 引用图谱 | 面向人类，单篇论文的引用图可视化 |
| **Semantic Scholar API** | 学术搜索 API | 开发者工具，单一数据源 |
| **OpenAlex API** | 学术元数据 API | 开发者工具，单一数据源 |
| **Litminer** | Agent 原生检索基础设施 | 面向 Agent，多源聚合 + 信任分层 + 失败透明 |

### 7.2 Litminer 的真正差异化

1. **Agent 原生**：不是给人用的搜索界面，而是 Agent 的结构化技能
2. **多源聚合与去重**：不绑定单一 API，跨源合并并保留溯源
3. **信任分层**：发现 → 验证 → 筛选 → 队列的递进式信任模型
4. **失败透明**：每一次 API 调用的成功/失败/限速/超时都有追踪记录
5. **断点续跑**：长时间运行可中断恢复，不丢失已完成的工作
6. **零运行时依赖**：纯标准库，MCP 服务器不需要任何第三方包

### 7.3 差异化的脆弱性

这些差异化中，**第 1 和第 3 点是真正的护城河**，其他都是实现优势（会被追赶）：

- 多源聚合：任何工具都可以调多个 API
- 失败透明：可以被复制
- 断点续跑：可以被复制
- 零依赖：是约束，不是优势（限制了功能扩展速度）

**Agent 原生 + 信任分层** 的组合是 Litminer 最独特的价值主张。应该围绕这两点来构建产品策略。

---

## 八、如果我来做这个产品

### 8.1 核心判断

Litminer 的架构底座是好的，但产品在价值链上停得太早了。如果我来做这个产品，我会做三个根本性的调整：

#### 调整一：从"文献检索管道"变成"Agent 研究能力层"

不仅仅是"找到论文"，而是让 Agent 能够"理解一组论文在说什么"。这意味着在当前管道之后增加一个 **结果分析层**：

```
当前管道：
  查询 → 发现 → 去重 → 验证 → 筛选 → 队列

升级后：
  查询 → 发现 → 去重 → 验证 → 筛选 → 分析 → 报告 → 队列
                                           ↑           ↑
                                        新增的核心价值
```

**分析层** 不需要 Litminer 自己做 LLM 推理。它需要做的是为 Agent 提供 **结构化的分析素材**：

- 年份分布统计（按年计数、趋势方向）
- 期刊分布统计（Top 10 期刊及占比）
- 高引论文标注（`cited_by_count` 排名 Top 10%）
- 主题词频统计（从标题和摘要中提取高频术语）
- 作者频次统计（出现次数最多的作者）
- 方法词频统计（如果概念配置中有方法相关的 optional concept）

这些全部可以用纯统计方法完成，不需要 LLM，不违反"不做科学判断"的原则。它们是 **描述性统计**，不是推理。

#### 调整二：将引用网络提升为一等公民

Semantic Scholar 的引用/引文扩展能力当前是一个孤立的函数，未集成到主流程。这是一个巨大的产品浪费。

引用网络应该成为发现阶段的一个正式通道：

```python
# 当前：仅关键词搜索
discovery_sources = [openalex, semantic_scholar, arxiv, europe_pmc]

# 升级后：关键词搜索 + 引用扩展
discovery_sources = [openalex, semantic_scholar, arxiv, europe_pmc]
expansion_sources = [citation_forward, citation_backward]
```

具体的工作流：
1. 首轮关键词发现找到候选集
2. 从候选集中的高优先级论文（Triage high）出发，做 1 跳引用扩展
3. 扩展得到的论文经过同样的去重 + 验证 + 筛选管道
4. `source_note` 字段记录 "cited by DOI:xxx" 或 "cites DOI:xxx"

这不是"功能添加"，而是 **检索能力的质变**。关键词检索的召回上限由查询词的覆盖度决定；引用扩展能发现那些用了完全不同术语但科学上高度相关的论文。

#### 调整三：从"一次性管道运行"变成"迭代式研究会话"

当前的工作模型是：配置参数 → 跑一次管道 → 读结果。如果结果不满意，调整参数再跑一次。

更好的模型是 **迭代式会话**：

```
第 1 轮：快速检索（--mode fast），产出初步结果
        ↓
Agent 分析初步结果，发现：
  - 发现了 "水分解" 方向，但查询中没包含 "water splitting"
  - 高引论文中有 3 篇是综述，应该排除
  - Semantic Scholar 被限速了
        ↓
第 2 轮：补充检索
  - 添加 "water splitting" 查询
  - 添加 "review" 作为 negative concept
  - 等待 S2 限速冷却后重试
  - 从第 1 轮的高优先级论文出发做引用扩展
        ↓
合并第 1 轮和第 2 轮的结果 → 再次去重 + 验证
```

当前的 `--resume` 机制是为"中断恢复"设计的（相同参数续跑）。迭代式会话需要的是 **"追加式运行"**——在已有结果上叠加新检索，合并新旧结果。

### 8.2 架构重构方向

如果允许大范围重构，我会做以下改动：

#### R1: 引入 `RunConfig` dataclass 替代 `argparse.Namespace`

这是前一份技术报告中已经指出的问题，但从产品角度看更加关键：`Namespace` 耦合意味着 CLI 和 MCP 的参数表面必须手动同步，每次新增功能都有同步遗漏的风险。

```python
@dataclass
class RunConfig:
    queries: list[str]
    year_from: int | None = None
    year_to: int | None = None
    mode: str = "fast"
    required_concepts: list[ConceptSpec] = field(default_factory=list)
    optional_concepts: list[ConceptSpec] = field(default_factory=list)
    negative_concepts: list[ConceptSpec] = field(default_factory=list)
    # ... 所有参数
    
    @classmethod
    def from_cli(cls, args: argparse.Namespace) -> RunConfig: ...
    
    @classmethod
    def from_mcp(cls, params: dict) -> RunConfig: ...
    
    @classmethod
    def from_dict(cls, d: dict) -> RunConfig: ...
```

#### R2: 提取共享 HTTP 客户端

6 个文件中重复的重试/退避/429 处理逻辑统一为：

```python
# litminer/sources/api/http_client.py

@dataclass
class RetryConfig:
    max_retries: int = 3
    max_wait_seconds: float = 120.0
    backoff_base: float = 2.0
    polite_pause_interval: int = 0  # 0 表示不做额外暂停

def fetch_json(url: str, *, headers: dict = None, 
               retry: RetryConfig = RetryConfig()) -> dict: ...

def fetch_xml(url: str, *, retry: RetryConfig = RetryConfig()) -> str: ...
```

#### R3: 结果分析模块

```python
# litminer/engine/result_analysis.py

def analyze_results(csv_path: Path) -> ResultAnalysis:
    """纯统计分析，不做科学判断"""
    return ResultAnalysis(
        total_count=...,
        year_distribution={2020: 15, 2021: 23, ...},
        top_journals=[("Nature Energy", 12), ...],
        top_authors=[("Zhang Wei", 8), ...],
        high_cited_papers=[...],  # cited_by_count top 10%
        keyword_frequency={...},  # 从标题提取的高频术语
        article_type_distribution={"article": 85, "review": 12, ...},
        oa_distribution={"gold": 30, "green": 15, "closed": 55},
        abstract_coverage=0.72,  # 有摘要的比例
        doi_coverage=0.95,       # 有 DOI 的比例
    )
```

#### R4: 引用扩展集成

```python
# litminer/engine/citation_expansion.py

def expand_citations(
    seed_csv: Path,
    output_csv: Path,
    *,
    direction: Literal["forward", "backward", "both"] = "both",
    max_seeds: int = 10,        # 从前 N 篇高优先级论文出发
    max_per_seed: int = 50,     # 每篇种子论文最多扩展 N 篇
    min_triage_priority: str = "high",  # 只从 high 优先级出发
) -> ExpansionResult: ...
```

#### R5: 导出模块

```python
# litminer/engine/export.py

def export_ris(csv_path: Path, output_path: Path) -> int: ...
def export_bibtex(csv_path: Path, output_path: Path) -> int: ...
def export_csv_summary(csv_path: Path, output_path: Path, max_rows: int = 50) -> int: ...
```

### 8.3 产品功能优先级矩阵

| 功能 | 用户价值 | 实现复杂度 | 护城河贡献 | 优先级 |
|------|----------|-----------|-----------|--------|
| 结果统计分析层 | 极高 | 低（纯统计） | 中 | **P0** |
| 引用扩展集成 | 高 | 中（代码已存在） | 高 | **P0** |
| 补全字段提取（机构、ORCID、资助） | 高 | 低（改 API select） | 低 | **P1** |
| RIS/BibTeX 导出 | 中 | 低 | 低 | **P1** |
| 迭代式追加运行 | 高 | 中 | 高 | **P1** |
| PubMed E-utilities 数据源 | 高（生医） | 中 | 低 | **P2** |
| 撤稿状态检查 | 中 | 低 | 中 | **P2** |
| 领域检测扩展 | 中 | 低 | 低 | **P2** |
| Web 结果查看界面 | 中 | 高 | 低 | **P3** |
| PDF 全文提取集成 | 极高 | 极高 | 高 | **P3（远期）** |

---

## 九、具体演进方案

### Phase 0: 立即修复（1-2 天）

这些是不需要架构变更的产品级修复：

1. **`cited_by_count` 用于排序**。在 `semantic_triage.py` 的评分公式中加入引用计数信号：
   ```
   citation_bonus = min(log2(cited_by_count + 1) * 0.3, 2.0)
   ```
   高引论文获得适度加分，但不压过概念匹配。

2. **`processing_report.md` 增加基础统计**。在报告末尾添加：年份分布直方图（ASCII）、Top 10 期刊列表、Top 10 高引论文。这些信息已经在 CSV 中，只需读取和格式化。

3. **`agent_summary.json` 增加结果摘要字段**。添加 `result_profile` 对象：
   ```json
   "result_profile": {
     "year_distribution": {"2020": 15, "2021": 23},
     "top_journals": [["Nature Energy", 12]],
     "top_cited": [{"title": "...", "doi": "...", "cited_by_count": 342}],
     "abstract_coverage": 0.72,
     "oa_rate": 0.45
   }
   ```

4. **补全 OpenAlex 字段提取**。在 `OPENALEX_SELECT_FIELDS` 中添加 `authorships`（机构、ORCID）和 `grants`（资助信息），对应新增 CSV 列 `affiliations`、`orcids`、`funding`。

### Phase 1: 核心产品升级（1-2 周）

#### 1.1 结果分析模块

新增 `litminer/engine/result_analysis.py`：

- 输入：`triaged_candidates.csv`（或任何管道阶段的 CSV）
- 输出：`result_analysis.json` + `result_analysis.md`
- 内容：年份分布、期刊分布、作者频次 Top 20、高引排行、OA 比例、关键词频次（从标题中用 TF 提取）、文章类型分布、摘要覆盖率
- 全部是描述性统计，不需要 LLM，不违反"不做科学判断"原则
- 在 `run_lit_search.py` 中作为 triage 之后的可选阶段
- 在 `agent_summary.json` 中添加摘要版本

这一步的价值：Agent 读完 `result_analysis.json` 后可以直接告诉用户"检索到 187 篇论文，主要发表在 Nature Energy (23 篇) 和 ACS Catalysis (18 篇)，2023 年后出版量增长 40%，Top 引用论文是 XXX"——而不需要自己扫描 CSV。

#### 1.2 引用扩展集成

将 `semantic_scholar_search.py` 中已有的 `get_citations()` / `get_references()` 集成到 `api_discovery.py` 或作为 `run_lit_search.py` 的独立阶段：

- 在 triage 之后、Crossref 验证之前，从 Top N（可配置）高优先级论文出发
- 每篇种子论文扩展最多 M 篇（可配置）
- 扩展结果进入正常的去重 → 验证 → 筛选管道
- 在 `agent_summary.json` 中报告扩展来源和新增发现数量
- 通过 `--citation-expand` / `--reference-expand` 或 `--expand-from-top N` CLI 参数控制
- MCP 工具 `litminer_citation_expand` 暴露此能力

#### 1.3 RIS/BibTeX 导出

新增 `litminer/engine/export.py`：

- 从 CSV 生成 RIS 和 BibTeX 格式
- 在 `run_lit_search.py` 的 finalize 阶段自动生成（可选）
- MCP 工具 `litminer_export` 暴露此能力
- 支持筛选条件（仅导出 high priority、仅导出 metric pass 等）

对于人类研究者，这是 **从"有用但不方便"到"实际可用"的跨越**。没有导出到 Zotero/EndNote 的能力，CSV 输出对于研究者的实际工作流帮助有限。

### Phase 2: 能力扩展（2-4 周）

#### 2.1 迭代式追加运行

当前 `--resume` 是"相同参数续跑"。需要新增 `--append-to <existing_output_dir>`：

- 在已有的输出目录上追加新查询的结果
- 新发现的候选与已有候选合并去重
- 已完成的 Crossref/Unpaywall 验证结果保留，仅验证新增行
- manifest 记录多轮运行的历史
- `processing_report.md` 标注每轮新增的候选数量

这使得 Agent 可以做 **渐进式文献检索**：先快速扫描，分析结果，调整策略，补充检索——而不是每次都从零开始。

#### 2.2 补全数据提取

- **OpenAlex**：添加 `authorships[].institutions`、`authorships[].author.orcid`、`grants`
- **Semantic Scholar**：添加 `authors[].affiliations`
- **Crossref**：添加 `funder` 字段
- **Europe PMC**：添加 MeSH terms（如果可用）

这些字段的代码改动量很小（修改 API select/fields 参数和行映射函数），但显著提升了输出的分析潜力。

#### 2.3 撤稿状态检查

接入 Retraction Watch API 或 CrossRef 的 `update-to` 字段，在 Crossref 验证阶段同时检查论文是否被撤稿。在 CSV 中添加 `retraction_status` 列，在 triage 中对已撤稿论文自动降级。

### Phase 3: 产品化（1-2 月，可选方向）

以下是更大的产品方向选择，取决于项目的战略定位：

#### 方向 A：深耕 Agent 技能生态

- 为 Claude Code、Codex、OpenAI Agents SDK 提供开箱即用的集成
- 发布到各 Agent 技能市场 / 注册表
- 添加更多 Agent 友好的输出格式（结构化的主题分类建议、检索完整度评估、推荐的下一步研究行动）
- 支持 Agent 之间的协作：一个 Agent 做检索，另一个 Agent 做全文分析，Litminer 作为中间数据层

#### 方向 B：面向人类研究者的轻量级前端

- 基于 `result_analysis.json` 构建一个简单的 HTML 报告页面（可以是静态生成的，不需要服务器）
- 交互式过滤和排序
- 引用网络可视化（简单的 D3.js 图）
- 一键导出到 Zotero

#### 方向 C：系统综述辅助工具

- 接入 PRISMA 流程（系统综述的标准框架）
- 自动生成 PRISMA 流程图（识别了多少、筛选了多少、排除了多少、最终纳入了多少）
- 添加 Cohen's Kappa 双人筛选一致性支持
- 接入 PubMed 和临床试验注册库

这三个方向不互斥，但资源有限时需要选择。**我的建议是方向 A 优先**，因为这是 Litminer 最独特的定位，且边际投入产出比最高。方向 B 和 C 意味着进入已有竞品的赛道。

---

## 十、优先级与节奏建议

### 第一阶段：「让产出物说话」（1-2 天）

> 目标：让 Agent 和人类拿到的结果从"原始数据"变成"有洞察的数据"

| 任务 | 工作量 | 产品影响 |
|------|--------|----------|
| `cited_by_count` 参与 triage 评分 | 30 分钟 | Agent 和人类都能看到高引论文优先 |
| `processing_report.md` 增加基础统计（年份/期刊/高引 Top 10） | 2 小时 | 人类打开报告就能理解结果概貌 |
| `agent_summary.json` 增加 `result_profile` | 1 小时 | Agent 不需要扫描 CSV 就能汇报要点 |
| 补全 OpenAlex 字段提取（机构、ORCID、资助） | 1 小时 | 为未来分析打基础 |

### 第二阶段：「连通价值链」（1-2 周）

> 目标：填补"检索到文献"与"理解文献集合"之间的鸿沟

| 任务 | 工作量 | 产品影响 |
|------|--------|----------|
| `result_analysis.py` 模块 | 3 天 | 核心产品升级，Agent 获得结构化分析素材 |
| 引用扩展集成到主管道 | 2 天 | 检索质量质变，发现关键词搜不到的论文 |
| RIS/BibTeX 导出 | 1 天 | 人类研究者从"能看"到"能用" |
| HTTP 客户端统一（顺手做） | 1 天 | 新 provider 开发效率翻倍 |

### 第三阶段：「扩展能力面」（2-4 周）

> 目标：覆盖更多检索场景，支持迭代式研究

| 任务 | 工作量 | 产品影响 |
|------|--------|----------|
| 迭代式追加运行 (`--append-to`) | 3 天 | 从一次性管道变成研究会话 |
| 补全字段提取（S2/Crossref/Europe PMC） | 2 天 | 机构和资助分析成为可能 |
| 撤稿状态检查 | 1 天 | 学术质量保障 |
| PubMed E-utilities 数据源 | 3 天 | 生医系统综述的必要补充 |
| 扩展 source_strategy 领域覆盖 | 1 天 | 超越当前 4 个领域的限制 |

### 第四阶段：「选择产品方向」（1-2 月）

在前三阶段完成后，基于实际使用反馈决定：
- 继续深耕 Agent 技能生态（方向 A）
- 还是向人类用户倾斜（方向 B/C）

---

## 附：一句话总结

**Litminer 的底座是对的——Agent 原生、信任分层、失败透明。但它在价值链上停得太早了。从"找到论文"到"理解论文集合"这一步，是当前最大的产品缺口，也是投入产出比最高的升级方向。**
