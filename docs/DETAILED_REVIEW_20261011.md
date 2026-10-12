# 前后端详细复查 · 2026-10-11

本次复查读取实际实现，并用失败用例验证结论。先新增 8 个回归用例，修复前 **8 个均失败**；保留记录 `artifacts/detailed-review-before.xml`。随后修复，并增加请求容量、并发读取顺序、取消记录和候选淘汰原因的测试。业务 `data` 目录未用于测试。

## 后端发现与修复

| 问题 | 实际影响 | 本轮处理 |
|---|---|---|
| Agent 的补充检索仅追加在原候选之后 | 初始池已满时，新检索结果既进不了下一次模型决策，也进不了最终上下文 | 不同轮次的本地分支独立记录排名，重新 RRF 融合；规划和回答使用同一有界候选池 |
| Agent 的 auto 问题包含“全文/全局” | 实际执行 QA，却被分配后台 1800 秒/5000 次预算，而非交互 60 秒/40 次 | 共用实际路由解析函数；Agent 一律使用交互预算，summary 才使用后台预算 |
| 重排直接发送完整网页正文 | 一页 30,000 个中文字就可能超过默认 64,000 字节请求上限，导致重排退回原序 | 保留候选 ID，按完整序列化请求和输出预留分配文本摘录；记录每个摘录的字符范围 |
| Agent 规划输入固定为每条 900 字符 | 默认 24 条中文候选已可能超过模型请求预算 | 规划输入同样受完整请求字节预算控制；记录输入大小和摘录范围 |
| 联网阶段耗尽总问答时间 | 已取得的本地证据或已读网页未进入最终结果 | 成功证据立即保留；超时后执行有界上下文筛选、保存 run，状态仍明确为 request_timeout，不生成超时后的回答 |
| 缓存命中复制旧预算审计 | 本次没有生成调用，记录仍显示源回答的调用数和请求体预算 | 本次计数独立记录；旧值保存为 source_model_budget / source_model_request_budget |
| 纯联网证据放不下上下文 | 被误报为 refused/没有召回，掩盖实际预算问题 | 按实际候选判断 context_budget_exceeded |
| HTTP 正文提前断开 | read1 返回 EOF 后，短于 Content-Length 的正文仍被标记 body_read | 拒绝不完整长度和已超时的正文；不保存为已读取快照 |
| 历史摘要 API 不含资料范围/流程 | 纯网页问答被前端标成“历史文档” | 返回 scope/workflow；老记录默认 documents/fixed |
| 多页读取按完成时间插入 | 并发网络速度可能改变候选顺序，影响重放比较 | 网页候选按搜索发现顺序排列，保留稳定的来源交替基线 |
| 模型请求被取消没有事件 | 调用次数已增加，model_calls 却看不到这次尝试 | 记录 cancelled；不编造供应商 token 用量或费用 |
| 候选池截断发生在调试记录之外 | 无法说明召回项为何没有进入重排 | 保留 fusion_candidates、pool_decisions 和淘汰原因，最终 Top-K 仍独立 |

主要实现：`app/service.py`、`app/online.py`、`app/models.py`、`app/web.py`、`app/reliability.py`、`app/main.py`。检索/预算策略进入运行 policy 与缓存标识。本轮不改变切块或 embedding 索引指纹，不需要因这些复查修复再次重建；此前升级造成的旧指纹不匹配仍按原规则显式重建，历史 ID 和引用保留。

文本摘录是容量控制，不证明相关性或语义完整。重排仍在整个候选 ID 集合上排序，长文本的摘录可能遗漏分散的线索，需真实质量评测决定是否改为段落级网页检索。完整网页快照保持不变。

## 前端发现与修复

- 统一约束 scope/workflow/strategy/route；Agent 禁用 multi/graph 和非 QA 任务，纯联网禁用无关的本地检索策略，离线清除不支持的 Agent/重排选择。
- 重复提交在提交处理函数入口拦截，不能只依赖按钮 disabled；正在执行时历史重试也不会再次提交。
- 离线重试历史 Agent 明确提示需要模型，避免悄悄改用固定流程。
- 历史列表显示“联网搜索”“资料 + 联网”和 Agent；无证据提示按资料范围区分；展示 Agent 停止状态。
- 引用页短暂 HTTP 失败后重置渲染状态，恢复后重新显示正文并清理旧位置列表。
- 引用 ID 先解码再用于 API 编码，修复 `web%3A...` 双重编码。
- 调试表显示补充轮次、池外候选及其淘汰原因、重排摘录数量、最终上下文处理。

