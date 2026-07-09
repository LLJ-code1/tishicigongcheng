# 本地 LLM 与外部 API

## 当前方案

文生图提示词生成统一使用 OpenAI 兼容的 `chat/completions` 接口。工作台只维护一套提示词编译、请求、JSON 校验和修复逻辑，推理来源可以在每次任务中选择：

- 本地 LLM：`llama.cpp` 的 `llama-server`，默认地址 `http://127.0.0.1:8080/v1`。
- 外部 API：任何实现 OpenAI 兼容接口的服务，后续填写接口地址、模型名和 API Key 即可。

两种来源生成的结果都必须通过相同的中英双语 SceneSpec 校验，不会因为切换供应商而改变工作台的数据结构。

本地 Qwen3 请求会附加 `/no_think`，响应层也会移除 `<think>` 包装，避免思考文本污染严格 JSON；外部 API 不会附加该本地模型专用命令。

## 本地模型

当前机器为 RTX 4070 Ti SUPER 16GB。第一版选择：

```text
运行时：llama.cpp CUDA 12.4
模型：Huihui-Qwen3-14B-abliterated-v2.Q4_K_M.gguf
工作台模型名：Huihui-Qwen3-14B-abliterated-v2.Q4_K_M.gguf
上下文：8192
GPU 层：99（尽可能全部卸载到 GPU）
Flash Attention：auto
CPU 线程：12
并发槽：1（完整 8192 上下文优先供当前生成使用）
```

该 GGUF 是第三方量化版本，不是 Qwen 官方发布的 GGUF。选择它是为了在 16GB 显存内提供较强的双语指令能力，并兼容当前素材中范围较宽的绘图题材。后续可以替换模型文件和启动参数，工作台上层接口无需重写。

工作台复用桌面一键启动脚本已经配置好的现有资产，不再下载第二份：

```text
运行时：H:\AI\apps\llama.cpp\b9442-cuda12.4\llama-server.exe
模型：H:\AI\models\llm\Huihui-Qwen3-14B-abliterated-v2.Q4_K_M.gguf
原启动器：H:\comyfui\start_comfyui_and_llm.py
工作台日志：prototype/data/
```

## 运行

独立管理本地模型：

```powershell
.\scripts\start_local_llm.ps1
.\scripts\local_llm_status.ps1
.\scripts\stop_local_llm.ps1
```

正常日常使用可以直接运行：

```powershell
.\start-local.ps1
```

总启动器会检测上述现有路径并启动本地 LLM。也可以继续使用桌面的“一键启动 ComfyUI + 本地LLM”；工作台会直接连接已经运行的 `8080` 服务，不会再启动第二份。模型启动后会常驻后台，以免每次生成都重新加载 9GB 权重；退出工作台时可执行停止脚本释放显存。

## 工作台控制

在右上角“模型与运行设置”中，本地来源提供：

- 启动：从网页启动本项目管理的 `llama-server`。
- 停止：释放本地模型进程和显存。
- 测试：发送一个极小请求，显示模型名与响应耗时。

如果端口上运行的是由其他软件启动的兼容服务，工作台可以连接并测试，但只停止持有本项目 PID 文件的进程。

## 外部 API

后续拿到 API 后填写：

```text
接口地址：https://供应商地址/v1
模型名称：供应商提供的模型 ID
API Key：供应商提供的密钥
```

接口地址不要填写到 `/chat/completions`，工作台会自动追加该路径。点击“测试外部 API”只发送一个要求回复 `OK` 的微型请求，不执行完整提示词扩展。

API Key 当前保存在本机 `prototype/data/prompt_studio.db` 的设置表中，且只由本地服务使用。它不会写入 Git，但当前版本尚未接入系统凭据保险库，因此不要把数据库文件对外分享。

## HTTP 接口

```text
GET  /api/local-llm/status
POST /api/local-llm/start
POST /api/local-llm/stop
POST /api/text/provider-test
POST /api/text/expand
```

`provider-test` 请求示例：

```json
{
  "provider": "local"
}
```

外部来源测试会读取工作台设置中的 URL、模型名和 API Key，因此密钥不需要重复出现在每次前端请求里。

## 故障检查

本地模型无法启动时依次检查：

1. `.\scripts\local_llm_status.ps1` 是否显示运行时和模型已安装。
2. `prototype/data/local_llm.stderr.log` 是否有显存、CUDA DLL 或模型格式错误。
3. `http://127.0.0.1:8080/v1/models` 是否能返回 `Huihui-Qwen3-14B-abliterated-v2.Q4_K_M.gguf`。
4. 端口 `8080` 是否已被其他程序占用。
