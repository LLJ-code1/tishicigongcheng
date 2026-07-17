# GitHub 同步差异、构建方案与对抗测试（2026-07-16）

> 本文冻结为 GitHub 同步阶段的审计快照；后续 M0 数据基线与工作台迭代状态、最新
> 测试数字以 [开发日志](development-log.md) 和 [路线图](roadmap.md) 为准。

## 执行结果更新

本次同步已经按审计方案实施，而不再只是模拟：

- 原本未提交的本地状态已保存到 `codex/pre-sync-snapshot-20260716`，快照提交为
  `65a52a8`。
- 已从 `origin/main` 创建 `codex/github-sync-20260716`，远端三项提交和本地真实增量
  已整合为提交 `06a8dd2`；GitHub 远端没有被推送或改写。
- 第 5 节修复前发现的 Host、跨站写、HEAD、请求 framing、严格 JSON、图片完整性、
  数据 schema、权重、Unicode、状态值和密钥回显问题均已实现修复并加入自动回归。
- 独立复审追加发现的 LLM start/stop 请求体、副作用时序、旧项目 ID、敏感字段别名、
  重复收藏、慢速滴流、斜杠 slug 缩略图和无效 Unicode 边界也已收口。
- 当前全量回归为 Python 131 项（130 通过、1 项因可选 NumPy 缺失而跳过）、Node
  74/74、脚本 4/4，`pip check`、生成表检查与 `git diff --check` 通过。最终对抗结果见
  5.2 节。

完整验证通过后，本地 `main` 以 `--ff-only` 快进到整合提交；安全快照分支继续保留。
这次操作只同步和加固本地仓库，没有 push、没有调用真实模型、没有改动主数据库。

## 初始审计结论

本地 `main` 不是一个可以直接执行 `git pull` 的干净分支：它落后
`origin/main` 3 个提交，同时有 14 个已修改文件和 1 组未跟踪文件。
远端前端升级、产品需求和词库原稿，与本地后续的安全加固、权重处理和测试改动
发生了交叉。

建议以 `origin/main` 为新基线，在安全快照分支保存本地状态后，只回放 7 个真正的
本地增量文件。不要直接覆盖、重置或整批选择某一侧。模拟后的目标树已经通过
Python、Node 和脚本测试，但对抗测试仍发现一项高风险和多项中风险问题；在修复
P0 安全项以前，服务应继续只监听回环地址，也不应当作可信局域网服务发布。

以上是实施前的审计判断。后续章节保留当时的差异、修复前证据和构建决策，便于
追溯“为什么改”；实际实施状态以本页开头的执行结果和最终验证为准。

## 1. 审计范围与基线

| 项目 | 审计值 |
| --- | --- |
| 本地工作目录 | `H:\提示词工程` |
| 本地分支与提交 | `main` / `5b33c70` |
| 远端基线 | `origin/main` / `09e6eb0` |
| 分叉状态 | 本地领先 0、落后 3 |
| 工作区状态 | 14 个已修改文件，1 组未跟踪模型提示词文件 |
| 远端总体差异 | 29 个文件，新增 5,476 行、删除 158 行 |

远端新增的 3 个提交为：

1. `5999056`：产品需求文档。
2. `738a8f5`：Prompt Studio 前端、文档、升级 patch 和审查报告。
3. `09e6eb0`：九类随机元素词库原稿，共 471 条。

初始审计过程中只执行了 `fetch` 和只读比较，没有执行 `pull`、`merge`、`reset`、
提交或推送。后续实施使用新分支和可追溯提交完成，没有 `pull` 或破坏性 `reset`，
也没有推送。测试使用临时工作树、临时数据库、假密钥和 stub 引擎；没有向外部模型
发起请求。

## 2. GitHub 差异梳理

### 2.1 远端带来了什么

远端相对本地提交新增或完善了以下内容：

- `docs/product-requirements.md`、`docs/implementation-plan.md` 和
  `docs/acceptance-criteria.md`，明确目标流程、分期和验收定义。
