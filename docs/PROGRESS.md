# 项目进度与接续说明

> 2026-10-11 升级为文档RAG + 联网 + 受控Agent。最新状态以 [RAG_UPGRADE_20261011](RAG_UPGRADE_20261011.md) 为准；下方M1–M5为历史记录。代码、固定联网流程、Agent控制器与离线验证已实施，真实模型/搜索/效果和独立人工标注仍未验收。新chunk/embedding输入契约需显式重建，旧引用保留。

> 2026-10-10 更新：独立验收发现的问题及本轮修复以 [ACCEPTANCE_REVIEW](ACCEPTANCE_REVIEW.md) 和 [ACCEPTANCE_FIXES](ACCEPTANCE_FIXES.md) 为准。旧解析索引需显式重建；历史完成声明不能替代真实云端及质量验收。

更新时间：2026-10-09（Asia/Shanghai）。项目：E:\codexproject\knowledge-agent。

## 当前状态

用户已取消逐里程碑停止确认。本轮按M1复核 → M2 → M3 → M4 → M5连续实施。所有可独立实现和验证的功能已交付；真实云端、人工效果和Linux/远程CI验收仍待外部条件。不要重做已完成阶段，也不要把本地替身测试当云端通过。

最新完整交付说明：[DELIVERY.md](DELIVERY.md)；安装/启动/演示：[../README.md](../README.md)。原M1审查及修复证据保留在 [M1_REVIEW.md](M1_REVIEW.md)，其历史停止点已失效。设计基线 [PLAN_REVIEW.md](PLAN_REVIEW.md) 已同步连续执行授权。

## 阶段完成记录

| 阶段 | 已完成 | 实际验证 | 待验证 | 自动进入的下一步 |
|---|---|---|---|---|
| M1收尾 | 核对R1–R5及正式测试，均已修复，不重复实施；拆分生成/embedding配置，保留共享兜底 | 原39项全部通过，31.12s | 真实模型中文质量、Qdrant Cloud、LinuxCI | M2 |
| M2 | 来源事实schema、实体/别名、财务期间/单位/口径、结构化校验、可选Neo4j、本地来源图、受限两跳、路径范围复核、单次融合、路由/一轮补检索；逐块章节摘要/覆盖、引用继承和全局观点/风险/数据聚合；4张机制卡 | 阶段41 passed，33.78s；后续补齐抽取/聚合/原文引用/图谱污染降级集成测试；Neo4j事务契约测试通过 | 真实抽取/Neo4j/全文效果、人工证据卡及真实dense对照 | M3 |
| M3 | 精确/语义缓存、快照/策略命名空间、TTL/容量、实体/年份/否定/方向保护；一次重试责任层、备用模型、跨请求冷却、模型与全请求预算、取消收尾；queue_ms | 阶段43 passed，34.68s；后续加入语义命中/Redis故障/恢复/HTTP排队截止/模型取消；12对离线保护样例错误放行0 | Redis账户、真实向量阈值/净收益、真实多供应商切换 | M4 |
| M4 | 本地事件/run持久化、Langfuse SDK OTLP；30题参考标签/初始dev-holdout；四路固定语料对照、候选/上下文证据指标、引用指标；RAGAS三项统一预算适配器；可选listwise rerank、上下文预算参数、摘要独立报告 | 阶段46 passed，33.54s；实际RAGAS指标类使用合成judge的契约测试通过；120次离线运行、另有11章/23块覆盖 | 人工审题、真实judge、Langfuse UI、rerank效果对照 | M5 |
| M5 | DOCX/HTML/PPTX/XLSX/XLS适配；正常/长文本/长单元格/坏文件/宏/公式缓存及摄入引用验证；公开长文档；全新环境安装、预检、独立服务器启动、交付文档 | 格式阶段56 passed，33.52s；全新Windows环境安装和pip check通过；独立Uvicorn八项HTTP检查通过；RFC9110摄入/3次检索通过 | Linux/远程CI及真实各格式模型效果 | 完成整体交付 |

## 最新整体验证

