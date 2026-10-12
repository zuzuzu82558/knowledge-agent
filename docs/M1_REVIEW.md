> 2026-10-09 执行方式更新：用户已取消逐里程碑确认；下方历史停止点失效。连续实施 M1 收尾及 M2–M5，真实云端验收单独标记。

# M1 独立审查结果

## 2026-10-09 修复复核（R1–R5）

下方原始审查记录保留，表示修复前状态。本节记录本轮实际修复；没有进入 M2，也没有调用真实云端模型或 Qdrant Cloud、读取真实 `.env` / 密钥、修改业务数据目录。

**结果：R1–R5 已修复并纳入正式测试；交付复核 `39 passed, 1 warning in 31.42s`，包含原有 17 项及新增 22 项。云端验收、Linux CI 仍待完成。**

### 逐项修复与验证

| 项目 | 已确认触发条件与根因修复 | 验证与限制 |
|---|---|---|
| R1 | 1800 字中文分成两个 900 字块，修复前 JSON 字节成本 3007/3005，默认预算 3000 全丢弃。新增 `app/context.py`；预算计算与 `CloudModels.answer` 共用实际模型证据投影，只发送 ID、正文、章节和适用的列名；超预算选择相关摘录，保留相对原 chunk 的半开字符区间，不修改原块。默认参数未调大。 | 默认及 500/1000/3000 预算通过；模拟 LiteLLM 调用验证实际载荷预算和 120 亿元证据，逐字核对摘录来源。元数据/最小有用摘录无法容纳时 `context_budget_exceeded` / `context.status=budget_exceeded`；零召回为 `refused` / `context.status=no_candidates`。仍是保守字节估计，预算不包含问题、系统提示和输出；相关摘录是启发式，不能证明证据完整。 |
| R2 | 长 CSV 记录首行被误当表头，后续片段重复完整 JSON 行。解析现在显式存储 `table_cells`（列名、值）；超长表格按单元格值切分，只附带有界的短字段上下文、当前列名和行标识。 | CSV、真实 Markdown 解析和模拟 PDF 表格均验证每块不超过 900 字符；长字段按 `cell_span` 重组与原值完全一致；字段名、公司、年度、单位保留；异常长列名和旧 ParseBlock 也不再越切越大。PDF 真实复杂版式与表格恢复能力未因此得到云端/生产验收。 |
| R3 | Qdrant 清理失败前逻辑删除已落库，但历史读取没有检查删除状态。`Service.run()` 读取时逐项核对冻结文档范围，任一文档删除/缺失即整条 run 返回 410，不返回问题、回答或证据。 | 单文档原始复现通过；混合文档 run 在删除失败后及重启后均不可读；不相关 run 仍返回 200；物理清理重试成功后旧 run 为 404。旧失败删除留下的历史记录也由读取检查保护，不要求先清空 runs。 |
| R4 | 同一文档同字节 TXT→MD 被 document+sha 的身份和唯一约束合并。新版本身份为 document+sha+suffix；旧库约束事务迁移为 `UNIQUE(document,sha,suffix)`，旧版本 ID、路径、代次、任务、chunk、run 均保留。同格式重新上传优先复用旧记录 ID。 | 同格式幂等、换格式新版本/新代次、切回 TXT、迁移备份、重启幂等、模拟迁移中途失败完整回滚均通过。旧 v1 解析/切分指纹不复用；UI/API 重建可从记录的原文件生成 v2 代次，无需删除旧库。旧错误操作留下的未登记后缀文件不自动解释或删除，需按真实格式重新上传。 |
| R5 | 标题数组位置不能代表真实层级。采用 `(level,title)` 栈，遇新标题弹出所有 level 大于等于当前层级的项。 | 连续/跳级/同级/返回上层/新顶层通过；`# 总览 → ### 第一节 → ### 第二节` 正确为 `[总览, 第二节]`。旧代次引用保持原历史内容，修复后的章节路径写入新代次。 |

关键代码：`app/context.py`、`app/parsing.py`、`app/storage.py`、`app/service.py`、`app/models.py`、`app/main.py`。正式回归：`tests/test_m1_regressions.py`；测试网络保护：`tests/conftest.py`。

### 本轮实际执行

