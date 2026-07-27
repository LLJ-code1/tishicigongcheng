# Anima Prompt Studio 本地工作台

这是一个运行时不依赖 Node 或 npm 的本地工作台。通过本地网关启动后，可以连接 AnimaDex 角色与画师库，并调用 OpenAI 兼容的文本模型完成基础文生图提示词拓展；Node 只用于前端自动测试。

目标显示环境为 Windows 桌面端 `1920×1080`，不设计手机端界面。

从仓库根目录直接打开 HTML 只能查看界面和演示素材：

```text
.\prototype\index.html
```

已经完成本地安装时，可以从项目根目录一键启动两个服务：

```powershell
.\start-local.ps1
```

也可以先启动 AnimaDex（默认 `http://127.0.0.1:5000`），再从仓库根目录单独运行工作台：

```powershell
.\AnimaDex\.venv\Scripts\python.exe .\prototype\server.py
```

浏览器打开 `http://127.0.0.1:57913/`。需要修改地址时设置环境变量：

```powershell
$env:ANIMADEX_URL="http://127.0.0.1:5000"
.\AnimaDex\.venv\Scripts\python.exe .\prototype\server.py
```

工作台会通过同源网关搜索角色、画师和加载缩略图。AnimaDex 不可用时自动保留内置演示素材。

当前状态：

- 统一首页用五步导航显示“说想法 → 选方向 → 确认简报 → 选模型 → 拆提示词”，并按
  当前阶段给出下一步说明和快捷动作；没有更多偏好时，可让总监补全简报或代为决定
  待确认问题。
- 创作总监请求会显示记录操作、准备参考信息、等待模型推理、解析并更新简报、保存
  创作阶段五个前端阶段、已用时间和失败位置。它用于定位浏览器请求卡点，不等于后端
  已提供可恢复任务或真正取消。
- “拓展提示词”“全随机”和保真拆解均调用所选本地 LLM 或外部 API。
- 首页会读取本机项目库；已保存作品可重新打开、恢复全部持久化版本和完整 Recipe
  后继续保存。当前前端把项目头和可选版本一次提交给
  `POST /api/workspace/commit`，并在发送前持久化稳定 `operationId`、项目/版本 ID 和
  冻结正文。数据库提交后即使响应丢失，同请求重放也只产生一个版本；不同正文复用
  同一键会返回冲突。
- 每个版本内嵌 Recipe v1：精确模型引用、正负提示词、十三块、LoRA 列表、参数及来源、
  random plan、中文修改历史、图片/来源引用和 Recipe Hash。旧的项目/版本 CAS 接口
  继续兼容，当前工作台保存不再依赖多次请求拼接。
- AI 局部修改会把完整十三块和中文指令交给已配置文本模型，允许场景、光影、特效等
  必要联动，并逐块展示中文前后差异与原因；模型不可用或结果无效时保持原配方不变，
  不回退到固定词典。确认和撤销结果都会再通过原子保存形成新版本。
- 配方控制台从模型档案 API 渲染出图模型选择器。当前仅提供 Anima_1.1；Sampler、
  Scheduler、Steps 和 CFG 可恢复为模型推荐值，未实机验证的分辨率只作为候选。
- LoRA-lite 可登记 HTTPS 原始链接、版本、触发词、建议权重、兼容模型和冲突。只有用户
  明确选择后才写入 Recipe 快照；不会扫描、下载、移动或删除本地 LoRA 文件。
- 单结构块与多结构块联合再生成使用真实模型，并先作为待应用修改返回。
- 图片解析通过本地 worker 调用已安装的 WD14、Florence、JoyCaption 或 Qwen3-VL；
  未安装的模型会在状态接口和界面中明确标记。
- 文生图目标模型为 Anima，输出无文字权重的 Tags 与简短自然语言混合格式。
- 9 类、471 条本地随机词已接入固定 Seed 的 `random plan`、确定性冲突重抽和轨迹
  保存。目录仍处于 `runtimeReady=false` / `semanticReviewRequired=true`，只有设置
  `PROMPT_STUDIO_EXPERIMENTAL_WORDLISTS=1` 后才能抽样；模型驱动“全随机”继续作为
  独立入口。见 `../docs/random-wordlists.md`。
- 首页和项目页可导出逻辑 ZIP，导入时先检查再恢复到隔离数据库；不会自动替换当前库。
  全库包包含安全的 LoRA-lite 元数据，但不打包 API Key、模型/LoRA 文件和二进制图片。

## 文本模型配置

点击右上角设置，在“文本推理来源”中填写：

本地 OpenAI 兼容服务默认值：

```text
接口地址：http://127.0.0.1:8080/v1
模型名称：Huihui-Qwen3-14B-abliterated-v2.Q4_K_M.gguf
```

