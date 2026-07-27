# Prompt Studio 构建与运行手册

## 创意总监 Skill 运维

设置抽屉的“创作总监 Skill”区域显示当前自定义覆盖。点击“保存 Skill”后从下一次导演
请求生效；点击“恢复默认”必须确认，确认后向设置接口写入空字符串。内置文件
`prototype/prompts/creative_director.md` 不会被界面改写。

若导演调用失败，先确认本地或外部文本提供商配置，再检查 Skill 覆盖是否要求了不受支持
的输出格式。自定义 Skill 不能放宽动作白名单、严格 JSON、密钥回显检测或 canonical
状态转换。排障时不要把 API Key、数据库、图片字节或本地模型路径复制进 Skill。

提交导演请求后，页面按“记录操作 → 准备参考信息 → 等待模型推理 → 解析并更新简报
→ 保存创作阶段”显示进度和已用时间。长时间停在“等待模型推理”说明请求已经交给本地
网关，正在等待所选文本提供商；停在后两个阶段则优先检查响应结构或工作区提交错误。
失败卡会保留具体卡住的阶段。该进度是浏览器侧可观察性，不代表后端任务可恢复或已
支持真正取消。

多图分析按图片独立运行；某张失败时在对应卡片重试，其他成功证据会保留。外部文本
提供商只收到最多八项的有界文字证据数组，原图始终留在本机。刷新后安全图片引用、
逐图用途和 brief 来源 `refId` 会恢复，但浏览器 `File` 对象不会持久化；界面只对缺失
文件的卡片提示重新附图。

Prompt Studio 是原生 HTML/CSS/JavaScript 加 Python 本地服务，没有 npm 打包步骤。
“构建”指初始化子模块、校验生成产物、运行测试并启动本地服务。

## 首次准备

```powershell
git submodule update --init --recursive
Push-Location .\AnimaDex
.\install.bat
Pop-Location
```

`AnimaDex\.venv\Scripts\python.exe` 是仓库默认的 Python 入口。Node.js 只用于前端
自动测试，工作台运行时不依赖 Node 或 npm。

## 启动

```powershell
.\start-local.ps1
```

多个 Git worktree/分支共用默认端口 `57913`。启动器会比较端口当前提供的 `app.js` 与
当前源码：若确认是旧的 Python `server.py`，会停止旧 Prompt Studio 后启动当前版本；
若占用者不是可识别的 Prompt Studio，则失败关闭并要求人工检查，不会结束未知进程。

不启动本地文本模型：

```powershell
.\start-local.ps1 -SkipLlm
```

在 Git worktree 中运行新版源码、但复用主目录的 AnimaDex 虚拟环境和本地模型启动脚本：

```powershell
.\start-local.ps1 -RuntimeRoot "H:\提示词工程"
```

`RuntimeRoot` 只提供 Python、AnimaDex 和本地 LLM 运行时；Prompt Studio 源码和数据库仍
来自执行该命令的当前 worktree，避免打开新版界面时切回旧数据目录。

默认服务：

| 服务 | 地址 |
| --- | --- |
| Prompt Studio | `http://127.0.0.1:57913/` |
| AnimaDex | `http://127.0.0.1:5000/` |
| 本地 OpenAI 兼容 LLM | `http://127.0.0.1:8080/v1` |

## 环境变量

