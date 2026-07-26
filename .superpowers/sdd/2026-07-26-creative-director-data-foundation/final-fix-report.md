# Creative Director Data Foundation Final Fix Report

日期：2026-07-26
基线：`a29026b014997ed730d89bd450d6ecfe0cb77993`

## 提交

- `63b3ef32bef432b1f54af3893341a1d81ecff648` — `fix: enforce creative intake stage and lock invariants`
- `cde41326e50428b2f7a81f78276af38d4f08ad5e` — `fix: close creative intake confirmation gaps`

第二个提交来自实现提交后的独立只读终审；复审 follow-up 结论为
`Critical: None`、`Important: None`、`Ready: Yes`。

## Finding 1：阶段校验不完整

### RED

当前 HEAD 修复前的直接探针稳定复现：

```text
PY_INVALID_CONFIRM decomposition_confirmed ready None None
JS_INVALID_NORMALIZED decomposition_draft 9 draft null null
```

即 `stage=decomposition_draft`、有 approved block、但 `brief=null` 且
`selectedModelProfileId=null` 时，Python 可确认到 `ready`，JS normalizer 也接受该状态。

先增加以下回归：

- Python domain：每个 stage 的 direction、brief、model、decomposition、recipeStatus
  精确跨字段组合；非法 decomposition draft 的确认。
- Node normalizer：同一精确组合 fail-closed。
- HTTP：真实 `/api/creative-intake/transition` 拒绝缺少 confirmed brief/model 的
  decomposition draft。

RED 命令和观察结果：

```powershell
& 'H:\提示词工程\AnimaDex\.venv\Scripts\python.exe' -m unittest `
  prototype.tests.test_creative_intake.PromptStudioCreativeIntakeTests.test_normalize_requires_exact_cross_field_state_for_each_stage `
  prototype.tests.test_creative_intake.PromptStudioCreativeIntakeTests.test_confirm_decomposition_rejects_incomplete_draft_stage -v
```

结果：2 tests，11 个预期 failure；非法状态未被拒绝。

```powershell
& 'C:\Users\13688\AppData\Local\OpenAI\Codex\bin\5b9024f90663758b\node.exe' `
  --test --test-name-pattern='creative intake normalization (enforces exact|rejects incomplete)' `
  prototype\tests\app.test.js
```

结果：2 tests，0 pass / 2 fail；normalizer 返回原非法状态。

```powershell
& 'H:\提示词工程\AnimaDex\.venv\Scripts\python.exe' -m unittest `
  prototype.tests.test_server.PromptStudioServerTests.test_creative_intake_transition_rejects_incomplete_decomposition_stage -v
```

结果：1 failure；HTTP 实际返回 `200`。

独立终审又发现 confirmed 状态仍可携带 open question/open conflict，或 confirmed
decomposition 携带 unapproved block。补充回归先观察到 Python 3 个 subtest failure、
Node 1 failure、HTTP 实际返回 `200`。

### GREEN

修改：

- `prototype/creative_intake.py`
  - 每个 stage 精确验证 selected direction、brief status、confirmed item locks、
    open questions/conflicts、selected model、decomposition status/approval 和 recipeStatus。
  - `confirm_decomposition` 防御性验证 confirmed brief、selected model 和 draft
    decomposition。
- `prototype/app.js`
  - 镜像 Python 跨字段不变量；非法历史/响应状态 fail-closed 到 empty session。
- `prototype/tests/test_creative_intake.py`
- `prototype/tests/app.test.js`
- `prototype/tests/test_server.py`

原始 GREEN：Python 2/2、Node 2/2、HTTP 1/1。
终审补充 GREEN：Python 1/1、Node 1/1、HTTP 1/1。

## Finding 2：锁保护绕过

### RED

直接探针稳定复现：

```text
PY_LOCK_BYPASS False silently changed
PY_CONFIRM_LOCK False
```

即先无授权把 locked item 降为 `locked=false`，下一次即可无授权改内容；同时
`confirm_brief` 不会锁定确认项。

先增加两步绕过与确认自动锁回归：

```powershell
& 'H:\提示词工程\AnimaDex\.venv\Scripts\python.exe' -m unittest `
  prototype.tests.test_creative_intake.PromptStudioCreativeIntakeTests.test_locked_item_cannot_be_downgraded_then_changed_without_approval `
  prototype.tests.test_creative_intake.PromptStudioCreativeIntakeTests.test_confirm_brief_locks_every_confirmed_item -v
```

结果：2 tests，0 pass / 2 fail。第一条完整两步链未抛错；第二条明确报告未锁 draft
不应被拒绝，而应在确认时自动锁定。

### GREEN

修改：

- locked item canonical comparison 加入 `locked` 字段，因此锁降级本身必须由本次
  `approvedLockedItemIds` 授权；授权仍不持久化。
- `confirm_brief` 在切换到 confirmed 前把全部 brief items 设为 `locked=true`。
- confirmed-stage normalizer 同时拒绝任何未锁 item，防止直接构造状态绕过转换。

同一命令结果：2/2 pass。

## 最终验证

专项：

```text
Python domain: 18/18 pass
HTTP creative-intake: 5/5 pass
Node creative-intake/persistence/hydration: 15/15 pass
```

完整回归（第二轮终审修复后的新鲜结果）：

```powershell
& 'H:\提示词工程\AnimaDex\.venv\Scripts\python.exe' `
  -m unittest discover -s prototype\tests -p 'test_*.py'
```

结果：`Ran 323 tests in 26.914s`，`OK (skipped=1)`，exit `0`。

```powershell
& 'C:\Users\13688\AppData\Local\OpenAI\Codex\bin\5b9024f90663758b\node.exe' `
  --test prototype\tests\app.test.js
```

结果：`116 tests`，`116 pass / 0 fail`，exit `0`。

`git diff --check` 与 staged `git diff --cached --check` 均为 exit `0`。

## 自审

- 只修改授权的五个实现/测试文件及本报告；未修改 `prototype/server.py`，HTTP 回归走
  现有真实路由和 domain error mapping。
- Python/JS 对 stage 的语义映射保持同步；服务端仍是状态转换唯一权威。
- 所有新测试断言真实 domain、normalizer 或 HTTP 行为，无 provider/network mock。
- 锁授权仍是单次动作能力，不写入 session；已确认内容默认锁定。
- reopen brief、切换 model 和 decomposition confirmation 的既有 stale/ready 传播仍由
  完整回归覆盖。
- 明确不处理已裁定不阻塞的 Minor：超出 JS safe integer 的 revision、逐步 revision
  断言粒度、极端 Unicode whitespace。
- 独立终审指出 `docs/data-model.md` 未补充“锁降级也受保护”和“确认自动锁”措辞；
  该项为 Minor，且文档不在本轮授权文件范围，保留给后续文档同步。