在项目目录、子进程环境显式 `APP_MODE=offline`，`MODEL_API_KEY` / `QDRANT_API_KEY` / `OPENAI_API_KEY` 置空，`LITELLM_LOCAL_MODEL_COST_MAP=True`，`TEMP/TMP` 指向项目 `.tmp`，`PYTHONUTF8=1`：

```powershell
# 修复前完整重现：5 failed, 1 warning in 6.72s
.\.venv\Scripts\python.exe -m pytest .tmp/m1_review_checks.py --basetemp=.tmp/m1-fix-before -p no:cacheprovider -s

# 首轮修复后：原 17 项、正式迁入 5 项及原脚本 5 项，共 27 passed
.\.venv\Scripts\python.exe -m pytest tests .tmp/m1_review_checks.py --basetemp=.tmp/m1-fix-first -p no:cacheprovider

# 扩展正式回归：35 passed, 1 warning in 29.77s
.\.venv\Scripts\python.exe -m pytest tests --basetemp=.tmp/m1-fix-expanded -p no:cacheprovider

# 最终正式回归：39 passed, 1 warning in 30.68s
.\.venv\Scripts\python.exe -m pytest tests --basetemp=.tmp/m1-fix-final -p no:cacheprovider

# 交付代码复核：39 passed, 1 warning in 31.42s
.\.venv\Scripts\python.exe -m pytest tests --basetemp=.tmp/m1-fix-delivery -p no:cacheprovider
```

外部网络/DNS 在正式测试中被自动 fixture 拦截，只允许本机事件循环 socket。模型载荷验证使用模拟 LiteLLM 返回，dense 使用合成向量。唯一警告为已有 Starlette/httpx TestClient 弃用提示。

交付补充检查：`.venv/Scripts/python.exe -m compileall -q app tests scripts`、`node --check app/static/app.js` 通过；`python -m pip --python .venv/Scripts/python.exe check` 返回 `No broken requirements found.`。没有重新安装依赖或修改运行模式默认值。

### 兼容性、升级与停止点

- 本次只操作源码与隔离测试数据，没有运行业务库迁移或自动发起云端重建。
- 用户下次启动时自动迁移旧 `versions` 约束，首次迁移前保存 SQLite 在线备份 `DATA_DIR/state.pre-format-v2.sqlite3`（含 WAL 已提交数据），不覆盖已有同名备份。迁移失败事务回滚；原版本、文件与所有历史 ID 保留。备份也包含敏感历史数据，应按业务数据管理。
- 解析契约升为 `m1-v2-level-stack-table-cells`，切分契约升为 `section-900-v2-bounded-table`，产生新索引指纹。旧索引/解析产物保持原样，旧数据未重建前不能用于当前配置新问答；旧引用/历史记录仍能读取，已删文档除外。
- 用户对已有文档点击“重建索引”，或调用 `POST /api/documents/{id}/rebuild`。使用已保存原文件创建新代次，成功后切换活动代次，失败保留旧代次及引用。怀疑旧格式切换被错误吞掉时，须重新上传真实格式文件；不能从旧版本记录推断当时用户意图。
- 在 local/cloud 模式重建会调用真实 embedding 并计费，应由用户配置凭据后显式执行。本轮未执行。
- 生成/embedding 独立凭据与锁优化仅列为后续事项，没有顺带实现。确认修复后仍需完成真实云端验收；等待用户确认，不进入 M2。

---

日期：2026-10-09（Asia/Shanghai）。范围：当前 M1 源码、进度说明、配置、CI 定义、已有测试及新增离线边界复现。未修改业务源码，未读取真实密钥，未调用云端模型或 Qdrant Cloud。

## 结论

M1 已有完整实现和可运行的离线链路，但不应直接进入 M2。当前有 5 个已复现缺陷；真实模型与 Qdrant Cloud 验收尚未完成，GitHub Actions 只有配置文件，没有真实运行记录。

建议顺序：修复以下缺陷并纳入正式回归 → 拆分生成/embedding 配置（若使用不同服务）→ 小规模真实云端验收与长文档人工核对 → 用户确认 M1 → M2 证据卡和基线。

## 验证记录

