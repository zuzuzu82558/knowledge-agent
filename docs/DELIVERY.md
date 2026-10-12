# M1–M5 交付与验收边界

> 2026-10-11：本文保留M1–M5历史边界。新增可调RAG、联网和受控Agent的最新交付与验证请见 [RAG_UPGRADE_20261011](RAG_UPGRADE_20261011.md)。

> 2026-10-10 更新：独立验收发现的问题及本轮修复以 [ACCEPTANCE_REVIEW](ACCEPTANCE_REVIEW.md) 和 [ACCEPTANCE_FIXES](ACCEPTANCE_FIXES.md) 为准。旧解析索引需显式重建；历史完成声明不能替代真实云端及质量验收。

日期：2026-10-09，Asia/Shanghai。用户已取消阶段停止确认。本轮连续完成可独立实施、运行和核验的工作，未发起真实付费调用，未创建密钥、读取密钥值或操作业务数据目录。

## 功能状态

| 阶段 | 已实现 | 未完成的真实验收 |
|---|---|---|
| M1 | R1–R5复核；上传/版本/索引代次/任务；PDF/MD/TXT/CSV；真实模型接口、独立生成/embedding配置；Qdrant dense+sparse、RRF、来源引用、删除与迁移 | 真实中文embedding/生成、Qdrant Cloud、长中文文档答案人工核对 |
| M2 | 小schema来源事实；结构化抽取与校验；本地/Neo4j存储；逐跳来源核验、两跳有界检索、单次融合；普通/关系/全文路由；最多一轮补检索；章节摘要复用与全局观点/风险/数据聚合 | Neo4j账户、真实抽取/摘要；四张证据卡的独立人工核对；真实dense/混合/多轮/图谱对照 |
| M3 | 精确/保守语义缓存、快照命名空间、TTL/容量/条件保护；重复请求串行合并；统一模型与全请求预算、取消收尾、受限重试、备用模型、跨请求冷却；故障降级 | Redis账户、真实语义阈值与净成本/延迟收益；备用模型真实兼容性、独立供应商切换 |
| M4 | 本地事件与run持久化、可选Langfuse OTLP；30题参考证据集及初始dev/holdout；四路隔离实验、证据/引用指标、失败与缺评分记录；RAGAS三项统一模型适配；可选LLM rerank；摘要独立覆盖报告 | 人工审题、真实RAGAS judge及中文一致性、Langfuse UI可见性、实际rerank模型对照 |
| M5 | DOCX/HTML/PPTX/XLSX/XLS适配；正常/长单元格/损坏/宏/公式缓存及下游引用测试；全新环境安装、预检、真实服务进程启动；公开长文档；重建/演示/数据边界说明 | Linux/远程CI，真实企业长文档及各格式模型效果 |

“功能实现”和“真实服务验收”是两种状态。上述待验收项没有写成通过，也不以测试替身或离线摘要替代。

## 本轮实际验证

| 验证 | 结果 |
|---|---|
| M1已有回归复核 | 39 passed / 1 warning，31.12s；R1–R5无需重复修复 |
| M2阶段回归 | 41 passed / 1 warning，33.78s |
| M3阶段回归 | 43 passed / 1 warning，34.68s |
| M4阶段回归 | 46 passed，33.54s |
| M5格式回归 | 56 passed，33.52s |
| 全新Windows环境 | `.tmp/delivery-env`，按requirements-dev/eval及锁定文件安装成功；pip check通过 |
| 最终整体验证 | 61 passed，35.10s；包括全部M1–M5及独立RAGAS适配器契约测试，新增请求截止/取消收尾通过 |
| 真实Uvicorn进程 | health、静态UI、摄入、问答、引用、缓存、全文覆盖、删除均通过，服务已关闭 |
| Python/JavaScript | compileall、node --check通过 |
| RAGAS适配器 | 实际Faithfulness/ResponseRelevancy/LLMContextRecall类 + 合成judge/embedding契约测试通过；不是模型分数 |
| 30题四路离线实验 | 120次：90 offline_extracts、30 dense_unavailable；另有全文章节覆盖报告；GraphRAG gain和质量分数均null |
| 四张机制证据卡 | 本次sparse基线对四卡均召回全部标注必要锚点，无已证明遗漏，不能据此预设图谱获胜 |
| 缓存12对样例 | 词项保护错误放行0；真实向量未调用，false_accepts/coverage为null，净收益未测 |
| 公开长文档 | RFC9110，502941字节，2121块解析内容/2168检索chunk；最大900字符，行号有效；摄入与3次检索通过。显式MAX_CHUNKS=3000，默认仍2000 |

