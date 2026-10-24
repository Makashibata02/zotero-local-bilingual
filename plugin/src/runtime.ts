declare const Zotero: any;
declare const IOUtils: any;
declare const PathUtils: any;
declare const RuntimeNative: any;

type Manifest = { version: string; protocol_version: number; platform: string; url: string | null;
  size: number; installed_size: number; sha256: string; filename: string };
type Catalog = {version: string; protocol_version: number; engines: Record<string, Manifest>};

export function selectManifest(catalog: Catalog | Manifest, platform: string): Manifest {
  const manifest = "engines" in catalog ? catalog.engines[platform] : catalog;
  if (!manifest || manifest.platform !== platform) throw new Error(`此安装包尚未包含 ${platform} 引擎，请取得对应平台的完整发布包。`);
  if (!/^\d+\.\d+\.\d+$/.test(manifest.version) || !/^[a-f0-9]{64}$/.test(manifest.sha256) ||
      manifest.protocol_version !== 2 || catalog.protocol_version !== 2 || manifest.version !== catalog.version ||
      !Number.isSafeInteger(manifest.size) || manifest.size <= 0 ||
      !Number.isSafeInteger(manifest.installed_size) || manifest.installed_size <= 0) throw new Error("引擎发布清单无效");
  return manifest;
}
type Feedback = (message: string) => void;
type Endpoint = { url: string; token: string };

export function safeEntry(name: string): boolean {
  return !!name && !name.includes("\\") && !name.includes(":") && !name.startsWith("/") &&
    name.split("/").every(p => p !== ".." && p !== "." && !/[. ]$/.test(p) &&
      !/^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(p));
}

export class RuntimeManager {
  private pending?: Promise<Endpoint>;
  private listeners = new Set<Feedback>();
  private endpoint?: Endpoint;
  private notify = (message: string) => { for (const fn of this.listeners) {try {fn(message);} catch (_) {/* A closed UI must not cancel engine preparation. */}} };
  constructor(private native: any, private io: any, private paths: any, private request: typeof fetch) {}

  async ensure(feedback: Feedback = () => {}, offline?: string): Promise<Endpoint> {
    this.listeners.add(feedback);
    try {
      if (offline && this.pending) throw new Error("引擎正在准备，请等待完成后导入离线包");
      if (!this.pending) this.pending = this.prepare(offline).finally(() => { this.pending = undefined; });
      return await this.pending;
    } finally { this.listeners.delete(feedback); }
  }