- 原有测试独立复跑：`17 passed, 1 warning in 18.64s`。
- 新增边界复现：`5 failed, 1 warning in 6.92s`。这些测试断言期望的正确行为，失败对应下文 5 个已复现问题，不是原 17 项测试回退。
- 初次沙箱执行遭遇 Windows 临时目录 PermissionError；放宽本机执行权限后，使用项目内独立临时目录复跑成功取得结果。此权限问题不计入产品缺陷。
- 测试的模型与 Qdrant 凭据在子进程环境中置空，使用显式 offline 模式。没有使用真实 `.env` 或业务数据目录。
- 有一条 Starlette/httpx TestClient 弃用警告；当前已通过的接口测试没有因此失败，暂不作为阻塞项。
- `git status` 返回当前目录不是 Git 仓库，不能宣称 GitHub CI 已运行或代码已有版本管理。

复现脚本：`.tmp/m1_review_checks.py`（审查辅助文件，不在默认 `tests/` 收集范围内）。

```powershell
$env:APP_MODE='offline'
$env:MODEL_API_KEY=''
$env:QDRANT_API_KEY=''
$env:LITELLM_LOCAL_MODEL_COST_MAP='True'
$env:TEMP = Join-Path $PWD '.tmp'
$env:TMP = $env:TEMP
.\.venv\Scripts\python.exe -m pytest .tmp/m1_review_checks.py --basetemp=.tmp/review-m1-boundaries-rerun -p no:cacheprovider -s
```

临时目录建议使用新的专用名称。后续修复时将对应测试迁入正式测试集，并保留隔离离线运行方式。

## R1 / P1：默认上下文预算把正常长度的中文片段全部丢掉

位置：`app/service.py:245-254`；相关切分：`app/parsing.py:107-116`。

复现：上传 1800 字中文段落，解析成功产生两个 900 字 chunk；稀疏检索命中两个候选，但最终 evidence 为空，结果为 refused。

实测 JSON UTF-8 字节成本为 3005 和 3007，而默认 CONTEXT_TOKENS 为 3000。代码把包含 ID、来源等元数据的整段 JSON 字节数作为 token 预算比较，一旦超限就整块跳过，没有重切或保留相关摘录的机制。这个上下文选择发生在生成调用之前，local/cloud 路径也使用同一逻辑。

影响：核心长中文文档问答出现“明明检索命中却报告没有证据”；现有短样例无法覆盖。PDF 页按 900 字切分后也存在触发条件。

建议：让切分大小、实际送入模型的证据结构与预算计算一致；保守预算可以保留，但必须能容纳正常 chunk，或按句子截取/重切并保留准确来源。区分“未召回”与“召回但预算无法容纳”。只提高默认数值不是完整修复，还要验证较小合法配置的行为。

## R2 / P2：长 CSV 行被当成表头重新复制到后续 chunk

位置：`app/parsing.py:111-114`；CSV 表示：`app/parsing.py:65-66`。

复现：一行含 2200 字说明的 CSV，规范化后记录长度 2226；切分输出长度为 `[900, 3127, 2653]`。

原因：CSV 被序列化成单行 JSON，通用 table 切分器用首行作为 header，于是把整条原始记录认成表头。在 offset > 0 的 chunk 前再次插入完整原记录。

影响：长表格切分越切越大，内容重复，破坏 embedding 输入长度和上下文预算，且浪费 token。

建议：将表头/schema 与数据行显式分开；后续块只复制必要字段名、单位和行标识，不复制完整原记录。测试 CSV 和 Markdown/PDF 表格的长行分块，验证长度与字段关联。

## R3 / P2：删除清理失败后，历史问答接口仍返回被删除文档正文

位置：`app/service.py:178-203`，`app/main.py:162-167`。

复现：先提问并得到 run_id，再模拟 Qdrant 删除失败。DELETE 返回 cleanup_pending，原文证据接口返回 410，但 GET `/api/runs/{run_id}` 仍返回 200，且包含原文片段。

原因：历史查询清理位于远端删除成功之后；远端故障直接进入 except，未清理 runs。历史接口也没有校验该 run 的文档删除状态。

影响：不符合“删除先屏蔽新访问”的已声明行为。单用户本机 MVP 的影响范围有限，但不应把远端清理故障变成历史正文继续可读。

建议：读取历史记录时检查冻结文档范围是否已删除，或在逻辑删除时同步使相关历史记录不可访问；远端物理清理独立重试。多文档 run 包含被删文档时也要遵循一致策略。

## R4 / P2：同一内容切换 TXT/Markdown 格式时错误复用旧解析代次

