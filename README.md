# Knowledge Agent

可追溯、可评测的文档 RAG，支持联网搜索和在线证据补充。FastAPI + 原生前端，默认离线只显示原文摘录。新增三种资料范围、冻结网页快照、固定联网基线与模型选择工具的受控 Agent。升级说明及验收状态见 [RAG_UPGRADE](docs/RAG_UPGRADE_20261011.md)，架构见 [ARCHITECTURE](docs/ARCHITECTURE.md)，30题来源复核见 [GOLDEN_REVIEW](docs/GOLDEN_REVIEW_20261011.md)。

**本地功能和接口契约已验证；真实模型、托管服务、独立人工效果验收仍待完成。没有真实 GraphRAG 增益或 RAGAS 分数。**

2026-10-11 首次有界真实服务检查已完成：千问向量化与 DeepSeek 文档问答通过，Tavily 搜索及两份正文读取成功；联网生成未通过输出契约，保留失败状态。配置、调用上限与独立联网预览启动方法见 [PROVIDER_SETUP](docs/PROVIDER_SETUP_20261011.md)。这不代表质量评测完成。

后续截图复查修复了联网上下文被第一篇长网页占满、生成失败缺少分类及网页错误提示重复的问题；157 项离线测试及 28 项浏览器检查通过。已有快照对照与真实验证限制见 [ONLINE_FAILURE_REVIEW](docs/ONLINE_FAILURE_REVIEW_20261011.md)。

后续前后端复查及修复见 [DETAILED_REVIEW](docs/DETAILED_REVIEW_20261011.md)，包含失败复现、Agent 跨轮证据、长网页重排、超时证据保留、缓存计数和浏览器交互验证。

## 安装、启动

Python 3.12（本机实测 3.12.10）：

```powershell
cd E:\codexproject\knowledge-agent
python -m venv .venv
python -m pip --python .venv/Scripts/python.exe install -r requirements.txt
if (!(Test-Path .env)) { Copy-Item .env.example .env }
.\.venv\Scripts\python.exe scripts/doctor.py
$env:MAX_UPLOAD_MB='20'
.\.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
```