- `prototype/app.js`、`prototype/index.html`、`prototype/styles.css` 等前端升级。
- `prototype/prompts/text/models/anima.md` 模型提示词。
- `词库原稿/` 下 1 个说明文件和 9 个分类词库，共 471 条原始条目。
- `0001-anima-prompt-studio-升级.patch`、`前端审查报告.md` 和
  `项目梳理与优化建议.md`，作为历史审查材料。
- `docs/README.md`、路线图、架构、数据模型和开发日志的配套更新。

远端共增加 17 个文件；其中 `prototype/prompts/text/models/anima.md` 已经以完全相同
内容存在于本地未跟踪目录，所以本地物理上缺少的是另外 16 个远端文件。

### 2.2 哪些本地文件其实与远端相同

下列 7 个本地改动或未跟踪文件的内容与 `origin/main` blob 完全相同，不需要手工
合并：

- `.gitignore`
- `README.md`
- `docs/data-model.md`
- `prototype/data.js`
- `prototype/index.html`
- `prototype/styles.css`
- `prototype/prompts/text/models/anima.md`

它们在新整合分支上直接采用远端版本即可。

### 2.3 哪些是远端升级后的本地有效增量

下列 4 个文件是“远端版本 + 本地后续修正”，应回放本地版本：

| 文件 | 本地新增价值 |
| --- | --- |
| `docs/architecture.md` | 补充静态文件边界、JSON/图片输入等安全说明 |
| `docs/development-log.md` | 记录 7 月 14 日的对抗修复和验证 |
| `prototype/app.js` | 负向块取消权重 UI、配方保留权重、复制与组合使用实时预览 |
| `prototype/tests/app.test.js` | 为这些行为增加 54 个测试行 |

### 2.4 哪些是只在本地存在的加固

下列 3 个文件远端没有相应修改，应完整回放：

| 文件 | 本地新增价值 |
| --- | --- |
| `prototype/server.py` | 公共静态文件白名单、JSON 大小/UTF-8/对象校验、图片魔数校验 |
| `prototype/tests/test_server.py` | 增加服务器安全边界测试 |
| `prototype/tests/test_vision_workers.py` | 缺少可选 `numpy` 时跳过对应测试，保持核心环境可验证 |

### 2.5 哪一侧必须明确胜出

`docs/roadmap.md` 应以远端为准。远端版本包含 PRD 导航和新的分阶段计划，本地版本
缺少这些信息。直接保留本地路线图会让已经确认的产品方向再次丢失。

`docs/README.md` 也应先采用远端版本，以保留产品需求、实施计划和验收标准的入口，
然后把本报告链接补回索引。

## 3. 安全整合方案

推荐顺序如下：

1. 在本地现状上建立 `codex/pre-sync-snapshot-20260716` 安全快照并提交，保证所有
   未提交内容可追溯。
2. 从 `origin/main` 新建 `codex/github-sync-20260716`，让远端 3 个提交成为干净基线。
3. 从快照只回放以下 7 个源码/测试/活文档文件：
   - `docs/architecture.md`
   - `docs/development-log.md`
   - `prototype/app.js`
   - `prototype/server.py`
   - `prototype/tests/app.test.js`
   - `prototype/tests/test_server.py`
   - `prototype/tests/test_vision_workers.py`
4. 同时带入本报告；`docs/README.md` 保留远端内容后，单独补入本报告的索引行。
5. 保留远端 `docs/roadmap.md`、PRD 三件套、词库原稿、审查材料和其余完全相同文件。
6. 修复第 5 节的 P0/P1 问题并补回归测试，通过完整验证后再合并回 `main`。

这样做的原因和效果是：

