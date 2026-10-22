import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { webcrypto } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { transform } from 'esbuild';

const source = await readFile(new URL('../src/main.ts', import.meta.url), 'utf8');
const { code } = await transform(source.replace(/^import[^\n]*\r?\n/, ''), { loader: 'ts', format: 'iife' });

function harness({ readError = false, brokenDisplay = false, brokenConstructor = false, success = false, serviceVersion = "0.2.0", prefs = {provider:"openai"}, closeDuringPolling = false, attachmentCount = 1, failPollOnce = false, loseSubmitResponse = false, loseSubmitBeforeCreate = false, cancelConfirmation = false, initialUnload = false } = {}) {
  prefs = {provider:"openai", ...prefs};
  const events = [], requests = [], errors = [], alerts = [], windows = [],dialogs=[];
  let lost = false, failedPoll = false;
  const serverJobs = new Set();
  const attachment = {
    id: 1, libraryID: 1, isAttachment: () => true, isPDFAttachment: () => true,
    getField: () => 'sample', getFilePathAsync: async () => '/sample.pdf'
  };
  class ProgressWindow {
    constructor(options) {
      this.options = options;
      windows.push(this);
      if (brokenConstructor) throw new Error('window unavailable');
      this.ItemProgress = class {
        constructor(_icon, text) { events.push(text); }
        setText(text) {
          if (brokenDisplay) throw new Error('display unavailable');
          events.push(text);
        }
        setProgress(value) { events.push(value); }
        setError() { events.push('error icon'); }
      };
    }
    // Match Zotero's native contract: addLines has no return value.
    addLines(text) { events.push(text); }
    changeHeadline() {}
    show() {}
    addDescription(text) { events.push(text); }
    close() { events.push('window closed'); }
    click() { if (this.options.closeOnClick) this.close(); }
    startCloseTimer() {}
  }
  const Zotero = {
    getMainWindow: () => ({openDialog: (url,_name,_features,args) => {
      const handlers=new Map();
      const dialog={addEventListener(name,callback){handlers.set(name,callback);},closed:false,focus(){events.push('dialog focused');},document:{getElementById:()=>null}};
      dialogs.push({url,dialog});
      if(url.endsWith('/confirm.xhtml')) {
        const choose=()=>args.resolve(cancelConfirmation ? null : {indices:args.entries.map((_,i)=>i),profile:'default',provider:'openai',target:'zh-CN',output:'dual',parallel:1});
        if(initialUnload) queueMicrotask(()=> {
          handlers.get('unload')?.(); // about:blank unloads before the UI is ready.
          args.ready=true;choose();
        }); else choose();
      }
      return dialog;
    }}),
    ProgressWindow, Prefs: { get: name => name.endsWith('serviceToken') ? 'test-token' : prefs[name.split(".").pop()], set: (name, value) => { prefs[name.split(".").pop()] = value; } },
    getActiveZoteroPane: () => ({ getSelectedItems: () => Array.from({length:attachmentCount}, (_,i) => ({...attachment, id:i+1})) }),
    logError: error => errors.push(String(error)), alert: (_parent, _title, text) => alerts.push(text),
    Promise: { delay: async () => { if (closeDuringPolling) windows[0].click(); } },
    Attachments: { importFromFile: async () => ({ id: 2, setField() {}, saveTx: async () => events.push('imported') }) },
    Items:{getAsync:async()=>null}, Reader: { open: async () => events.push('opened') }
  };
  vm.runInNewContext(code, {
    Zotero, Headers, FormData, Blob, crypto: webcrypto,
    runtimeManager: {ensure:async()=>({url:"http://127.0.0.1:17865",token:"test-token"})},
    IOUtils: {
      read: async () => { if (readError) throw new Error('PDF unreadable'); return new Uint8Array([37, 80, 68, 70]); },
      write: async () => {}, makeDirectory:async()=>{}, exists:async()=>true
    },
    PathUtils: { tempDir: '/tmp', join: (...parts) => parts.join('/'), filename:p=>p.split('/').pop() }, URLSearchParams,
    fetch: async (url, init) => {
      if (url.endsWith("/v1/health")) return Response.json({version: serviceVersion, managed:serviceVersion==="0.2.0",protocol_version: serviceVersion==="0.2.0"?2:1});
      if(url.endsWith('/v1/profiles'))return Response.json([{id:'default',name:'Default',provider:'openai',model:'mock'}]);
      if(url.endsWith('/v1/capabilities'))return Response.json({languages:[{id:'zh-CN',name:'简体中文'}],providers:{openai:{'zh-CN':{supported:true}}}});
      if(url.endsWith('/v1/scheduler'))return Response.json({max_parallel:1});
      if(url.endsWith("/default"))return Response.json({default_profile_id:"default"});
      requests.push({ url, init });
      if (url.endsWith('/v1/jobs')) {
        const id = init.body.get('client_request_id');
        if (loseSubmitBeforeCreate && !lost) { lost=true; throw new Error('connection lost before submit'); }
        serverJobs.add(id);
        if (loseSubmitResponse && !lost) { lost=true; throw new Error('submit response lost'); }
        return Response.json({ id, status: 'queued', progress: 0 });
      }
      if (/\/v1\/jobs\/[^/]+$/.test(url)) {
        if (failPollOnce && !failedPoll) { failedPoll=true; throw new Error('poll disconnected'); }
        if (!serverJobs.has(url.split('/').pop())) return Response.json({detail:'missing'}, {status:404});
      }
      if (url.endsWith('/artifacts/result.pdf')) return new Response('%PDF');
      return Response.json(success
        ? { id: 'test-job', status: 'completed', progress: 100, artifact_name: 'result.pdf' }
        : { id: 'test-job', status: 'failed', progress: 0, error: 'worker failed' });
    }
  });
  return { run: async () => {await Zotero.LocalBilingual.translateSelection(); await new Promise(resolve=>setTimeout(resolve,20));}, events, requests, errors, alerts, windows,dialogs };
}

