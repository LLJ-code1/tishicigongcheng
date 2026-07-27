# 架构说明

## 当前架构

```text
浏览器
  ↓
Prompt Studio 静态前端
  ↓
Prompt Studio 本地后端 :57913
  ├─ Prompt Studio SQLite
  ├─ AnimaDex 本地服务 :5000
  ├─ OpenAI 兼容文本服务（本地或外部）
  └─ 本地视觉 worker（WD14 / Florence / JoyCaption / Qwen3-VL）
```

## 统一创意导演工作流

统一首页、创意导演调用、单图本地证据、需求卡、模型选择闸门和工作台交接已经接通。
数据流如下：

```text
统一文字/多图输入 UI（0–8 张安全引用）
  -> 每张原图独立 POST /api/vision/analyze（仅本地）
  -> POST /api/creative-intake/director（模型仅提出候选动作）
  -> POST /api/creative-intake/transition（服务器权威转换）
  -> prototype/creative_intake.py（规范化、阶段校验、锁与冲突规则）
  -> 前端接收服务器规范化后的会话
  -> POST /api/workspace/commit
  -> projects.metadata_json.creativeIntake
```

`creative_intake.py` 是创意意图状态转换的唯一权威：调用方可提交一个动作，但
不能自行确认阶段或绕过已锁定的 brief 条目。服务端返回完整的规范化会话；当前前端
状态层已实现会话 normalizer、项目保存/恢复和多维过期响应 guard。模型返回的展示文案
不是真实状态；只有经过 `creative_intake.py` 校验的动作才能改变 canonical session。

前端另维护临时的 `directorProgress`，把一次导演调用展示为记录操作、准备参考信息、
等待模型推理、解析并更新简报和保存创作阶段，并计算已用时间。它不进入 canonical
session、项目 metadata 或 Recipe，也不是服务端返回的任务进度；刷新后无需恢复，当前
仍不能据此中断已经提交的后端推理。

会话仍跟随现有项目元数据，通过 `workspace/commit` 的同一原子事务保存，因此不会
单独增加 SQLite 表。项目重新打开、逻辑备份和隔离恢复
会保留该元数据；历史项目缺少或带有无效 `creativeIntake` 时，前端安全地回退为空会话。

图片字节只发送给本地 `/api/vision/analyze`。浏览器以稳定图片 ID 管理最多八个独立
`File`/object URL；canonical session 只保存同序的安全引用。每张图独立分析和重试，
单图失败不会丢弃其他成功证据。`/api/creative-intake/director` 的 `imageEvidence`
始终是按 canonical 图片顺序排列的数组，外部文本提供商最多接收有界文字证据。
brief 借用项通过 `source: {"type":"image","refId":"<图片 ID>"}` 追溯来源。

刷新、项目重开或逻辑恢复会保留安全引用、逐图 `requestedUses` 和 brief `refId`，但
浏览器 `File`、object URL、图片字节和分析缓存不持久化；仅缺少本地文件的卡片提示
重新附图。任何 path、base64、blob URL 或额外图片字段都在进入 canonical 状态前拒绝。
设置中的 `creativeDirectorSkillOverride` 是内置只读 Skill 的可选覆盖层，最大
100,000 字符；空字符串表示恢复默认。官网原始链接研究和不可变模型档案生命周期已
接通。确认 brief 选择模型后，`model_adapted_decomposition.py` 只读取当前激活的精确
档案版本/Hash 和已批准 claim，生成语义层与模型适配层分离的十三块；逐块审核确认后
一次投影到工作台和 Recipe。LoRA-lite 以用户明确登记的 HTTPS 原始链接、版本、触发词、
建议权重、兼容模型与冲突记录为边界；选择必须由用户触发，切换模型会清空选择并重新确认。

拆解异步结果同时绑定项目修订、会话修订、模型 ID、档案版本和内容 Hash。更改 brief
或模型会清除当前拆解并把 Recipe 标记为 stale；历史 Recipe 保持不可变。确认交接会把
brief 条目、图片 ID、claim ID 和 evidence ID 写入 `sourceRefs`，不会重新生成语义。

