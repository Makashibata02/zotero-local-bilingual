# macOS 字体与性能修复验证

日期：2026-10-22；2026-10-23 补充确认窗口、工作台菜单修复及 Intel 候选。基于 `v1.1.0`（`b385e9fc4731d2c2b15ebb3d994387735282b2f1`），本地分支 `fix/macos-adaptation`。主分支仍是 1.0.0，请不要直接把这份差异套到 1.0.0。

## 修复范围

- 设置页仅在 `Zotero.isMac` 时设置 `data-platform="macos"`。Mac 按钮关闭原生固定高度外观，使用可随字体缩放的高度、明确行高和垂直居中；保留键盘焦点、禁用状态与主题颜色。
- 确认窗口和工作台仅在 Mac 加载 `macos-menus.css`，按宿主要求依次导入原生 `popup.css`、`menu.css`。Gecko 为 HTML select 动态创建 XUL 菜单，每项含普通和高亮两份标签；缺少主题会使两份标签同时显示，并缺少弹出层外观。修复补齐菜单主题，不替换选择控件或加载会改变 HTML 按钮的完整全局主题。Windows 不加载新增样式，继续使用原有控件和逻辑。
- Mac 下载按 4 MiB 缓冲写盘，避免每个网络小块都重新打开文件。异常退出仍保存已接收前缀，保留 Range 续传、文件大小检查和完整 SHA-256 校验。Windows 保留逐块写入。
- Mac 解压先检查所有路径及解压大小，再使用 Mozilla 原生 ZIP 文件写出，缓存已创建目录，每约 16 ms 在文件之间让出事件循环。每个普通文件仍设置 0600，仅 Python 入口设置 0700。Windows 保留原来的流式解压，以兼容长路径。大文件内部的原生解压仍是同步的。
- Mac 的 BabelDOC 0.6.4 加载器启用 CoreML 编译缓存。缓存位于数据目录的 `cache/coreml/`，不写入引擎资源。缓存键包含实际静态模型内容、ONNX Runtime 版本、CPU 架构及 macOS 版本；文件锁避免两篇任务同时写同一缓存。首次自检可以预热，安装目录从 staging 移动后仍能复用。CoreML 或缓存不可用时该进程回退 CPU。
- Windows 仍调用原版 `DocLayoutModel.load_onnx()`；没有修改模型、Windows 依赖、Windows 构建脚本、API 并发、限流或付费额度设置。

## 实机结果

环境：macOS 26.6.2，Apple Silicon / arm64，Zotero 10.0.6，便携 Python 3.12.14，BabelDOC 0.6.4，ONNX Runtime 1.23.2。所有测试使用独立 Zotero profile、生成的测试 PDF 和本机模拟接口，不读取用户文库、API Key 或私人论文。

| 检查 | 原版 | 修复版 |
|---|---:|---:|
| 原生设置页按钮文字垂直中心偏差 | 约 6.33 CSS px，文字超出按钮底部 | 约 0.5 CSS px，文字完整包含 |
| 同一 447,086,707 字节引擎 ZIP 解压 | 24.000 秒 | 7.795 秒 |
| 生成测试页：独立进程模型加载 | 6.684 秒 | 首次 6.298 秒；复用缓存 0.636 秒 |

解压后逐一比对 11,499 个文件的大小和 CRC，并检查所有文件权限，全部通过。原生主题下普通字号与 125% 缩放均验证文字边界。6 个版面识别框及置信度通过 `numpy.allclose(rtol=1e-4, atol=1e-3)` 一致性检查；实际预测仍使用原有 CoreML 和 CPU providers。

这些是单机、固定样本的测量，不是对所有 Mac 的速度保证。文件系统缓存与首次系统加载会影响时间；另一次空白图测试为原版 10.254 秒、缓存复用 2.099 秒。下载优化验证的是写盘次数：4 MiB + 37 字节、16 KiB 网络块从 258 次写入降至 3 次（含初始化空文件），没有声称提高 GitHub 到本机的网络带宽。

完整 PDF 验收成功，双语产物页数、流程和用量记录正常。首次新目录运行为 72.6 秒，后续运行为 16.0 秒；首次耗时主要发生在模型加载前的依赖初始化（54.6 秒）。这两个完整流程数字不作为原版/修复版速度对比。首次安装及系统安全检查仍可能较慢。

## 回归与产物

- TypeScript 检查、插件构建通过；前端 68 项测试通过。
- 后端 175 项测试通过，1 项原有平台条件测试跳过。
- 182 项引擎资源自检、空闲安全退出、退出时拒绝新写入、重启及自然空闲退出通过。
- 本机模拟 API 的端到端 PDF 翻译通过，未调用付费服务。
- Windows 路径由回归测试覆盖，本轮没有 Windows 实机复测；Intel Mac 同样未做实机验收。

本地测试包位于 `dist/macos-fix-1.1.0/`。为了保留已有 Windows 引擎与版本配对，该候选包内部版本仍为 1.1.0，文件名带 `macfix`；这是共用源码的本地候选包，不是独立 Mac 产品或新的正式 Release。XPI 中更新两种 Mac 引擎的清单和哈希，Windows 引擎条目逐字段保持原样。

安装顺序：在 Zotero 中从文件安装 `zotero-local-bilingual-1.1.0-macfix.xpi`，重启 Zotero，再在插件设置中离线导入对应架构引擎。Apple Silicon 原生 Zotero 使用 `zlb-engine-1.1.0-macos-arm64-macfix.zip`；Intel 或 Rosetta 下的 Intel Zotero 使用 `zlb-engine-1.1.0-macos-x64-macfix.zip`。请等待正在运行的翻译任务结束后导入。仅安装 XPI 会修复界面和安装流程；模型缓存修复需要同时导入新引擎。本地候选引擎没有在线发布地址，不能通过在线按钮下载这个候选版本。