| 变量 | 默认值 / 作用 |
| --- | --- |
| `PROMPT_STUDIO_DB` | `prototype/data/prompt_studio.db`；工作数据库 |
| `PROMPT_STUDIO_HOST` | `127.0.0.1`；监听地址 |
| `PROMPT_STUDIO_PORT` | `57913` |
| `ANIMADEX_URL` | `http://127.0.0.1:5000` |
| `ANIMADEX_TIMEOUT` | AnimaDex 上游读取超时，默认 3 秒 |
| `PROMPT_STUDIO_EXPERIMENTAL_WORDLISTS` | 设为 `1` 才允许确定性词库计划 |
| `PROMPT_STUDIO_ALLOW_NETWORK` | 非回环监听的第一道显式开关，设为 `1` |
| `PROMPT_STUDIO_ALLOWED_HOSTS` | 非回环监听时必须列出允许的 Host，逗号分隔 |
| `PROMPT_STUDIO_LLAMA_DIR` | 自定义 `llama.cpp` 目录 |
| `PROMPT_STUDIO_LLM_MODEL` | 自定义本地文本模型路径 |
| `PROMPT_STUDIO_LLAMA_SERVER` | 视觉 GGUF worker 使用的 `llama-server` |
| `PROMPT_STUDIO_VISION_PYTHON` | 视觉 worker Python 路径 |
| `PROMPT_STUDIO_VISION_MODELS` | 视觉模型根目录 |
| `PROMPT_STUDIO_FLORENCE_MODEL` | Florence 模型目录 |

个人实验词库启动示例：

```powershell
$env:PROMPT_STUDIO_EXPERIMENTAL_WORDLISTS = "1"
.\start-local.ps1
```

这不会把目录标记为已审核；界面仍显示“实验 / 待人工语义审核”。

非回环监听会扩大本地密钥和进程控制接口的攻击面，只有明确需要局域网访问时才同时
设置两个网络开关。普通个人使用保持默认回环地址。

## 生成产物

修改文本提示词模块后重新导出：

```powershell
.\AnimaDex\.venv\Scripts\python.exe .\scripts\export_text_expand_prompt.py
```

修改 `词库原稿/` 后重新编译：

```powershell
.\AnimaDex\.venv\Scripts\python.exe .\scripts\build_random_wordlists.py
```

只检查已提交产物是否过期：

```powershell
.\AnimaDex\.venv\Scripts\python.exe .\scripts\generate_unicode15_ranges.py --check
.\AnimaDex\.venv\Scripts\python.exe .\scripts\build_random_wordlists.py --check
```

## 完整验证

```powershell
.\AnimaDex\.venv\Scripts\python.exe -m unittest discover -s prototype\tests -p "test_*.py"
node --test prototype\tests\app.test.js
.\AnimaDex\.venv\Scripts\python.exe -m unittest discover -s scripts\tests -p "test_*.py"
git diff --check
```

前端测试需要 Node 24；CI 会在 Windows 干净环境安装 Python 3.12、Node 24 和固定版本
Pillow，并执行三组测试及三个生成物一致性检查。

自动测试必须使用临时数据库。不要把 `PROMPT_STUDIO_DB` 指向真实库后再启动测试
服务器；测试不依赖真实模型、外部 API 或 AnimaDex 完整数据。

## 备份操作

工作台首页可导出全库备份，项目页可导出当前作品。恢复流程固定为：

1. 选择备份文件。
2. 调用 inspect 完成格式、Hash 和边界检查。
3. 明确确认后恢复到 `.prompt-studio-recovery/restore-preview-*.db`。
4. 检查响应必须为 `activated=false`，在隔离库中人工核验项目和版本。

当前产品不会自动替换正在使用的数据库。这是故意的安全边界；如需切换隔离库，应先
停止服务、保留当前库副本，再由人工设置 `PROMPT_STUDIO_DB` 指向已核验的隔离库启动。

逻辑备份不包含 API Key、接口 URL、模型/LoRA/checkpoint 文件、资产图片或其他二进制
文件。全库备份会包含 `resources` 中的 LoRA-lite 元数据和版本 Recipe 中的选择快照。
需要迁移本机模型资产时，另行复制受管理资产目录并核对 Hash；不要把模型文件塞进
逻辑备份 ZIP。

### LoRA-lite 操作与恢复

LoRA-lite 页面只维护名称、版本、HTTPS 原始链接、触发词、建议权重、备注、兼容模型
和冲突档案。它不会扫描、下载、移动或删除 LoRA 文件。出现
`lora_profile_conflict` 时刷新目录，核对服务端最新档案后重新编辑，不要盲目覆盖。

