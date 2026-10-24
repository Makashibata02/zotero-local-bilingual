# Mac 安装与使用 · 1.1.2

适用 macOS 14 Sonoma 及以上、Zotero 7–10。Windows 与 Apple Silicon Mac 已通过用户实机验证；Intel Mac 引擎已提供，尚未实机验证。

## 安装

1. 从 [1.1.2 发布页](https://github.com/Makashibata02/zotero-local-bilingual/releases/tag/v1.1.2)下载 `zotero-local-bilingual-1.1.2.xpi`。在 Zotero 的“工具 → 插件／附加组件 → 齿轮 → 从文件安装插件”中安装，完全退出并重启 Zotero。
2. 从菜单栏“Zotero → 设置”打开“本地双语 PDF”，点击顶部“在线下载并安装”。插件会按 Zotero 进程架构选择引擎，无需 Python、Homebrew 或终端。
3. 无法在线下载时，从同一发布页下载下表 ZIP，点击“选择离线 ZIP 并安装”。保持 ZIP 原样，不要解压，也不要使用 Windows 引擎。
4. 选择免费 Bing／腾讯，或填写 API 地址、模型、Key 和配置名称，点击“保存并完整测试”。首次成功部署使用一页 PDF，以后只测试连接与实际调用；API 测试会产生少量用量。

| Zotero 运行方式 | 离线引擎 |
|---|---|
| Apple Silicon 原生运行 | `zlb-engine-1.1.2-macos-arm64.zip` |
| Intel Mac／Rosetta 下的 Intel Zotero | `zlb-engine-1.1.2-macos-x64.zip` |

Apple Silicon 建议使用原生 Zotero。插件跟随 Zotero 进程架构，而不是只看电脑芯片。XPI 与引擎均使用 1.1.2；已有翻译任务应先等待完成再升级。

## 翻译与模型配置

右键 PDF 或文献条目，选择“翻译 PDF…”。默认使用指定的 API 模型、简体中文和双语输出；没有 API 配置时默认免费 Bing。模型与语言均通过下拉框选择，临时切换不会改变默认模型。页码留空翻译全文，也可先填 `1` 查看首页效果。

工作台显示论文标题、阶段、进度、用量及操作。生成的 PDF 添加到原文献条目；“双语＆译文”共用一次翻译。关闭工作台不会取消任务，原附件保留。额度到限后可确认追加，恢复不会清零累计用量。

API Key 存在 **macOS 钥匙串**。Windows 的 Key 不自动迁移，需重新填写。若系统询问钥匙串访问，请核对请求对应本插件引擎后再授权。配置的默认设置、删除及任务保护规则与 Windows 相同。

## 常见问题

- **版本或哈希不符**：重新下载匹配架构的 1.1.2 ZIP，不跳过校验。
- **系统阻止启动／无法验证开发者**：项目尚未提供 Apple Developer ID 签名或公证。保留错误文字反馈，不建议关闭 Gatekeeper；插件不自动绕过系统安全策略。
- **钥匙串被锁定或拒绝访问**：在系统“钥匙串访问”中检查登录钥匙串后重试。免费服务不需要模型 Key。
- **旧后台仍有任务**：先等待任务结束再更新，不删除锁或任务库绕过保护。
- **数据位置**：`~/Library/Application Support/ZoteroLocalBilingual`。引擎在本机运行，待翻译文本发送至所选服务。
- **反馈问题**：提供芯片、macOS 与 Zotero 版本和脱敏错误文字。不要公开 Key、令牌、数据库或论文全文。

完整配置和使用方法见[使用手册](使用手册.md)。