| 决策 | 原因 | 预期效果 |
| --- | --- | --- |
| 先快照，不直接 pull | 当前工作树有大量未提交且交叉修改 | 随时可以逐文件追溯或撤回，不丢本地加固 |
| 以远端为基线 | PRD、路线图和 471 条词库已经形成完整提交链 | 历史清晰，避免手工复制漏文件 |
| 只回放 7 个真实增量 | 其余本地文件要么与远端相同，要么远端更完整 | 把冲突面从整个工作树缩到可审查集合 |
| 先安全和数据基线，再扩功能 | 对抗测试已证明输入和本地服务边界仍不完整 | 后续配方、中文编辑和图片融合建立在可迁移、可恢复基础上 |

模拟目标树相对 `origin/main` 只保留上述 7 个文件的差异，共新增 253 行、删除 15 行；
`git diff --check` 通过，没有冲突标记。远端 PRD、路线图和词库在模拟中保持不变。

## 4. 怎么构建和验证

### 4.1 运行形态

Prompt Studio 没有 `package.json`，也不需要 npm 打包；它是原生 HTML/CSS/JS 加
Python 本地服务器。图片完整性验证复用 AnimaDex 虚拟环境中已声明的 Pillow 依赖。

首次环境准备：

```powershell
git submodule update --init --recursive
Push-Location .\AnimaDex
.\install.bat
.\.venv\Scripts\python.exe -m animadex genkey
Pop-Location
```

本地启动：

```powershell
.\start-local.ps1
# 不启动本地 LLM 时：
.\start-local.ps1 -SkipLlm
```

模型提示词发生变化时，使用同一 Python 环境重新导出运行时提示词：

```powershell
.\AnimaDex\.venv\Scripts\python.exe .\scripts\export_text_expand_prompt.py
```

完整验证应包含：

```powershell
.\AnimaDex\.venv\Scripts\python.exe -m unittest discover -s prototype\tests -p "test_*.py"
node --test prototype\tests\app.test.js
.\AnimaDex\.venv\Scripts\python.exe -m unittest discover -s scripts\tests -p "test_*.py"
```

本机系统 `PATH` 没有 Node，但 Codex 工作区自带的 Node 24.14.0 已完成等价验证。

### 4.2 初始整合模拟结果

| 验证项 | 结果 |
| --- | --- |
| Python `prototype/tests` | 发现 90 项，89 通过、1 跳过 |
| Node 前端测试 | 64/64 通过 |
| `scripts/tests` | 3/3 通过 |
| 提示词导出一致性 | 运行时与编译结果 SHA-256 均为 `780caf4357a69e546b2f53ac6cdf937e098e593d90cbddb7d8b42b65a3862bcc` |
| 数据副作用 | 未创建新主数据库，主数据库大小、时间和哈希未变化 |
| 依赖健康 | `pip check` 通过 |

远端原始版本在核心虚拟环境中会因 `test_vision_workers` 直接导入可选 `numpy` 而失败；
回放本地的可选依赖跳过逻辑后，完整发现过程可运行，表现为上述 1 项跳过。

### 4.3 最终实现验证

| 验证项 | 最终结果 |
| --- | --- |
| Python `prototype/tests` | 发现 131 项，130 通过、1 跳过 |
| Node 前端测试 | 74/74 通过 |
| `scripts/tests` | 4/4 通过 |
| 严格语法与工作树检查 | `py_compile`、`git diff --check` 通过 |
| 提示词导出一致性 | 自动回归通过，运行时提示词与编译审阅文件一致 |
| 依赖健康 | `pip check` 通过 |
| 数据副作用 | 主数据库大小、mtime、SHA-256 前后不变 |

跳过项只涉及可选 NumPy 的 WD14 预处理测试，不影响核心服务器、前端或本次安全边界。

### 4.4 启动脚本仍需收口

- `-AnimaDexPort` 目前只影响检测和上游 URL，不会自动改变 AnimaDex 实际监听端口；
  还需同步设置 `ANIMADEX_SERVER_PORT` 或配置文件。
- 启动脚本只判断端口是否被占用，没有验证占用者是不是预期服务或健康实例。
- 本地 LLM 路径写死在 `start-local.ps1`；自定义环境目前需要 `-SkipLlm` 后另行启动。
- 缺少统一的 PID 记录和停止命令。
- 视觉模型仍依赖本机 `H:` 路径和 ComfyUI 环境，尚未形成锁定版本的可复现安装。

