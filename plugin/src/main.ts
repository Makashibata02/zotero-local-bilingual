import { runtimeManager } from "./runtime";
declare const RuntimeNative: any;
declare const Zotero: any;
declare const IOUtils: any;
declare const PathUtils: any;
declare const __PLUGIN_VERSION__: string;
const DISPLAY_VERSION = typeof __PLUGIN_VERSION__ === "undefined" ? "development" : __PLUGIN_VERSION__;

const PREF_ROOT = "extensions.zotero.local-bilingual.";
const PLUGIN_ID = "local-bilingual@local.invalid";

type Job = {
  source_title?: string;
  metadata?: Record<string,string>;
  target_language?: string;
  output_mode?: string;
  artifacts?: {kind:string;name:string}[];
  hidden?: boolean;
  is_selftest?: boolean;
  cleanup_state?: string;
  queue_position?: number;
  service_name?: string;
  id: string;
  status: string;
  stage?: string;
  progress: number;
  error?: string;
  artifact_name?: string;
  report_name?: string;
  budget?: {max_total_tokens: number; max_requests: number; can_extend: boolean};
  metrics?: { usage_kind?: string; characters?: number | null; request_count?: number | null; total_tokens?: number | null; elapsed_seconds?: number | null; budget_total_tokens?: number | null; unconfirmed_tokens?: number | null; required_budget_tokens?: number | null };

};

let selectionInProgress = false;
type PendingJob = { id: string; profile: string; title: string; pages: string; options: Record<string, unknown> };
function pendingJobs(): Record<string, PendingJob> {
  try {
    const old = JSON.parse(pref("pendingJobs", "{}"));
    const normalized: Record<string, PendingJob> = {};
    for (const [key,value] of Object.entries(old)) {
      const parts = key.split("|");
      const target = key.startsWith("v3|") ? key : parts.length >= 3 ? `managed|${parts.slice(-2).join("|")}` : key;
      if (normalized[target] && normalized[target].id !== (value as PendingJob).id) throw new Error("同附件存在多个待处理任务");
      normalized[target] = value as PendingJob;
    }
    setPref("pendingJobs",JSON.stringify(normalized));
    return normalized;
  }
  catch (_error) { throw new Error("待处理任务记录损坏，请先检查最近任务报告，避免重复计费。"); }
}
function pendingKey(attachment: any): string {
  return `managed|${attachment.libraryID}|${attachment.key || attachment.id}`;
}
function storePending(key: string, value?: PendingJob): void {
  const jobs = pendingJobs();
  if (value) jobs[key] = value; else delete jobs[key];
  setPref("pendingJobs", JSON.stringify(jobs));
}
const STAGES: Record<string, string> = {
  running:"运行",cancelling:"取消中",cancelled:"已取消",needs_review:"待检查",
  paused: "暂停，等待手动恢复", paused_after_upgrade: "升级后暂停，等待手动恢复",
  budget_limit: "额度已到，等待确认追加",
  queued: "排队", starting_engine: "启动引擎", load_layout_model: "加载版面模型",
  "Parse PDF and Create Intermediate Representation": "解析 PDF",
  "DetectScannedFile": "检查文本层", "Parse Page Layout": "识别版面",
  "Parse Paragraphs": "整理段落", "Parse Formulas and Styles": "识别公式与样式",
  "Translate Paragraphs": "翻译正文", "Typesetting": "排版",
  "Add Fonts": "嵌入字体", "Generate drawing instructions": "生成页面",
  "Subset font": "整理字体", "Save PDF": "保存 PDF", combining_pages: "生成左右对照",
  completed: "完成", failed: "失败"
};
function usageText(job: Job): string {
  const parts: string[] = [];
  if (job.metrics?.usage_kind === "characters" && job.metrics.characters != null) parts.push(`${job.metrics.characters} 字符`);
  if (job.metrics?.usage_kind !== "characters" && job.metrics?.total_tokens != null) parts.push(`${job.metrics.total_tokens} tokens`);
  if ((job.metrics?.unconfirmed_tokens || 0) > 0) parts.push(`${job.metrics!.unconfirmed_tokens} tokens 用量待确认，已预留`);
  if (job.budget) {
    parts.push(`累计上限 ${job.budget.max_total_tokens} tokens / ${job.budget.max_requests} 次请求`);
    if (job.metrics?.required_budget_tokens != null && job.budget.can_extend) parts.push(`下一请求含预留需累计上限至少 ${job.metrics.required_budget_tokens} tokens`);
  }
  if (job.metrics?.request_count != null) parts.push(`${job.metrics.request_count} 次请求`);
  if (job.metrics?.elapsed_seconds != null) parts.push(`${Math.round(job.metrics.elapsed_seconds)} 秒（含排队）`);
  return parts.length ? ` · ${parts.join(" · ")}` : "";
}

function pref(name: string, fallback = ""): string {
  const value = Zotero.Prefs.get(PREF_ROOT + name, true);
  return value === undefined || value === null ? fallback : String(value);
}

function setPref(name: string, value: string): void {
  Zotero.Prefs.set(PREF_ROOT + name, value, true);
}

function elementValue(document: Document, id: string): string {
  return String((document.getElementById(id) as HTMLInputElement | null)?.value || "").trim();
}

function setStatus(document: Document, text: string, id = "zlb-test-status"): void {
  const status = document.getElementById(id);
  if (status) status.textContent = text;
}

let workbenchWindow: any;
let engineStatus = "等待用户开始测试或翻译";
async function ensureEngineReady(feedback: (message: string) => void = () => {}): Promise<void> {
  const endpoint = await runtimeManager.ensure(message => {
    engineStatus = message;
    feedback(message);
    if (workbenchWindow && !workbenchWindow.closed) {
      const status = workbenchWindow.document.getElementById("zlb-engine-status");
      if (status) status.textContent = message;
    }
  });
  setPref("serviceUrl", endpoint.url);
  setPref("serviceToken", endpoint.token);
}

function openWorkbench(showCurrent = false): void {
  if (workbenchWindow && !workbenchWindow.closed) {
    workbenchWindow.focus();
    if (showCurrent) plugin.workbench.showCurrent(workbenchWindow);
    return;
  }
  workbenchWindow = Zotero.getMainWindow().openDialog("chrome://local-bilingual/content/workbench.xhtml",
    "zlb-workbench", "chrome,dialog=no,resizable,centerscreen,width=900,height=640", plugin);
  if (!workbenchWindow) throw new Error("Zotero 未能打开任务工作台，请从插件设置手动打开");
  const opened=workbenchWindow;
  opened._zlbShowCurrent=showCurrent;
  opened.addEventListener("load",()=> {
    if (!opened.document.getElementById("zlb-jobs")) return; // Ignore about:blank.
    void plugin.workbench.onLoad(opened).catch((error:any)=> {
      Zotero.logError(error);
      setStatus(opened.document,`工作台初始化失败：${String(error)}`,"zlb-workbench-status");
    });
    opened.focus();
  });
  opened.addEventListener("unload",()=> {
    if (opened._zlbLoaded) {opened.clearTimeout?.(opened._zlbTimer);if(workbenchWindow===opened) workbenchWindow=undefined;}
  });
  opened.focus();
}

async function openJobResult(job: Job, artifactName = job.artifact_name): Promise<void> {
  if (!artifactName) throw new Error("测试文件已清理，或任务尚无成品 PDF");
  const response = await api(`/v1/jobs/${encodeURIComponent(job.id)}/artifacts/${encodeURIComponent(artifactName)}`);
  const folder=PathUtils.join(PathUtils.tempDir,`zlb-${job.is_selftest ? "test-viewer" : "viewer"}-${job.id.slice(0,8)}`);
  await IOUtils.makeDirectory(folder,{ignoreExisting:true});
  const path = PathUtils.join(folder, artifactName);
  await IOUtils.write(path, new Uint8Array(await response.arrayBuffer()));
  await Zotero.launchFile(path);
  if (job.is_selftest) {
    setPref("testViewerPath",path);
    const copies = JSON.parse(pref("testViewerCopies","[]"));
    if (!copies.includes(path)) {copies.push(path); setPref("testViewerCopies",JSON.stringify(copies));}
    try {await api(`/v1/jobs/${job.id}/cleanup-test`, {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({viewer_copy_opened:true})});}
    catch (error) {Zotero.logError(error);} // A cleanup failure never changes translation success.
  }
}

