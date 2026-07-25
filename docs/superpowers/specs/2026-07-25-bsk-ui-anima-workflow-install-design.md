# bsk_UI 与 Anima 全功能工作流安装设计

## 目标

将用户下载的 `ComfyUI_bsk_UI` 安装到当前秋叶版 ComfyUI，并把插件内置的
`Anima_全功能.png` 作为独立工作流保存，确保用户既能在 bsk_UI 面板中使用，
也能将工作流图片拖入原生 ComfyUI 排查节点或模型缺失。

## 范围

- 源插件：
  `G:\新建文件夹\插件-260523-120932684\ComfyUI-bsk_UI_26072301\ComfyUI-bsk_UI`
- ComfyUI：
  `H:\conmfui\ComfyUI-aki(1)\ComfyUI-aki-v3\ComfyUI-aki-v3\ComfyUI`
- 安装目录：上述 ComfyUI 的 `custom_nodes\ComfyUI-bsk_UI`
- 独立工作流：复制内置 `panel\Anima\Anima_全功能.png` 到
  `H:\comyfui\workflows\bsk_UI\Anima_全功能.png`

## 安装策略

1. 停止当前监听 `127.0.0.1:8191` 的 ComfyUI 实例。
2. 确认目标插件目录不存在；若已存在则停止操作，不覆盖未知内容。
3. 完整复制插件目录，保留许可证、面板资源与内置工作流。
4. 不覆盖现有 `ComfyUI-WD14-Tagger` 文件。插件说明表明新版本无需覆盖。
5. 不主动安装训练依赖、下载模型或执行插件自带的安装 API。
6. 创建独立工作流目录并复制 `Anima_全功能.png`。
7. 使用现有项目启动器重新启动 ComfyUI。

## 验证

- `http://127.0.0.1:8191/system_stats` 返回成功。
- ComfyUI 日志包含 bsk_UI 扩展加载信息，且没有该插件导致的导入失败。
- `/object_info` 中存在 bsk_UI 的代表节点，例如 `RandomSeedNode`。
- 独立工作流图片存在且可读取。
- 打开 ComfyUI 页面供用户使用。

## 使用路径

1. 打开 ComfyUI 左上角的 bsk_UI 按钮。
2. 在面板设置中加载服务器配置，选择 Anima 全功能工作流。
3. 若面板加载失败，将独立的 `Anima_全功能.png` 拖入原生 ComfyUI。
4. 安装或选择缺失模型后，先在原生界面跑通。
5. 按 `W` 打开 bsk_UI 工作流编辑器，执行“从面板导入”与“发送到面板”。
6. 工作流中的随机种子使用插件提供的 `RandomSeedNode`。

## 安全边界

- 不使用“清空输入”或“清空输出”功能。
- 不通过该插件自动安装、覆盖或删除其他插件。
- 不修改用户现有工作流、模型、输入图或输出图。
- 若启动验证发现缺失模型，只报告缺失项，不擅自下载大型模型。