- 全新环境：`.tmp/delivery-env`，Python3.12.10/Windows，按requirements-dev/eval和锁定文件安装。
- 全量核心+独立RAGAS契约：最终61 passed，35.10s；已包含提示词缓存版本更新及全部核心/可选评测契约。
- `pip check`：No broken requirements found。
- `compileall -q app tests scripts`、`node --check app/static/app.js`通过。
- 真实独立Uvicorn进程：health、UI、ingest、query、citations、cache、full_document_coverage、delete均通过，测试进程已关闭。
- 2026-10-09 19:40（本机）：处理浏览器8000端口连接拒绝；原因是验收临时进程已退出。已用现有`.venv`在后台启动`127.0.0.1:8000`，APP_MODE=offline、DATA_DIR=项目data目录；实际HTTP验证health、首页、JS、CSS全部200。该服务保持运行，未调用云端。
- 30题四路：120次，90 offline_extracts、30 dense_unavailable；没有真实dense、多轮生成或图谱增益结论。全文章节11/11、原文块23/23访问（离线摘录，不是生成摘要质量）。
- 四张机制卡：本次sparse基线全部必要锚点到齐，无已证明遗漏；真实dense/图谱对照及独立人工核对待验证。
- 缓存12对保护诊断：错误放行0；实际embedding=false，真实误命中/覆盖/净成本与延迟收益均未测。
- RFC9110：502941字节、2168检索chunk、最大900字符、行号有效；使用独立MAX_CHUNKS=3000，默认仍2000。不是中文模型质量验收。
- 真实云端项目：0；本机无.env和服务凭据，没有模型费用。
- 没有Git仓库/远程Actions结果。WSL只枚举到docker-desktop，Docker Linux engine未运行，无本轮可用常规Linux runner；没有自动安装/启动外部基础设施。

测试替身均明确标记，正式测试拦截外部网络。RAGAS评测依赖锁定0.2.15/LangChain0.3/Pydantic2.12.5，修复本轮发现的导入错误和卡住；LangChain不进入应用运行架构。Neo4j managed transaction改用字符串语句与官方unit_of_work(timeout=10)，避免Query对象不兼容。没有通过跳过失败断言或伪造评分制造通过。

## 产物位置

- `artifacts/evaluation-offline.json`：最终源码指纹、稳定文档/版本/块ID、每题快照与检索记录、缺评分状态及summary_runs。
- `artifacts/evidence-cards.json`：四卡及对应run/来源。
- `artifacts/cache-guard-report.json`：12对缓存保护样例。
- `artifacts/startup-smoke.json`：独立服务器检查。
- `artifacts/public-document-parse.json`、`public-document-smoke.json`：公开长文档验证。
- `eval/golden.jsonl`：30题，human_review=pending；`eval/cache-pairs.json`：困难负例。
- `scripts/doctor.py`、`startup_smoke.py`、`public_smoke.py`、`evaluate.py`、`cache_calibrate.py`：可直接执行的预检与本地验证。
- `scripts/cloud_smoke.py`、`services_smoke.py`：显式随机命名空间云契约验收，未实际运行。

## 已知限制与兼容

没有已复现而留置的本地回归缺陷；尚未真实服务/人工验证的路径仍可能暴露缺陷。具体限制见DELIVERY，尤其注意：引用有效不是语义正确；无OCR/复杂Office完美恢复；无认证/多租户/高可用/OS内存配额；串行摄入和问答；语义缓存保守；无受控计算工具（计算要求拒答）；观测span事后导出，实际耗时看metadata。

开发与测试使用独立数据目录；上述本机服务启动后正常初始化项目data目录，没有删除业务数据。新增事实/摘要表为兼容升级，保留旧ID、原文件、版本、代次和引用。旧format-v1约束自动事务迁移并备份；旧解析契约显式重建，当前M1-v2索引继续使用。更换存储endpoint/namespace使用独立DATA_DIR或保留原清理归属。删除先屏蔽访问，失败可重试；备份/WAL/旧Redis TTL桶及远端trace不保证安全擦除。

## 接续任务（只做待验收项）

1. 用户在本机配置已授权的服务，明确真实模型/备用模型/judge与预算，不发送明文密钥到聊天。先local模型smoke，再独立cloud存储smoke，各自保存报告。
2. `services_smoke.py --allow-cloud`只验已配置Neo4j/Redis/Langfuse，不要求模型密钥；合成存储fixture不证明图抽取效果，Langfuse仍需人工检查UI可见性。
3. 独立人工核对30题和证据卡；再真实运行四路及RAGAS、全文摘要和rerank对照，保留失败/缺评分，必要时修复新缺陷。没有增益也如实报告。
4. 真实缓存困难样例校准及净成本/延迟收益；真实备用模型故障注入与恢复。
5. 用户选择远程仓库/可见性和Linux runner后运行CI；不要未经授权发布项目或敏感数据。

以上外部验收不要求重新实现M2–M5，不恢复旧逐阶段停止确认流程。
