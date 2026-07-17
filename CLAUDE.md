# 项目协作规则

## 项目边界

- 主产品是 `prototype/` 中的 Anima Prompt Studio；`AnimaDex/` 是只读资源服务。
- 目标运行环境是 Windows 桌面端，默认端口为 Prompt Studio `57913`、
  AnimaDex `5000`、本地 LLM `8080`。
- 不把 API Key、`.env`、本地 SQLite、日志、PID、虚拟环境或模型文件提交为源码。
- `models/`、`animadex-data/` 和 `prototype/data/prompt_studio.db` 是本机状态；
  测试不得依赖其存在。

## 常用命令

```powershell
.\start-local.ps1
.\start-local.ps1 -SkipLlm
.\AnimaDex\.venv\Scripts\python.exe -m unittest discover -s prototype\tests -p "test_*.py"
node --test prototype\tests\app.test.js
.\AnimaDex\.venv\Scripts\python.exe -m unittest discover -s scripts\tests -p "test_*.py"
```

## 修改约束

- HTTP 路由集中在 `prototype/server.py`，SQLite 结构和访问集中在 `prototype/db.py`。
- 文本提示词规则以 `prototype/prompts/text/` 为源；
  `prototype/prompts/compiled/text_expand_anima.md` 由
  `scripts/export_text_expand_prompt.py` 生成。
- AnimaDex 数据保持只读；用户收藏、译名、备注、版本和设置写入 Prompt Studio。
- 当前前端保存只走 `POST /api/workspace/commit`，保持稳定 `operationId` /
  `Idempotency-Key` 和冻结正文；不要重新拆成项目、版本、metadata 多次写入。
- 词库 `random-plan` 必须保留实验环境变量与目录发布门禁；未完成语义审核前不得把
  `runtimeReady` 改为 true。
- 产品恢复只能写隔离数据库并返回 `activated=false`；不得由 HTTP 请求替换当前库。
- 视觉 worker 必须输出统一 JSON，并允许单模型失败时保留其他模型结果。
- 新增或修改行为时同步 Python/Node 测试；不得用真实外部 API 作为自动测试前提。

## 文档同步

- 当前事实更新 `README.md`、`docs/architecture.md`、`docs/data-model.md` 或
  对应接入文档；完成状态更新 `docs/roadmap.md`。
- `docs/superpowers/specs/` 与 `docs/superpowers/plans/` 是历史快照，不作为当前待办。
- 新增 API 时同步架构 API 速查；新增表或字段时同步数据模型。
- API 示例见 `docs/integration-guide.md`；环境变量、构建、回归和恢复操作见
  `docs/operator-runbook.md`。