## 官网模型研究边界

官网研究是独立于工作区保存的资料库边界：

```text
受支持的 https 原始模型页
  -> SourceAdapter 注册表（当前仅 Civitai 模型页）
  -> URL / DNS / 每跳重定向校验
  -> 有界 fetcher（最多 5 次重定向、2 MiB、15 秒、UTF-8 HTML/JSON）
  -> 不可变 snapshot + proposed claims
  -> draft 修订 -> reviewed 子版本
  -> compare-and-swap 激活
  -> GET /api/model-profiles 的统一模型档案目录
```

它不是任意 URL 抓取器。入口拒绝非 HTTPS、凭据、fragment、非默认端口、
localhost、IP 字面量、不受支持 host/path，以及任一非 global DNS 结果；重定向后的
URL、adapter 所有权和 DNS 会逐跳重验。自动测试只注入 fake resolver/fetcher/clock，
不访问真实网络。

每个结论都是带 `original_source`、`supplemental_source`、`ai_inference` 或
`local_validation` 证据类别的 claim；本阶段 HTTP 入口只抓取注册的原始页面，不接受
客户端上传补充 URL、页面正文或伪造证据类别。只有人工明确 `approved` 的 claim 才能
投影到档案；抓取失败仍形成 `pending_verification` 草稿，用户可继续选择已有模型和
使用通用规则。

snapshot、claim 内容和档案版本正文均追加式保存。修订和审核创建带
`parent_version_id` 的新版本；激活只改变生命周期指针，并用预期活动版本做 CAS。
活动研究档案经过与内置档案兼容的校验后合并进 `/api/model-profiles`。历史 Recipe
保存精确 `profileVersionId`、`profileContentSha256` 和档案快照；以后激活新版本不会
回写历史作品。

## 数据边界

AnimaDex 负责：

- 角色搜索
- 画师搜索
- 缩略图
- 原始英文触发词和标签

Prompt Studio 当前已持久化：

- 作品及提示词版本；新版本内嵌完整 Recipe v1、十三块、参数来源、随机轨迹和中文
  修改/撤销历史
- 角色、画师收藏和结构块配方收藏
- 设置
- `resources.type=lora_profile` 的 LoRA-lite 元数据档案；Recipe 内嵌当次选择快照，目录
  条目以后被编辑或删除也不会改写历史版本

当前仅存在于前端会话内存：

- “新建资源”创建的自建素材
- 单结构块“保存为资源”的结果

`resources` 表中的其他自建素材、参考提示词库、中文释义缓存和可检索图片解析记录仍属
预留/目标能力；LoRA-lite 已接通不代表这些资源类型也已完成。

`词库原稿/` 负责人工维护随机元素，`scripts/build_random_wordlists.py` 负责严格校验并
生成可重建审查目录。`GET /api/text/random-catalog` 可读目录；实验开关开启时，
`POST /api/text/random-plan` 使用 `sha256-counter-v1` 产生确定性计划并写入 Recipe。
目录仍标记为待人工语义审核；既有 `POST /api/text/random` 继续使用文本模型生成独立
随机蓝图。

逻辑备份模块只读取 Prompt Studio 数据记录，输出带 manifest、大小和 SHA-256 的 JSON
或 ZIP。HTTP 恢复只写 `.prompt-studio-recovery/` 隔离数据库，不替换当前库；LoRA-lite
元数据作为 `resources` 记录进入全库备份，但模型/LoRA 文件、图片二进制、API Key 和
内部幂等账本不进入备份。

## SQLite 覆盖库

默认数据库路径：

```text
prototype/data/prompt_studio.db
```

数据库使用 `PRAGMA user_version = 2` 和 Prompt Studio 专用
`application_id` 标记结构。服务启动时只初始化一次：

1. 空库直接创建 v2，不生成人工故障回滚副本。
2. 未登记版本的旧库先做完整性、表、字段、索引、唯一约束和外键检查；额外的
   trigger/view 也视为不兼容结构，在任何备份或写入前拒绝。