使用真实无头 Edge 检查桌面及 390px 布局。浏览器检查包含真正的本机 HTTP 上传/解析/索引、联合问答在离线网络失败时保留证据、历史重试、引用读取、删除和失败恢复。网页展示、显式保存按钮、模型模式选项及网页 ID 页面使用明确的 UI 替身；这部分不是公网服务验收。浏览器阻断测试服务之外的页面请求。

## 验证与复现

| 验证 | 实测结果 |
|---|---|
| 完整 pytest（原有 130 + 本轮 12） | **142 通过，0 失败/错误/跳过，72.776 秒** |
| Edge 浏览器 | **24 项通过，0 JS 异常**；Codex 已查看桌面与 390px 截图 |
| 独立真实 HTTP 冒烟 | **8 项通过**：启动、页面、摄入、问答、引用、缓存、全文覆盖、删除 |
| 冻结网页回放 | 9 行：6 个离线摘录结果，3 个 Agent 因离线明确不执行；不作为回答质量分数 |
| Python/JS 语法、pip 依赖一致性 | 通过 |
| 原始失败复现 | 修复前的 8 项失败报告保留用于说明问题，非当前失败 |

结果见 `artifacts/detailed-review-all-tests.xml`、`artifacts/rag-upgrade-browser/browser.json`、`artifacts/startup-smoke.json`、`artifacts/detailed-review-online-replay.json`。汇总与源码指纹见 `artifacts/detailed-review-summary.json`。旧的 `rag-upgrade-*` 测试/评测文件是首次升级记录，不代表本轮新代码的结果。

```powershell
.\.venv\Scripts\python.exe -m pytest tests eval/tests -q --basetemp=.tmp/review-reproduce --junitxml=artifacts/review-reproduce.xml
.\.venv\Scripts\python.exe scripts/browser_smoke.py
.\.venv\Scripts\python.exe scripts/startup_smoke.py
.\.venv\Scripts\python.exe scripts/evaluate_online.py --report artifacts/review-online-replay.json
node --check app/static/app.js
node --check app/static/evidence.js
python -m pip --python .venv/Scripts/python.exe check
```

普通 pytest 在 `tests/conftest.py` 阻止外部 DNS 和 socket；模型、搜索、页面均使用明确的替身。浏览器和 HTTP 冒烟只使用随机本机端口和临时目录，退出时关闭自己创建的进程树。未执行真实 embedding、生成、重排、搜索或付费 judge。

## 仍需如实保留的限制

1. 真实模型质量、搜索可用性、重排/图谱/语义缓存收益、RAGAS 分数和账单仍未验证。后续需要本机凭据、具体模型与版本、总费用和调用预算；不能用本轮合成测试通过来代替。
2. 30 题已有来源复核，独立人工逐条判断语义支持仍待完成。本轮未虚构人工结论；文字锚点命中和引用 ID 合法都不等于事实被支持。
3. Service 仍以单个锁串行处理摄入、问答、重建等操作。长任务会让问答排队甚至超时；适合单用户演示，尚不适合多用户并发部署。
4. 全文摘要使用长 HTTP 请求，成功块可恢复，但尚无持久化任务队列、交互取消和完整后台任务 UI。
5. 网页快照尚无保留期、磁盘总配额和删除 UI；大量搜索会持续增加存储。静态 HTML 和发布时间提取均有边界，动态站点、登录、验证码及 PDF 正文不支持。
6. 上下文预算是 UTF-8 字节契约和输出估算，不是供应商 tokenizer。页面去噪、片段位置、文档/网页交替和跨轮 RRF 都需数据验证效果。
7. 本机单用户应用尚无认证、多租户、分布式队列或高可用验证。此次是功能与边界复查，不是独立渗透测试。

本次只读探测 `http://127.0.0.1:8000/api/health` 得到连接拒绝（WinError 10061）；没有启动、停止或重启业务服务，不能宣称业务端已在线。开发验证通过的是隔离服务。当前目录也没有 Git 元数据，因此没有提交号或可比较的 Git diff；保留源码和测试/审查产物供复核。
