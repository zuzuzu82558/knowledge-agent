# 独立完整验收报告

验收时间：2026-10-09 至 2026-10-10，Asia/Shanghai。项目：`E:\codexproject\knowledge-agent`。

## 1. 验收结论与证据边界

**结论：当前不能按规划判定 M1–M5 整体验收通过。** 本地离线主流程可以运行，原有测试通过，M1 的 R1–R5 修复在本轮回归中通过；但独立验证复现了正文遗漏、表格语义损坏、重建失败导致现有文档不可查询、缓存与能力状态不一致等问题。真实 embedding、生成、图抽取、全文摘要质量及托管服务尚未验收。

本次先阅读了 README、PLAN_REVIEW、PROGRESS、DELIVERY、M1_REVIEW、全部正式测试、CI 配置和相关实现，再执行测试及浏览器操作。进度文档只用于确定待验收范围，没有作为功能通过证据。README 对离线与真实服务的区分基本诚实；PROGRESS 中“没有已复现而留置的本地回归缺陷”已不适用于本轮结果。

只新增审查脚本、隔离数据、截图和本报告。没有修改业务代码、原有测试、配置或 `.env`，没有读取真实密钥，没有调用付费模型或托管业务资源。原有 `data/` 和 8000 端口服务不在测试目标内；独立服务使用 `127.0.0.1:18765` 和 `.tmp/acceptance-20261009/server-data`。对 app/tests/eval/scripts 文件做了 SHA-256 前后对比，结果一致。

证据目录：[artifacts/acceptance-20261009](../artifacts/acceptance-20261009)。以下所有本地产物均为本轮新执行结果，历史 artifacts 未被覆盖。

| 编号 | 本轮证据 | 能证明什么 |
|---|---|---|
| T1 | [pytest-official.txt](../artifacts/acceptance-20261009/pytest-official.txt) | 正式核心测试 + RAGAS 契约共 **61 passed，55.33s**；云相关测试使用替身 |
| T2 | [test_independent.py](../artifacts/acceptance-20261009/test_independent.py)、[pytest-independent.txt](../artifacts/acceptance-20261009/pytest-independent.txt) | 新增 14 个期望行为用例；初跑 11 failed / 3 passed，其中一个失败是审查 PDF 生成器缺 reportlab，不是产品问题 |
| T3 | [extra-checks.json](../artifacts/acceptance-20261009/extra-checks.json) | PDF fixture 改为无额外依赖的真实 PDF 字节后，单独复跑 **1 passed / 13 deselected，2.49s**；综合为 10 个未满足断言、4 个通过，**不是同一次完整运行的计数** |
| B1 | [browser.cjs](../artifacts/acceptance-20261009/browser.cjs)、[browser.json](../artifacts/acceptance-20261009/browser.json) | 真实 Edge 154.0.4258.62 无头浏览器，21 个操作检查；真实后端、真实文件上传，未伪造回答 |
| B2 | [browser-followup.cjs](../artifacts/acceptance-20261009/browser-followup.cjs)、[browser-followup.json](../artifacts/acceptance-20261009/browser-followup.json) | 重启后持久性、删除状态、失败状态、HTML 遗漏的 UI 复现和基础标签/键盘检查 |
| E1 | [evaluation.json](../artifacts/acceptance-20261009/evaluation.json) | 30 题 × 4 路共 120 次：90 offline_extracts、30 dense_unavailable；图谱增益和质量结论为 null |
| E2 | [cache-calibrate.json](../artifacts/acceptance-20261009/cache-calibrate.json) | 12 对词项保护样例 guard_false_allows=0；actual_embedding=false，真实误命中率和净收益未测 |
| L1 | [extra-checks.json](../artifacts/acceptance-20261009/extra-checks.json) | RFC9110 502941 字节、2121 个解析块、2168 个检索块、最大900字符；三次真实离线检索和逐字来源核对通过；默认2000块限制明确失败 |
| S1 | [source-before.json](../artifacts/acceptance-20261009/source-before.json)及 L1 | 应用、正式测试、评测集和脚本未被本次修改 |
| I1 | [install-readme.txt](../artifacts/acceptance-20261009/install-readme.txt)、[fresh-doctor.txt](../artifacts/acceptance-20261009/fresh-doctor.txt)、[fresh-smoke.json](../artifacts/acceptance-20261009/fresh-smoke.json) | 新建虚拟环境核心依赖安装成功；doctor、pip check及新环境真实浏览器上传/解析/检索/引用/删除通过 |

