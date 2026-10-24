# Local Bilingual PDF · 本地双语 PDF

[English](README.en.md) · [下载 1.1.2](https://github.com/Makashibata02/zotero-local-bilingual/releases/tag/v1.1.2) · [使用手册](docs/使用手册.md)

在 Zotero 中翻译英文论文和讲义，生成左右整页对照的双语 PDF 或独立译文 PDF，尽量保留原版式、图片和公式。支持免 Key 免费翻译，也支持自己的大模型 API。

**当前版本：1.1.2。** 支持 Windows 10/11 x64、macOS 14+，插件声明兼容 Zotero 7–10。Windows 与 Apple Silicon Mac 已通过用户实机验证；Intel Mac 提供引擎，尚未实机验证。

## 功能

- **三种输出**：双语（左原文、右译文）、译文、双语＆译文；同时生成两份文件只翻译一次，原附件保留。
- **自由选择服务**：实验性免费 Bing／腾讯，或 DeepSeek、自定义 OpenAI 兼容 API；支持保存、删除多套配置和指定默认 API 模型。
- **七种目标语言**：默认简体中文，另有繁体中文、日语、韩语、法语、德语和西班牙语；可用选项以所选服务为准。
- **任务与额度管理**：多篇排队、最多两篇并行（免费任务最多一篇），查看进度与用量，取消、恢复和管理历史；达到额度上限后需确认才能继续。

## 安装

1. 从 [1.1.2 发布页](https://github.com/Makashibata02/zotero-local-bilingual/releases/tag/v1.1.2)下载 `zotero-local-bilingual-1.1.2.xpi`。在 Zotero 的**工具 → 插件／附加组件 → 齿轮 → 从文件安装插件**中选择它，然后完全退出并重启 Zotero。
2. 打开**设置 → 本地双语 PDF**（Mac 从菜单栏 **Zotero → 设置**进入），点击顶部的**在线下载并安装**，自动选择匹配引擎。无法在线下载时，从同一发布页下载下表 ZIP，点击**选择离线 ZIP 并安装**，无需解压。
3. 选择免费服务，或填写 API 地址、模型、Key 和配置名称，点击**保存并完整测试**。首次成功部署运行单页 PDF 测试，以后只测试连接与实际调用；API 测试会消耗少量额度。
4. 右键 PDF 或文献条目，选择**翻译 PDF…**，确认模型、语言、输出和页码，开始翻译。

| 平台／Zotero 运行方式 | 离线引擎 |
|---|---|
| Windows x64 | `zlb-engine-1.1.2-windows-x64.zip` |
| Apple Silicon（M 系列），原生 Zotero | `zlb-engine-1.1.2-macos-arm64.zip` |
| Intel Mac，或 Rosetta 下的 Intel Zotero | `zlb-engine-1.1.2-macos-x64.zip` |

所有平台共用一个 XPI。无需自行安装 Python、BabelDOC 或运行终端；引擎下载约 415–430 MiB，安装约 884–933 MiB。Apple Silicon 建议使用原生 Zotero，详见 [Mac 安装与使用](docs/macOS安装与验收.md)。

## 使用

默认选择已指定的 API 模型、**简体中文、双语输出**；没有 API 配置时使用免费 Bing。临时切换不改变默认模型。页码留空翻译全文，也可填写 `1-3,8`。

工作台自动显示论文标题、阶段、进度和用量，完成后将所选产物添加到原文献条目。关闭工作台不会取消任务；移除历史记录只隐藏记录，文件与费用审计保留。

新 API 任务默认每篇累计上限 **100 万 tokens、1000 次请求**，可在高级设置中降低。它是用量上限，不是固定金额；到限暂停后可在工作台确认追加额度。

## 隐私与限制

- API Key 保存在 **Windows 凭据管理器／macOS 钥匙串**；Windows 的 Key 不会自动迁移到 Mac，需重新填写。
- 引擎在本机运行；**待翻译文本会发送至所选服务**。数据目录为 Windows 的 `%LOCALAPPDATA%\ZoteroLocalBilingual` 或 Mac 的 `~/Library/Application Support/ZoteroLocalBilingual`。
- 免费服务属于实验功能，可能限流或失效，不会自动改用收费 API。复杂公式、扫描件、图中文字和多栏排版仍需核对；现有批注不迁移。
- Mac 引擎尚无项目方开发者签名／公证，可能受到系统安全策略限制，处理方式见 [Mac 说明](docs/macOS安装与验收.md)。

[完整手册](docs/使用手册.md) · [开发与构建](docs/开发说明.md) · [贡献](CONTRIBUTING.md) · [安全反馈](SECURITY.md)

采用 [AGPL-3.0-or-later](LICENSE)，第三方依赖及资源见 [THIRD_PARTY.md](THIRD_PARTY.md)。