Intel 候选由 SHA-256 验证通过的官方 1.1.0 Intel 引擎覆盖当前服务源码及运行时元数据生成；11,380 个其他文件内容与权限保持原样，182 项资源哈希匹配，29 个服务文件与当前源码一致。未执行 Intel 解释器，`native_verified=false`，需要 Intel Mac 实机验收；不能把 Apple Silicon 测量结果套用于 Intel。

未修改正在使用的 Zotero profile、正式引擎、文库或凭据；未向 GitHub 推送、合并或发布。

## 原生菜单与窗口验收（2026-10-23）

在独立 Zotero 10.0.6 / Apple Silicon profile 中，实际加载构建后的控制器和 XHTML，通过 HTML select 的 `showPicker()` 展开宿主原生菜单：

- 原版对照复现每个菜单项同时显示两份文字；修复后每项仅一份可见标签，各行有独立高度且互不重叠。
- 确认窗口的模型、目标语言，以及工作台的状态、并发菜单，在正常、480px 窄窗口及 125% 缩放通过。
- 方向键跳过缺少 Key 的模型；Enter 提交模型和语言；Esc 不提交临时选项。测试等待 Mac 菜单选择闪烁动画结束后读取结果。
- 切换至只支持简体中文的模拟服务时，其他六个目标语言保持禁用，选中值回退并给出说明。确认后的请求参数保持正确。
- 工作台详情可展开，页面没有水平溢出，窄窗口和缩放后确认按钮仍在可见区域。
- 设置页的已保存模型、服务商、思考模式、字体四个菜单正常，按钮文字未被边框裁切。

所有翻译服务、任务元数据与接口响应都是测试夹具，没有请求付费模型。原生弹出菜单是单独的系统窗口，页面快照仅展示页面；菜单结论以原生控件几何、可见标签和真实键盘事件结果为依据。

复现方式（先构建插件，在仓库根目录执行）：

```sh
python3 scripts/prepare_macos_ui_check.py
/Applications/Zotero.app/Contents/MacOS/zotero -no-remote \
  -profile "$PWD/output/native-ui/profile" -headless -ZoteroDebugText
```

脚本只准备专用测试 profile，测试完成后该 Zotero 进程自行退出。检查 `output/native-ui/results.json` 的 `ok: true`；检查项应均通过，并且菜单的 `forced` 均为 false。该测试不等于 Windows 或 Zotero 7–9 的实机验收。

## 复现模型性能检查

在 Mac 上使用现有引擎中的 Python，传入完整引擎目录和一个尚不存在的输出目录：

```sh
"/path/to/runtime/python/bin/python3.12" -B scripts/benchmark_macos_layout.py \
  --runtime "/path/to/runtime" --output output/macos-layout-benchmark
```

脚本分别启动原版、首次缓存、重复缓存三个独立进程；生成一页 PDF，比较识别框，输出加载时间与推理时间，不发起翻译请求。模型缓存可删除后重建，但请在翻译任务和后台自检都停止时操作。

技术依据：[ONNX Runtime CoreML 缓存说明](https://onnxruntime.ai/docs/execution-providers/CoreML-ExecutionProvider.html)、[Mozilla IOUtils 接口说明](https://firefox-source-docs.mozilla.org/dom/ioutils_migration.html)。

## 1.1.1 Windows 交接包（2026-10-24）

用户确认 Mac 适配可用后，将插件、服务、项目元数据和三平台引擎统一为 1.1.1。依赖锁定仅更新项目自身版本，没有更新第三方依赖。产物使用规范文件名，不再带 `macfix`；Mac 的历史候选及上述性能测量记录保留用于追溯。

- 共用插件：`zotero-local-bilingual-1.1.1.xpi`。
- 三平台引擎：`zlb-engine-1.1.1-windows-x64.zip`、`zlb-engine-1.1.1-macos-arm64.zip`、`zlb-engine-1.1.1-macos-x64.zip`。
- 配套提供源码 ZIP、引擎清单、`updates.json` 和 `SHA256SUMS`。下载链接统一指向项目的 `v1.1.1` Release；上传前仍应使用离线安装。
- Windows 引擎由已校验官方 1.1.0 包更新服务源码、版本及许可/锁定快照生成；12,296 个非替换文件的内容和权限保持一致。Windows 和 Intel 均标记 `native_verified=false`，需对应平台实机验收。
- 1.1.1 源码通过 TypeScript 检查、68 项前端测试及 175 项后端测试，1 项原有平台条件测试跳过。
- 新构建的 1.1.1 控制器在独立 Zotero profile 中通过原生菜单、键盘、禁用选项、窄窗口、缩放及设置页检查；24 项原生检查完成，没有手动强制展开菜单的替代路径。
- 从 1.1.1 ARM64 ZIP 解出的引擎通过 182 项资源自检、后台安全退出、退出期间拒绝新写入、重启和自然空闲退出。
- 新引擎通过本机模拟 API 的完整单页 PDF 翻译，双语产物正常生成，未使用付费模型；该次冷启动结果不作为新旧版本性能对比。

外层交接 ZIP 同时包含 `source/`、`release/`、相对原 `v1.1.0` 的补丁、发布说明和 Windows 只读校验脚本，不包含 `.git`、依赖安装目录、测试 profile、数据库、令牌或用户论文。GitHub 源码提交、标签和 Release 上传留待 Windows 端完成，本地没有执行上传。