test('progress row allows upload and shows worker errors', async () => {
  const h = harness(); await h.run();
  assert.equal(h.requests[0].init.method, 'POST');
  assert.ok(h.requests[0].url.endsWith('/v1/jobs'));
  assert.equal(h.requests[0].init.body.get('file').size, 4);
  assert.ok(h.events.some(e => String(e).includes('worker failed')));
  assert.ok(h.events.includes('error icon'));
  assert.equal(h.errors.length, 1);
});
test('unreadable PDF is displayed without sending a job', async () => {
  const h = harness({ readError: true }); await h.run();
  assert.equal(h.requests.length, 0);
  assert.ok(h.events.some(e => String(e).includes('PDF unreadable')));
  assert.equal(h.errors.length, 1);
});
test('broken progress display falls back to an alert and logs the original error', async () => {
  const h = harness({ brokenDisplay: true }); await h.run();
  assert.equal(h.alerts.length, 1);
  assert.match(h.alerts[0], /display unavailable/);
  assert.equal(h.errors.length, 1);
});
test('progress window construction failure is caught', async () => {
  const h = harness({ brokenConstructor: true }); await h.run();
  assert.equal(h.alerts.length, 1);
  assert.match(h.alerts[0], /window unavailable/);
});
test('completed job downloads and imports the PDF', async () => {
  const h = harness({ success: true }); await h.run();
  assert.ok(h.events.includes('imported'));
  assert.ok(h.events.includes('所选 PDF 产物已导入'));
  assert.ok(h.events.includes('opened'));
  assert.equal(h.errors.length, 0);
});