适用于 llama-server、LM Studio、Ollama 的 OpenAI 兼容接口或其他兼容服务。接口最终会请求：

```text
POST <接口地址>/chat/completions
```

外部 API 需要填写接口地址、模型名称和 API Key。API Key 保存在本机 SQLite 设置表中。

本项目自带的本地 LLM 安装、运行与外部 API 接入说明见 `docs/local-llm-and-external-api.md`。设置抽屉可以直接启动、停止和测试本地模型，也可以在取得外部服务凭据后测试外部 API。

如果文本模型未启动，点击“拓展并结构化”会显示连接错误，不会回退为模拟提示词。

## 正式拓展提示词母版

运行时依次组合：

1. `prompts/text/core/visual_expansion.md`：输入保真、题材路由、角色与物理逻辑、构图光影、自检。
2. `prompts/text/modes/expand.md`：拓展模式流程，以及严格、均衡、创意三档扩写强度。
3. `prompts/text/formats/hybrid.md`：Tags、短自然语言、双语转换、顺序和去重编译规则。
4. `prompts/text/models/anima.md`：Anima 固定质量词、负面词和模型限制。
5. `prompts/text/schemas/bilingual_structured_output.md`：十三个结构块和严格 JSON 输出契约。

页面中的“编辑文生图规则”可以切换并修改这五个模块。

`prompts/compiled/text_expand_anima.md` 是上述模块编译后的完整系统消息，也是实际发送给文本 LLM 的规则全文。运行下面的命令可以重新生成：

```powershell
python scripts/export_text_expand_prompt.py
```

`prompts/review_cases/text_expand_cases.json` 保存三组母版审阅用例和验收条件。

## 建议体验流程

1. 在统一首页输入初步想法或附加参考图，为本次任务选择“本地模型”或“API 推理”；
   顶部五步导航会显示当前位置和下一步。
2. 与创作总监确认方向和 brief；没有更多补充时，可用快捷按钮让总监补全，提交后从
   请求进度卡查看当前阶段和耗时。
3. 选择基础模型并审核十三块适配结果后进入工作台。
4. 也可在工作台输入中文、英文或混合提示词，点击“拓展并结构化”，得到中英文 V1。
5. 在中间十三块修改动作、服装等内容，或点击“重生成”让当前项基于其他结构块重新
   生成。也可以在中文指令栏输入受支持的局部修改，先检查差异与锁定范围再确认。
6. 锁定已经确定的结构块，可阻止它被再次生成；锁定不等于应用修改。
7. 点击“应用修改（待保存）”，右侧输出更新但仍为未保存状态；再点击“保存作品”，
   服务端才创建连续的新版本。
8. 点击结构块中的“保存为资源”，或从快速资源处“新建”，可在本次页面会话的“我的
   素材”中复用；这两个入口尚未持久化。编辑器顶部“存为配方”会通过 favorites API
   持久化整组结构块。
9. “参考库”目前仍是界面入口，带图片、标签与检索的持久参考库尚未上线。
10. 进入“图片解析”，选择 WD14、Florence、JoyCaption 或 QwenVL，点击“开始多模型分析”。
10. 点击右上角设置，查看“智能驻留、用完即释放、保持加载”三种本地模型策略。

## 确定性词库实验

从启动服务的同一 PowerShell 会话开启：

```powershell
$env:PROMPT_STUDIO_EXPERIMENTAL_WORDLISTS = "1"
.\AnimaDex\.venv\Scripts\python.exe .\prototype\server.py
```

在配方控制台输入 32 位十六进制 `librarySeed`，或留空让服务端生成。相同目录版本、
Seed、抽取配置和锁定项会得到相同计划。英文元素直接进入结构块，中文先显示“待本地
LLM 翻译”。实验开关不取消人工语义审核和不可变版本历史的发布门槛。

## API 与运维

原子保存、Recipe、词库、中文编辑和备份协议见
`../docs/integration-guide.md`；环境变量、构建、回归与隔离恢复流程见
`../docs/operator-runbook.md`。

## 文件

- `index.html`：页面结构
- `styles.css`：1920×1080 桌面端样式
- `data.js`：演示案例、模型和提示词数据
- `app.js`：状态逻辑与页面交互
- `prompt_engine.py`：提示词组合、模型调用和 JSON 校验
- `recipe.py` / `model_profiles.py` / `lora_profile_store.py`：完整 Recipe、基础模型档案与
  LoRA-lite 元数据档案
- `random_sampler.py`：确定性词库抽样、锁定、重抽和轨迹
- `ai_edit_engine.py`：AI 局部修改提案、签名预览和确认应用
- `edit_engine.py`：旧确定性编辑兼容与撤销
- `backup.py`：逻辑导出、检查和隔离恢复
- `prompts/text/`：基础文生图模块
- `tests/app.test.js`：状态与静态结构测试
- `assets/`：本地参考图
