/* global Components, ChromeUtils, Services, Zotero */
function createRuntimeNative(context) {
  const Cc = Components.classes, Ci = Components.interfaces;
  const { Subprocess } = ChromeUtils.importESModule("resource://gre/modules/Subprocess.sys.mjs");
  const io = () => context.IOUtils, paths = () => context.PathUtils;
  function file(path) {
    const result = Cc["@mozilla.org/file/local;1"].createInstance(Ci.nsIFile);
    result.initWithPath(path);
    return result;
  }
  function platform() {
    const os = Services.appinfo.OS, abi = Services.appinfo.XPCOMABI;
    if (os === "WINNT" && abi.startsWith("x86_64")) return "windows-x64";
    if (os === "Darwin" && /^(aarch64|arm64)/.test(abi)) return "macos-arm64";
    if (os === "Darwin" && abi.startsWith("x86_64")) return "macos-x64";
    return null;
  }
  function baseDir() {
    const custom = Services.env.get("ZLB_DATA_DIR");
    if (custom) return custom;
    if (Services.appinfo.OS === "Darwin") {
      const home = Services.dirsvc.get("Home", Ci.nsIFile).path;
      return paths().join(home, "Library", "Application Support", "ZoteroLocalBilingual");
    }
    return paths().join(Services.env.get("LOCALAPPDATA"), "ZoteroLocalBilingual");
  }
  async function launch(root, data, preflight) {
    const process = await Subprocess.call({command: platform() === "windows-x64" ? paths().join(root, "python", "python.exe") : paths().join(root, "python", "bin", "python3.12"),
      arguments: ["-I", "-B", "-m", "zotero_local_bilingual.cli", preflight ? "--preflight" : "--managed",
        "--runtime-root", root, "--data-dir", data],
      environmentAppend: true, environment: {PYTHONHOME: null, PYTHONPATH: null, PYTHONUTF8: "1", PYTHONNOUSERSITE: "1"},
      stderr: "pipe", workdir: root});
    return process;
  }
  async function readAll(pipe) {
    let output = "";
    while (true) {
      const chunk = await pipe.readString();
      if (!chunk) break;
      output += chunk;
      if (output.length > 2000000) throw new Error("引擎自检输出过大");
    }
    return output;
  }
  return {
    platform,
    checkSystem() {
      if (Services.appinfo.OS === "Darwin") {
        // nsSystemInfo.version is Darwin's kernel version: 23 is macOS 14.
        const kernel = Number(String(Services.sysinfo.getProperty("version")).split(".")[0]);
        if (!Number.isFinite(kernel) || kernel < 23) throw new Error("Mac 引擎要求 macOS 14 或更新系统，请先确认系统版本");
      }
    },
    baseDir,
    samePath: (a, b) => Services.appinfo.OS === "WINNT"
      ? a.replace(/\\/g, "/").replace(/\/$/, "").toLowerCase() === b.replace(/\\/g, "/").replace(/\/$/, "").toLowerCase()
      : a.replace(/\/$/, "") === b.replace(/\/$/, ""),
    async secureDirectory(path) {
      if (Services.appinfo.OS === "Darwin") await io().setPermissions(path, 0o700);
    },
    legacyURL: () => String(Zotero.Prefs.get("extensions.zotero.local-bilingual.serviceUrl", true) || "http://127.0.0.1:17865"),
    freeSpace: path => file(path).diskSpaceAvailable,
    delay: ms => Zotero.Promise.delay(ms),
    sha256(path) {
      const stream = Cc["@mozilla.org/network/file-input-stream;1"].createInstance(Ci.nsIFileInputStream);
      stream.init(file(path), -1, 0, 0);
      try {
        const hash = Cc["@mozilla.org/security/hash;1"].createInstance(Ci.nsICryptoHash);
        hash.init(hash.SHA256);
        hash.updateFromStream(stream, 0xffffffff);
        return Array.from(hash.finish(false), c => c.charCodeAt(0).toString(16).padStart(2, "0")).join("");
      } finally { stream.close(); }
    },
    async extract(archive, destination, safeEntry, maximum, progress = () => {}) {
      const zip = Cc["@mozilla.org/libjar/zip-reader;1"].createInstance(Ci.nsIZipReader);
      zip.open(file(archive));
      try {
        if (Services.appinfo.OS === "Darwin") {
          // nsIZipReader can write directly on macOS. Avoid copying every byte
          // into a JS number array and reopening the file for every 512 KB.
          const entries = zip.findEntries(null), checked = [];
          let total = 0;
          while (entries.hasMore()) {
            const name = entries.getNext(), entry = zip.getEntry(name);
            if (!safeEntry(name)) throw new Error("ZIP 含不安全路径，未解压");
            if ((entry.permissions & 0o170000) === 0o120000) throw new Error("引擎 ZIP 含符号链接，未安装");
            total += entry.realSize;
            if (total > maximum) throw new Error("ZIP 解压体积超过清单");
            checked.push({name, directory: entry.isDirectory, size: entry.realSize});
          }
          const directories = new Set([destination]);
          let written = 0, lastPercent = -1, lastYield = Date.now();
          for (const entry of checked) {
            const target = paths().join(destination, ...entry.name.replace(/\/$/, "").split("/"));
            const directory = entry.directory ? target : paths().parent(target);
            if (!directories.has(directory)) {
              await io().makeDirectory(directory, {ignoreExisting: true, permissions: 0o700});
              directories.add(directory);
            }
            if (!entry.directory) {
              try {
                const output = file(target);
                zip.extract(entry.name, output);
                output.permissions = entry.name === "python/bin/python3.12" ? 0o700 : 0o600;
              } catch (error) {throw new Error(`引擎文件解压失败：${entry.name}：${String(error)}`);}
            }
            written += entry.size;
            const percent = Math.min(99, Math.floor(written / (maximum / 1.05) * 100));
            if (percent !== lastPercent) {lastPercent = percent; progress(percent);}
            // Native extraction is synchronous; yield between files for repaint
            // and cancellation/other Zotero work, without a timer per tiny file.
            if (Date.now() - lastYield >= 16) {await Zotero.Promise.delay(0); lastYield = Date.now();}
          }
          return;
        }
        const entries = zip.findEntries(null);
        let total = 0, lastPercent = -1;
        while (entries.hasMore()) {
          const name = entries.getNext();
          if (!safeEntry(name)) throw new Error("ZIP 含不安全路径，未解压");
          const entry = zip.getEntry(name);
          // Release ZIPs contain regular files only; never follow an archive link.
          if ((entry.permissions & 0o170000) === 0o120000) throw new Error("引擎 ZIP 含符号链接，未安装");
          total += entry.realSize;
          if (total > maximum) throw new Error("ZIP 解压体积超过清单");
          const target = paths().join(destination, ...name.replace(/\/$/, "").split("/"));
          if (entry.isDirectory) await io().makeDirectory(target, {ignoreExisting: true});
          else {
            await io().makeDirectory(paths().parent(target), {ignoreExisting: true});
            // IOUtils supports long Unicode paths; nsIZipReader.extract uses MAX_PATH on Windows.
            const input = Cc["@mozilla.org/binaryinputstream;1"].createInstance(Ci.nsIBinaryInputStream);
            input.setInputStream(zip.getInputStream(name));
            try {
              await io().write(target, new Uint8Array());
              while (input.available()) {
                const bytes = new Uint8Array(input.readByteArray(Math.min(input.available(), 512 * 1024)));
                await io().write(target, bytes, {mode: "append"});
              }
            } catch (error) {throw new Error(`引擎文件解压失败：${name}：${String(error)}`);}
            finally {input.close();}
            if (Services.appinfo.OS === "Darwin") {
              // Streaming extraction does not restore UNIX modes automatically.
              // Only the fixed interpreter entry point needs to be executable.
              await io().setPermissions(target, name === "python/bin/python3.12" ? 0o700 : 0o600);
            }
            const percent = Math.min(99, Math.floor(total / (maximum / 1.05) * 100));
            if (percent !== lastPercent) {lastPercent = percent; progress(percent);}
          }
        }
      } finally { zip.close(); }
    },
    async preflight(root, data) {
      const process = await launch(root, data, true);
      let finished = false;
      const timeout = Zotero.Promise.delay(180000).then(() => { if (finished) return undefined; process.kill(); throw new Error("引擎自检超时"); });
      const result = Promise.all([readAll(process.stdout), readAll(process.stderr), process.wait()]);
      const [stdout, stderr, status] = await Promise.race([result, timeout]).finally(() => {finished = true;});
      if (status.exitCode !== 0) throw new Error(`引擎资源自检失败：${stderr.slice(-1200) || stdout.slice(-1200)}`);
      const response = JSON.parse(stdout.trim().split("\n").pop());
      if (!response.ok || response.protocol_version !== 2) throw new Error("引擎自检协议不符");
    },
    async start(root, data) {
      const process = await launch(root, data, false);
      const status = {exited: false};
      // Always consume both pipes. A full pipe must never stall a worker/service.
      for (const [name, pipe] of [["stdout", process.stdout], ["stderr", process.stderr]]) {
        void (async () => {
          const log = paths().join(data, "logs", `launcher-${name}.log`);
          await io().makeDirectory(paths().parent(log), {ignoreExisting: true});
          while (true) {
            const chunk = await pipe.readString();
            if (!chunk) break;
            await io().writeUTF8(log, chunk, {mode: "append"});
          }
        })().catch(error => Zotero.logError(error));
      }
      void process.wait().then(() => {status.exited = true;});
      return status;
    },
    async chooseZip() {
      const { FilePicker } = ChromeUtils.importESModule("chrome://zotero/content/modules/filePicker.mjs");
      const picker = new FilePicker();
      picker.init(Zotero.getMainWindow(), "导入与插件版本对应的离线引擎 ZIP", picker.modeOpen);
      picker.appendFilter("引擎 ZIP", "*.zip");
      return await picker.show() === picker.returnOK ? picker.file : null;
    }
  };
}
