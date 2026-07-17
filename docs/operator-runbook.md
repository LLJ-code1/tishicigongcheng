# Prompt Studio 构建与运行手册

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

不启动本地文本模型：

```powershell
.\start-local.ps1 -SkipLlm
```

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

逻辑备份不包含 API Key、接口 URL、模型、LoRA、checkpoint、资产图片或其他二进制
文件。需要迁移这些本机资产时，另行复制受管理资产目录并核对 Hash；不要把模型文件
塞进逻辑备份 ZIP。

## 故障排查

- 首页打不开：检查 57913 端口以及服务进程输出。
- AnimaDex 显示离线：检查 5000 端口和 `ANIMADEX_URL`。
- 文本生成失败：先用设置中的“测试”或检查 8080 `/v1/models`。
- 确定性计划返回 409：确认实验环境变量在启动 Python 服务的同一进程环境中。
- 备份恢复冲突：默认 `reject` 不写入；确认需要保留两份时才使用 `rename`。
- 数据库被判定为未来版本、损坏或错误应用：停止操作，不要强行降级；使用隔离备份
  检查和人工恢复流程。