主要产物：

- `artifacts/evaluation-offline.json`：每题输入、参考证据、分支排名、上下文、回答状态、引用、快照、错误/缺评分；含应用源码SHA-256及独立summary_runs。
- `artifacts/evidence-cards.json`：4张机制卡及实际sparse基线，没有虚构dense遗漏或关系路径。
- `artifacts/cache-guard-report.json`：困难负例保护诊断。
- `artifacts/startup-smoke.json`：独立真实进程HTTP检查。
- `artifacts/public-document-parse.json`、`public-document-smoke.json`：公开长文档解析和来源验证。

正式测试禁外部网络。模型契约、dense向量、judge返回在对应测试中为明确测试替身；离线应用没有合成dense向量。Python3.12.10/Windows为实际环境。初期Starlette警告在安装已锁定的httpx2测试依赖后消失。

## 安装、启动和演示

按根目录README执行。核心：`python -m pip --python .venv/Scripts/python.exe install -r requirements.txt`，然后 `.venv/Scripts/python.exe -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000`。不要覆盖已有.env，不开多个worker/reload。

离线演示：上传 `examples/supply-chain.md` → completed →勾选 →普通问答/关系降级/全文覆盖 →重复问题查看缓存 →更新版本 →查看历史来源 →删除。离线没有模型回答或真实图抽取。

可执行预检：`scripts/doctor.py`。离线独立验证：`startup_smoke.py`、`public_smoke.py`、`evaluate.py --summary`、`cache_calibrate.py`。`evaluate.py --context-budget 1000`可做固定语料上下文预算对照；`--rerank`需先配置实际排序模型。每路使用同一次摄入索引；实验文档/版本/chunk身份稳定，可从样例重建，但远端随机隔离collection不同，不承诺重放供应商历史IDF状态或模型随机输出。

## 真实云端已验证与待验证

**已验证真实云端项目：无。** 本机无.env，也没有配置生成、embedding、Qdrant、Neo4j、Redis或Langfuse凭据；未调用真实云端，未发生模型费用。

| 待验收 | 用户配置/操作 | 证据要求 |
|---|---|---|
| 模型 + 本地存储 | `.env`中设APP_MODE=local，独立或共享凭据、实际模型/维度/revision；先明确预算 | cloud_smoke.py --allow-cloud，保存local报告；再人工核对中文长文档与拒答 |
| Qdrant Cloud | APP_MODE=cloud，URL/key、独立DATA_DIR | 同脚本保存cloud报告，写查过滤及清理全部通过 |
| Neo4j/Redis账户 | 按.env.example配置已拥有的服务 | services_smoke.py --allow-cloud；只测试已配置服务，不需模型密钥。此独立脚本的显式开关允许联网，应用offline仍不联网。无配置项标not_configured，fixture只证明存储契约 |
| 真实图抽取/摘要 | 当前文档构建图谱或GRAPH_ENABLED=true | 校验逐条关系语义、财务期间/单位/口径及冲突；观察partial/failed；不能只检查ID存在 |
| Langfuse | public/secret/base URL | SDK认证和flush后人工检查UI trace，异步flush不等于可见性通过 |
| 语义缓存 | 真实embedding及代表性问法 | cache_calibrate.py --allow-cloud，扩大人工标注困难负例，报告误接受、覆盖、净延迟/成本 |
| 效果评测 | 独立人工核对30题，再提供judge调用预算 | evaluate.py --allow-cloud --graph --judge --summary；拒答独立评估，保留失败/null，不人为补零或满分 |
| rerank/备用模型 | 有授权且兼容的模型与预算 | 真实有/无排序对照；两模型对比至少两份真实报告；备用模型故障注入与恢复另测 |
| Linux/CI | 创建用户选择的远程仓库/runner | 当前没有Git仓库或远程Actions记录，不自动发布仓库；矩阵与评测适配器job已提供 |

无需把密钥发送到聊天。真实服务与预算未授权时保持offline即可。脚本默认没有付费调用；模型smoke/评测均需显式--allow-cloud。云端smoke/评测使用随机隔离命名空间，清理失败明确报错，不删除用户业务索引。Langfuse测试trace可能按服务保留策略留存。

