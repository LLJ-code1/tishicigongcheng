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
按 `,，;；` 与换行拆项、casefold 跨块去重；块权重非 100% 时英文项
编译为 `(item:factor)`，0% 剔除整块。后端
`compile_normalized_blocks` 仅在 LLM 原始输出规范化时执行
（此时权重均为默认 100），两处拆项与去重规则保持一致；
若后端未来引入权重编译，需与前端语法对齐。

## 开发约定

- 前端展示和交互仍在 `prototype/app.js`。
- 后端 HTTP 入口仍在 `prototype/server.py`。
- SQLite 逻辑独立在 `prototype/db.py`。
- 模型调用提示词模板放在 `prototype/prompts/`，先按文生图和图生图分开。
- 每次实现明显功能后，更新 `docs/development-log.md`。
- 重要设计变化更新 `docs/architecture.md` 或 `docs/roadmap.md`。
- API Key 仅保存在本机 Prompt Studio SQLite；数据库文件不得分享或提交。
- 静态 HTTP 只提供工作台入口、脚本、样式和 `assets/`；`data/`、源码和提示词文件
  不得通过静态路径下载。所有 JSON API 仅接受 UTF-8 对象且请求体上限为 30 MB；
  图片分析还会校验 PNG/JPG/WEBP 文件签名与声明格式一致。
- 自动测试使用临时数据库和 mock provider，不依赖本机模型或外部网络。