  private async healthy(url: string): Promise<any | undefined> {
    if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(url)) return undefined;
    try {
      const response = await this.request(url + "/v1/health", { signal: AbortSignal.timeout(2000) });
      if (response.ok) return await response.json();
    } catch (_) {}
    return undefined;
  }

  private async stopIdleService(data: any, health: any, base: string): Promise<void> {
    if (!health.managed || health.protocol_version !== 2 || !health.data_dir || !this.native.samePath(health.data_dir, base)) {
      throw new Error("检测到非本插件托管的后台，未停止。请先关闭旧手动服务后重试。");
    }
    if (!health.shutdown_requested && (health.active_job || health.active_jobs?.length || health.pending_jobs > 0 || health.business_requests > 0)) {
      throw new Error(`后台 ${health.version} 仍有任务或请求处理中，未停止。请待当前操作完成后重试。`);
    }
    if (!health.supports_idle_shutdown) {
      const token = (await this.io.readUTF8(this.paths.join(base,"service-token.txt"))).trim();
      const response = await this.request(data.url+"/v1/scheduler",{headers:{"X-Local-Bilingual-Token":token},signal:AbortSignal.timeout(3000)});
      if (!response.ok) throw new Error(`无法确认旧后台 ${health.version} 的任务状态，未停止。请检查后台或稍后重试。`);
      const scheduler = await response.json();
      const counts = scheduler.counts || {};
      if (["queued","running","cancelling"].some(state=>counts[state]>0)) {
        throw new Error(`旧后台 ${health.version} 仍有运行或排队任务，未停止。请先等待任务完成。`);
      }
      throw new Error(`旧后台 ${health.version} 当前空闲，但该版本不支持自动退出。请停止操作插件，等待约 10 分钟，或重启电脑后重试；只重启 Zotero 不会结束后台。`);
    }
    if (!health.process_id || (data.pid && health.process_id !== data.pid)) throw new Error("后台身份已变化，未停止，请刷新后重试。");
    if (!health.shutdown_requested) {
      const token = (await this.io.readUTF8(this.paths.join(base,"service-token.txt"))).trim();
      this.notify(`后台 ${health.version} 当前空闲，正在安全退出并切换引擎…`);
      const response = await this.request(data.url+"/v1/runtime/shutdown-if-idle",{method:"POST",headers:{"Content-Type":"application/json","X-Local-Bilingual-Token":token},body:JSON.stringify({expected_version:health.version,expected_pid:health.process_id}),signal:AbortSignal.timeout(5000)});
      if (!response.ok) {
        let reason="无法安全退出旧后台，未启动第二个服务";
        try {const result=await response.json();if(typeof result.detail==="string")reason=result.detail;} catch (_) {}
        throw new Error(reason);
      }
    }
    const deadline = Date.now()+30000;
    for (let i=0;i<60 && Date.now()<deadline;i++) {
      await this.native.delay(500);
      const current=await this.healthy(data.url);
      if (!current) {this.endpoint=undefined;return;}
      if (current.process_id !== health.process_id || current.version !== health.version) throw new Error("后台身份在切换时发生变化，未启动第二个服务，请刷新后重试。");
    }
    throw new Error("旧后台正在退出但尚未完成，请稍后重试；没有启动第二个服务。");
  }

  private async prepare(offline?: string): Promise<Endpoint> {
    const platform = this.native.platform();
    if (!platform) throw new Error("自动引擎支持 Windows x64、macOS Apple Silicon 和 Intel；当前架构暂不支持");
    this.native.checkSystem();
    const base = this.native.baseDir();
    await this.io.makeDirectory(base, { ignoreExisting: true });
    await this.native.secureDirectory(base);
    const catalog = await (await this.request("chrome://local-bilingual/content/engine-manifest.json")).json() as Catalog | Manifest;
    const manifest = selectManifest(catalog, platform);
    // Refuse the old terminal service before creating/migrating its task database.
    const oldURL = this.native.legacyURL();
    const legacy = await this.healthy(oldURL);
    if (legacy && (!legacy.data_dir || this.native.samePath(legacy.data_dir, base)) && (!legacy.managed || legacy.protocol_version !== 2)) {
      throw new Error("检测到旧手动服务。请关闭手动启动服务的终端一次，再点击完整测试；原配置、Key 和任务会保留。");
    }
    if (this.endpoint && !offline) {
      const health = await this.healthy(this.endpoint.url);
      if (health?.managed && health.version === manifest.version && !health.shutdown_requested) return this.endpoint;
    }
    const descriptor = this.paths.join(base, "managed-service.json");
    if (await this.io.exists(descriptor)) {
      const data = await this.io.readJSON(descriptor);
      const health = await this.healthy(data.url);
      if (health) {
        if (health.version !== manifest.version || offline || health.shutdown_requested || !health.managed) {
          await this.stopIdleService(data,health,base);
        } else {
          this.endpoint = {url: data.url, token: (await this.io.readUTF8(this.paths.join(base, "service-token.txt"))).trim()};
          return this.endpoint;
        }
      }
    }
    const runtime = this.paths.join(base, "runtimes", platform === "windows-x64" ? manifest.version : `${manifest.version}-${platform}`);
    const ready = this.paths.join(runtime, ".ready.json");
    let installed = false;
    try { installed = (await this.io.readJSON(ready)).sha256 === manifest.sha256; } catch (_) {}
    if (!installed || offline) {
      this.notify("准备安装引擎（首次需要下载数百 MB）…");
      const downloads = this.paths.join(base, "downloads");
      await this.io.makeDirectory(downloads, { ignoreExisting: true });
      if (this.native.freeSpace(base) < manifest.installed_size * 2 + manifest.size * 2) throw new Error("磁盘空间不足，未更改已有引擎");
      const archive = offline || this.paths.join(downloads, manifest.sha256 + ".part");
      if (!offline) await this.download(manifest, archive);
      this.notify("校验引擎 SHA-256…");
      if ((await this.io.stat(archive)).size !== manifest.size || this.native.sha256(archive) !== manifest.sha256) {
        if (!offline) await this.io.remove(archive, { ignoreAbsent: true });
        throw new Error("引擎大小或 SHA-256 不符；未更改已有引擎，请重新下载正确版本");
      }
      const staging = this.paths.join(base, "runtimes", ".i-" + crypto.randomUUID().slice(0,8));
      await this.io.makeDirectory(staging, { ignoreExisting: true });
      this.notify("解压引擎与模型资源…");
      try {
        await this.native.extract(archive, staging, safeEntry, manifest.installed_size * 1.05, (percent: number) => this.notify(`解压引擎：${percent}%`));
        const metadata = await this.io.readJSON(this.paths.join(staging, "runtime.json"));
        if (metadata.version !== manifest.version || metadata.platform !== platform || metadata.protocol_version !== 2) throw new Error("离线引擎的平台或版本不符，未安装");
        this.notify("自检 Python、模型、字体和分词资源…");
        await this.native.preflight(staging, base);
        await this.io.writeJSON(this.paths.join(staging, ".ready.json"), {sha256: manifest.sha256});
        // Only verified staging can become active. Preserve the previous directory.
        let backup: string | undefined;
        if (await this.io.exists(runtime)) {
          backup = runtime + ".previous-" + Date.now();
          await this.io.move(runtime, backup);
        }
        try { await this.io.move(staging, runtime); }
        catch (error) { if (backup) await this.io.move(backup, runtime); throw error; }
      } catch (error) {
        await this.io.remove(staging, { recursive: true, ignoreAbsent: true });
        throw error;
      }
    }
    this.notify("启动本机后台服务…");
    const child = await this.native.start(runtime, base);
    for (let i = 0; i < 240; i++) {
      await this.native.delay(500);
      if (await this.io.exists(descriptor)) {
        const data = await this.io.readJSON(descriptor);
        const health = await this.healthy(data.url);
        if (health?.managed && health.version === manifest.version) {
          this.endpoint = {url: data.url, token: (await this.io.readUTF8(this.paths.join(base, "service-token.txt"))).trim()};
          this.notify("引擎安装、后台启动与资源自检通过");
          return this.endpoint;
        }
      }
      if (child.exited) throw new Error("后台服务启动失败，请查看本机 logs/service.log");
    }
    throw new Error("后台服务启动超时；请在工作台重试，未提交翻译任务");
  }

  private async download(manifest: Manifest, file: string): Promise<void> {
    if (!manifest.url || !/^https:\/\/github\.com\//.test(manifest.url)) {
      throw new Error("尚未配置正式引擎下载地址，请使用离线导入。");
    }
    let offset = 0;
    if (await this.io.exists(file)) offset = (await this.io.stat(file)).size;
    if (offset === manifest.size) return;
    if (offset > manifest.size) { await this.io.remove(file); offset = 0; }
    const response = await this.request(manifest.url, {credentials: "omit", headers: offset ? {Range: `bytes=${offset}-`} : {}, signal: AbortSignal.timeout(1800000)});
    if (!response.ok || !response.body) throw new Error(`引擎下载失败（${response.status}）。可以导入离线引擎 ZIP。`);
    if (offset && response.status === 206) {
      const range = response.headers.get("Content-Range");
      if (!range?.startsWith(`bytes ${offset}-`) || !range.endsWith(`/${manifest.size}`)) throw new Error("下载续传范围不符，请删除临时下载后重试");
    } else {
      offset = 0;
      await this.io.write(file, new Uint8Array());
    }
    const reader = response.body.getReader();
    // Network chunks can be only a few KB. On Mac, coalesce them so each
    // IOUtils call does useful work instead of reopening the file for each chunk.
    const buffer = manifest.platform.startsWith("macos-") ? new Uint8Array(4 * 1024 * 1024) : null;
    let buffered = 0, lastPercent = -1;
    const flush = async () => {
      if (buffer && buffered) {
        const bytes = buffer.subarray(0, buffered);
        buffered = 0;
        await this.io.write(file, bytes, {mode: "append"});
      }
    };
    try {
      while (true) {
        const {done, value} = await reader.read();
        if (done) break;
        if (offset + value.length > manifest.size) throw new Error("下载内容超过发布清单大小");
        if (buffer) {
          let at = 0;
          while (at < value.length) {
            const count = Math.min(buffer.length - buffered, value.length - at);
            buffer.set(value.subarray(at, at + count), buffered);
            buffered += count;
            at += count;
            if (buffered === buffer.length) await flush();
          }
        } else {
          await this.io.write(file, value, {mode: "append"});
        }
        offset += value.length;
        const percent = Math.floor(offset / manifest.size * 100);
        if (!buffer || percent !== lastPercent) {
          this.notify(`下载引擎：${percent}%（${Math.round(offset / 1048576)} / ${Math.round(manifest.size / 1048576)} MB）`);
          lastPercent = percent;
        }
      }
    } finally {
      // Preserve the received prefix on a network interruption for Range resume.
      try { await flush(); } finally { reader.releaseLock(); }
    }
  }
}

export const runtimeManager = new RuntimeManager(
  typeof RuntimeNative === "undefined" ? {} : RuntimeNative,
  typeof IOUtils === "undefined" ? {} : IOUtils,
  typeof PathUtils === "undefined" ? {} : PathUtils,
  (...args) => fetch(...args));
