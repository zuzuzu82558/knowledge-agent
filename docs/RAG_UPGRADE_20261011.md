# RAG 联网与受控 Agent 升级交付

日期：2026-10-11（Asia/Shanghai）。开发使用临时数据目录和独立端口，未改写 `data`、未停止/重启8000业务服务；当前业务进程仍需由使用者在安排好时机后切换到新版代码。项目没有Git仓库，没有提交或发布远程产物。

## 完成范围

| 顺序 | 实施 |
|---|---|
| 预算与契约 | 交互/后台/单调用预算；解析后开始模型计时；embedding批次检查点、图谱块检查点与原有章节摘要复用；明确证据/请求两类字节预算；embedding供应商document/query角色与指纹隔离。 |
| 可调RAG | legacy与boundary对照，可选overlap，显式重建；sparse/dense/hybrid独立分支；branch/pool/top-k/RRF参数；在候选池重排后选证据；缓存与run记录参数和筛选原因。 |
| 固定联网 | 正式Tavily适配器，三种资料范围；有界静态HTML读取、DNS地址绑定与逐跳校验；正文失败降级；web:ID不可变快照、统一引用和显式保存资料；进度与调试UI。 |
| 受控Agent | LLM真实选择动作，程序验证参数/范围/去重/两轮补检索/页面与总预算/停止条件；决策及工具耗时落盘；固定流程保留基线。 |
| 评测交付 | 同索引三路+重排成对评测；网络冻结回放与独立实时smoke；用量/延迟/失败记录；30题来源语义复核及待人工验收表；配置/README/架构/命令更新。 |

1280/1281块的交互40次边界仍按约定存在，但摄入走后台预算，1281块的41个32块批次可完成；成功批次在预算耗尽后能复用，不需要重付所有已完成embedding。仍受后台时间/次数上限，不把它做成无限预算。图谱和全文摘要不足则partial并可恢复，不能声称未完成内容已总结。

## 本轮实际验证

- 核心pytest：129项通过（含本轮52项），与独立RAGAS契约合计130项，`artifacts/rag-upgrade-all-tests.xml`；本轮新测试单列于 `artifacts/rag-upgrade-new-tests.xml`。
- 独立RAGAS真实指标类+合成judge契约：1项通过，`artifacts/rag-upgrade-ragas-contract.xml`；无真实judge评分。
- compileall和两份前端JS语法检查通过。
- 独立真实Uvicorn进程：8项HTTP生命周期检查通过，`artifacts/startup-smoke.json`；临时进程已关闭。
- 浏览器：首次升级11项检查通过；后续[前后端复查](DETAILED_REVIEW_20261011.md)扩展至24项，并更新 `artifacts/rag-upgrade-browser/` 的报告和桌面/390px截图。使用私有headless Edge、本地临时端口并阻止页面外网。部分网页交互使用显式合成UI fixture，不代表在线服务验收。`scripts/browser_smoke.py`无需npm依赖，Node需支持WebSocket，非Windows环境需配置EDGE_BINARY。
- 30题sparse/dense/hybrid×重排开关：180次。120次offline_extracts、60次dense_unavailable；重排实际未运行。sparse与离线hybrid完整文字锚点各29/30，不能解读成语义支持率或质量提升。`artifacts/rag-comparison-offline-20261011.json`。
- 联网3题合成冻结快照：6次固定流程离线摘录、3项Agent not_run_offline。`artifacts/online-comparison-offline-20261011.json`。测试替身中的模型选择工具路径有测试覆盖，但无真实Agent效果数据。
- 30题来源复核：已逐题检查源段落与参考答案，发现g13/g16/g20/g21年份或指代/范围歧义，记录于 `docs/GOLDEN_REVIEW_20261011.md`；独立人工验收仍pending。

以上报告生成后若继续修复，测试与对应报告须按最终源码重新生成；不沿用旧阶段通过数来声称新代码已验收。

## 使用与兼容

新配置契约增加chunk/input字段，即使保留legacy默认值，旧配置的活动代次也会显示“规则更新，请重建”。**只在用户显式点击重建时创建新代次，历史chunk/版本/引用保留；不要删除业务目录来升级。** 新后台失败不替换原可用代次。SQLite只新增表，不改旧ID。

开发启动示例（使用全新独立目录，8000保留业务）：

```powershell
$env:APP_MODE='offline'
$env:DATA_DIR='E:\codexproject\knowledge-agent\.tmp\rag-dev-new'
.\.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 18780
```

复现：

```powershell
.\.venv\Scripts\python.exe -m pytest tests eval/tests --basetemp=.tmp/rag-reproduce
.\.venv\Scripts\python.exe scripts/startup_smoke.py
.\.venv\Scripts\python.exe scripts/evaluate.py --strategies sparse,dense,hybrid --rerank-comparison --report artifacts/rag-comparison-offline.json
.\.venv\Scripts\python.exe scripts/evaluate.py --strategies sparse,dense,hybrid --chunk-strategy boundary --chunk-size 900 --chunk-overlap 100 --report artifacts/rag-boundary-offline.json
.\.venv\Scripts\python.exe scripts/evaluate_online.py --report artifacts/online-replay-offline.json
```

公网搜索采用 [Tavily官方Search契约](https://docs.tavily.com/documentation/api-reference/endpoint/search)。输入角色按 [Cohere](https://docs.cohere.com/docs/embeddings) 与 [Voyage](https://docs.voyageai.com/reference/embeddings-api) 的query/document定义处理；LiteLLM适配见 [Cohere](https://docs.litellm.ai/docs/providers/cohere)、[Voyage](https://docs.litellm.ai/docs/providers/voyage)。本轮查阅官方接口文档不等于实际账户/服务验收。

## 真实验收仍需集中配置

1. 本机服务凭据：LLM、embedding、可选rerank/judge；具体供应商model/revision/input profile/dimension/endpoint。不要将密钥明文发到聊天。
2. 联网：Tavily服务端密钥，实际搜索账户额度、授权公开页面/查询；把虚构星河测试集换成可核对的真实对象。
3. 费用预算：总货币上限及模型/search/judge调用上限。CLI次数计数器覆盖重试/摄入，不能测量或保证供应商账单；成本未知时保持null。
4. 独立人工审题和逐claim引用支持标注。g16不能因文字锚点缺失直接判语义不支持，也不能因引用ID合法直接判正确。

授权后按README命令先实时接口smoke，再同索引真实三路/重排，冻结网页三流程比较，最后judge、图谱及缓存收益。未执行前所有真实质量、提升幅度、RAGAS及成本结论保持未验证。

## 已知限制

静态HTML、不执行JS、无登录/验证码/PDF正文；正文去噪与发布时间仅启发式。网页快照目前无保留期限/删除UI，可能增长；同一URL重新读取产生新快照。多源pool使用可复现的交替顺序，不保证最优相关性。Agent输出JSON依赖模型遵约，坏参数被拒绝并保留已有证据；两轮是附加搜索/检索的总次数，不保证复杂问题完成。全文摘要仍通过长HTTP请求执行而非持久化后台任务队列，但成功块可恢复。精确tokenizer、认证/多租户、高可用、OS内存配额未实现。供应商同名模型的实际revision须真实验收确认。
