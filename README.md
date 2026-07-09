# 提示词工程

本仓库的主产品是 Anima Prompt Studio：一个 Windows 本地优先的 AI
绘图提示词工作台。它已经具备作品与版本持久化、AnimaDex 角色/画师资源、
本地或外部文本模型、模型驱动全随机、保真拆解、结构块再生成，以及本地
多模型图片分析。

## 快速开始

前置条件：

- `AnimaDex\.venv\Scripts\python.exe` 已存在；缺失时先在 `AnimaDex` 目录完成安装。
- 本地 LLM 和视觉模型是可选能力，路径及安装方式见
  [本地 LLM 与外部 API](docs/local-llm-and-external-api.md)。

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

- Prompt Studio：`http://127.0.0.1:57913/`
- AnimaDex：`http://127.0.0.1:5000/`
- 本地数据：`prototype/data/prompt_studio.db`（本机状态，不应提交或分享）

## 目录

- `prototype/`：工作台前端、Python 后端、提示词模块和测试。
- `AnimaDex/`：只读角色/画师数据服务（Git submodule）。
- `Anima-Style-Explorer/`：上游风格浏览器（Git submodule）。
- `animadex-data/`：本机 AnimaDex 导入数据与缩略图，不提交。
- `提示词原文归档/`：可追溯的提示词原文与清单。
- `models/`：本机视觉模型；体积大，不属于源码。
- `scripts/`：启动、模型管理、提示词归档与导出工具。
- `docs/`：当前文档、历史设计规格和实施记录。

## 验证

```powershell
.\AnimaDex\.venv\Scripts\python.exe -m unittest discover -s prototype\tests -p "test_*.py"
node --test prototype\tests\app.test.js
.\AnimaDex\.venv\Scripts\python.exe -m unittest discover -s scripts\tests -p "test_*.py"
```

## 文档

从 [文档索引](docs/README.md) 开始。开发过程记录集中在
[开发日志](docs/development-log.md)，历史计划不再作为当前待办清单。
