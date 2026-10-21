# 贡献指南

欢迎提交 Issue 和 Pull Request。

## 开发环境

需要 Python 3.12。

```bash
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
APP_MODE=offline .venv/bin/python -m pytest
APP_MODE=offline .venv/bin/python scripts/startup_smoke.py
```

Windows 下把 `.venv/bin/` 换成 `.venv\Scripts\`，并用 `$env:APP_MODE='offline'` 设置环境变量。离线模式不调用任何模型、搜索或其他付费服务，CI 也只运行离线测试。

## 提交 PR 前

1. 从 `main` 新建分支，一个 PR 只解决一件事。
2. 修复 Bug 时先写出能复现问题的回归测试，再修改代码；新增功能附带对应测试。
3. 本地运行完整离线测试与 `scripts/startup_smoke.py`，确保全部通过。
4. 不要提交 `.env`、API Key、`data/` 下的用户数据或大体积运行产物。
5. PR 描述写清楚：问题与根因、修改内容、验证方式、尚未覆盖的风险。不要把离线测试结果描述成真实模型效果。

## 报告问题

Issue 请写明操作系统、Python 版本、运行模式（offline / local / cloud）、复现步骤、预期结果和实际结果。安全问题请参考 [SECURITY.md](SECURITY.md)。