## 5. 对抗式测试

### 5.1 修复前复现结果

- 现有服务器测试：23/23 通过。
- 自定义隔离后端场景：72 个；54 个符合加固预期，18 个暴露边界偏差。
- 另行验证了数据库 schema/error containment、前端权重、Unicode 去重和渲染出口。
- 测试前后工作树状态一致；只使用临时数据库、假 API Key 和 stub 模型引擎。

### 5.2 修复后最终对抗结果

最终矩阵在代码冻结后重新执行，覆盖真实本地 HTTP 线程、原始 socket、临时 SQLite、
前端纯函数以及模型 stub。业务测试不打开主数据库、不调用真实模型或外部 API；仅在
测试后只读取得主数据库文件大小、mtime 和 SHA-256 指纹。

| 验证层 | 最终结果 |
| --- | --- |
| HTTP 边界专项 | 24/24 通过，覆盖 Host/来源、静态白名单、framing、总时限、副作用时序、图片与精确路由 |
| 数据库专项 | 20/20 通过，覆盖 schema、严格 JSON/UTF-8、事务并发、旧 ID、重复收藏和敏感 metadata |
| 全量自动回归 | Python 131 项：130 通过、1 可选依赖跳过；Node 74/74；脚本 4/4 |
| Unicode 全 scalar 常规场景 | 1,112,064 个 scalar × 4 组 Python/JS 成对结果，共 4,448,256 组，0 差异 |
| Greek Final Sigma 上下文 | 286,719 个 Unicode 15 已分配 scalar × 4 组上下文，共 1,146,876 组，0 差异 |
| 生成与环境 | Unicode 表 `--check`、`py_compile`、`pip check`、`git diff --check` 全通过 |
| 数据副作用 | 77,824 字节；mtime `2026-07-09T15:02:25.2769031Z`；SHA-256 `88F597EF8094775F2449F8693806E99FA00FFD72EFC3782EF5CB273C03682395`，与初始指纹一致 |

对抗过程中先发现 Python Unicode 15 与 Node Unicode 17 对新分配码点有 92 个漂移，
随后又发现 Greek Final Sigma 的上下文属性有 5 个漂移。这两类问题均在最终结果前
修复：仓库现在生成并提交 707 个 Unicode 15 已分配区间和 1,433 条 Unicode 15
单码点 lowercase 映射；未分配字符作为规范化片段边界原样保留。修复后的上述两轮
穷举均为 0 差异。最终未发现残余 P0/P1。

剩余非阻断观察：当前敏感 metadata 过滤器有意偏保守，类似 `tokenCount` 的无害未来
字段也可能被剔除；若 metadata schema 扩展，应改用字段白名单或显式例外。数据库
schema version、迁移/备份和项目 UI 重开仍属于第 6 节 M0，不伪装成本次已完成能力。

### 5.3 P0：修复前的合并阻断项

下表保留修复前证据；这些项目现已修复并有自动回归。

| 问题 | 证据 | 为什么要改 | 修复后的效果 |
| --- | --- | --- | --- |
| 未校验 `Host`，设置接口返回密钥明文 | 恶意 `Host` 和 absolute-form URL 请求 `/api/settings` 均返回 200 及测试密钥 | 回环监听不能完全防住 DNS rebinding；绑定 `0.0.0.0` 时风险更高 | 非回环/非允许 Host 被拒绝，浏览器不能借域名读取本地秘密 |
| 状态修改接口不校验来源 | 恶意 `Origin` + `text/plain` 可成功触发 `/api/local-llm/start` | CORS 只限制读响应，不等于阻止跨站写操作 | 只接受同源 JSON 请求，本地进程动作不能被任意网页触发 |
| `HEAD` 绕过静态白名单 | 对 DB、`.env`、源码和提示词的 GET 为 404，HEAD 却为 200 并泄露大小 | 攻击者可探测敏感文件是否存在和尺寸 | HEAD 与 GET 共用公共文件白名单，不再形成旁路 |