已有虚拟环境可直接执行最后两条。不要覆盖已有 `.env`。打开 [本机工作台](http://127.0.0.1:8000)。只开一个 worker，不加 `--reload`。Windows 摄入子进程需要 Uvicorn 的 Proactor 事件循环，正常 Ctrl+C 会等待摄入收尾。

Linux：`python3.12 -m venv .venv`，`.venv/bin/python -m pip install -r requirements.txt`，`test -f .env || cp .env.example .env`，`.venv/bin/python -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000`。Linux/CI 配置已提供，尚无真实 runner 通过记录。不依赖 Docker、GPU、Node 构建或本地神经模型。

## 演示

1. 上传 `examples/supply-chain.md`，等任务 completed，勾选文档。
2. 普通问答“星河2025年收入是多少？”查看原文摘录、来源及运行记录。
3. 重复问题查看缓存；勾选“旁路答案缓存”可重新执行。
4. 选择关系分析。未建图谱时明确降级；真实模型配置后点击“构建 / 重试图谱”，查看逐跳证据。
5. 选择全文洞察。离线显示全章节摘录和覆盖；真实模式生成章节摘要及带原文引用的核心观点、风险点、关键数据。
6. 查看版本/解析质量，更新同文档内容，再核对旧引用。删除后旧引用与混合历史 run 不可访问。
7. 配置服务后选择“联网搜索”（无需上传）或“资料 + 联网”，查看搜索/读取进度、网页快照与正文不可用状态。只有点击“保存这份快照到我的资料”才导入文档。
8. 高级设置可选 sparse/dense/hybrid、候选池重排及固定流程/受控 Agent；结果下方调试表展示分支、融合、重排与上下文筛选。

样例是自编虚构材料。公开长文档 `examples/rfc9110.txt` 保留原始许可，生成2168块；上传需显式设 `MAX_CHUNKS=3000`，或执行隔离的 `scripts/public_smoke.py`。不改变默认2000块上限。

## 模式和配置

| APP_MODE | 模型与存储 |
|---|---|
| offline（默认） | 无模型、遥测或远端存储请求；本地 SQLite/文件/Qdrant sparse |
| local | 真实 embedding/生成，本地 Qdrant |
| cloud | 真实模型和 Qdrant Cloud；原文件、SQLite仍在本地 |

在本机 `.env` 配置，不把密钥发送到聊天或提交仓库。默认模型名是配置示例，未验证账户。

- `MODEL_API_KEY/BASE` 是共享兜底；`LLM_API_KEY/BASE`、`EMBEDDING_API_KEY/BASE` 可独立覆盖。可选空字符串视为未配置。
- 配置 `LLM_MODEL`、`EMBEDDING_MODEL`、`EMBEDDING_DIMENSION`、`EMBEDDING_REVISION`。更换 embedding 契约后需要显式重建，不能混用向量。
- cloud另需 `QDRANT_URL/API_KEY`。更换 endpoint/collection prefix 使用独立 `DATA_DIR`，保留清理归属。
- `GRAPH_ENABLED=true` 开启摄入抽取，或通过 UI/API手动构建。无Neo4j时使用本地来源图；Aura配置 `NEO4J_URI/USER/PASSWORD/DATABASE`。
- 缓存默认有界内存；Upstash REST用 `REDIS_URL/TOKEN`，不是redis://。默认TTL3600秒、容量100、阈值0.98。阈值未经过真实校准，词序/实体/数字/否定/口径保护刻意保守。
- `FALLBACK_MODEL` 使用同一生成凭据/网关，不能默认抵御供应商级故障。`RERANK_MODEL` 是可选LLM listwise排序，在候选池上重排后选择最终证据。
- `CHUNK_STRATEGY=legacy/boundary`、`CHUNK_SIZE=900`、`CHUNK_OVERLAP=0`；legacy保留旧固定字符策略，boundary尽量保留段落/句子边界。表格维持专用有界切分，不套普通overlap。更改切块或embedding输入契约必须显式重建。
- `RETRIEVAL_BRANCH_LIMIT=20`、`RETRIEVAL_CANDIDATE_POOL=24`、`RETRIEVAL_TOP_K=8`、`RRF_K=60`；进入缓存标识和运行记录，不改变索引契约。
- `EVIDENCE_BUDGET_BYTES=3000` 明确计量证据JSON的UTF-8字节；旧`CONTEXT_TOKENS`仅为兼容别名。`MODEL_REQUEST_BUDGET_BYTES=64000`另外限制完整messages JSON加输出预留，不能当作精确token计数。
- `SEARCH_PROVIDER=tavily` 和服务器端 `SEARCH_API_KEY` 接入正式API；默认disabled，offline禁止联网。搜索只发现页面，答案证据来自公开静态HTML正文。详细界限在配置示例与升级说明中。
- `LANGFUSE_PUBLIC_KEY/SECRET_KEY/BASE_URL` 开启SDK OTLP导出。默认不传正文；本地 events.jsonl/runs保留报告。

交互问答预算为 `MAX_MODEL_CALLS=40`、`REQUEST_TIMEOUT=60`（含排队）；后台embedding、图谱和全文摘要使用 `BACKGROUND_MAX_MODEL_CALLS=5000`、`BACKGROUND_TIMEOUT=1800`，模型预算在解析后创建；单调用 `MODEL_CALL_TIMEOUT=60`。成功embedding批次、图谱块和章节摘要可在重试时复用；新索引成功后才切换活动代次。瞬态错误最多重试1次，跨请求冷却10秒，认证/参数错误不重试。embedding不换模型；dense策略不可用时不伪装成其他策略。生成失败保留证据。

## 验证

```powershell
python -m pip --python .venv/Scripts/python.exe install -r requirements-dev.txt
$env:APP_MODE='offline'
$env:PYTHONUTF8='1'
.\.venv\Scripts\python.exe -m pytest tests
.\.venv\Scripts\python.exe scripts/startup_smoke.py
.\.venv\Scripts\python.exe scripts/public_smoke.py
.\.venv\Scripts\python.exe scripts/evaluate.py
.\.venv\Scripts\python.exe scripts/evaluate.py --strategies sparse,dense,hybrid --rerank-comparison --report artifacts/rag-comparison-offline.json
.\.venv\Scripts\python.exe scripts/evaluate_online.py --report artifacts/online-comparison-offline.json
.\.venv\Scripts\python.exe scripts/cache_calibrate.py
```

Windows临时目录异常时创建 `.tmp`，设置 `$env:TEMP="$PWD\.tmp"; $env:TMP=$env:TEMP`，pytest使用新的 `--basetemp=.tmp/专用目录`。正式测试禁止外部网络。

评测依赖独立安装，核心启动不需要LangChain：

```powershell
python -m pip --python .venv/Scripts/python.exe install -r requirements-eval.txt
$env:RAGAS_DO_NOT_TRACK='true'
.\.venv\Scripts\python.exe -m pytest eval/tests
```

RAGAS0.2.15、LangChain0.3系列、Pydantic2.12.5已锁定。合成judge测试只证明实际三种指标类的调用契约，不代表模型质量。30题黄金集、4张机制卡仍待独立人工核对；初始dev/holdout不是统计充分的盲测。

配置真实服务并授权预算后才执行以下命令（可能计费）：

```powershell
# 先 APP_MODE=local，再 cloud + 独立 DATA_DIR 保存另一份报告
.\.venv\Scripts\python.exe scripts/cloud_smoke.py --allow-cloud --report artifacts/models-local.json
.\.venv\Scripts\python.exe scripts/services_smoke.py --allow-cloud
.\.venv\Scripts\python.exe scripts/cache_calibrate.py --allow-cloud --report artifacts/cache-real.json
.\.venv\Scripts\python.exe scripts/evaluate.py --allow-cloud --model-call-budget 300 --judge --judge-call-budget 500 --strategies sparse,dense,hybrid --rerank-comparison --report artifacts/evaluation-real.json
# 以下次数仅为示例上限；先确认供应商、授权凭据与实际费用预算。
.\.venv\Scripts\python.exe scripts/web_smoke.py --allow-live --search-call-budget 1
# 快照回放 + 真实模型；不会联系搜索/网页服务器
.\.venv\Scripts\python.exe scripts/evaluate_online.py --allow-model --model-call-budget 100
# 独立采集真实网页快照，再在同一快照上比较三种流程（只支持local存储）
.\.venv\Scripts\python.exe scripts/evaluate_online.py --allow-model --model-call-budget 100 --live-network --search-call-budget 3 --capture-output artifacts/web-snapshots.live.json --report artifacts/online-live.json
```

评测默认旁路缓存，各策略使用同一冻结索引、模型和上下文预算。`--rerank-comparison`成对运行并记录是否实际执行；`--chunk-strategy boundary --chunk-overlap 100`用于独立切块实验，不预设有收益。网络默认使用明确标记的合成快照；未记录的Agent查询返回replay miss，不伪造搜索结果，不偷偷联网。真实采集应改用实际公开对象的数据集；虚构星河样例不能用来验证真实公司搜索。拒答单列，缺评分保留null和原因。云smoke用随机命名空间并清理。Langfuse flush不等于UI已可见。

## 架构和接口

FastAPI静态UI → Service → SQLite/文件/Qdrant；dense+sparse和带来源图谱 → 一次RRF → 有界上下文 → LiteLLM。摘要、缓存、引用复用版本/代次/语料修订快照。图谱使用生成代次隔离实体和事实、固定参数化Cypher，应用内最多5起点、10扩展/节点、2跳、20路径、1000条作用域事实。

主要API：上传、任务查询/重试、版本/质量、重建、图谱构建、问答、原文证据、历史run、删除。`POST /api/query`新增 `scope=documents/web/combined`、`workflow=fixed/agent`、可选UUID `progress_id`；strategy为sparse/dense/hybrid/multi/graph。web无需document_ids，其余范围须选择就绪文档。联网与Agent初版只支持QA；Agent使用sparse/dense/hybrid基线，最多额外两轮搜索/检索。`GET /api/query-progress/{UUID}`查看进度；`POST /api/web-evidence/{web:ID}/save`主动导入冻结正文。完整路径见 `/docs`。

SQLite新增处理检查点与网页快照表，保留历史ID和业务数据。旧format-v1约束按M1修复事务迁移并备份；新chunk与embedding输入契约需显式重建。当前解析契约为 m5-v3-html-dom-table-grid，新增输入角色契约purpose-v1；旧索引在界面显式重建，历史引用仍可查看。同版本重建创建独立代次，失败时保留原有可用代次；升级旧 generations 唯一约束前自动备份为 state.pre-rebuild-v3.sqlite3。图谱单独构建，失败不阻塞混合问答。不要通过删除业务目录升级。

支持PDF/MD/TXT/CSV/DOCX/HTML/PPTX/XLSX/XLS。无OCR；Office不含可靠页码、图片文字、SmartArt、备注、宏；Excel首行作表头、公式只读缓存，XLS日期可能是序列数；HTML不执行脚本或获取外链。

本机无认证/多租户/高可用，摄入和问答串行。默认20MB上传；Office解压100MB/5000项，300页或幻灯片、30工作表、20000行、200列、200万抽取字符，解析60秒；无OS内存配额。上下文预算为保守UTF-8字节，非模型精确token计数。引用和quote校验只证明来源范围，不证明语义正确。详细数据边界、删除残留及已知限制见 [DELIVERY](docs/DELIVERY.md)。

修复与验证记录见 [ACCEPTANCE_FIXES](docs/ACCEPTANCE_FIXES.md)。界面提供最近问答恢复、可读原文记录、版本原文件下载和任务失败恢复提示。评测的 `candidate_anchor_recall/context_anchor_recall/all_required_anchors_present` 仅表示文字锚点匹配，`semantic_support=not_judged`，不能视为语义证据充分；黄金集已改为较完整的来源段落锚点，仍待人工复核。