3. 兼容旧库通过 SQLite backup API 写入 `.prompt-studio-recovery/`，验证人工故障
   回滚副本后才迁移并登记 v2。
4. 未来版本、错误应用标识、残缺或损坏文件会在写入前拒绝。

该人工故障回滚副本可能包含本机 API Key，已被 Git 忽略，只用于迁移故障时由用户
手动回滚；它不可分享，也不等同于经过过滤、检查和隔离恢复的产品逻辑备份。

第一阶段表：

- `projects`：作品记录。
- `prompt_versions`：作品提示词版本。
- `resources`：`type=lora_profile` 已用于 LoRA-lite；其余自建素材和参考库资源仍预留。
- `favorites`：角色、画师和结构块配方等已接入收藏。
- `settings`：用户偏好、本地路径及使用保留前缀的内部幂等账本。账本不会由设置 API
  返回，也不会进入逻辑备份。
- `model_research_runs`、`model_evidence_snapshots`、`model_evidence_claims`：
  官网研究运行、不可变来源快照和证据结论。
- `model_profile_versions`：追加式模型档案版本链；局部唯一索引保证每个 profile
  最多一个 `active` 版本。

## API 约定

读取与持久化：

- `POST /api/creative-intake/transition`
- `POST /api/creative-intake/decomposition-preview`
- `POST /api/workspace/commit`（当前工作台原子保存入口）
- `GET /api/projects`
- `POST /api/projects`
- `GET /api/projects/<id>`
- `PUT /api/projects/<id>`
- `POST /api/projects/<id>/versions`
- `GET /api/favorites`
- `POST /api/favorites`
- `DELETE /api/favorites/<id>`
- `GET /api/settings`
- `PUT /api/settings`
- `GET /api/prompt-templates`
- `GET /api/prompt-templates/<id>`
- `PUT /api/prompt-templates/<id>`

完整配方与模型档案：

- `GET /api/model-profiles`
- `GET /api/model-profiles/<id>`
- `POST /api/model-research`
- `POST /api/model-research/by-hash`（仅 SHA-256 的 Civitai by-hash 证据查询；失败关闭，AIR 只保留为外部证据）
- `GET /api/model-research/<runId>`
- `GET /api/model-profile-versions/<versionId>`
- `PUT /api/model-profile-versions/<versionId>`
- `POST /api/model-profile-versions/<versionId>/review`
- `POST /api/model-profile-versions/<versionId>/activate`
- `GET|POST /api/lora-profiles`
- `GET|PUT|DELETE /api/lora-profiles/<id>`
- `POST /api/recipe/resolve`
- `POST /api/generation-results/inspect`（只读解析 PNG 中的 A1111/ComfyUI 元数据并返回 Recipe 字段差异；不触发生成或保存）
- `GET /api/managed-assets`、`POST /api/managed-assets/import`、`POST /api/managed-assets/<id>/unlink`、`DELETE /api/managed-assets/<id>?confirmLocalFileDeletion=1`（本地 PNG 资产、引用和显式删除）
- `POST /api/experiments/matrix/plan`（仅生成可复现实验参数计划，不触发出图）
- `POST /api/recipe/roles/edit`（仅对显式 Recipe v2 的一个未锁定角色做局部预览；有关联关系时必须显式确认）
- `POST /api/recipe/relationships/edit`（仅在显式确认后编辑 Recipe v2 的角色互动关系）
- `GET /api/projects?q=&status=&modelId=&loraId=`、`GET /api/project-insights`（项目筛选和项目页展示的历史使用候选，统计不改默认值）

确定性词库与 AI 局部编辑：

- `GET /api/text/random-catalog`
- `POST /api/text/random-plan`
- `POST /api/text/edit-preview`
- `POST /api/text/edit-apply`
- `POST /api/text/edit-undo`

`edit-preview` 使用已配置的 OpenAI 兼容文本服务理解完整十三块和中文指令，严格校验
模型返回的受影响块、双语完整内容与中文原因。服务端以进程内 HMAC 签名预览；
`edit-apply` 不重复调用非确定性模型，只合并签名覆盖的块。模型不可用或输出无效时
失败关闭，不调用旧确定性词典兜底。