T2 中故障注入只用于证明控制流和状态缺陷：测试直接替换模型返回、索引写入或图存储函数，并拦截外网。这不是 Neo4j/模型账户成功或失败的实测。评测弱锚点用例属于对抗诊断，不能据此声称现有30题已产生错误模型答案。

## 2. M1–M5 验收矩阵

“已实现”表示列出的本地行为有运行证据；“部分实现”表示存在已复现缺陷或只有部分退出条件满足；“未实现”表示代码中缺少对应能力；“未能验证”表示需要尚不具备的配置、授权或环境。阶段状态不等同于文件是否存在。

| 阶段/规划项 | 状态 | 本轮判断与证据 |
|---|---|---|
| M1：单实例启动、配置、SQLite/文件、静态UI | 已实现 | 独立 Uvicorn + Edge 实际运行；显式offline；B1/B2，T1 |
| M1：上传、任务、幂等、版本/格式身份 | 已实现 | UI 重复上传复用任务；更新新版本；格式身份、旧库迁移正式回归通过 |
| M1：PDF/MD/TXT/CSV、章节、表头、来源 | 已实现（已测样例） | T1、独立 CSV 多行负数/MD代码围栏、真实 PDF 线框表格与低文本页；不含复杂PDF排版或OCR承诺 |
| M1：失败重试、重启恢复 | 部分实现 | 损坏PDF可重试且失败原因落库；模拟中断任务重启标 interrupted 后可重试；同配置重建破坏已有可用性，见 F2 |
| M1：中文 sparse、过滤、RRF、上下文 | 已实现（本地机制） | T1 的成熟BM25对照和Qdrant本地范围测试；B1、L1、M1 R1回归 |
| M1：真实 embedding、生成、中文拒答 | 未能验证 | 仅模型接口契约和合成向量通过；离线无模型答案，不满足真实问答退出条件 |
| M1：Qdrant Cloud 写查删 | 未能验证 | 未连接账户；本地Qdrant成功不能外推云端 |
| M1：旧引用、历史范围、删除隔离 | 已实现（后端） | 更新后旧引用200；删除后旧引用410、旧run404；重启保持；清理失败屏蔽由T1覆盖。UI旧证据残留见U3 |
| M1：CI | 部分实现 | 有Windows/Linux配置；本地61项通过；目录不是Git仓库，无远程CI通过证据 |
| M2：schema、quote、别名、财务口径校验 | 已实现（契约） | T1检验字段、来源范围、无效引用；不证明抽取语义正确 |
| M2：本地来源图、受限两跳、一次融合、范围复核 | 已实现（机制） | T1图污染降级/范围/路径限制，合成事实；没有真实GraphRAG增益结论 |
| M2：Neo4j真实存储、真实图抽取 | 未能验证 | 只有managed transaction替身契约；B1离线构图返回409 |
| M2：三类路由、一轮补检索 | 已实现（机制） | B1普通/关系/全文实际离线入口；T1模型替身补检索；离线关系明确降级 |
| M2：全章节访问、章节摘要、全局观点/风险/数据 | 部分实现 | 离线11/11章、23/23块；真实聚合仅替身契约；失败后持久状态错误见F6；解析遗漏使“覆盖”不能代表源文件完整性见F1 |
| M2：证据卡、四路对照、失败归因 | 部分实现 | 4张卡与30题来源已逐项静态核对，答案大体受自编原文支持；该核对由AI完成，不算独立人工验收。四路只有离线结果，真实dense/图谱和人工语义核对缺失 |
| M3：精确/语义缓存、TTL、容量、困难条件保护 | 部分实现 | 精确缓存真实可用；合成向量语义匹配、TTL/容量/Redis故障契约通过；真实语义阈值、覆盖和净收益未验证 |
| M3：版本/索引/语料/图能力缓存失效 | 部分实现 | 正常更新失效通过；图重建失败仍命中旧图答案，见F3 |
| M3：有限重试、冷却、截止时间、取消收尾 | 已实现（故障逻辑） | T1重试/认证失败/预算/冷却恢复/超时取消测试通过；不是实际供应商恢复验收 |
| M3：备用模型与降级答案隔离 | 部分实现 | 替身回退成功；备用模型答案进入普通缓存，见F5；真实备用模型兼容性未验证 |
| M3：Upstash真实操作、真实净收益 | 未能验证 | 未连接账户；12对离线词项保护不等于语义缓存验收 |
| M4：本地事件、run、计时、用量记录 | 已实现（本地） | B1/B2与events日志；run重启可读；成本unknown/无云调用明确，不伪造价格 |
| M4：Langfuse完整trace可见性 | 未能验证 | 只有SDK接入和本地降级机制；未验证账户认证、导出或UI可见性 |
| M4：30题人工核对及开发/保留集 | 部分实现 | 题目、标签和划分存在且离线重跑；human_review仍pending；短锚点误判见E3 |
| M4：RAGAS三项、真实judge与rerank效果 | 部分实现 | 实际三项指标类的合成judge契约通过；真实分数、排序增益、模型/参数效果对照未验证 |
| M4：固定快照、失败/null、问答/摘要分开 | 已实现（离线实验） | E1快照和缺评分清楚，摘要独立；不能把离线四路视为四种真实检索对比 |
| M5：DOCX/PPTX/XLSX/XLS/HTML正常与坏文件 | 部分实现 | T1正常/长文本/宏/公式缓存/坏文件通过；HTML正文和共享表格处理有遗漏/失真，见F1/F4 |
| M5：README启动、公开长文档、边界说明 | 部分实现 | 已有环境实际启动及浏览器主流程通过；公开长文档L1通过；全新安装结果见第7节，Linux/远程CI未验证 |
| 规划：受控四则运算 | 未实现（已披露取舍） | models.py明确提示计算问题拒答；DELIVERY已披露。若仍作为原规划硬要求，应补实现或明确移出验收范围，不混称已完成 |
| 用户体验：历史查看、原文件定位、易懂状态 | 部分实现 | 后端来源与run可读；UI多处仅JSON/内部状态，刷新没有历史入口，见U1–U6 |