删除档案前界面会确认；删除只影响当前目录，已经保存到 Recipe 的 LoRA 快照仍会随
项目重开和逻辑备份恢复。恢复核验时同时检查：目录中现存档案可读取、历史版本中的
`recipe.loras` 未被当前目录覆盖、缺失目录项在页面标记为“历史快照”。

### 官网模型研究的备份与恢复

研究运行、snapshot、claim 和档案版本保存在工作数据库的
`model_research_runs`、`model_evidence_snapshots`、`model_evidence_claims` 和
`model_profile_versions`。当前产品逻辑 ZIP 尚未导出这四张表；要保留研究证据链，
必须在服务停止后对整个 SQLite 文件做文件级备份，或使用 SQLite backup API 生成一致
副本。不要只复制 `-wal`/`-shm`，也不要在服务写入时直接复制主文件。

文件级恢复先复制到新的隔离路径，设置 `PROMPT_STUDIO_DB` 指向该副本启动并核对：

1. `PRAGMA integrity_check` 和 schema 初始化通过。
2. snapshot/claim 数量与研究运行相符。
3. 档案版本的 `content_sha256` 校验通过，父版本链完整。
4. 每个 `profile_id` 最多一个 active 版本。
5. `GET /api/model-profiles` 能读取活动版本。

恢复核验前不要替换当前数据库；这与逻辑恢复的 `activated=false` 原则一致。研究页面
提取文本可能包含第三方内容，备份按敏感本机资料保管，不提交 Git。

### 官网研究故障恢复

- `unsupported_source`：换成支持的官网原始模型页，或继续使用已有模型。
- `unsafe_address` / DNS 策略拒绝：transport 没有发送请求；检查 URL 和 DNS，不关闭
  SSRF 防线。
- timeout、抓取或解析失败：保留 `pending_verification` 草稿，显式重试，或只人工
  补充精确模型身份；未验证优化继续留空。
- 证据不完整：不批准缺少证据的 claim；工作台继续使用通用规则和可见 warning。
- `active_version_changed`：刷新活动档案，核对将被替换的版本后重新确认；不要原样
  自动重试旧 CAS。
- `profile_hash_mismatch` 或版本链异常：停止审核/激活，保留数据库和日志上下文，
  从隔离的完整 SQLite 副本恢复，不手工改 JSON/Hash。

## 故障排查

- 首页打不开：检查 57913 端口以及服务进程输出。
- 页面像旧版：重新运行目标 worktree 的启动器；若它拒绝替换端口占用者，检查 57913
  当前监听进程，确认来源后再人工处理。
- AnimaDex 显示离线：检查 5000 端口和 `ANIMADEX_URL`。
- 文本生成失败：先用设置中的“测试”或检查 8080 `/v1/models`。
- 创作总监点击后像没反应：查看请求进度卡。停在“等待模型推理”时测试所选提供商；
  失败时按卡片显示的阶段检查图片分析、模型 JSON 或 workspace commit，不要重复盲点。
- 多图卡片显示“需重新附图”：这是刷新/重开后的安全边界，不是引用丢失。按卡片原文件名
  重新选择本地文件；不要把路径、base64 或 blob URL 写入项目 metadata。
- 某张图分析失败：只点该卡片“重试”；若仍失败，检查图片是否为 PNG/JPEG/WebP、
  不超过 20 MiB 和 5,000 万像素，并检查对应本地识图模型状态。不要删除其他成功卡片。
- brief 来源不符：核对条目的 `source.refId` 是否指向 canonical `inputs.images` 的
  稳定 ID；不要根据条目文案或当前卡片顺序猜测来源。
- 官网研究被拒：先看返回 code；策略拒绝不会产生外部请求，抓取失败则保留待验证
  草稿。不要把页面正文、Cookie、API Key 或任意 URL 直接写入数据库绕过 adapter。
- 确定性计划返回 409：确认实验环境变量在启动 Python 服务的同一进程环境中。
- 备份恢复冲突：默认 `reject` 不写入；确认需要保留两份时才使用 `rename`。
- 数据库被判定为未来版本、损坏或错误应用：停止操作，不要强行降级；使用隔离备份
  检查和人工恢复流程。
