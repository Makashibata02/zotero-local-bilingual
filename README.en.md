# Local Bilingual PDF

[简体中文](README.md) · [Download 1.1.2](https://github.com/Makashibata02/zotero-local-bilingual/releases/tag/v1.1.2) · [User guide (Chinese)](docs/使用手册.md)

Translate English papers and lecture PDFs inside Zotero. Create full-page, side-by-side bilingual PDFs or standalone translated PDFs while preserving layout, figures and equations where possible. Use an experimental free service or your own model API key.

**Current version: 1.1.2.** Windows 10/11 x64 and macOS 14+; the plugin declares support for Zotero 7–10. Users have verified Windows and Apple Silicon Mac. An Intel Mac engine is available but has not been verified on Intel hardware.

## Features

- **Three output modes:** bilingual (original left, translation right), translation only, or both. Both outputs share one translation pass; the original attachment is preserved.
- **Provider choice:** experimental key-free Bing/Tencent, DeepSeek or a custom OpenAI-compatible API. Save/delete named configurations and set a default API model.
- **Seven target languages:** Simplified Chinese by default, plus Traditional Chinese, Japanese, Korean, French, German and Spanish. Availability depends on the selected provider.
- **Queue and usage controls:** queue multiple PDFs, run up to two at once (one free task), track progress and usage, cancel/resume tasks and manage history. Continuing beyond a usage limit requires confirmation.

## Install

1. Download `zotero-local-bilingual-1.1.2.xpi` from the [1.1.2 release](https://github.com/Makashibata02/zotero-local-bilingual/releases/tag/v1.1.2). In Zotero, open **Tools → Plugins / Add-ons → gear → Install Add-on From File**, select the XPI, then fully quit and restart Zotero.
2. Open **Settings → 本地双语 PDF** (on Mac, use **Zotero → Settings** in the menu bar). Click **在线下载并安装** to download and install the matching engine. Alternatively, download the ZIP below from the same release and choose **选择离线 ZIP 并安装**; do not extract it first.
3. Choose a free provider or enter your API endpoint, model, key and configuration name. Click **保存并完整测试**. The first successful deployment uses a one-page PDF; subsequent tests only check the connection with an actual provider call. API tests consume a small amount of usage.
4. Right-click a PDF or library item and choose **翻译 PDF…**. Confirm the model, language, output mode and page range, then start.

| Platform / Zotero architecture | Offline engine |
|---|---|
| Windows x64 | `zlb-engine-1.1.2-windows-x64.zip` |
| Apple Silicon (M series), native Zotero | `zlb-engine-1.1.2-macos-arm64.zip` |
| Intel Mac, or Intel Zotero under Rosetta | `zlb-engine-1.1.2-macos-x64.zip` |

All platforms use the same XPI. No separate Python, BabelDOC or terminal setup is needed. The engine download is about 415–430 MiB and requires about 884–933 MiB installed. Native Zotero is recommended on Apple Silicon. See the [Mac guide (Chinese)](docs/macOS安装与验收.md) for details. The plugin interface is currently in Chinese.

## Use

Each confirmation starts with your default API model, **Simplified Chinese and bilingual output**. Without an API configuration, it defaults to free Bing. A temporary selection does not change your default. Leave pages blank for the whole PDF, or enter a range such as `1-3,8`.

The workbench shows paper titles, stages, progress and usage. Selected outputs are attached to the original library item. Closing the workbench does not cancel tasks; removing a history entry only hides it and retains its files and usage audit.

New API tasks default to cumulative limits of **1 million tokens and 1,000 requests per PDF**. You can lower these limits in advanced settings. They are usage limits, not a fixed monetary budget. A paused task can continue after you explicitly approve a higher limit.

## Privacy and limitations

- API keys are stored in **Windows Credential Manager / macOS Keychain**. Windows keys do not transfer automatically; enter them again on Mac.
- The engine runs locally; **text for translation is sent to your chosen provider**. Application data lives in `%LOCALAPPDATA%\ZoteroLocalBilingual` on Windows or `~/Library/Application Support/ZoteroLocalBilingual` on Mac.
- Free services are experimental and may be rate-limited or unavailable. They never silently fall back to a paid API. Review complex equations, scanned pages, text inside figures and multi-column layouts. Existing annotations are not migrated.
- Mac engines are not signed or notarized by this project and may be blocked by system security policies; see the [Mac guide](docs/macOS安装与验收.md).

[Full guide (Chinese)](docs/使用手册.md) · [Development (Chinese)](docs/开发说明.md) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md)

Licensed under [AGPL-3.0-or-later](LICENSE). See [THIRD_PARTY.md](THIRD_PARTY.md) for dependencies and resource notices.