## 重要取舍、兼容与已知限制

1. 保留单实例原架构，没有引入队列平台、多Agent或应用LangChain框架。RAGAS所需LangChain只在可选评测依赖中。真实生成、embedding、judge统一经LiteLLM；应用自主管理一次重试责任层、预算和冷却，未额外套用Router的重试。这是为避免重复重试/难以说明总调用数的取舍，不宣称实现完整熔断器。
2. 问答截止时间包含排队；排队超时返回504，执行中超时保存request_timeout并取消模型。已启动同步SDK IO不能强杀，释放快照锁/关闭存储前等待其自身有限超时收尾，因此硬截止可能加上收尾时间。
3. 图谱身份包含生成代次、实体类别和名称，保守处理别名，不跨文档融合。Neo4j保存实体、事实和SUBJECT/OBJECT关系；应用读取最多1000条范围事实再有界遍历，超过即降级，不伪装完整图谱。每一步再核验真实chunk范围和逐字quote。quote/引用有效仍不证明关系语义正确。
4. 抽取不通过schema/quote/财务口径校验的不写入；有效部分可保存重建，但状态partial时默认不参与图检索。模型字段错误不自动强改为“正确”。重建不删除旧版本或引用。更换Qdrant或图谱存储endpoint/namespace必须保留原清理归属或使用独立DATA_DIR。
5. 全文访问每个章节/块，章节结果按模型、配置和内容复用。摘录不能计作完整原块覆盖；最终聚合超预算、无效引用或模型失败时标partial，保留章节结果。风险分explicit/inference，数据保留期间/单位/口径/断言性质，不跨口径求和。当前没有受控计算工具，计算问题要求拒答、数字事实引用原数值。
6. 缓存词序保护保守，很多同义问法不会命中。TTL/容量和快照失效可运行；远端每个快照桶有界，但历史快照桶总量随TTL窗口增长。当前进程桶删除尽力执行；重启前遗留桶按TTL过期，语料revision保证不可命中。降级/警告云答案不进正常缓存。
7. 单实例串行锁保证固定快照，有queue_ms；不适合多用户吞吐。摄入任务落库，中断后手动重试，不做断点续跑。模型用量来自供应商；无价格表成本unknown，缓存本次耗时/调用与原回答成本分开。
8. Langfuse导出实测阶段元数据与用量，不默认发送正文；SDK span为事后导出，阶段耗时看metadata elapsed_ms，不将导出span持续时间冒充实际模型/检索耗时。认证/导出失败保留本地记录。尚无云端端到端trace可见性验收。
9. 默认无认证、租户隔离、高可用或OS级内存配额。仅绑定本机，无任意跨域写。Office解压条目/大小、行/页/表及抽取字符有界，解析子进程超时；无OCR/SmartArt/图片文字/复杂版式完美恢复。DOCX页眉页脚/文本框、PPTX备注和组合形状不支持。XLS日期可能是序列数；Excel首行作表头，缺公式缓存为空。
10. 模型收到待嵌入/抽取/摘要正文、问题与证据；Qdrant保存向量/代次；Neo4j保存事实quote与引用；Redis保存问题、向量、回答证据；Langfuse默认保存ID、步骤、用量和计时。删除先屏蔽读取、再清理与重试；数据库/WAL/备份、缓存TTL残留和远端观测历史不保证取证级擦除。
11. 30题是自编合成领域语料，锚点回归通过不等于人类独立审题。开发/保留集存在共享概念，尚不能作盲测/显著性/p95承诺。四张机制卡当前sparse并未遗漏必要锚点，不预设GraphRAG增益。公开RFC长文档只验证解析、检索和来源，不代表中文真实模型质量。

技术依据：Neo4j事务按[官方Python事务文档](https://neo4j.com/docs/python-manual/current/transactions/)及本机6.4.0实现核对；Redis使用[Upstash REST命令接口](https://upstash.com/docs/redis/features/restapi)；Langfuse使用[当前SDK/OTLP路线](https://langfuse.com/faq/all/deprecated-api-migration)，未采用待下线的旧trace ingestion；RAGAS按锁定0.2.15实际指标类运行契约测试。上述依据不是账户级验收。