逻辑备份：

- `GET /api/backups/export?scope=full`
- `GET /api/backups/export?scope=project&projectId=<id>`
- `POST /api/backups/inspect`
- `POST /api/backups/stage-restore?conflict=reject|rename`

文本模型：

- `POST /api/text/expand`
- `POST /api/text/random`
- `POST /api/text/decompose`
- `POST /api/text/regenerate-block`
- `POST /api/text/regenerate-blocks`
- `POST /api/text/translate-pending`
- `POST /api/text/provider-test`
- `GET /api/local-llm/status`
- `POST /api/local-llm/start`
- `POST /api/local-llm/stop`

图片分析：

- `GET /api/vision/status`
- `POST /api/vision/analyze`
- `POST /api/vision/cancel`（按服务端 taskId 终止当前本地识图 worker；不把共享 llama.cpp HTTP 推理伪称为可取消）

识图响应还会携带仅供参考的构图字段：景别、视角、主体位置、景深和光影。它只从原始识图文本中匹配这些字段，不回写角色、服装或场景内容到提示词。检测到多人标签时，前端仅展示当前模型档案中已经验证的尺寸预设，且不会自动套用。

当工作台已有英文正向提示词时，识图响应还会返回提示词命中诊断。它只对逗号分隔的字面标签做已确认／不确定／冲突／未观察到的比较；结果始终是辅助证据，不能改写 Recipe 或替代人工判断。

个人资源继续复用 `favorites` 存储：普通单块片段使用 `snippets`，目标块为 `negative` 的命名资源使用 `negative_presets`。两类资源都可插入相应结构块，并随既有逻辑备份导出和隔离恢复，不新增图片二进制或第二套资源库。
“存为项目模板”同样复用 `snippets`：其 metadata 保存版本化的启用结构块 ID 集合与参数预设。用户明确带入模板后，结构块仍作为待应用修改；参数仅以受限的手动预设写入当前工作区，不会从历史统计自动套用。

`POST /api/recipe/resolve` 同时返回非阻塞诊断：标签数量、构图互斥提示和已知抽象词；
只有配置 `PROMPT_STUDIO_TOKENIZER_PATH` 且可由本机离线加载目标模型 tokenizer 时才返回真实 token 计数；其他情况明确返回不可用，不以估算值伪装精确 token 数。
同一诊断还会在主体标签能唯一定位时返回其前缀的真实 token 数。工作台的“主体前移”必须先取得该证据，随后只把 `subject` 结构块移到待应用顺序；它不自动改写或保存 Recipe。

AnimaDex 网关：

- `GET /api/animadex/status`
- `GET /api/animadex/resources`
- `GET /api/animadex/thumb/<type>/<slug>`

## 前端编译约定

结构块到最终提示词的编译在前端 `app.js` 的 `compileBlocks` 完成：
按 `,，;；` 与换行拆项，使用
`Unicode 15 已分配片段 NFKC → 固定 Unicode 15 单码点 lowercase 表 → Unicode
White_Space 与复制 BOM 折叠并去除首尾空白 → ß→ss` 规范化键跨块去重。Unicode 15
未分配的码点作为片段边界并原样保留；已分配范围和 1,433 条 lowercase 映射均由
`scripts/generate_unicode15_ranges.py` 同步生成前后端表，避免浏览器 ICU 与 Python
Unicode 数据版本不同而产生不同去重键。
块权重非 100% 时英文项
编译为 `(item:factor)`，0% 剔除整块。后端
`compile_normalized_blocks` 仅在 LLM 原始输出规范化时执行
（此时权重均为默认 100），两处拆项与去重规则保持一致；
若后端未来引入权重编译，需与前端语法对齐。

结构块权重在前端导入、配方、版本恢复和数据库写入各入口统一限制为 0–120；
画师混合器保留独立的 10–150 范围。未知模型状态统一降级为 `unknown`，不把服务端
原值直接拼入 HTML class、正文或 title。