test('old service is rejected before upload', async () => {
  const h=harness({serviceVersion: '0.1.7'}); await h.run();
  assert.equal(h.requests.length,0);
  assert.ok(h.events.some(e=>String(e).includes('匹配')));
});
test('fixed glossary and layout settings reach the service', async () => {
  const h=harness({prefs:{glossaryText:'optical interrogation=光学探测',primaryFontFamily:'sans-serif',disableRichText:'true'}});
  await h.run();
  const options=JSON.parse(h.requests[0].init.body.get('options_json'));
  assert.equal(options.glossary_text,'optical interrogation=光学探测');
  assert.equal(options.primary_font_family,'sans-serif');
  assert.equal(options.disable_rich_text_translate,true);
});
test('simultaneous clicks submit one job for an attachment', async () => {
  const h=harness(); await Promise.all([h.run(),h.run()]);
  assert.equal(h.requests.filter(r=>r.init.method==='POST').length,1);
  assert.ok(h.alerts.some(s=>s.includes('正在提交')));
});


test('error window can be dismissed by clicking it', async () => {
  const h = harness(); await h.run();
  assert.ok(h.events.includes('error icon'));
  h.windows[0].click();
  assert.ok(h.events.includes('window closed'));
  assert.ok(h.events.some(e => String(e).includes('关闭提示不会取消')));
});
test('closing a running window allows the task to finish without reopening it', async () => {
  const h = harness({ success: true, closeDuringPolling: true }); await h.run();
  assert.ok(h.events.includes('window closed'));
  assert.ok(h.events.includes('imported'));
  assert.ok(h.events.includes('opened'));
  assert.ok(!h.events.includes('所选 PDF 产物已导入'));
  assert.ok(!h.requests.some(r => r.url.endsWith('/cancel')));
});


test('simultaneous multiselection submits each attachment once', async () => {
  const h=harness({attachmentCount:2, success:true}); await Promise.all([h.run(),h.run()]);
  assert.equal(h.requests.filter(r=>r.init.method==='POST').length,2);
  assert.equal(h.events.filter(e=>e==='imported').length,2);
});
test('poll failure resumes persisted job without a new POST', async () => {
  const h=harness({success:true, failPollOnce:true}); await h.run(); await h.run();
  assert.equal(h.requests.filter(r=>r.init.method==='POST').length,1);
  assert.ok(h.events.includes('imported'));
});
test('lost submit response finds the original job on next invocation', async () => {
  const h=harness({success:true, loseSubmitResponse:true}); await h.run(); await h.run();
  assert.equal(h.requests.filter(r=>r.init.method==='POST').length,1);
  assert.ok(h.events.includes('imported'));
});
test('unsent request retries with the same persisted idempotency ID', async () => {
  const h=harness({success:true, loseSubmitBeforeCreate:true}); await h.run(); await h.run();
  const posts=h.requests.filter(r=>r.init.method==='POST');
  assert.equal(posts.length,2);
  assert.equal(posts[0].init.body.get('client_request_id'), posts[1].init.body.get('client_request_id'));
  assert.ok(h.events.includes('imported'));
});

 test('cancel confirmation creates no job or paid request',async()=>{
  const h=harness({cancelConfirmation:true});await h.run();assert.equal(h.requests.length,0);assert.equal(h.windows.length,0);
});

test('initial chrome document unload does not discard the later confirmation',async()=> {
  const h=harness({initialUnload:true,success:true});await h.run();
  assert.equal(h.requests.filter(request=>request.url.endsWith('/v1/jobs')&&request.init.method==='POST').length,1);
  assert.equal(h.dialogs.filter(dialog=>dialog.url.endsWith('/workbench.xhtml')).length,1);
  assert.ok(h.events.includes('dialog focused'));assert.ok(h.events.includes('imported'));
});

test('cancel after initial chrome unload still creates no task or workbench',async()=> {
  const h=harness({initialUnload:true,cancelConfirmation:true});await h.run();
  assert.equal(h.requests.length,0);
  assert.equal(h.dialogs.filter(dialog=>dialog.url.endsWith('/workbench.xhtml')).length,0);
});