位置：`app/service.py:58-65`，`app/storage.py` 的 versions 唯一约束。

复现：将同一段 Markdown 文本先以 `.txt` 上传，再作为同一文档的新版本以 `.md` 上传。返回相同 job_id、generation_id，idempotent=true；没有按 Markdown 重新解析。

原因：版本 ID 使用 document + sha，数据库唯一性也是 document + sha，没有把影响解析行为的 suffix/格式纳入；虽然无 document_id 时的去重查询检查 suffix，指定文档更新的路径没有对应隔离。

影响：用户明确改变格式后仍沿用旧的结构和章节来源；还可能留下未被版本表引用的新后缀文件。

建议：将解析格式作为内容版本或解析代次身份的一部分，同步调整唯一约束、文件路径和去重逻辑；若坚持文档不可换格式，必须明确拒绝并要求新建，而不是返回成功并静默复用。

## R5 / P2：Markdown 跳级标题被错误嵌套

位置：`app/parsing.py:76-78`。

复现：`# 总览` 后依次出现 `### 第一节`、`### 第二节`。第二节正文的 headings 实际是 `[总览, 第一节, 第二节]`，正确应是 `[总览, 第二节]`。

原因：代码按数组长度截取父标题，假设实际标题层级连续；跳级后数组位置不再等于标题深度。

影响：章节引用错误，后续基于章节的全文摘要和图谱抽取分组也会受到污染。

建议：保存 `(level, title)` 栈，新标题到来时弹出 level 大于等于当前 level 的项，再加入当前标题。测试连续层级、跳级、同级和返回上层。

## 非本次运行失败，但下一步需要明确的限制

### 生成与 embedding 共用 key/base

`app/config.py:18-19` 和 `app/models.py:15-17` 只提供同一份 MODEL_API_KEY、MODEL_API_BASE，两类请求均显式传入。因此只有一个网关能同时提供两类模型时配置才顺畅；若选择生成服务 A 和 embedding 服务 B，当前无法独立设置。

建议在真实接入前支持生成和 embedding 独立凭据/endpoint，并保留共享配置作为可选兜底。embedding endpoint 影响索引身份，生成 endpoint 的改变不应无故触发全部向量重建。此项是实际接入约束，不意味着必须换成某家模型。

### 摄入与问答共用长时间持有的锁

`Service.ingest()` 整个解析、embedding、索引过程持有同一锁，query、upload、delete 也需要该锁。文档已如实说明串行行为，可接受为 M1 取舍，但会让旧文档问答等待另一份文档摄入。当前 elapsed_ms 在获取锁后计时，不能代表用户端总等待时间。

建议先展示排队状态并分别记录排队/执行时间；M2 扩大任务量前再评估锁粒度，不必立即重做并发架构。

### 语料统计和测试数据规模

BM25 b=0、保留旧代次对 collection IDF 的影响均在文档说明，暂不作为隐藏缺陷。M2 对照必须冻结专用 collection。现有 6 题和短虚构文档是 smoke 数据，不能证明长文档质量；至少补一份真实公开长文档，验证跨页、长段落、表格、数值和拒答。

### 云端与 CI 验收未完成

进度记录明确为待凭据；本次没有替代这项验收。修复后先做 real models + local storage，再做 real models + Qdrant Cloud，分别保存模式、配置和结果。云端脚本通过也只证明小样契约，还需人工核对真实长文档答案。

当前目录没有 Git 仓库，也没有远程 Actions 结果。建立本地版本管理和实际跑 CI 是工程收尾；发布远程仓库需按用户选择的账号、可见性和目标执行。

## 建议的下一步

1. 在 M1 范围内修复 R1–R5，将独立复现转为正式回归；保持原测试通过。
2. 确定生成和 embedding 提供方；需要不同服务时先拆分配置，密钥由本地环境提供。
3. 对真实公开长文档做本地存储模式的真实模型验收，再做 Qdrant Cloud 验收；记录未通过项。
4. 完善 Git/CI 和进度记录，区分已验证、仅模拟验证与待验证。
5. 用户确认 M1 后进入 M2，先做 3–5 张机制证据卡和冻结基线，不先堆图谱代码。

本次审查没有要求提前实现 M2–M5，也没有修改业务实现。优先修复现有功能，避免把解析和上下文选择错误误归因于“需要 GraphRAG”。