阶段总评：**M1、M2、M3、M4、M5均为部分验收；没有一个阶段能以现有证据宣称所有原规划退出条件已完成。** 这不意味着全部需要重做，而是需补齐具体缺陷及真实服务证据。

## 3. M1_REVIEW.md 修复复核

| 原问题 | 本轮结果 | 实际复核范围 |
|---|---|---|
| R1 长中文证据因默认预算全部丢弃 | 通过 | 正式回归长中文保留证据；500/1000/3000预算、真实发送结构的模拟载荷、摘录范围、无召回与预算不足区分 |
| R2 长CSV/表格越切越大 | 通过 | 正式回归CSV/MD/PDF表格及单元格重组、长度≤900；本轮表格F4是另一问题，不能据此判R2回退 |
| R3 删除清理失败后历史正文仍可读 | 通过 | 单文档/混合run、清理失败及重启正式回归通过；B1/B2成功删除后404/410。U3是已加载的前端内容未清空，不是后端接口泄露重现 |
| R4 同字节TXT/MD错误复用代次 | 通过 | 同格式幂等、跨格式不同代次、切回、迁移保留ID/备份/事务回滚、旧配置重建正式测试通过 |
| R5 跳级标题错误嵌套 | 通过 | 正式层级栈测试通过；独立围栏内伪标题不污染后续 `[总览,B]` 路径 |

补充：生成与embedding已分设配置；摄入/问答依然串行，是已披露取舍；query有queue_ms。F2针对**当前相同配置**重建，不是已经测试通过的“旧配置迁移到新配置”场景。

## 4. 功能缺陷（按严重程度）

严重程度：P1阻碍主要能力/破坏数据可用性，P2需修复的特定路径错误，P3可优化。未发现足够证据认定P0。

### F1 / P1：常见HTML正文被静默漏掉，全文覆盖仍可显示完整