## 项目保存与重开

- 首页项目列表读取 `GET /api/projects` 的 `versionCount` 和 `latestVersion` 摘要，并可将名称、状态、模型 ID、LoRA ID 作为只读查询参数；
  空库、加载中、失败和正常列表分别呈现。
- 打开项目时读取 `GET /api/projects/<id>` 的全部持久化版本，以服务端版本号作为唯一
  权威。生成和本地应用修改只标记“待保存”，不提前伪造版本号。
- 当前前端保存前先分配 `operationId`、项目 ID 和可选版本 ID，冻结完整正文，并把
  pending journal 写入 `localStorage`。`operationId` 同时作为 `Idempotency-Key`。
- `POST /api/workspace/commit` 在一个 `BEGIN IMMEDIATE` 中校验项目更新时间、最新版本、
  完整 Recipe、幂等 Hash，再创建/更新项目头、追加可选版本并写入成功账本。任何一步
  失败都会整体回滚，不存在“版本成功但项目头失败”的半保存。
- 数据库已提交但 HTTP 响应丢失时，启动恢复或用户重试会发送相同正文和键，服务端
  重放第一次成功响应。相同键配不同正文返回 `409 idempotency_conflict`；账本损坏或
  引用不存在数据时失败关闭。多个相同并发请求只产生一个版本。
- 旧 `POST /api/projects`、`PUT /api/projects/<id>` 和版本 POST 继续提供独立幂等键及
  CAS 兼容；新前端不再用多个端点拼一次保存。
- 从历史版本继续时只在前端载入旧内容并记录 `restoredFromVersion`，保存成功后才由
  服务端生成新的连续版本。
- 项目头 metadata 与最新版本基线一致时，重开尊重项目头保存的 text/image 模式和
  草稿；项目头缺失或过期时才用最新版本来源兜底，避免“新草稿 + 旧模式”混合恢复。
- 创意意图会话保存在项目 `metadata.creativeIntake`。其修改同样标记项目为待保存；
  即使本次没有创建提示词版本，`POST /api/workspace/commit` 也会原子保存该项目元数据。
  打开项目时，前端仅接受当前项目元数据中的规范会话；缺失或不合规的历史值不会写回
  数据库，而是回退为空会话。
- 保存、项目打开和项目列表各自有请求去重/过期响应保护。文本生成、图片分析、翻译、
  单块/多块再生成等工作区异步请求还会捕获会话 ID、工作区修订和项目修订；同一项目
  中请求发出后继续编辑，也不会让迟到结果覆盖新内容，丢弃结果时会同步清理加载态。
  切换、新建或打开失败时也会解除旧任务加载态；真正替换工作区时清除上一项目的识图
  raw/error 和悬浮 Tag 补全 DOM 引用，但保留分析器安装状态与选择配置。
  离开有未保存内容的工作区前需要明确确认。刷新或关闭浏览器时已通过
  `beforeunload` 触发浏览器原生确认；站内新建/切换作品使用应用内确认。
- AI 中文编辑应用和撤销只产生新的 Recipe；前端随后走同一原子保存路径，历史版本和
  `instructionHistory` 都追加，不原地删除旧记录。

## HTTP 与数据边界

- 默认只监听回环地址；请求 `Host` 只接受 `127.0.0.1`、`localhost`、`::1` 和显式
  配置值。非回环监听必须同时设置 `PROMPT_STUDIO_ALLOW_NETWORK=1` 与
  `PROMPT_STUDIO_ALLOWED_HOSTS`。
- POST、PUT、DELETE 拒绝跨站 `Origin`/`Sec-Fetch-Site`。普通写接口要求 UTF-8
  `application/json`，正文上限 1 MiB；图片分析 JSON 上限 30 MiB。备份上传接受
  `application/zip`、`application/json` 或 `application/octet-stream`，上限 64 MiB。
