# 提示词工程

本仓库的主产品是 Anima Prompt Studio：一个 Windows 本地优先的 AI
绘图提示词工作台。它已经具备作品列表、原子保存/重放、完整版本重开、AnimaDex
角色/画师资源、本地或外部文本模型、模型驱动全随机、保真拆解、结构块再生成，以及
本地多模型图片分析。

编辑体验方面：结构块修改和权重调整会实时编译并预览最终双语提示词
（权重编译为 `(item:1.2)` 语法，0% 剔除该块，跨块自动去重）；随机变体
支持新旧对比后保留或回退；"矩阵×3"一次生成多组候选并排挑选；版本
时间线可载入历史版本，显式保存后形成新版本；工作台使用十三个结构块，并把模型、
正负提示词、参数来源、Seed、随机轨迹和中文修改历史保存为 Recipe v1；结构块可整组
存为素材配方并再次带入。画师收藏支持勾选混权重生成画师块；英文结构块输入内置 Tag
联想补全。中文局部修改采用“预览差异 → 确认 → 新版本”，撤销也追加为新版本。
文本生成
与图片分析目前可由前端停止等待，后端真正中断推理仍在后续范围。快捷键：`Ctrl+Enter` 拓展、`Ctrl+Shift+Enter`
应用修改、`Ctrl+S` 保存作品、`Esc` 关闭弹层。

9 类、471 条词库已经接入确定性 `random plan` 和 Seed 恢复，但仍受实验开关保护：
目录尚未完成逐条语义审核，不能描述为公开发布就绪。工作台还提供全库/单项目逻辑 ZIP
导出、只读检查和隔离恢复；恢复不会自动替换当前数据库。

## 快速开始

前置条件：

- `AnimaDex\.venv\Scripts\python.exe` 已存在；缺失时先在 `AnimaDex` 目录完成安装。
- 本地 LLM 和视觉模型是可选能力，路径及安装方式见
  [本地 LLM 与外部 API](docs/local-llm-and-external-api.md)。
- Node.js 只用于运行前端自动测试，不是工作台运行依赖。

首次克隆时同时获取两个上游组件：

```powershell
git clone --recurse-submodules https://github.com/LLJ-code1/tishicigongcheng.git
```

已经克隆仓库时：

```powershell
git submodule update --init --recursive
```

从仓库根目录启动：

```powershell
.\start-local.ps1
```

不启动本地文本模型：

```powershell
.\start-local.ps1 -SkipLlm
```

需要个人验证确定性词库时：

```powershell
$env:PROMPT_STUDIO_EXPERIMENTAL_WORDLISTS = "1"
.\start-local.ps1
```

- Prompt Studio：`http://127.0.0.1:57913/`
- AnimaDex：`http://127.0.0.1:5000/`
- 本地数据：`prototype/data/prompt_studio.db`（本机状态，不应提交或分享）

旧版数据库第一次由新版服务启动时，会先在同目录的
`.prompt-studio-recovery/` 生成人工故障回滚副本，再登记 schema v1。该副本可能包含
本机 API Key，只用于迁移故障时由用户手动回滚，不可分享，也不是产品备份。

产品备份是经过 manifest/Hash 校验的逻辑 ZIP，排除密钥、模型、LoRA 和二进制资产；
恢复只写入隔离数据库。具体操作见 [构建与运行手册](docs/operator-runbook.md)。

## 目录

- `prototype/`：工作台前端、Python 后端、提示词模块和测试。
- `AnimaDex/`：只读角色/画师数据服务（Git submodule）。
- `Anima-Style-Explorer/`：上游风格浏览器（Git submodule）。
- `animadex-data/`：本机 AnimaDex 导入数据与缩略图，不提交。
- `提示词原文归档/`：可追溯的提示词原文与清单。
- `词库原稿/`：9 类、471 条随机元素源；已接入受门禁保护的确定性计划，仍待人工语义审核。
- `models/`：本机视觉模型；体积大，不属于源码。
- `scripts/`：启动、模型管理、提示词归档与导出工具。
- `docs/`：当前文档、历史设计规格和实施记录。

## 验证

```powershell
.\AnimaDex\.venv\Scripts\python.exe -m unittest discover -s prototype\tests -p "test_*.py"
node --test prototype\tests\app.test.js
.\AnimaDex\.venv\Scripts\python.exe -m unittest discover -s scripts\tests -p "test_*.py"
& .\AnimaDex\.venv\Scripts\python.exe scripts\build_random_wordlists.py --check
```

同一套回归已配置在 `.github/workflows/test.yml`，用于 Windows 干净环境验证；首次
线上 workflow run 仍待确认。

## 文档

从 [文档索引](docs/README.md) 开始。开发过程记录集中在
[开发日志](docs/development-log.md)，历史计划不再作为当前待办清单。
词库的真实接入边界见 [可复现随机词库](docs/random-wordlists.md)，程序化接入见
[API 接入指南](docs/integration-guide.md)。