P0 的具体改动建议：

1. 只允许 `127.0.0.1`、`localhost`、`::1` 和显式配置的 Host；非回环监听必须显式开启。
2. 对所有修改状态的接口校验 `Origin`/`Sec-Fetch-Site`，并强制
   `Content-Type: application/json`。
3. `/api/settings` 只返回“已配置”或掩码；PUT 未携带密钥时保留旧值，不再回显明文。
4. 显式实现 `do_HEAD`，复用 GET 的公共静态文件判断。

### 5.4 P1：修复前的输入、数据和前后端一致性

下表同样记录修复前状态。当前实现还补充了固定请求总时限、请求头连接超时、旧项目
ID 兼容、斜杠 slug 缩略图、重复收藏清理和通用 UTF-8 校验。

| 问题 | 已复现行为 | 建议修改 | 预期效果 |
| --- | --- | --- | --- |
| 请求 framing 不严格 | 负 Content-Length、短 body、冲突长度、chunked 可能被当空对象接受；慢 body 会占住线程 | 唯一且非负的长度、拒绝不支持的 Transfer-Encoding、精确读取、socket 超时、按路由限额 | 避免请求走样和线程耗尽 |
| JSON 接受非有限数 | `{"cfg": NaN}` 被接受，响应还能输出非标准 `NaN` | 解析时拒绝 NaN/Infinity，序列化使用 `allow_nan=False` | 浏览器和数据库只接收严格 JSON |
| 图片只看魔数 | 8 字节 PNG、3 字节 JPEG、12 字节 WEBP 可进入分析引擎 | 在字节/像素上限后用 Pillow `verify` 检查结构和格式 | 损坏图片不会进入模型或底层解析器 |
| 数据接口缺少 schema 和错误收口 | 非法版本或重复项目导致连接中断；`slash/id` 可写不可读；错误 blocks 类型可入库 | 路由 schema、ID/字段长度/枚举约束、服务端版本号、400/409/500 JSON 错误封装 | 数据可往返、冲突可解释、单次坏请求不打断连接 |
| 权重只受 UI slider 约束 | 导入值可为负数、99、150、百万并进入编译；非数值静默回落 | 所有入口统一校验有限数和范围，例如 0–120 | 导入、配方、数据库和 UI 行为一致 |
| 去重口径前后端不同 | 前端 `toLowerCase` 无法合并 `Straße/STRASSE`、组合/分解重音；不同 Unicode 版本还会在新分配码点和 Final Sigma 上下文产生漂移 | 只对 Unicode 15 已分配片段做 NFKC，随后使用生成的固定 Unicode 15 单码点 lowercase 表，再统一 White_Space/BOM 与 `ß→ss` | 当前 Python/Node 的 5,595,132 组穷举成对结果一致，预览、复制和运行时得到相同去重键 |
| 状态标签存在潜在 HTML 出口 | 未知 status 原样返回并进入 `innerHTML`/`title` | 状态白名单，未知值固定显示 `unknown`，或统一转义 | 将来后端或插件出现异常值时不形成 XSS sink |
| 缺少基础响应头 | 静态响应无 CSP、`X-Content-Type-Options`、Referrer-Policy | 增加适合本地应用的安全头 | 降低内容嗅探和意外外链泄露影响 |

普通敏感路径 GET 防护是有效的：对数据库、源码、提示词和 `.env` 的单/双编码、
`..`、编码斜杠、反斜杠、query、NUL 等 16 个路径场景均为 404 且未泄露正文。
无效 JSON、非 UTF-8、顶层非对象、超限 JSON/图片、MIME 与签名不一致、坏 Base64
和未知分析器也都被正确拒绝。这些通过项应保留为回归测试，不能在重构中退化。

## 6. 产品构建顺序

远端 PRD 给出的长期阶段可以保留，但近期应按以下三个里程碑构建：

### M0：整合与可信数据基线

