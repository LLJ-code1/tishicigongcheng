# Anima Prompt Studio 本地工作台

这是一个不依赖 Node 或 npm 的本地工作台。通过本地网关启动后，可以连接 AnimaDex 角色与画师库，并调用 OpenAI 兼容的文本模型完成基础文生图提示词拓展。

目标显示环境为 Windows 桌面端 `1920×1080`，不设计手机端界面。

直接打开 HTML 只能查看界面和演示素材：

```text
H:\提示词工程\prototype\index.html
```

已经完成本地安装时，可以从项目根目录一键启动两个服务：

```powershell
.\start-local.ps1
```

也可以先启动 AnimaDex（默认 `http://127.0.0.1:5000`），再单独运行工作台：

```powershell
python server.py
```

浏览器打开 `http://127.0.0.1:57913/`。需要修改地址时设置环境变量：

```powershell
$env:ANIMADEX_URL="http://127.0.0.1:5000"
python server.py
```

工作台会通过同源网关搜索角色、画师和加载缩略图。AnimaDex 不可用时自动保留内置演示素材。

当前状态：

- “拓展提示词”“全随机”和保真拆解均调用所选本地 LLM 或外部 API。
- 单结构块与多结构块联合再生成使用真实模型，并先作为待应用修改返回。
- 图片解析通过本地 worker 调用已安装的 WD14、Florence、JoyCaption 或 Qwen3-VL；
  未安装的模型会在状态接口和界面中明确标记。
- 文生图目标模型为 Anima，输出无文字权重的 Tags 与简短自然语言混合格式。

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
5. `prompts/text/schemas/bilingual_structured_output.md`：十个结构块和严格 JSON 输出契约。

页面中的“编辑文生图规则”可以切换并修改这五个模块。

`prompts/compiled/text_expand_anima.md` 是上述模块编译后的完整系统消息，也是实际发送给文本 LLM 的规则全文。运行下面的命令可以重新生成：

```powershell
python scripts/export_text_expand_prompt.py
```

`prompts/review_cases/text_expand_cases.json` 保存三组母版审阅用例和验收条件。

## 建议体验流程

1. 在首页进入“文生图”，为本次任务选择“本地 LLM”或“外部 API”。
2. 输入中文、英文或混合提示词，点击“拓展并结构化”，得到中英文 V1。
3. 在中间结构块修改动作、服装等内容，或点击“重生成”让当前项基于其他结构块重新生成。修改先保持为待应用状态，右侧仍显示上一版本。
4. 锁定已经确定的结构块，可阻止它被再次生成；锁定不等于应用修改。
5. 点击“应用修改并生成新版本”，右侧输出更新并进入 V2、V3。
6. 点击结构块中的“保存为资源”，或从快速资源处“新建”，将常用内容加入“我的素材”。
7. 打开“参考库”，可将图片案例一键带入“拓展”继续修改。
8. 进入“图片解析”，选择 WD14、Florence、JoyCaption 或 QwenVL，点击“开始多模型分析”。
9. 点击右上角设置，查看“智能驻留、用完即释放、保持加载”三种本地模型策略。

## 文件

- `index.html`：页面结构
- `styles.css`：1920×1080 桌面端样式
- `data.js`：演示案例、模型和提示词数据
- `app.js`：状态逻辑与页面交互
- `prompt_engine.py`：提示词组合、模型调用和 JSON 校验
- `prompts/text/`：基础文生图模块
- `tests/app.test.js`：状态与静态结构测试
- `assets/`：本地参考图
