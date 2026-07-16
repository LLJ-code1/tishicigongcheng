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

## 数据边界

AnimaDex 负责：

- 角色搜索
- 画师搜索
- 缩略图
- 原始英文触发词和标签

Prompt Studio 负责：

- 作品
- 提示词版本
- 收藏
- 我的素材
- 参考提示词库
- 设置
- 中文释义缓存
- 图片解析记录

## SQLite 覆盖库

默认数据库路径：

```text
prototype/data/prompt_studio.db
```

第一阶段表：

- `projects`：作品记录。
- `prompt_versions`：作品提示词版本。
- `resources`：用户自建素材和后续参考库资源。
- `favorites`：角色、画师、参考提示词等收藏。
- `settings`：用户偏好和本地路径。

## API 约定

读取与持久化：

- `GET /api/projects`
- `POST /api/projects`
- `GET /api/projects/<id>`
- `POST /api/projects/<id>/versions`
- `GET /api/favorites`
- `POST /api/favorites`
- `DELETE /api/favorites/<id>`
- `GET /api/settings`
- `PUT /api/settings`
- `GET /api/prompt-templates`
- `GET /api/prompt-templates/<id>`
- `PUT /api/prompt-templates/<id>`

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

## HTTP 与数据边界

- 默认只监听回环地址；请求 `Host` 只接受 `127.0.0.1`、`localhost`、`::1` 和显式
  配置值。非回环监听必须同时设置 `PROMPT_STUDIO_ALLOW_NETWORK=1` 与
  `PROMPT_STUDIO_ALLOWED_HOSTS`。
- POST、PUT、DELETE 拒绝跨站 `Origin`/`Sec-Fetch-Site`，并要求 UTF-8
  `application/json`。普通 JSON 请求体上限 1 MiB，图片分析 JSON 上限 30 MiB。
- 请求只允许唯一、非负的 `Content-Length`，拒绝 `Transfer-Encoding`；连接建立后
  请求头同时受空闲超时和固定总时限约束，请求体精确读取并使用固定总时限，慢速滴流
  不能延长 deadline。普通 JSON 正文总时限为 2 秒，30 MiB 视觉正文使用独立的 30 秒
  时限。JSON 拒绝重复键、NaN、Infinity 和非对象顶层。
- GET 与 HEAD 共用静态白名单，只提供工作台入口、脚本、样式和 `assets/`。响应增加
  CSP、`X-Content-Type-Options`、`X-Frame-Options` 与 Referrer-Policy。
- 图片先限制 20 MiB 和 5,000 万像素，再用 Pillow 完整验证和解码 PNG、JPEG、WEBP；
  只有完整文件才进入视觉 worker。
- 项目、版本和收藏在数据库层校验 ID、类型、字段长度、结构块形状与权重；版本号
  由服务端分配。校验失败返回 400、唯一性冲突返回 409、未知错误返回固定 JSON 500。
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
- 模型调用提示词模板放在 `prototype/prompts/`，先按文生图和图生图分开。
- 每次实现明显功能后，更新 `docs/development-log.md`。
- 重要设计变化更新 `docs/architecture.md` 或 `docs/roadmap.md`。
- API Key 仅保存在本机 Prompt Studio SQLite；数据库文件不得分享或提交。
- `data/`、源码和提示词文件不得通过静态路径下载；HTTP 与数据输入必须遵守上面的
  Host、同源、严格 JSON、分路由大小限制和完整图片验证约定。
- 自动测试使用临时数据库和 mock provider，不依赖本机模型或外部网络。