- 触发：HTML同时含h/p语义标签和div等容器中的正文。
- 复现：上传 `<main><h1>审计</h1><div>唯一指标ZX875为875万元</div><p>意见无保留</p></main>`，等待completed，选中文档询问 `ZX875`，再选择全文洞察。
- 预期：保留div正文且能检索；无法抽取时明确提示覆盖缺口。
- 实际：上传成功，查询refused、evidence为空；全文显示1/1章、2/2块，却只有“审计”“意见无保留”。
- 原因/位置：[formats.py:97](../app/formats.py#L97)只遍历固定标签，[formats.py:106](../app/formats.py#L106)仅在一个块都没有时回退，混合结构没有补采集遗漏文本。
- 证据：T2 `test_html_mixed_semantic_and_plain_blocks_preserve_all_text`；B2 `html_loss_real_UI`/`html_lost_content_coverage`；[截图09](../artifacts/acceptance-20261009/09-html-lost-content.png)。这是实际浏览器+实际摄入复现。
- 影响：文档内明确存在的事实不可检索；用户容易把解析后块覆盖率理解成源文档覆盖率。
- 建议：按DOM顺序采集正文文本节点，去重保留容器直接文本；覆盖报告区分原始提取质量与已提取块访问率。

### F2 / P1：同配置重建失败使原有可查询文档失效

- 触发：已有ready文档，在相同配置下重建，索引写入失败。
- 复现：T2 `test_failed_same_config_rebuild_preserves_current_availability`：成功摄入并查询→将该实例的index.upsert注入ConnectionError→rebuild→等待failed→再次查询。
- 预期：旧活动代次继续可查询，失败候选可单独重试。
- 实际：同一个generation ID被重用并写成failed；查询HTTP409“所选文档未就绪、已删除或索引配置不匹配”。旧引用仍存在，但文档不能正常问答。
- 位置：[service.py:198](../app/service.py#L198)按version+fingerprint复用generation；[service.py:128](../app/service.py#L128)覆盖状态；[service.py:172](../app/service.py#L172)写失败；查询只选ready。
- 影响：主动维护操作在故障时降低已有数据可用性，与“重建失败保留旧代次”承诺不符。
- 建议：每次重建使用独立候选代次，原子切换活动指针；保留原代次、引用和摘要/图谱身份。增加同配置失败、部分写入、重启后的回归。注入验证证明本地逻辑，未实际中断云索引。

### F3 / P2：图谱重建失败未使旧图答案缓存失效

- 触发：图ready且已有可缓存关系答案；再次构图时Graph.replace抛错。
- 复现：T2 `test_failed_graph_rebuild_invalidates_previous_graph_answer`，使用带逐字quote的合成事实先成功构图/提问，再注入存储失败并重复相同问题。
- 预期：按当前graph_status=failed走混合降级，或明确说明使用保留的旧图快照。
- 实际：build返回failed，但下一次query直接exact命中此前图答案，无图谱降级warning。
- 位置：[service.py:266](../app/service.py#L266)、[service.py:286](../app/service.py#L286)改变能力状态；[graph.py:93](../app/graph.py#L93)只在成功替换后bump；[service.py:342](../app/service.py#L342)缓存命名空间及先查缓存流程没有能力失败代次。
- 影响：同一文档当前能力状态与返回结果矛盾；重试失败被缓存掩盖。并未证明该旧答案语义一定错误。
- 建议：图构建开始/失败也更新能力修订号，或显式保留已发布图版本并在结果中标明；缓存命中前核对依赖的能力版本。

### F4 / P2：共享表格解析器误认首行为表头，HTML合并单元格会截掉数据

- 触发A：DOCX/PPTX/HTML中的无表头键值表，例如两行 `公司 | 验收星河`、`收入 | 875万元`。
- 复现A：T2参数化 `test_headerless_key_value_table_keeps_first_row`。
- 预期：保留行列关系，不把数据值变成字段名。
- 实际A：输出 `{"1:公司":"收入","2:验收星河":"875万元"}`，原来的公司/收入字段关系被改写；三个格式均复现。
- 触发B：HTML首行 `<th colspan="2">年度指标</th>`，次行 `2025 | 收入875万元`。
- 实际B：仅留下 `{"1:年度指标":"2025"}`，金额丢失；T2 `test_html_colspan_does_not_drop_trailing_cells`。
- 位置：[formats.py:27](../app/formats.py#L27)无条件首行表头、zip截断；[formats.py:104](../app/formats.py#L104)未展开colspan/rowspan。
- 影响：下游检索、摘要与图事实的输入损坏，格式正常上传测试不能覆盖。Excel“首行作表头”的已披露取舍不应自动套到所有Office/HTML表格。
- 建议：保留原始行/单元格及跨度；依据实际表头标记或显式策略解释表头。不能可靠恢复时保留原始网格并报告警告，不静默截断。

### F5 / P2：备用模型回答进入普通答案缓存

- 触发：主模型限流，有限重试后备用模型返回有效带引用回答。
- 复现：T2 `test_fallback_answer_not_served_as_normal_primary_cache` 用真实CallBudget/CloudModels控制流，替换LiteLLM返回；主模型两次RateLimitError，backup成功，再重复问题。
- 预期：遵守规划中降级答案不进入正常缓存、或单独降级命名空间的规则。
- 实际：usage正确记录backup，但warnings为空，普通缓存写入；第二次exact命中，policy仍列primary。缓存响应还沿用旧model_calls事件，容易误读为本次调用。
- 位置：[models.py:48](../app/models.py#L48)回退、[service.py:483](../app/service.py#L483)只按status/warnings判断缓存资格；[service.py:349](../app/service.py#L349)命中复制历史结果。
- 影响：降级行为无法从普通成功状态识别，主模型恢复后可能继续使用备用结果直到TTL过期。不是说备用答案必然质量差。
- 建议：显式记录actual_model、fallback_used；根据实际策略隔离或跳过缓存，并分开本次model_calls和source_model_calls。真实备用模型兼容性仍需另验。

### F6 / P2：全文聚合失败，但持久summary_status仍为ready

- 触发：各块章节摘要成功，最终聚合返回无效结构/引用或超预算。
- 复现：T2 `test_failed_aggregation_does_not_publish_ready_summary_status`注入无效aggregate。
- 预期：query、文档能力状态与版本接口一致标partial。
- 实际：query为partial_summary、aggregate为failed_section_union_retained，但generations.summary_status为ready。
- 位置：[insights.py:84](../app/insights.py#L84)只依据章节失败数更新状态，忽略aggregate失败；返回状态另在第89行判断。
- 影响：状态消费者可能把没有最终全文结论的文档判为已完成。
- 建议：分别持久化章节与聚合状态，或统一用完整流水线结果计算summary_status；保留可重试原因。

### E3 / P2：证据评测短锚点会误判“必要证据已全部到齐”（诊断性复现）

- 触发：golden g01仅用“苏州”等短词作证据组，候选文本含同词但主体不同。
- 复现：T2 `test_evidence_metric_requires_correct_subject_context`，输入“竞争对手总部位于苏州，星河总部未披露。”，锚点组为 `[苏州]`。
- 预期：不能当作星河总部位置的完整必要证据。
- 实际：candidate/context recall均1.0，all_necessary_evidence=true。
- 位置：[evaluation.py:8](../app/evaluation.py#L8)仅判断子串，[golden.jsonl:1](../eval/golden.jsonl#L1)短锚点。
- 影响：机制分数可能高估可回答性；这是度量局限的实测，不证明当前30题真实模型回答错误。已有报告保留semantic_support=not_judged是正确的。
- 建议：锚点包含主体、谓词、时间和口径，并绑定来源范围；增加相同数值/城市但不同实体的负例；保留锚点指标名称，避免将其当语义证据召回率。

## 5. 独立的用户体验问题

这些条目基于实际操作或明确标注的静态发现，依据是可完成性、状态一致性和可核对性，不以配色、风格等个人偏好定缺陷。

### U1 / P2：文件已失败，文档列表却一直提示“等待任务完成”

- 触发/步骤：上传内容为普通文本的“损坏.pdf”，等待任务failed，刷新或重启。
- 预期：文档行明确显示失败原因/对应任务/更换文件或重试入口。
- 实际：任务显示failed/parse/parse_failed_PdfminerException，文档行仍为“索引：等待任务完成”；列表还显示无法用于这个未就绪文档的重建/构图按钮。任务只有短UUID，没有文件名，多个上传时难以对应。
- 证据：B2 `failed_doc_vs_job`，[截图07](../artifacts/acceptance-20261009/07-failed-retry.png)；[main.py:96](../app/main.py#L96)按active join，[app.js:13](../app/static/app.js#L13)、第19行。
- 影响/建议：误导用户无效等待；应关联最新任务到文档、显示文件名和中文失败原因，并区分重试与换文件。

### U2 / P2：每5秒刷新重建控件，键盘焦点丢失

- 步骤：选中文档后将焦点放在其复选框，等待超过一次5秒轮询。
- 预期：焦点留在同一控件，Tab/空格连续操作不被中断。
- 实际：activeElement从INPUT(name=doc)变成BODY；勾选状态仍保留。
- 证据：B1 `keyboard_focus_during_polling`；[app.js:9](../app/static/app.js#L9)、第19/30行。
- 影响/建议：键盘和辅助技术用户导航被打断；按ID增量更新DOM，必要时恢复焦点，避免重建正在交互的select与按钮。

### U3 / P2：删除成功后当前页面仍保留被删文档的证据

- 步骤：对文档提问→查看证据→删除该文档并确认。
- 预期：当前结果清空或显著标注来源已删除，链接失效状态同步。
- 实际：文档行消失、notice=deleted，但“星河2025年收入为999亿元”等证据和trace仍展示，链接仍可点；后端旧引用410、旧run404是正确行为。
- 证据：B1 `delete`，[截图08](../artifacts/acceptance-20261009/08-after-delete.png)；[app.js:16](../app/static/app.js#L16)仅refresh列表。
- 影响/建议：界面展示与删除结果矛盾；根据结果快照跟踪依赖文档，删除后清空/标记相关结果。此项不声称能抹除浏览器先前下载的所有副本。

### U4 / P2：引用与版本核对依赖原始JSON

- 步骤：问答后点击“查看此版本的原文记录”，或点击“版本 / 解析质量”。
- 预期：可读地看到文件名、版本、章节/页/行、对应原文；便于定位核对。
- 实际：新页是API JSON；版本也是整段JSON。页面证据卡只显示heading及JSON location，没有可读文件名/版本标签，也没有原文件预览或页内高亮。
- 证据：[截图03](../artifacts/acceptance-20261009/03-evidence-json.png)、[截图06](../artifacts/acceptance-20261009/06-versions.png)；[app.js:14](../app/static/app.js#L14)、第27行。
- 影响/建议：技术用户能核对，第一次使用者需要理解chunk、generation等字段；增加来源详情视图、可读版本标识和位置导航。现有JSON保留在高级诊断中。

### U5 / P2：不可用操作和错误恢复反馈需要理解内部术语

- 步骤：离线点击构图、选择纯向量；或让health请求暂时失败后恢复，再点刷新。
- 预期：不可用能力提前解释；失败给出下一步，恢复成功清除过期错误。
- 实际：构图显示offline_has_no_model_extraction，关系降级显示graph_not_complete_hybrid_fallback，纯向量显示dense_unavailable；注入网络故障后显示Failed to fetch，恢复成功后仍保留同一句错误。
- 证据：B1 graph_build_offline/dense_offline/network_failure_injected/network_recovered；[app.js:3](../app/static/app.js#L3)、第4/16/25行。
- 影响/建议：模式限制与产品失败难区分；提供中文原因和可执行指引，按mode禁用/解释不可用选项，用独立连接状态展示并在恢复时更新。精确错误码可置于详情。

### U6 / P2：刷新后没有恢复本次结果或浏览历史的入口

- 步骤：完成提问→刷新页面；再重启服务后打开页面。
- 预期：有可发现的最近运行入口，或明确告知结果仅在当前页面、如何回看。
- 实际：选中文档和回答重置为空；run_id只是一段文字，无历史列表/链接。后端旧run重启后仍200且快照一致，说明数据并未丢失。
- 证据：B1 reload、B2 server_restart_persistence；[app.js:28](../app/static/app.js#L28)、[main.py:173](../app/main.py#L173)的单run接口。
- 影响/建议：普通用户无法找回已保存结果，重复提问可能增加未来模型费用；增加最近run列表/可分享本机链接。应与“每次问题独立，不使用聊天历史”区别：回看历史不等于多轮聊天。

另一个**静态发现、未真实模型浏览器复现**：app.js第26行渲染insights仅用text/basis，没有渲染每条聚合insight自带的citations；图路径也主要留在trace JSON。建议真实模型验收时重点检查“最终观点逐条点引用”和“逐跳证据可读性”，本次不把它计为真实摘要效果失败。

## 6. 实际用户流程、布局与可访问性结果

实际走通：打开隔离服务→上传自编供应链MD→等待completed→勾选→提问→打开原文记录→重复问题命中缓存→关系模式明确降级→全文覆盖→纯向量不可用→无结果拒答→同文档更新→旧引用核对→重建→坏PDF失败/重试→删除→重启后继续查询和读取旧run。未通过静态HTTP200冒充操作完成。

桌面1440×1000与窄屏390×844均实际渲染、保存截图并查看。窄屏document.scrollWidth=390，无横向溢出，控件可点；长证据列表需要纵向滚动，未将此本身列为缺陷。文件、上传目标、问题、任务、策略控件均有关联label；notice具有role=status，回答区aria-live=polite；问题框按Tab能到任务选择。轮询焦点缺陷见U2。未进行真实屏幕阅读器、触摸设备、全套WCAG或多浏览器验证。

浏览器JS未捕获异常：B1与B2均为0。B1控制台错误为离线构图409和主动注入的health网络失败；未发现额外意外失败请求。故障注入解除后网络已恢复，但界面notice未清理，见U5。真实生成答案里的claim引用点击、真实图抽取/全局洞察、云端错误UI未能验证。

截图：[首次使用](../artifacts/acceptance-20261009/01-empty-desktop.png)、[桌面检索](../artifacts/acceptance-20261009/02-query-desktop.png)、[无结果](../artifacts/acceptance-20261009/04-no-result.png)、[窄屏](../artifacts/acceptance-20261009/05-narrow.png)。

## 7. 安装、执行命令与环境边界

本轮使用Python3.12.10现有环境运行正式测试，使用项目已有Playwright驱动启动系统Edge。没有安装/替换现有业务环境依赖。新建 `.tmp/acceptance-20261009/fresh-env` 后，按README用基础Python的pip安装requirements.txt成功；一次执行中断后在同一隔离新环境续装成功，不能称为一次无中断安装。doctor确认核心依赖存在，pip check无冲突。随后使用这个新环境实际启动Uvicorn，真实浏览器完成上传→解析子进程→检索→引用200→删除成功，详见I1。新环境未安装可选RAGAS，doctor显示ragas=false，核心功能仍可运行，与README可选评测依赖描述一致；RAGAS契约测试是在已有环境运行的。

主要可复现命令（从项目根目录执行，runner会置空服务凭据，设置独立DATA_DIR/TEMP及offline）：

```powershell
.\.venv\Scripts\python.exe artifacts/acceptance-20261009/runner.py tests
.\.venv\Scripts\python.exe artifacts/acceptance-20261009/runner.py independent
.\.venv\Scripts\python.exe artifacts/acceptance-20261009/runner.py evaluate
.\.venv\Scripts\python.exe artifacts/acceptance-20261009/extra_checks.py
.\.venv\Scripts\python.exe artifacts/acceptance-20261009/runner.py install
.\.venv\Scripts\python.exe artifacts/acceptance-20261009/runner.py start
node artifacts/acceptance-20261009/browser.cjs
node artifacts/acceptance-20261009/browser-followup.cjs
node --check app/static/app.js
```

复现脚本是本次执行记录：pytest基目录、浏览器初始数据和18765端口应使用新的隔离资源，勿与现有实例冲突；browser-followup依赖browser.json中的本轮ID。独立测试断言保留正确预期，失败未被改成xfail或跳过。

README的uvicorn factory/单worker/本机地址实际可运行；文件上传子进程可用，未使用reload。跨服务重启后的旧文档、新查询、旧run和已删除状态由B2证实。`node --check`通过。README中的Linux步骤、Windows不同机器安装、远程CI尚不能据本机结果验收。

环境事件与产品问题分开记录：

- 沙箱内最初pytest无输出、浏览器crashpad/管道启动失败；解除本机执行限制后测试和浏览器正常。早先零字节pytest.txt不是通过结果。
- 新增PDF测试最初缺审查用reportlab，改为直接构造有效PDF后通过；不是项目缺依赖。
- 安装脚本最初误用无pip的已有虚拟环境；改用README所用基础Python的pip，不据此判README失败。
- 2026-10-09一次长文档命令被自动审批拦截：原因是审批用量限制，未执行，不是安全拒绝；用户继续后，2026-10-10重提审核并执行通过。
- 没有`.env`文件；本次不读取任何真实密钥值。未配置和未授权的真实模型/云验收集中列于下节，不因这些阻塞其余离线检查。

收尾：本次浏览器均已关闭；18765验收服务器的父子Python进程按保存PID、父子关系及uvicorn端口参数核对后关闭，见[cleanup.json](../artifacts/acceptance-20261009/cleanup.json)。保留隔离数据和复现材料供修复核对，没有删除业务目录。

## 8. 未验证事项及所需条件

| 未验证项 | 所需条件/下次证据 |
|---|---|
| 真实生成与embedding，包括中文长段落、拒答、批量/维度 | 用户本地配置实际模型、endpoint、凭据、维度/revision；明确付费预算；分别运行local模型smoke与人工答案核对 |
| Qdrant Cloud | 隔离collection/账户授权，写查过滤/更新/删除/清理失败证据 |
| Neo4j/Aura真实存储和图抽取质量 | 隔离namespace及授权；真实抽取后人工核对关系方向、quote、期间/单位/口径和逐跳组合 |
| 真实全文章节摘要及聚合 | 足够覆盖长文档的调用预算；检查遗漏、冲突、风险explicit/inference与最终引用，不只看status |
| Upstash及语义缓存净收益 | REST配置、真实embedding困难负例、TTL/容量/重启/删除，并报告误接受、命中率、实际成本与延迟 |
| 备用模型/供应商恢复 | 实际备用模型授权和兼容性，受控故障注入；当前同凭据fallback不等于独立供应商容灾 |
| Langfuse | 配置及账户UI；核对trace可见性、用量/计时，不以flush成功代替 |
| RAGAS、rerank、四路效果 | 独立人工审核黄金集及证据卡，真实judge预算，固定快照/模型/上下文预算，并保留失败/null |
| Linux及远程CI | 可用runner和用户选择的仓库；本次未创建或发布远程仓库 |
| 更广泛真实文档/交互 | 脱敏企业PDF/Office样本、复杂表格、真实浏览器辅助技术；无OCR、SmartArt等已声明不支持项不按缺陷新增范围 |

## 9. 下一步优先级

**必须修复后再验收：** F1正文遗漏、F2同配置重建回退；F3图失败缓存失效、F4表格保真、F5降级缓存、F6摘要状态一致性；U1失败状态、U2焦点及U3删除后当前结果状态。修复后保留61项原回归，加入本次独立复现，不以提高预算或隐藏错误代替修复。

**建议优化并补证据：** E3证据锚点；U4来源详情、U5中文错误/能力说明和恢复状态、U6历史入口；验证真实insight逐条引用。先完成小范围真实模型+本地存储，再按需要验托管服务，最后做效果/缓存/rerank对照，避免同时改变多个变量。

**可以推迟：** 多租户、认证平台、高可用、队列架构、OCR、复杂版式完美重建、跨文档实体融合、Docker Compose和装饰性图谱可视化，均不是本轮已有承诺的修复前提。受控计算需明确范围决策，当前拒算行为应继续如实展示。

是否通过的标准应为：已复现缺陷关闭；离线能力与真实能力明确分开；真实服务和人工效果要求有相应证据，或经明确范围调整排除。不能用测试数量、目录内文件数量或“M1–M5完成”标签替代这些条件。

## 后续修复

用户随后授权修改；2026-10-10 修复与复验记录见 [ACCEPTANCE_FIXES.md](ACCEPTANCE_FIXES.md)。本文保留修复前审查事实，不将当时失败结果改写为通过。