async function api(path: string, init: RequestInit = {}): Promise<Response> {
  const serviceUrl = pref("serviceUrl", "http://127.0.0.1:17865").replace(/\/$/, "");
  const headers = new Headers(init.headers || {});
  if (path !== "/v1/health") {
    const token = pref("serviceToken");
    if (!token) throw new Error("尚未填写本机服务令牌");
    headers.set("X-Local-Bilingual-Token", token);
  }
  const response = await fetch(serviceUrl + path, { ...init, headers });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") {
        detail = body.detail;
      } else if (Array.isArray(body.detail)) {
        const names: Record<string, string> = {
          base_url: "API Base URL", model: "模型名称", api_key: "API Key",
          id: "配置 ID", qps: "请求速率"
        };
        detail = body.detail.map((issue: any) => {
          const field = String(issue.loc?.[issue.loc.length - 1] || "配置");
          return `${names[field] || field}：${issue.msg || "填写格式不正确"}`;
        }).join("\n");
      }
    } catch (_error) {
      // Keep the HTTP status if the response is not JSON.
    }
    const error = new Error(detail) as Error & { status?: number };
    error.status = response.status;
    throw error;
  }
  return response;
}

async function selectedPdfAttachments(): Promise<any[]> {
  const pane = Zotero.getActiveZoteroPane();
  const selected = pane?.getSelectedItems() || [];
  const explicit = new Set(selected.filter((item:any)=>item.isAttachment?.() && item.isPDFAttachment?.()).map((item:any)=>item.id));
  const attachments: any[] = [];
  for (const item of selected) {
    if (item.isAttachment?.() && item.isPDFAttachment?.()) {
      item._zlbDefault = true;
      attachments.push(item);
      continue;
    }
    if (item.isRegularItem?.()) {
      const ids = item.getAttachments?.() || [];
      const best = await item.getBestAttachment?.();
      let foundDefault = false;
      const candidates:any[]=[];
      for (const id of ids) {
        const attachment = await Zotero.Items.getAsync(id);
        if (attachment?.isPDFAttachment?.() && !/^(双语|译文)（本地）/.test(String(attachment.getField?.("title") || ""))) {
          attachment._zlbDefault = explicit.has(attachment.id) || (best ? attachment.id === best.id : !foundDefault);
          if (attachment._zlbDefault) foundDefault = true;
          attachments.push(attachment);
          candidates.push(attachment);
        }
      }
      if (!foundDefault && candidates.length) candidates[0]._zlbDefault=true;
    }
  }
  return attachments.filter(
    (attachment, index, all) =>
      all.findIndex((candidate) => candidate.id === attachment.id) === index
  );
}

function makeProgress(title: string): {
  update: (text: string, percent?: number) => void;
  done: (text: string) => void;
  fail: (text: string) => void;
} {
  const window = new Zotero.ProgressWindow({ closeOnClick: true });
  let closed = false;
  const close = window.close.bind(window);
  window.close = () => { closed = true; close(); };
  window.changeHeadline(title);
  window.addDescription?.("点击此窗口即可关闭；关闭提示不会取消正在运行的翻译。");
  // addLines() returns void; ItemProgress is Zotero's updatable row API.
  const line = new window.ItemProgress("attachment-pdf", "准备任务…");
  window.show();
  return {
    update(text, percent) {
      if (closed) return;
      line.setText?.(text);
      if (percent !== undefined) line.setProgress?.(Math.max(0, Math.min(100, percent)));
    },
    done(text) {
      if (closed) return;
      line.setText?.(text);
      line.setProgress?.(100);
      window.startCloseTimer?.(5000);
    },
    fail(text) {
      if (closed) return;
      line.setText?.(text);
      line.setError?.();
    }
  };
}

async function importArtifact(attachment: any, job: Job, artifact = {kind:"dual",name:job.artifact_name!}): Promise<any> {
  if (!artifact.name) throw new Error("任务完成但没有 PDF 产物");
  const importedMap = JSON.parse(pref("importedArtifacts","{}"));
  const identity = `${attachment.libraryID}|${job.id}|${artifact.kind}`;
  if (importedMap[identity]) {
    const existing = await Zotero.Items.getAsync(importedMap[identity]);
    if (existing) return existing; // A user's deletion must not be undone by a refresh.
  }
  const productTitle = `${artifact.kind === "mono" ? "译文" : "双语"}（本地）${job.source_title || attachment.getField("title") || "PDF"} [${job.target_language || "zh-CN"};${job.id};${artifact.kind}]`;
  // Recover an import interrupted after saveTx but before the preference was written.
  if (attachment.parentItemID) {
    const parent = await Zotero.Items.getAsync(attachment.parentItemID);
    for (const id of parent?.getAttachments?.() || []) {
      const existing = await Zotero.Items.getAsync(id);
      if (existing?.getField("title") === productTitle) {importedMap[identity]=id; setPref("importedArtifacts",JSON.stringify(importedMap)); return existing;}
    }
  }
  const response = await api(
    `/v1/jobs/${encodeURIComponent(job.id)}/artifacts/${encodeURIComponent(artifact.name)}`
  );
  const bytes = new Uint8Array(await response.arrayBuffer());
  const tempFolder=PathUtils.join(PathUtils.tempDir,`zlb-${job.id.slice(0,8)}`);
  await IOUtils.makeDirectory(tempFolder,{ignoreExisting:true});
  const tempPath = PathUtils.join(tempFolder,artifact.name);
  await IOUtils.write(tempPath, bytes);
  const options: Record<string, unknown> = { file: tempPath };
  if (attachment.parentItemID) {
    options.parentItemID = attachment.parentItemID;
  } else {
    options.libraryID = attachment.libraryID;
  }
  const imported = await Zotero.Attachments.importFromFile(options);
  imported.setField(
    "title",
    productTitle
  );
  await imported.saveTx();
  importedMap[identity] = imported.id; setPref("importedArtifacts",JSON.stringify(importedMap));
  return imported;
}

