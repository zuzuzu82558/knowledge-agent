# 服务配置与首次真实验收（2026-10-11）

凭据保存在本机 `.env`（已忽略），本文与测试报告不含密钥。

当前使用 Tavily basic 搜索、DeepSeek `deepseek-flash` 生成（关闭思考模式）、
千问 OpenAI 兼容 `text-embedding-v3` 向量化。千问地址为
`https://maas.qianwenaiapi.com/compatible-mode/v1`，1024 维，每批最多 10 条。
兼容接口没有 DashScope `text_type` 参数，因此当前采用 symmetric 输入契约，
没有宣称已经使用 query/document 非对称优化。
官方契约：[千问向量接口](https://platform.qianwenai.com/docs/api-reference/text-embedding/openai-embedding)、
[输入方式差异](https://platform.qianwenai.com/docs/developer-guides/embeddings/embedding)、
[DeepSeek 接口](https://api-docs.deepseek.com/)。

`LLM_EXTRA_BODY` 只发给生成接口，并以指纹进入问答缓存、章节摘要和图谱抽取检查点。
不允许通过该配置覆盖模型、消息、输出长度或连接控制。更换生成供应商时必须同时修改此项。
LiteLLM 使用 PRODUCTION 模式，禁止它首次导入时再次加载 `.env` 改写进程环境；
配置统一由 Settings 读取。普通测试仍使用替身，阻止外部网络。

最终离线回归：149 项通过，0 失败、0 错误、0 跳过，耗时 66.386 秒。
报告：`artifacts/provider-config-tests-final.xml`。

## 有界真实验收

用户批准最多人民币 1 元、1 次搜索、2 次生成、2 次 embedding，不重试。
执行命令：

```powershell
.venv/Scripts/python.exe scripts/provider_smoke.py --allow-live --budget-yuan 1 --data-dir .tmp/provider-smoke-20261011 --report artifacts/provider-smoke.live.json
```

该命令要求全新 `.tmp` 子目录，重跑须更换目录并重新批准调用预算。
脚本按请求类别设置硬调用上限，限制模型输出和证据大小，不使用备用模型、图谱、重排或托管存储。
人民币金额是人工批准的预算；程序没有供应商余额查询或通用人民币计费器，
最终费用须核对供应商账单，报告中的调用次数不能冒充实际金额。

实际执行了 1 次搜索、2 次生成、2 次向量化：

- 文档 embedding 成功，26 tokens；问题 embedding 成功，6 tokens，均通过维度与数值校验。
- 文档 RAG 成功，生成用量 292 输入 / 56 输出 tokens；返回“测试项目代号为青松”，引用测试文档。
- 固定联网流程发现 3 个结果，2 个正文读取成功，1 个目标因 non_public_address_denied 被拒绝。
- 联网生成请求完成，用量 1879 输入 / 346 输出 tokens，但返回值未通过 JSON/答案/引用契约，
  最终 generation_failed，证据保留。当前报告只记录 ValueError，无法确定具体哪项契约失败；
  没有追加收费调用来猜测或重试。

接口可达与文档链路通过；固定联网回答未完全通过。没有真实 Agent、rerank、30 题质量对比或 RAGAS 验收。
引用的存在、范围与快照校验均不等于语义支持判断。
证据与历史 run 位于 `.tmp/provider-smoke-20261011`，报告位于 `artifacts/provider-smoke.live.json`。

## 网站

- 离线预览：`http://127.0.0.1:18780/`。
- 联网预览：`http://127.0.0.1:18781/`，启动时覆盖 APP_MODE=local，
  DATA_DIR 为 `.tmp/online-preview-20261011/data`；首页及健康接口返回 200。
- `.env` 的默认 APP_MODE 仍是 offline，避免下一次启动业务目录时自动触发模型调用或混用索引。
- 联网预览中的新提问、上传、网页保存会调用相关服务，属于用户主动操作，
  不计入已经执行完毕的自动验收。搜索每次最多 1 次，重试与备用模型关闭。

重新启动联网预览：

```powershell
$env:APP_MODE='local'
$env:DATA_DIR='.tmp/online-preview-20261011/data'
.venv/Scripts/python.exe -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 18781
```

资料范围选“联网搜索”可以直接提问，选“资料 + 联网”须上传并勾选就绪文档。
旧离线索引与当前 embedding 契约不同，需要明确重建后才能做向量检索；未对业务库执行重建。