- 请求只允许唯一、非负的 `Content-Length`，拒绝 `Transfer-Encoding`；连接建立后
  请求头同时受空闲超时和固定总时限约束，请求体精确读取并使用固定总时限，慢速滴流
  不能延长 deadline。普通 JSON 正文总时限为 2 秒，30 MiB 视觉正文使用独立的 30 秒
  时限。JSON 拒绝重复键、NaN、Infinity 和非对象顶层。
- GET 与 HEAD 共用静态白名单，只提供工作台入口、脚本、样式和 `assets/`。响应增加
  CSP、`X-Content-Type-Options`、`X-Frame-Options` 与 Referrer-Policy。
- 路由前会拒绝 query 之前任意路径段中的字面 `;` 参数，且 GET、HEAD、POST、PUT、
  DELETE 使用同一入口校验；URL 编码后的 `%3B` 仍可作为历史 ID 数据，query 中的
  分号也不会被误拒绝。
- 图片先限制 20 MiB 和 5,000 万像素，再用 Pillow 完整验证和解码 PNG、JPEG、WEBP；
  只有完整文件才进入视觉 worker。
- 备份上传使用独立 30 秒总读取时限；检查 ZIP 路径穿越、重复/加密/非普通成员、文件
  数量、单文件和总解压大小、压缩比、严格 JSON、manifest 和 SHA-256。无效包在当前库
  或隔离库写入前拒绝。
- 项目、版本和收藏在数据库层校验 ID、类型、字段长度、结构块形状与权重；版本号
  由服务端分配。校验失败返回 400、缺少项目更新前置条件返回 428、唯一性或并发基线
  冲突返回 409、未知错误返回固定 JSON 500。
- 收藏的 AnimaDex 原始 ID 可以包含斜杠等真实 slug 字符；对外删除使用服务端生成的
  `favorite-<hash>` 路由 ID，删除会清理历史重复行。缩略图路由允许单段 URL 编码的
  斜杠 slug，同时拒绝控制字符与 `.`/`..` 路径段。
- 新建项目的显式 ID 只允许安全单段字符；历史数据库中含空格、Unicode 或斜杠的 ID
  仍可通过单段 URL 编码形式读取和追加版本，避免“列表可见但无法打开”。
- `/api/settings` 永不回显 API Key，只返回空值与 `*Configured` 标记；PUT 省略或留空
  密钥表示保留旧值。项目 metadata 和历史记录读取也会剔除密钥字段。

## 开发约定

- 前端展示和交互仍在 `prototype/app.js`。
- 后端 HTTP 入口仍在 `prototype/server.py`。
- SQLite 逻辑独立在 `prototype/db.py`。
- 创意意图规范、校验和服务器权威转换在 `prototype/creative_intake.py`；HTTP 路由仍由
  `prototype/server.py` 注册。
- Recipe/模型档案、词库 sampler、中文编辑和逻辑备份分别在 `prototype/recipe.py`、
  `model_profiles.py`、`random_sampler.py`、`edit_engine.py` 与 `backup.py`。
- 官网抓取策略/证据投影与不可变档案仓储分别在 `prototype/model_research.py` 和
  `prototype/model_profile_store.py`；生产 transport 仍通过 `server.py` 注入。
- 模型调用提示词模板放在 `prototype/prompts/`，先按文生图和图生图分开。
- 每次实现明显功能后，更新 `docs/development-log.md`。
- 重要设计变化更新 `docs/architecture.md` 或 `docs/roadmap.md`。
- API Key 仅保存在本机 Prompt Studio SQLite；数据库文件不得分享或提交。
- `data/`、源码和提示词文件不得通过静态路径下载；HTTP 与数据输入必须遵守上面的
  Host、同源、严格 JSON、分路由大小限制和完整图片验证约定。
- 自动测试使用临时数据库和 mock provider，不依赖本机模型或外部网络。
- GitHub Actions 已配置为在 Windows 干净环境运行 Python、Node、脚本测试以及
  Unicode、文本提示词和随机词库生成产物一致性检查；首次线上 workflow run 仍待验证。

具体请求示例见 [API 接入指南](integration-guide.md)，启动、环境变量、回归和恢复操作见
[构建与运行手册](operator-runbook.md)。