type Selection = {profile:string;provider:string;target:string;output:string;parallel:number;batch:string};
const monitors = new Set<string>();
const artifactImports = new Set<string>();
async function attachmentMetadata(attachment: any): Promise<Record<string,string>> {
  const parent = attachment.parentItemID ? await Zotero.Items.getAsync(attachment.parentItemID) : undefined;
  const file = await attachment.getFilePathAsync();
  return {title:String(parent?.getField("title") || PathUtils.filename(file || "PDF")),
    authors_year:parent ? `${parent.getField("firstCreator") || ""} · ${parent.getField("date") || ""}` : "",
    citation_label:parent ? `${parent.getField("firstCreator") || "作者"} - ${String(parent.getField("date") || "").match(/\d{4}/)?.[0] || "无年份"} - ${parent.getField("title") || attachment.getField("title") || "PDF"}` : PathUtils.filename(file || "PDF").replace(/\.pdf$/i,""),
    filename:PathUtils.filename(file || "PDF"), attachment_id:String(attachment.id), library_id:String(attachment.libraryID), parent_id:String(attachment.parentItemID || "")};
}
async function submitAttachment(attachment: any, selection?: Selection): Promise<void> {
  const key = selection ? `v3|${attachment.libraryID}|${attachment.key || attachment.id}|${selection.profile}|${selection.target}|${selection.output}` : pendingKey(attachment);
  const metadata = await attachmentMetadata(attachment);
  const title = metadata.title;
  let progress: ReturnType<typeof makeProgress> | undefined;
  try {
    progress = makeProgress(`本地双语 PDF：${title}`);
    openWorkbench();
    await ensureEngineReady(message => progress?.update(message, 1));
    const health = await (await api("/v1/health")).json();
    if (health.protocol_version !== 2 || !health.managed || (DISPLAY_VERSION!=="development" && health.version!==DISPLAY_VERSION)) {
      throw new Error(`需要与插件 ${DISPLAY_VERSION} 匹配的自动引擎；请关闭旧服务并导入匹配引擎`);
    }
    let pending = pendingJobs()[key];
    let job: Job | undefined;
    if (pending) {
      progress.update("恢复已有任务状态…", 1);
      try { job = await (await api(`/v1/jobs/${encodeURIComponent(pending.id)}`)).json() as Job; }
      catch (error) { if ((error as { status?: number }).status !== 404) throw error; }
    }
    if (!job) {
      const filePath = await attachment.getFilePathAsync();
      if (!filePath) throw new Error("附件文件不在本机");
      progress.update("读取 PDF…", 1);
      const bytes = await IOUtils.read(filePath);
      pending = pending || {
        id: crypto.randomUUID().replace(/-/g, ""),
        profile: selection?.profile || pref("profileId", "default"), title, pages: pref("pages"),
        options: {
          target_language: selection?.target || "zh-CN", output_mode:selection?.output || "dual",
          primary_font_family: pref("primaryFontFamily", "serif"),
          custom_system_prompt: ["bing", "tencent"].includes(selection?.provider || pref("provider", "bing")) ? "" : pref("customSystemPrompt"),
          glossary_text: ["bing", "tencent"].includes(selection?.provider || pref("provider", "bing")) ? "" : pref("glossaryText"),
          disable_rich_text_translate: pref("disableRichText", "false") === "true",
          thinking_mode: pref("thinkingMode", "auto"),
          auto_extract_glossary: !["bing", "tencent"].includes(selection?.provider || pref("provider", "bing")) && pref("autoExtractGlossary", "false") === "true",
          max_total_tokens: Number(pref("maxTotalTokens", "1000000")),
          max_requests: Number(pref("maxRequests", "1000")),
          free_max_requests: Number(pref("freeMaxRequests", "2000")),
          max_characters: Number(pref("maxCharacters", "1000000"))
        }
      };
      // Persist before POST: a lost response can be retried with the same request ID.
      storePending(key, pending);
      progress.update("上传 PDF，创建任务…", 2);
      const form = new FormData();
      form.append("file", new Blob([bytes], { type: "application/pdf" }), metadata.filename);
      form.append("metadata_json",JSON.stringify({...metadata,batch_id:selection?.batch || ""}));
      form.append("client_request_id", pending.id);
      form.append("profile_id", pending.profile);
      form.append("source_title", pending.title);
      if (pending.pages) form.append("pages", pending.pages);
      form.append("options_json", JSON.stringify(pending.options));
      try { job = await (await api("/v1/jobs", { method: "POST", body: form })).json() as Job; }
      catch (error) {
        const status = (error as { status?: number }).status;
        // Definite validation/auth failures create no job; transport/5xx errors are ambiguous.
        if (status && [400, 401, 403, 404, 413, 422].includes(status)) storePending(key);
        throw error;
      }
    }
    setPref("lastJobId", job.id);
    if (selection) {
      if (!monitors.has(job.id)) {
        monitors.add(job.id);
        void monitorJob(attachment,job,key,progress).finally(() => monitors.delete(job!.id));
      }
      return;
    }
    await monitorJob(attachment,job,key,progress);
  } catch (error) {
    Zotero.logError(error);
    try {if (!progress) throw error;progress.fail(String(error));}
    catch (_displayError) {Zotero.alert(null,"本地双语 PDF",`任务失败：${String(error)}`);}
  }
}
async function monitorJob(attachment:any,job:Job,key:string,progress:ReturnType<typeof makeProgress>) {
  try {
    while (["queued", "running", "cancelling"].includes(job.status)) {
      progress.update(`${STAGES[job.stage || job.status] || job.stage || job.status}（${Math.round(job.progress)}%）${usageText(job)}`, job.progress);
      await Zotero.Promise.delay(2000);
      job = (await (await api(`/v1/jobs/${encodeURIComponent(job.id)}`)).json()) as Job;
    }
    if (job.status !== "completed") {
      if (job.budget?.can_extend) {
        progress.update(`任务因额度上限暂停。请在工作台点击“确认追加额度并继续”。${usageText(job)}`,job.progress);
        return;
      }
      storePending(key);
      throw new Error(`${job.error || `任务状态：${job.status}`}\n任务 ${job.id}${usageText(job)}`);
    }
    progress.update("导入 Zotero 附件…", 100);
    let imported: any;
    for (const artifact of job.artifacts?.length ? job.artifacts : [{kind:"dual",name:job.artifact_name!}]) imported = await importArtifact(attachment, job, artifact);
    storePending(key);
    progress.done(`所选 PDF 产物已导入${usageText(job)}`);
    try {
      await Zotero.Reader.open(imported.id);
    } catch (_error) {
      // Import succeeded; opening is a convenience only.
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    Zotero.logError(error);
    try {
      if (!progress) throw error;
      progress.fail(message);
    } catch (_displayError) {
      Zotero.alert(null, "本地双语 PDF", `任务失败：${message}`);
    }
  }
}

async function translateSelection(): Promise<void> {
  if (selectionInProgress) {
      Zotero.alert(null, "本地双语 PDF", "正在提交当前批次，请等待上传入队完成。");
    return;
  }
  selectionInProgress = true;
  try {
    const attachments = await selectedPdfAttachments();
    if (!attachments.length) {
      Zotero.alert(null, "本地双语 PDF", "请选择一个 PDF 附件或含 PDF 的文献条目。");
      return;
    }
    await ensureEngineReady();
    await plugin.preferences.initializeDefaultProfile();
    const profiles = await (await api("/v1/profiles")).json();
    const capabilities = await (await api("/v1/capabilities")).json();
    const scheduler = await (await api("/v1/scheduler")).json();
    const entries = await Promise.all(attachments.map(async attachment => ({attachment, metadata:await attachmentMetadata(attachment)})));
    const choice: any = await new Promise(resolve => {
      const parent=Zotero.getMainWindow();
      let finished=false, closeTimer:any;
      const finish=(value:any)=> {
        if (finished) return;
        finished=true;
        if (closeTimer!==undefined) parent.clearInterval(closeTimer);
        resolve(value);
      };
      const args = {plugin,entries,profiles,capabilities,parallel:scheduler.max_parallel,ready:false,resolve:finish};
      const dialog = parent.openDialog("chrome://local-bilingual/content/confirm.xhtml", "zlb-confirm", "chrome,dialog,resizable,centerscreen,width=560,height=640",args);
      // Chrome windows can unload their initial about:blank before the real UI.
      // Only the loaded confirmation document can cancel this selection.
      dialog.addEventListener("unload",() => {if (args.ready) finish(null);});
      if (!finished && parent.setInterval) closeTimer=parent.setInterval(()=>{if(dialog.closed) finish(null);},250);
    });
    if (!choice) return;
    engineStatus=`已确认 ${choice.indices.length} 份 PDF，正在准备上传入队…`;
    openWorkbench(true);
    const selectedProfile = profiles.find((p:any) => p.id === choice.profile);
    if (!selectedProfile) {
      const body={id:`profile-${crypto.randomUUID().replace(/-/g,"").slice(0,16)}`,name:choice.provider === "bing" ? "免费 Bing" : "免费腾讯",provider:choice.provider};
      await api("/v1/profiles",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
      choice.profile=body.id;
    }
    await api("/v1/scheduler",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({max_parallel:choice.parallel})});
    const selection = {...choice,batch:crypto.randomUUID().replace(/-/g,"")};
    for (const index of choice.indices) await submitAttachment(entries[index].attachment,selection);
    if (workbenchWindow && !workbenchWindow.closed) void plugin.workbench.refresh(workbenchWindow,false);
  } catch(error) {
    if (workbenchWindow && !workbenchWindow.closed) setStatus(workbenchWindow.document,`提交失败：${String(error)}`,"zlb-workbench-status");
    throw error;
  } finally { selectionInProgress = false; }
}

const plugin = {
  windows: new Set<any>(),
  preferencePaneID: "",
  confirmation: {
    cancel(window:any) {window.arguments[0].resolve(null);window.close();},
    choice(window:any, group:string): any {
      const element=window.document.getElementById(group);
      return element.localName === "select" ? element.selectedOptions[0] : element.querySelector("input:checked");
    },
    renderChoices(window:any, group:string, items:any[], preferred:string) {
      const d=window.document, container=d.getElementById(group);
      container.replaceChildren();
      if (container.localName === "select") {
        const chosen=items.find(item=>item.value===preferred && !item.disabled);
        if (!chosen) {
          const placeholder=d.createElementNS("http://www.w3.org/1999/xhtml","option");
          placeholder.value="";placeholder.textContent="请选择已支持的选项";placeholder.disabled=true;placeholder.selected=true;container.append(placeholder);
        }
        for (const item of items) {
          const option=d.createElementNS("http://www.w3.org/1999/xhtml","option");
          option.value=item.value;option.textContent=item.title+(item.disabled ? "（未支持／未验收）" : "");
          option.disabled=!!item.disabled;option.selected=item===chosen;option.title=option.textContent;
          if (item.provider) option.dataset.provider=item.provider;
          container.append(option);
        }
        return;
      }
      const chosen=items.find(item=>item.value===preferred && !item.disabled) || items.find(item=>!item.disabled);
      for (const item of items) {
        const label=d.createElementNS("http://www.w3.org/1999/xhtml","label");
        label.className="choice"+(item.disabled ? " unavailable" : "")+(item===chosen ? " selected" : "");
        const input=d.createElementNS("http://www.w3.org/1999/xhtml","input");
        input.type="radio";input.name=`zlb-${group}`;input.value=item.value;
        input.checked=item===chosen;input.disabled=!!item.disabled;
        if (item.provider) input.dataset.provider=item.provider;
        const text=d.createElementNS("http://www.w3.org/1999/xhtml","span");
        const title=d.createElementNS("http://www.w3.org/1999/xhtml","strong");
        title.textContent=item.title;text.append(title);
        if (item.description) {
          const detail=d.createElementNS("http://www.w3.org/1999/xhtml","small");
          detail.textContent=item.description;text.append(detail);
        }
        input.addEventListener("change",()=> {
          for (const card of container.querySelectorAll(".choice")) card.classList.toggle("selected",!!card.querySelector("input").checked);
          plugin.confirmation.changed(window);
        });
        label.append(input,text);container.append(label);
      }
    },
    onLoad(window:any) {
      const args=window.arguments[0], d=window.document;
      args.ready=true;
      const models=args.profiles.map((p:any)=>({value:p.id,title:p.name+(p.model ? " · "+p.model : "")+(p.is_default ? "（默认）" : ""),provider:p.provider,disabled:p.provider==="openai" && p.has_api_key===false}));
      for (const [provider,id,name] of [["bing","free-bing","免费 Bing（实验性）"],["tencent","free-tencent","免费腾讯（实验性）"]]) {
        if (!models.some((p:any)=>p.provider===provider)) models.push({value:id,title:name,provider,disabled:false});
      }
      const defaultProfile=args.profiles.find((p:any)=>p.is_default && p.provider==="openai" && p.has_api_key!==false);
      const preferred=defaultProfile?.id || models.find((p:any)=>p.provider==="bing")?.value || "free-bing";
      plugin.confirmation.renderChoices(window,"model",models,preferred);
      d.getElementById("model-note").textContent=defaultProfile ? "本次切换不会改变默认模型。" : "尚未设置默认 API 模型；默认使用免费 Bing，也可手动选择其他服务。";
      plugin.confirmation.renderChoices(window,"output",[
        {value:"dual",title:"双语",description:"一份 PDF：左侧完整原页，右侧对应译文页。"},
        {value:"dual_and_mono",title:"双语＆译文",description:"两份 PDF：左右对照版和独立译文版。只翻译一次。"},
        {value:"mono",title:"译文",description:"一份 PDF：仅包含译文页。原附件保持不变。"}
      ],"dual");
      plugin.confirmation.renderChoices(window,"parallel",[
        {value:"1",title:"1 篇",description:"默认串行排队。"},
        {value:"2",title:"2 篇",description:"API 可并行；免费仍最多一篇。"}
      ],String(args.parallel));
      args.entries.forEach((entry:any,index:number) => {
        const label=d.createElementNS("http://www.w3.org/1999/xhtml","label");
        const box=d.createElementNS("http://www.w3.org/1999/xhtml","input");
        box.type="checkbox";box.value=String(index);box.checked=entry.attachment._zlbDefault!==false;
        const text=d.createElementNS("http://www.w3.org/1999/xhtml","span");
        text.textContent=`${entry.metadata.title}\n${entry.metadata.authors_year}\n附件：${entry.metadata.filename}`;
        label.append(box,text); d.getElementById("files").append(label);
      });
      plugin.confirmation.changed(window);
    },
    changed(window:any) {
      const d=window.document,args=window.arguments[0];
      const provider=plugin.confirmation.choice(window,"model")?.dataset.provider || "openai";
      const language=d.getElementById("language");
      // Rebuild only when the provider changes; preserve keyboard focus otherwise.
      if (language.dataset.provider!==provider) {
        const previous=plugin.confirmation.choice(window,"language")?.value || "zh-CN";
        const items=args.capabilities.languages.map((lang:any)=>({value:lang.id,title:lang.name,disabled:!args.capabilities.providers[provider]?.[lang.id]?.supported}));
        const previousSupported=items.some((lang:any)=>lang.value===previous && !lang.disabled);
        const fallback=items.some((lang:any)=>lang.value==="zh-CN" && !lang.disabled) ? "zh-CN" : "";
        plugin.confirmation.renderChoices(window,"language",items,previousSupported ? previous : fallback);
        d.getElementById("language-note").textContent=previousSupported ? "" : fallback ? "所选服务不支持刚才的语言，已切换为简体中文。" : "所选服务不支持简体中文，请手动选择已支持的语言。";
        language.dataset.provider=provider;
      }
      const count=[...d.querySelectorAll("#files input")].filter((x:any)=>x.checked).length;
      const tokens=Number(pref("maxTotalTokens","1000000")), requests=Number(pref("maxRequests","1000"));
      d.getElementById("limits").textContent=`已选择 ${count} / ${args.entries.length} 份 PDF。`+(provider==="openai" ? `\n每篇上限 ${tokens} tokens / ${requests} 请求；合计 ${count*tokens} tokens / ${count*requests} 请求。人民币费用取决于服务商。` : "\n免费服务按字符计量，每次最多一篇运行，不会自动切换收费 API。")+(provider==="openai" && plugin.confirmation.choice(window,"parallel")?.value==="2" ? "\n两篇并行会同时消耗 API 额度。" : "");
      const selectedModel=plugin.confirmation.choice(window,"model"), selectedLanguage=plugin.confirmation.choice(window,"language");
      d.getElementById("confirm").disabled=!count || !selectedModel || selectedModel.disabled || !selectedLanguage || selectedLanguage.disabled;
      d.getElementById("error").textContent="";
    },
    accept(window:any) {
      const d=window.document,indices=[...d.querySelectorAll("#files input")].filter((x:any)=>x.checked).map((x:any)=>Number(x.value));
      const option=plugin.confirmation.choice(window,"model"), language=plugin.confirmation.choice(window,"language");
      if (!indices.length || !option || option.disabled || !language || language.disabled) {d.getElementById("error").textContent="请勾选至少一份 PDF，并选择已支持的目标语言。";return;}
      window.arguments[0].resolve({indices,profile:option.value,provider:option.dataset.provider,target:language.value,output:plugin.confirmation.choice(window,"output").value,parallel:Number(plugin.confirmation.choice(window,"parallel").value)});window.close();
    }
  },

  hooks: {
    async onStartup() {
      await Zotero.initializationPromise;
      if (pref("defaults030")!=="true") {
        setPref("maxTotalTokens","1000000");setPref("maxRequests","1000");setPref("freeMaxRequests","2000");setPref("maxCharacters","1000000");setPref("defaults030","true");
      }
      const remaining: string[] = [];
      for (const path of JSON.parse(pref("testViewerCopies","[]"))) {
        const parent=PathUtils.parent(path);
        const ownFolder=PathUtils.filename(parent).startsWith("zlb-test-viewer-") && PathUtils.parent(parent)===PathUtils.tempDir;
        const legacy=PathUtils.filename(path).startsWith("zlb-test-") && parent===PathUtils.tempDir;
        if (!ownFolder && !legacy) {remaining.push(path); continue;}
        try {await IOUtils.remove(path,{ignoreAbsent:true});if(ownFolder) await IOUtils.remove(parent,{ignoreAbsent:true});} catch (_error) {remaining.push(path);}
      }
      setPref("testViewerCopies",JSON.stringify(remaining)); setPref("testViewerPath","");
      if (!pref("provider")) {
        const legacy = !!pref("serviceToken");
        setPref("provider", legacy ? "openai" : "bing");
        if (legacy) setPref("migrationNotice", "true");
      }
      plugin.preferencePaneID = Zotero.PreferencePanes.register({
        pluginID: PLUGIN_ID,
        src: "chrome://local-bilingual/content/preferences.xhtml",
        label: "本地双语 PDF",
        image: "chrome://local-bilingual/content/icon.svg"
      });
      await Zotero.uiReadyPromise;
      for (const window of Zotero.getMainWindows()) {
        await plugin.hooks.onMainWindowLoad(window);
      }
      Zotero.debug("LocalBilingual startup complete");
    },

    async onMainWindowLoad(window: any) {
      await Zotero.uiReadyPromise;
      const document = window.document as Document;
      if (document.getElementById("zlb-translate-menuitem")) return;
      const menu = document.getElementById("zotero-itemmenu");
      if (!menu) return;
      const item = (document as any).createXULElement("menuitem");
      item.id = "zlb-translate-menuitem";
      item.setAttribute("label", "翻译 PDF…（选择模型、语言与输出）");
      item.setAttribute("class", "menuitem-iconic");
      item.setAttribute("image", "chrome://local-bilingual/content/icon.svg");
      item.addEventListener("command", () => void translateSelection().catch((error) => {
        Zotero.logError(error);
        Zotero.alert(null, "本地双语 PDF", `任务失败：${String(error)}`);
      }));
      menu.appendChild(item);
      plugin.windows.add(window);
    },

    async onMainWindowUnload(window: any) {
      window.document.getElementById("zlb-translate-menuitem")?.remove();
      plugin.windows.delete(window);
    },

    onShutdown() {
      for (const window of plugin.windows) {
        window.document.getElementById("zlb-translate-menuitem")?.remove();
      }
      plugin.windows.clear();
      workbenchWindow?.close();
      Zotero.LocalBilingual = undefined;
    }
  },

  workbench: {
    showCurrent(window:any) {
      const d=window.document;
      if (!d.getElementById("zlb-jobs")) {window._zlbShowCurrent=true;return;}
      window._zlbOffset=0;
      const search=d.getElementById("zlb-search"),filter=d.getElementById("zlb-filter"),hidden=d.getElementById("zlb-hidden");
      if (search) search.value="";
      if (filter) filter.value="";
      if (hidden) hidden.checked=false;
      window._zlbShowCurrent=false;
      if (window._zlbLoaded) void plugin.workbench.refresh(window,false);
    },
    async setParallel(window:any) {
      const d=window.document;
      const feedback=(message:string)=>setStatus(d,message,"zlb-parallel-status");
      try {
        const value=Number(elementValue(d,"zlb-parallel"));
        if (value===2 && !window.confirm("开启两篇并行会让 API 任务同时消耗额度。免费任务仍最多一篇。确认启用？")) {feedback("未更改同时处理上限。");return;}
        await ensureEngineReady(feedback);
        await api("/v1/scheduler",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({max_parallel:value})});
        feedback(`已设置同时处理上限 ${value}；已有运行任务不会被强制中断。`);
      } catch(error) {feedback(`设置失败：${String(error)}`);}
    },
      async extendBudget(window: any, job: Job): Promise<void> {
        if (!job.budget?.can_extend) throw new Error("此任务不能追加额度，请刷新工作台");
        const old = job.budget;
        const tokens = window.prompt(`任务 ${job.id.slice(0,8)}：已用 ${job.metrics?.total_tokens ?? "未知"} tokens，${job.metrics?.request_count ?? "未知"} 次请求。\n当前累计上限 ${old.max_total_tokens} tokens。\n填写新的累计 token 上限（不是新增数量，最高 2000000）：`,
          String(Math.min(2000000, Math.max(old.max_total_tokens + 60000, job.metrics?.required_budget_tokens || 0))));
        if (tokens === null) throw new Error("未确认追加额度，任务保持暂停。");
        const requests = window.prompt(`当前累计请求上限 ${old.max_requests}。\n填写新的累计请求上限（最高 5000）：`,String(Math.min(5000,old.max_requests+100)));
        if (requests === null) throw new Error("未确认追加额度，任务保持暂停。");
        const maxTokens = Number(tokens), maxRequests = Number(requests);
        if (!Number.isInteger(maxTokens) || maxTokens < old.max_total_tokens || maxTokens > 2000000 ||
            !Number.isInteger(maxRequests) || maxRequests < old.max_requests || maxRequests > 5000 ||
            (maxTokens === old.max_total_tokens && maxRequests === old.max_requests)) throw new Error("新上限需至少增加一项，不能降低原上限。");
        if (!window.confirm(`确认继续调用你的收费 API？\n已用 ${job.metrics?.total_tokens ?? "未知"} tokens；待确认预留 ${job.metrics?.unconfirmed_tokens || 0} tokens。\n累计上限：${old.max_total_tokens} → ${maxTokens} tokens\n请求上限：${old.max_requests} → ${maxRequests}\n本次增加上限 ${maxTokens-old.max_total_tokens} tokens / ${maxRequests-old.max_requests} 次请求。\n缓存和累计用量保留。人民币费用取决于服务商，追加后可能继续扣费。`)) throw new Error("未确认追加额度，任务保持暂停。");
        const approvals = JSON.parse(pref("pendingBudgetApprovals","{}"));
        const values = {confirmed:true, expected_max_total_tokens:old.max_total_tokens, expected_max_requests:old.max_requests,
          max_total_tokens:maxTokens, max_requests:maxRequests};
        const previous = approvals[job.id];
        const payload = previous && Object.entries(values).every(([key,value]) => previous[key] === value)
          ? previous : {...values, client_request_id:crypto.randomUUID().replace(/-/g,"")};
        approvals[job.id] = payload; setPref("pendingBudgetApprovals",JSON.stringify(approvals));
        await api(`/v1/jobs/${job.id}/extend-budget`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
        delete approvals[job.id]; setPref("pendingBudgetApprovals",JSON.stringify(approvals));
      },
    async onLoad(window: any) {
      if (window._zlbLoaded || !window.document.getElementById("zlb-jobs")) return;
      window._zlbLoaded=true;
      window.document.getElementById("zlb-engine-status").textContent = engineStatus;
      if (window._zlbShowCurrent) plugin.workbench.showCurrent(window);
      // Responsive widths replace the old saved pixel widths; leave legacy preferences untouched.
      window._zlbOpenDetails ||= new Set<string>();
      await plugin.workbench.refresh(window, false);
    },
    async refresh(window: any, start = true) {
      if (window.closed || window._zlbRefreshing || window._zlbActions || !window.document.getElementById("zlb-jobs")) return;
      window._zlbRefreshing = true;
      window.clearTimeout(window._zlbTimer);
      const document = window.document as Document;
      const feedback = (message: string) => setStatus(document, message, "zlb-workbench-status");
      try {
        if (start) await ensureEngineReady(feedback);
        const offset=window._zlbOffset || 0;
        const query=new URLSearchParams({limit:"25",offset:String(offset),search:elementValue(document,"zlb-search"),status:elementValue(document,"zlb-filter"),include_hidden:String((document.getElementById("zlb-hidden") as HTMLInputElement)?.checked || false)});
        const jobs = await (await api(`/v1/jobs?${query}`)).json() as Job[];
        const scheduler=await (await api("/v1/scheduler")).json();
        const parallel=document.getElementById("zlb-parallel") as HTMLSelectElement;
        if (parallel && document.activeElement!==parallel) parallel.value=String(scheduler.max_parallel);
        setStatus(document,`运行 ${scheduler.counts.running || 0} · 排队 ${scheduler.counts.queued || 0} · 暂停 ${scheduler.counts.paused || 0} · 失败 ${scheduler.counts.failed || 0} · 完成 ${scheduler.counts.completed || 0} · 同时处理上限 ${scheduler.max_parallel}`,"zlb-counts");
        setStatus(document,`第 ${Math.floor(offset/25)+1} 页（每页最多 25 条）`,"zlb-page");
        const container = document.getElementById("zlb-jobs")!;
        container.replaceChildren();
        for (const job of jobs) {
          const make = (tag:string, className="") => {
            const node=document.createElementNS("http://www.w3.org/1999/xhtml",tag) as HTMLElement;
            node.className=className;return node;
          };
          const row=make("tr","job-row");row.dataset.jobId=job.id;
          const cell=(className:string) => {const td=make("td",className);row.append(td);return td;};
          const line=(parent:HTMLElement,text:string,className="") => {
            const node=make("span",`ellipsis ${className}`);node.textContent=text;node.title=text;parent.append(node);return node;
          };
          const titleText=job.metadata?.title || job.source_title || job.metadata?.filename || "PDF";
          const filename=job.metadata?.filename || "旧任务：文件名未记录";
          const language=job.target_language || "zh-CN", service=job.service_name || "旧配置";
          const mode=({dual:"双语",mono:"译文",dual_and_mono:"双语＆译文"} as any)[job.output_mode || "dual"];
          const documentCell=cell("job-document");
          const title=line(documentCell,titleText);title.style.fontWeight="600";
          line(documentCell,filename,"muted");
          const setting=cell("job-setting");line(setting,service);line(setting,`${language} · ${mode}`,"muted");
          const state=cell("job-state");
          line(state,`${STAGES[job.status] || job.status}${job.hidden ? "（已移除）" : ""}${job.queue_position ? ` · 第 ${job.queue_position} 位` : ""}`,"job-status");
          line(state,STAGES[job.stage || job.status] || job.stage || job.status,"muted");
          const percent=Number.isFinite(job.progress) ? Math.max(0,Math.min(100,Math.round(job.progress))) : 0;
          const progressCell=cell("job-progress-cell");line(progressCell,`${percent}%`);
          const bar=make("div","job-progress");bar.setAttribute("role","progressbar");bar.setAttribute("aria-label",titleText);
          bar.setAttribute("aria-valuemin","0");bar.setAttribute("aria-valuemax","100");bar.setAttribute("aria-valuenow",String(percent));
          const fill=make("span");fill.style.width=`${percent}%`;bar.append(fill);progressCell.append(bar);
          const usage=cell("job-usage"), metrics=job.metrics;
          const amount=metrics?.usage_kind==="characters" ? (metrics.characters == null ? "用量未记录" : `${metrics.characters} 字符`) : (metrics?.total_tokens == null ? "用量未记录" : `${metrics.total_tokens} tokens`);
          line(usage,amount);line(usage,`${metrics?.request_count ?? "—"} 次请求${metrics?.elapsed_seconds != null ? ` · ${Math.round(metrics.elapsed_seconds)} 秒` : ""}`,"muted");
          usage.title=usageText(job).replace(/^ · /,"");
          const actions=cell("job-actions"), quick=make("div","task-actions");actions.append(quick);
          const detailRow=make("tr","job-details");detailRow.id=`zlb-detail-${job.id}`;
          window._zlbOpenDetails ||= new Set<string>();
          detailRow.hidden=!window._zlbOpenDetails.has(job.id);
          const detailCell=make("td");detailCell.setAttribute("colspan","6");detailRow.append(detailCell);
          const info=make("p","detail-info");
          info.textContent=`论文：${titleText}\n附件：${filename}\n翻译：${service} · ${language} · ${mode}\n用量：${usageText(job).replace(/^ · /,"") || "暂未记录"}\n任务 ID：${job.id}${job.error ? "\n错误："+job.error : ""}${job.cleanup_state ? "\n测试清理："+job.cleanup_state : ""}`;
          detailCell.append(info);
          const extra=make("div","detail-actions");detailCell.append(extra);
          const choices:{label:string,action:()=>Promise<void>}[]=[];
          const add=(label:string,action:()=>Promise<void>)=>choices.push({label,action});
          if (["queued", "running", "cancelling", "paused"].includes(job.status) || (job.status==="failed" && job.budget?.can_extend)) add("取消任务",async()=>{await api(`/v1/jobs/${job.id}/cancel`,{method:"POST"});});
          if(job.budget?.can_extend) add("确认追加额度",()=>plugin.workbench.extendBudget(window,job));
          else if(!job.cleanup_state && ["failed","cancelled","paused"].includes(job.status)) add("恢复任务",async()=>{await api(`/v1/jobs/${job.id}/retry`,{method:"POST"});});
          for(const artifact of job.artifacts?.length ? job.artifacts : job.artifact_name ? [{kind:"dual",name:job.artifact_name}] : []) add(artifact.kind==="mono" ? "打开译文 PDF" : "打开双语 PDF",()=>openJobResult(job,artifact.name));
          add("复制任务 ID",async()=>{Zotero.Utilities.Internal.copyTextToClipboard(job.id);});
          if(["completed","failed","cancelled","needs_review"].includes(job.status)) add(job.hidden ? "恢复到列表" : "从列表移除",async()=>{await api(`/v1/jobs/${job.id}/history`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({hidden:!job.hidden})});});
          choices.forEach(({label,action},index)=> {
            const b=make("button");b.textContent=label;b.title=label==="恢复任务" ? "恢复任务（保留累计预算）" : label;
            const status=make("p","action-feedback");status.setAttribute("role","status");status.setAttribute("aria-live","polite");
            b.addEventListener("click",async()=> {
              window._zlbActions=(window._zlbActions || 0)+1;window.clearTimeout(window._zlbTimer);
              b.setAttribute("disabled","true");status.textContent="正在处理…";
              try {await ensureEngineReady(m=>{status.textContent=m;});await action();status.textContent="操作成功";}
              catch(error){status.textContent=String(error);}
              finally {b.removeAttribute("disabled");window._zlbActions-=1;if(!window._zlbActions) window._zlbTimer=window.setTimeout(()=>void plugin.workbench.refresh(window,false),3000);}
            });
            const group=make("div","action-group");group.append(b,status);(index===0 ? quick : extra).append(group);
          });
          const toggle=make("button","details-toggle");toggle.textContent=job.error ? "错误／详情" : "详情／更多";
          const syncToggle=()=>toggle.setAttribute("aria-expanded",String(!detailRow.hidden));
          toggle.setAttribute("aria-controls",detailRow.id);syncToggle();
          toggle.addEventListener("click",()=> {
            detailRow.hidden=!detailRow.hidden;
            if(detailRow.hidden) window._zlbOpenDetails.delete(job.id);else window._zlbOpenDetails.add(job.id);
            syncToggle();
          });
          quick.append(toggle);container.append(row,detailRow);
          // A historical artifact error must not hide newer tasks or stop progress.
          if (job.status==="completed" && !job.is_selftest && job.metadata?.attachment_id && !monitors.has(job.id) && !artifactImports.has(job.id)) {
            artifactImports.add(job.id);
            void (async()=> {
              try {
                const attachment=await Zotero.Items.getAsync(Number(job.metadata!.attachment_id));
                if (attachment && String(attachment.libraryID)===job.metadata!.library_id) for (const artifact of job.artifacts || []) await importArtifact(attachment,job,artifact);
              } catch(error) {
                Zotero.logError(error);
                if (!window.closed && actions.isConnected) {
                  const message=document.createElementNS("http://www.w3.org/1999/xhtml","p");
                  message.className="import-warning";message.textContent=`附件导入失败：${String(error)}；任务进度显示不受影响。`;detailCell.append(message);toggle.textContent="导入提示／详情";toggle.title=message.textContent;
                }
              } finally {artifactImports.delete(job.id);}
            })();
          }
        }
        feedback(jobs.length ? "关闭工作台不取消任务。" : "还没有任务。请先保存并完整测试，或右键 PDF 开始翻译。");
        // Polling never starts a new service and never extends its idle timer.
        window._zlbTimer = window.setTimeout(() => void plugin.workbench.refresh(window, false), 2500);
      } catch (error) {
        feedback(`暂未连接服务：${String(error)}。点击刷新可准备或重新启动引擎。`);
        window._zlbTimer = window.setTimeout(() => void plugin.workbench.refresh(window, false), 5000);
      } finally {window._zlbRefreshing = false;}
    }
  },

  preferences: {
    async initializeDefaultProfile() {
      await api(`/v1/profiles/${encodeURIComponent(pref("profileId","default"))}/default`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({initialize_only:true})});
    },
    async loadProfiles(document:Document, strict=false) {
      try {
        await plugin.preferences.initializeDefaultProfile();
        const profiles=await (await api("/v1/profiles")).json();
        const select=document.getElementById("zlb-profile-select") as HTMLSelectElement;
        if (!select) return;
        select.replaceChildren();
        for (const p of profiles) {const o=document.createElementNS("http://www.w3.org/1999/xhtml","option") as HTMLOptionElement;o.value=p.id;o.textContent=p.name+(p.model ? " · "+p.model : "")+(p.is_default ? "（默认）" : "");select.append(o);}
        select.value=profiles.find((p:any)=>p.id===pref("profileId","default"))?.id || profiles.find((p:any)=>p.is_default)?.id || profiles[0]?.id || "";
        (document as any)._zlbProfiles=profiles;
        if(profiles.length && !profiles.some((p:any)=>p.id===pref("profileId","default"))) plugin.preferences.profileChanged(document);
        plugin.preferences.updateProfileActions(document);
      } catch (error) {if(strict) throw error; /* Settings open without starting the engine. */}
    },
    updateProfileActions(document:Document) {
      const p=(document as any)._zlbProfiles?.find((p:any)=>p.id===elementValue(document,"zlb-profile-select"));
      const busy=!!(document as any)._zlbProfileBusy;
      const defaults=document.getElementById("zlb-default-profile-button") as HTMLButtonElement;
      const remove=document.getElementById("zlb-delete-profile-button") as HTMLButtonElement;
      if(defaults) defaults.disabled=busy || !p || p.provider!=="openai" || !p.has_api_key || p.is_default;
      if(remove) remove.disabled=busy || !p || p.can_delete===false;
      const selector=document.getElementById("zlb-profile-select") as HTMLSelectElement;
      if(selector) selector.disabled=busy;
      const test=document.getElementById("zlb-test-button") as HTMLButtonElement;
      if(test && busy) test.disabled=true;
      setStatus(document,p?.delete_block_reason || (p?.is_default ? "此配置是默认 API 模型；浏览其他配置不会改变默认。" : "选择配置不会调用模型。可将已保存的 API 配置设为默认。"),"zlb-profile-hint");
    },
    async setDefaultProfile(document:Document) {
      if((document.getElementById("zlb-test-button") as HTMLButtonElement)?.disabled) {setStatus(document,"正在保存或测试配置，请等待完成。","zlb-default-profile-status");return;}
      if((document as any)._zlbProfileBusy || selectionInProgress) return;
      const id=elementValue(document,"zlb-profile-select");if(!id) return;
      (document as any)._zlbProfileBusy=true;plugin.preferences.updateProfileActions(document);
      try {
        setStatus(document,"正在设为默认 API 模型…","zlb-default-profile-status");
        await ensureEngineReady();
        await api(`/v1/profiles/${encodeURIComponent(id)}/default`,{method:"POST"});
        await plugin.preferences.loadProfiles(document,true);
        setStatus(document,"默认 API 模型已保存，未调用翻译服务。","zlb-default-profile-status");
      } catch(error) {setStatus(document,`设置失败：${String(error)}`,"zlb-default-profile-status");}
      finally {(document as any)._zlbProfileBusy=false;const test=document.getElementById("zlb-test-button") as HTMLButtonElement;if(test)test.disabled=false;plugin.preferences.updateProfileActions(document);}
    },
    async deleteProfile(document:Document) {
      if((document.getElementById("zlb-test-button") as HTMLButtonElement)?.disabled) {setStatus(document,"正在保存或测试配置，请等待完成。","zlb-delete-profile-status");return;}
      if((document as any)._zlbProfileBusy || selectionInProgress) return;
      const profiles=(document as any)._zlbProfiles || [],p=profiles.find((p:any)=>p.id===elementValue(document,"zlb-profile-select"));
      if(!p) return;
      if(p.can_delete===false) {setStatus(document,p.delete_block_reason || "配置正在使用，暂不能删除。","zlb-delete-profile-status");return;}
      const replacement=profiles.find((item:any)=>item.id!==p.id && item.provider==="openai" && item.has_api_key);
      const next=p.is_default ? replacement ? `\n删除后默认 API 改为：${replacement.name} · ${replacement.model}` : "\n没有其他 API 配置，默认将回到免费 Bing。" : "";
      const message=`删除配置：${p.name}${p.model ? " · "+p.model : ""}？\n同时删除该配置保存的 API Key；历史任务和 PDF 保留。\n使用此配置的失败或取消任务将不能直接恢复。${next}`;
      if(!Zotero.getMainWindow().confirm(message)) {setStatus(document,"已取消删除，配置与 Key 保留。","zlb-delete-profile-status");return;}
      (document as any)._zlbProfileBusy=true;plugin.preferences.updateProfileActions(document);
      try {
        setStatus(document,"正在删除配置及其 Key…","zlb-delete-profile-status");
        await ensureEngineReady();
        await api(`/v1/profiles/${encodeURIComponent(p.id)}`,{method:"DELETE"});
        await plugin.preferences.loadProfiles(document,true);
        if(elementValue(document,"zlb-profile-select")) plugin.preferences.profileChanged(document);
        else {
          setPref("profileId",`profile-${crypto.randomUUID().replace(/-/g,"").slice(0,16)}`);setPref("provider","bing");setPref("profileName","默认翻译服务");setPref("baseUrl","");setPref("model","");
          for(const [id,value] of [["zlb-profile-name","默认翻译服务"],["zlb-provider","bing"],["zlb-base-url",""],["zlb-model",""],["zlb-api-key",""]]) (document.getElementById(id) as HTMLInputElement).value=value;
          plugin.preferences.providerChanged(document,false);
        }
        setStatus(document,"配置及其 Key 已删除，历史任务和 PDF 保留。","zlb-delete-profile-status");
      } catch(error) {setStatus(document,`删除失败：${String(error)}。请刷新配置列表后重试。`,"zlb-delete-profile-status");}
      finally {(document as any)._zlbProfileBusy=false;const test=document.getElementById("zlb-test-button") as HTMLButtonElement;if(test)test.disabled=false;plugin.preferences.updateProfileActions(document);}
    },
    profileChanged(document:Document) {
      const p=(document as any)._zlbProfiles?.find((p:any)=>p.id===elementValue(document,"zlb-profile-select"));
      if (!p) return;
      setPref("profileId",p.id);setPref("provider",p.provider);setPref("baseUrl",p.base_url);setPref("model",p.model);
      for (const [id,value] of [["zlb-profile-name",p.name],["zlb-provider",p.provider],["zlb-base-url",p.base_url],["zlb-model",p.model],["zlb-api-key",""]]) (document.getElementById(id) as HTMLInputElement).value=value;
      setPref("profileName",p.name);
      plugin.preferences.providerChanged(document,false);
      plugin.preferences.updateProfileActions(document);
    },
    showWorkbench(document: Document) {
      try {openWorkbench(); setStatus(document,"任务工作台已打开。关闭窗口不取消任务。","zlb-workbench-open-status");}
      catch (error) {setStatus(document,`打开失败：${String(error)}`,"zlb-workbench-open-status");}
    },
    providerChanged(document: Document, preset = true) {
      const selected = elementValue(document, "zlb-provider");
      const free = selected === "bing" || selected === "tencent";
      (document.getElementById("zlb-api-fields") as HTMLElement).hidden = free;
      for (const id of ["zlb-custom-prompt", "zlb-glossary", "zlb-glossary-text", "zlb-thinking", "zlb-max-tokens", "zlb-max-requests"]) {
        (document.getElementById(id) as HTMLInputElement).disabled = free;
      }
      setStatus(document, free ? "实验性免 Key 服务。按字符计量；不支持大模型提示词和术语提取。不会自动转用收费服务。" : "完整测试会消耗 API 额度：最多 4 次请求、累计 12000 tokens、单次输出 1024 tokens；不是人民币固定费用。", "zlb-provider-hint");
      setStatus(document, free ? "本次完整测试使用免费服务，不需要 API Key。" : "点击将消耗 API 额度，最多 4 次请求、12000 tokens；非人民币固定费用。", "zlb-test-cost");
      if (pref("pdfSelftestPassed")==="true") {
        setStatus(document,free ? "连接测试只翻译一个短句，不重复生成 PDF。" : "连接测试仅调用一次 API，最多输出 32 tokens；实际费用取决于服务商。","zlb-test-cost");
        const button=document.getElementById("zlb-test-button");if(button) button.textContent="保存并测试连接";
      }
      if (preset && selected === "deepseek") {
        (document.getElementById("zlb-base-url") as HTMLInputElement).value = "https://api.deepseek.com/v1";
        (document.getElementById("zlb-model") as HTMLInputElement).value = "deepseek-chat";
      }
    },
    saveTranslationOptions(document: Document) {
      try {
        for (const [id, name, min, max] of [["zlb-max-tokens","maxTotalTokens",5000,2000000], ["zlb-max-requests","maxRequests",1,5000], ["zlb-free-requests","freeMaxRequests",1,5000], ["zlb-max-characters","maxCharacters",1000,1000000]] as const) {
          const value = Number(elementValue(document, id));
          if (!Number.isInteger(value) || value < min || value > max) throw new Error(`用量上限应为 ${min} 至 ${max} 的整数`);
          setPref(name, String(value));
        }
        const glossary = elementValue(document, "zlb-glossary-text");
        if (glossary.split("\n").some(line => line.trim() && !line.trim().startsWith("#") && !/^[^=]+=[^=]+$/.test(line))) throw new Error("术语表请每行填写 英文=中文");
        for (const [id,name] of [["zlb-glossary-text","glossaryText"], ["zlb-font-family","primaryFontFamily"], ["zlb-pages","pages"], ["zlb-custom-prompt","customSystemPrompt"], ["zlb-thinking","thinkingMode"]]) setPref(name, elementValue(document,id));
        setPref("disableRichText", String((document.getElementById("zlb-disable-rich") as HTMLInputElement).checked));
        setPref("autoExtractGlossary", String((document.getElementById("zlb-glossary") as HTMLInputElement).checked));
        setStatus(document, "翻译选项已保存，未调用翻译服务。", "zlb-options-status");
      } catch (error) {setStatus(document, `保存失败：${String(error)}`, "zlb-options-status");}
    },
    onLoad(document: Document) {
      setStatus(document,`本地双语 PDF ${DISPLAY_VERSION} · 引擎由插件自动准备与启动。翻译前可选择语言与输出。`,"zlb-version");
      void api("/v1/health").then(r=>r.json()).then(health=>{
        setStatus(document,`后台版本 ${health.version}${health.version===DISPLAY_VERSION ? "（匹配）" : "（版本不匹配，请导入匹配引擎）"}`,"zlb-runtime-version");
        if (health.deployment_verified) {setPref("pdfSelftestPassed","true");plugin.preferences.providerChanged(document,false);}
      }).catch(()=>setStatus(document,"后台未启动或引擎尚未安装。完整测试／翻译时自动准备。","zlb-runtime-version"));
      const name=document.getElementById("zlb-profile-name") as HTMLInputElement;
      if (name) name.value=pref("profileName","默认翻译服务");
      void plugin.preferences.loadProfiles(document);
      const old = pref("migrationNotice") === "true" || (!!pref("serviceToken") && !pref("provider"));
      const provider = pref("provider", old ? "openai" : "bing");
      (document.getElementById("zlb-provider") as HTMLInputElement).value = provider;
      for (const [id,name,fallback] of [
        ["zlb-base-url","baseUrl",""], ["zlb-model","model",""], ["zlb-pages","pages",""],
        ["zlb-custom-prompt","customSystemPrompt",""], ["zlb-glossary-text","glossaryText",""],
        ["zlb-font-family","primaryFontFamily","serif"], ["zlb-thinking","thinkingMode","auto"],
        ["zlb-max-tokens","maxTotalTokens","1000000"], ["zlb-max-requests","maxRequests","1000"],
        ["zlb-free-requests","freeMaxRequests","2000"], ["zlb-max-characters","maxCharacters","1000000"]
      ]) (document.getElementById(id) as HTMLInputElement).value = pref(name, fallback);
      (document.getElementById("zlb-disable-rich") as HTMLInputElement).checked = pref("disableRichText","false") === "true";
      (document.getElementById("zlb-glossary") as HTMLInputElement).checked = pref("autoExtractGlossary","false") === "true";
      plugin.preferences.providerChanged(document, false);
      if (old) setStatus(document, "已保留旧 API 配置。首次测试时会读取原模型与 Key；如旧终端仍在运行，请关闭一次。");
    },
    downloadEngine(document: Document) {
      return plugin.preferences.prepareEngine(document, false);
    },
    importEngine(document: Document) {
      return plugin.preferences.prepareEngine(document, true);
    },
    openEngineRelease(document: Document) {
      try {
        Zotero.launchURL(`https://github.com/Makashibata02/zotero-local-bilingual/releases/tag/v${DISPLAY_VERSION}`);
        setStatus(document,"已打开匹配版本下载页。离线安装请选择其中的引擎 ZIP。","zlb-release-page-status");
      } catch (error) {setStatus(document,`打开失败：${String(error)}`,"zlb-release-page-status");}
    },
    async prepareEngine(document: Document, offline: boolean): Promise<void> {
      const button = document.getElementById(offline ? "zlb-import-engine-button" : "zlb-download-engine-button") as HTMLButtonElement;
      if (button?.disabled) return;
      const buttons = ["zlb-download-engine-button", "zlb-import-engine-button"].map(id => document.getElementById(id) as HTMLButtonElement).filter(Boolean);
      const feedback = (text: string) => setStatus(document,text,offline ? "zlb-install-status" : "zlb-online-install-status");
      for (const item of buttons) item.disabled = true;
      try {
        feedback(offline ? "请选择匹配版本的引擎 ZIP…" : "正在准备在线下载与安装…");
        let path: string | undefined;
        if (offline) {
          path = await RuntimeNative.chooseZip();
          if (!path) {feedback("已取消选择，未导入引擎。");return;}
        }
        const endpoint = await runtimeManager.ensure(feedback, path);
        setPref("serviceUrl", endpoint.url); setPref("serviceToken", endpoint.token);
        feedback(`${offline ? "离线导入" : "在线准备"}成功：引擎已就绪，未调用翻译模型。现在可以保存配置并测试。`);
        setStatus(document,`后台版本 ${DISPLAY_VERSION}（匹配）`,"zlb-runtime-version");
        await plugin.preferences.loadProfiles(document);
      } catch (error) {feedback(`${offline ? "导入" : "在线安装"}失败：${String(error)}${offline ? "" : "。可使用旁边的离线导入，或打开 GitHub 下载页。"}`);}
      finally {for (const item of buttons) item.disabled = false;}
    },
    async openTestResult(document: Document) {
      try {
        const viewer=pref("testViewerPath");
        if (viewer && await IOUtils.exists(viewer)) {await Zotero.launchFile(viewer);setStatus(document,"已打开当前测试临时副本。","zlb-open-test-status");return;}
        await ensureEngineReady();
        const id = pref("lastSelftestId");
        if (!id) throw new Error("尚未完成完整测试");
        await openJobResult(await (await api(`/v1/jobs/${id}`)).json());
        setStatus(document, "已打开测试 PDF；没有导入文献库。", "zlb-open-test-status");
      } catch (error) {setStatus(document, `打开失败：${String(error)}`, "zlb-open-test-status");}
    },
    async saveAndTest(document: Document) {
      const button = document.getElementById("zlb-test-button") as HTMLButtonElement;
      if (button.disabled || (document as any)._zlbProfileBusy) return;
      if (selectionInProgress) {setStatus(document,"翻译批次正在进行，请先完成或取消当前批次，再更改服务并完整测试。"); return;}
      button.disabled = true;
      const feedback = (text: string) => setStatus(document,text);
      try {
        const selected = elementValue(document,"zlb-provider");
        const provider = selected === "deepseek" ? "openai" : selected;
        await ensureEngineReady(feedback);
        await plugin.preferences.initializeDefaultProfile();
        const profiles = await (await api("/v1/profiles")).json();
        const pendingTests = JSON.parse(pref("pendingSelftestRequests","{}"));
        if (pref("pendingSelftestId")) {
          pendingTests[pref("profileId","default")] = pendingTests[pref("profileId","default")] || pref("pendingSelftestId");
          setPref("pendingSelftestRequests",JSON.stringify(pendingTests));setPref("pendingSelftestId","");
        }
        const previous = profiles.find((p: any) => p.id === pref("profileId", "default"));
        const saveAsNew=(document.getElementById("zlb-save-new") as HTMLInputElement)?.checked;
        if (saveAsNew && provider==="openai" && !elementValue(document,"zlb-api-key")) throw new Error("另存为新 API 配置时请填写 Key；不会把旧配置的 Key 复制到新配置。");
        const body = {
          id: saveAsNew ? `profile-${crypto.randomUUID().replace(/-/g,"").slice(0,16)}` : pref("profileId","default"), name:elementValue(document,"zlb-profile-name") || "默认翻译服务", provider,
          base_url: provider === "openai" ? elementValue(document,"zlb-base-url") || previous?.base_url : undefined,
          model: provider === "openai" ? elementValue(document,"zlb-model") || previous?.model : "",
          api_key: provider === "openai" ? elementValue(document,"zlb-api-key") || undefined : undefined,
          qps: provider === "openai" ? 2 : 1, no_send_temperature:false
        };
        await api("/v1/profiles", {method:"POST", headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
        setPref("profileId",body.id);setPref("profileName",body.name);
        if (saveAsNew) (document.getElementById("zlb-save-new") as HTMLInputElement).checked=false;
        await plugin.preferences.loadProfiles(document);
        setPref("provider", selected);
        if (provider === "openai") {setPref("baseUrl", body.base_url || ""); setPref("model", body.model || "");}
        (document.getElementById("zlb-api-key") as HTMLInputElement).value = "";
        const health=await (await api("/v1/health")).json();
        if ((health.deployment_verified || pref("pdfSelftestPassed")==="true") && !pendingTests[body.id]) {
          setPref("pdfSelftestPassed","true");
          feedback("配置已保存，正在测试一次服务连接…");
          const result=await (await api(`/v1/profiles/${body.id}/test`,{method:"POST"})).json();
          feedback(`✓ 配置保存\n✓ 服务连接与实际调用\n${result.response_preview}\n${result.latency_ms} 毫秒 · ${result.usage?.request_count || 1} 次请求 · ${result.usage?.total_tokens ?? result.usage?.characters ?? "用量未知"} ${result.usage?.usage_kind === "characters" ? "字符" : "tokens"}\n已通过部署测试，本次没有翻译 PDF。`);
          plugin.preferences.providerChanged(document,false);return;
        }
        feedback("✓ 引擎安装\n✓ 后台启动\n✓ 资源加载\n正在测试翻译服务、提取与排版…");
        // Reuse an ambiguous request after a transport failure. Completed/failed tests get a new ID only on another user click.
        const requestID = pendingTests[body.id] || crypto.randomUUID().replace(/-/g,"");
        pendingTests[body.id]=requestID;setPref("pendingSelftestRequests",JSON.stringify(pendingTests));
        let job = await (await api(`/v1/profiles/${body.id}/selftest`, {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({client_request_id:requestID})})).json() as Job;
        setPref("lastSelftestId",job.id);
        while (["queued","running","cancelling"].includes(job.status)) {
          feedback(`✓ 引擎安装、后台启动、资源加载\n${STAGES[job.stage || job.status] || job.stage || job.status} · ${Math.round(job.progress)}%${usageText(job)}`);
          await Zotero.Promise.delay(2000);
          job = await (await api(`/v1/jobs/${job.id}`)).json();
        }
        delete pendingTests[body.id];setPref("pendingSelftestRequests",JSON.stringify(pendingTests));
        if (job.status !== "completed") throw new Error(`${job.error || job.status}${usageText(job)}`);
        await openJobResult(job);
        setPref("pdfSelftestPassed","true");plugin.preferences.providerChanged(document,false);
        feedback(`✓ 引擎安装\n✓ 后台启动\n✓ 资源加载\n✓ 翻译服务\n✓ PDF 提取、翻译与排版\n✓ 打开测试 PDF（未导入文献库）${usageText(job)}`);
      } catch (error) {feedback(`完整测试未通过：${error instanceof Error ? error.message : String(error)}`);}
      finally {button.disabled = false;}
    }
  },
  openWorkbench,

  translateSelection
};

Zotero.LocalBilingual = plugin;