要改：完成安全整合和 P0/P1；增加 schema version、迁移与备份；补项目列表、重开和
真实数据校验；在干净 clone 环境跑 CI。

为什么：现在“能写入 SQLite”不等于项目真正可恢复，也不等于未来 schema 可以安全
升级。安全边界和迁移能力是后续配方、编辑历史与资产库的共同底座。

效果：工作树与 GitHub 重新形成单一可追溯基线；坏请求可控；已有项目可验证地
保存、重开、迁移和恢复。

### M1：完整的 Anima 1.1 出图配方

要改：把当前“结构块配方”扩为完整生成配方，记录精确模型版本、LoRA、采样器、
scheduler、steps、CFG、分辨率、seed、denoise、参数来源和优先级；建立 Anima 1.1
模型 ID `3004063` 的版本化 profile，并提供一键复制/复用。

为什么：现有 recipe 主要保存提示词块，与 PRD 所说的可复现完整配方不是同一概念。

效果：一次成功出图能够被准确复现、比较和迁移，而不是只记住相似的提示词文本。

### M2：中文最小差异编辑

要改：实现“中文指令 → 识别受影响块和锁定块 → 前后 diff → 用户确认 → 写入审计
记录”；兼容地把 10 块演进到 PRD 的 13 块，再加入多角色卡和互动关系。

为什么：直接让模型重写整段提示词会破坏锁定内容，也很难解释变化来源。

效果：用户可以只改服装、镜头、光线等指定部分，未授权块保持不变，每次修改可
预览、确认和回退。

图像融合、LoRA 资产、来源研究和素材/备份阶段排在 M2 之后。`词库原稿/` 的 471 条
内容目前明确是原始素材，不应在 M0 直接接进随机逻辑；应等 M1 已有 seed 和来源记录
后，再加入可选 loader、格式/重复检查、固定 seed 回归测试和抽取结果持久化。否则只会
得到“更随机”，得不到“可复现”。

## 7. 文档与规范审计备注

- `CLAUDE.md` 是项目规则的单一来源；`AGENTS.md` 只有指向它的说明，没有规则冲突。
- `.gitignore` 未发现被跟踪的 `.env`、数据库、日志、PID、模型或虚拟环境，仓库文本
  未发现疑似真实密钥。
- 文档中的“配方”应区分“结构块配方”和“完整生成配方”，避免把当前能力写成已满足
  PRD。
- 修复前文档和代码的去重口径不一致；现在前后端与架构文档均使用 Unicode 15
  已分配片段 NFKC 和生成的 Unicode 15 lowercase 表，并统一 White_Space/BOM 折叠
  与 `ß→ss`。
- 项目总览和路线图不能把“有数据库表”表述成“已有可重开的项目库”。验收应以 UI
  可重开和迁移/恢复测试为准。
- 根目录来源材料中曾有 `safe` 词要求，而当前运行模型提示词按新 PRD 已移除；这是
  “来源材料”和“当前运行事实”的区分，不应把旧文档重新当作运行配置。

## 8. 完成定义

本次 GitHub 同步与边界加固只有同时满足以下条件，才视为完成：

- 整合分支只保留经过逐文件确认的本地增量，远端 PRD/路线图/词库无意外改写。
- P0 用例全部由“可复现”转为“被拒绝”，并加入自动回归。
- P1 的 schema、严格 JSON、请求 framing、图片验证、权重和 Unicode golden tests 通过。
- Python、Node、脚本测试和提示词导出一致性全部通过。
- 本地 `main` 只做快进整合，安全快照保留，GitHub 远端不被意外推送。
- 主数据库和真实模型不参与测试，前后指纹保持不变。

新 clone 启动验收、端口健康检查、PID/停止流程、数据库版本化迁移、备份和项目 UI
重开属于第 6 节 M0 产品建设的后续交付，不伪装成本次“同步已完成”。本轮已经兼容
读取和追加旧式项目 ID，但尚未引入完整 schema migration/backup 系统。

