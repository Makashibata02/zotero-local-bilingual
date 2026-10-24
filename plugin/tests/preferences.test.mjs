import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFile} from 'node:fs/promises';
import {webcrypto} from 'node:crypto';
import {transform} from 'esbuild';
const source=await readFile(new URL('../src/main.ts',import.meta.url),'utf8');
const {code}=await transform(source.replace(/^import[^\n]*\r?\n/,''),{loader:'ts',format:'iife'});

function harness({copyFails=false, deploymentVerified=false, zipChoice="/tmp/engine.zip", engineFails=false, isMac=false}={}) {
  const prefs={},calls=[],nodes=new Map(),opened=[],runtimeCalls=[],urls=[];
  const ids=[...source.matchAll(/"(zlb-[a-z-]+)"/g)].map(m=>m[1]);
  for(const id of ids)nodes.set(id,{value:'',textContent:'',checked:false,disabled:false,replaceChildren(){},append(){},setAttribute(k,v){this[k]=v;}});
  nodes.get('zlb-provider').value='bing';
  const document={getElementById:id=>nodes.get(id)};
  const Zotero={isMac,Prefs:{get:key=>prefs[key.split('.').pop()],set:(key,val)=>prefs[key.split('.').pop()]=val},
    Promise:{delay:async()=>{}},launchFile:path=>opened.push(path),launchURL:url=>urls.push(url),logError(){}};
  vm.runInNewContext(code,{Zotero,Headers,FormData,Blob,crypto:webcrypto,
    runtimeManager:{ensure:async (feedback,path)=>{runtimeCalls.push(path);feedback('引擎就绪');if(engineFails)throw Error('HTTP 503');return{url:'http://127.0.0.1:1234',token:'local-token'};}},
    RuntimeNative:{chooseZip:async()=>zipChoice},
    PathUtils:{tempDir:'/tmp',join:(...p)=>p.join('/')},IOUtils:{makeDirectory:async()=>{},write:async()=>{if(copyFails)throw Error('copy failed');},exists:async()=>true},
    fetch:async(url,init={})=>{
      calls.push({url,init});
      if(url.endsWith('/v1/health'))return Response.json({deployment_verified:deploymentVerified});
      if(url.endsWith('/test'))return Response.json({ok:true,response_preview:'信号空间',latency_ms:10,usage:{usage_kind:'tokens',total_tokens:21,request_count:1}});
      if(url.endsWith('/v1/profiles')&&init.method!=='POST')return Response.json([]);
      if(url.includes('/artifacts/'))return new Response('%PDF');
      if(url.endsWith('/selftest'))return Response.json({id:'a'.repeat(32),status:'completed',is_selftest:true,artifact_name:'result.pdf',progress:100,metrics:{usage_kind:'characters',characters:343,request_count:4}});
      return Response.json({});
    }});
  return{api:Zotero.LocalBilingual.preferences,document,nodes,calls,opened,prefs,runtimeCalls,urls};
}

test('Mac button layout is enabled only inside the Mac preferences pane',()=>{
  for(const isMac of [true,false]) {
    const h=harness({isMac});h.api.onLoad(h.document);
    assert.equal(h.nodes.get('zlb-settings')['data-platform'],isMac?'macos':undefined);
  }
});

test('free mode disables LLM-only options and does not require a key',async()=>{
  const h=harness();h.api.providerChanged(h.document);
  assert.equal(h.nodes.get('zlb-api-fields').hidden,true);
  assert.equal(h.nodes.get('zlb-custom-prompt').disabled,true);
  await h.api.saveAndTest(h.document);
  const profile=JSON.parse(h.calls.find(c=>c.url.endsWith('/v1/profiles')&&c.init.method==='POST').init.body);
  assert.equal(profile.provider,'bing');assert.ok(!profile.api_key);
  assert.equal(h.opened.length,1);
  assert.match(h.nodes.get('zlb-test-status').textContent,/343 字符/);
  assert.match(h.nodes.get('zlb-test-status').textContent,/未导入/);
});
test('API test billing notice is placed beside test button and preset fills fields',()=>{
  const h=harness();h.nodes.get('zlb-provider').value='deepseek';h.api.providerChanged(h.document);
  assert.equal(h.nodes.get('zlb-model').value,'deepseek-chat');
  assert.match(h.nodes.get('zlb-test-cost').textContent,/12000 tokens/);
  assert.equal(h.nodes.get('zlb-custom-prompt').disabled,false);
});
test('advanced save validation reports beside its own button',()=>{
  const h=harness();h.nodes.get('zlb-test-status').textContent='unchanged';h.api.saveTranslationOptions(h.document);
  assert.match(h.nodes.get('zlb-options-status').textContent,/保存失败/);
  assert.equal(h.nodes.get('zlb-test-status').textContent,'unchanged');
});
test('all preference status areas use accessible adjacent blocks',async()=>{
  const html=await readFile(new URL('../addon/content/preferences.xhtml',import.meta.url),'utf8');
  for(const id of ['zlb-test-status','zlb-open-test-status','zlb-install-status','zlb-options-status','zlb-workbench-open-status','zlb-default-profile-status','zlb-delete-profile-status']) {
    assert.match(html,new RegExp(`</html:button>\\s*<html:p id="${id}"[^>]*role="status"[^>]*aria-live="polite"`));
  }
  assert.ok(!html.includes('zlb-service-token'));assert.ok(!html.includes('checkService'));
});

test('first deployment uses PDF then subsequent saves call connection only',async()=>{
  const h=harness();await h.api.saveAndTest(h.document);await h.api.saveAndTest(h.document);
  assert.equal(h.calls.filter(c=>c.url.endsWith('/selftest')).length,1);
  assert.equal(h.calls.filter(c=>c.url.endsWith('/test')).length,1);
  assert.equal(h.calls.filter(c=>c.url.endsWith('/cleanup-test')).length,1);
  assert.ok(h.prefs.testViewerPath);
});
test('failed viewer copy retains server test files',async()=>{
  const h=harness({copyFails:true});await h.api.saveAndTest(h.document);
  assert.equal(h.calls.filter(c=>c.url.endsWith('/cleanup-test')).length,0);
  assert.match(h.nodes.get('zlb-test-status').textContent,/copy failed/);
});
test('existing deployed service uses connection test without generating PDF',async()=>{
  const h=harness({deploymentVerified:true});await h.api.saveAndTest(h.document);
  assert.equal(h.calls.filter(c=>c.url.endsWith('/selftest')).length,0);
  assert.equal(h.calls.filter(c=>c.url.endsWith('/test')).length,1);
  assert.equal(h.opened.length,0);
});

test('engine choices are visible above model configuration without a collapsed details element',async()=>{
  const html=await readFile(new URL('../addon/content/preferences.xhtml',import.meta.url),'utf8');
  const section=html.match(/<html:section id="zlb-engine-setup"[\s\S]*?<\/html:section>/)?.[0];
  assert.ok(section);assert.ok(!section.includes('<html:details'));
  assert.ok(html.indexOf('id="zlb-engine-setup"')<html.indexOf('id="zlb-profile-select"'));
  for(const id of ['zlb-download-engine-button','zlb-import-engine-button','zlb-online-install-status','zlb-install-status'])assert.ok(section.includes(`id="${id}"`));
});
test('online preparation sends no model request and reports beside its button',async()=>{
  const h=harness();h.nodes.get('zlb-install-status').textContent='offline unchanged';
  await h.api.downloadEngine(h.document);
  assert.deepEqual(h.runtimeCalls,[undefined]);assert.equal(h.calls.length,2);assert.ok(h.calls.every(c=>c.url.endsWith("/v1/profiles")||c.url.endsWith("/default")));
  assert.match(h.nodes.get('zlb-online-install-status').textContent,/未调用翻译模型/);
  assert.equal(h.nodes.get('zlb-install-status').textContent,'offline unchanged');
  assert.equal(h.nodes.get('zlb-download-engine-button').disabled,false);
});
test('offline import uses chosen file and does not call a translation provider',async()=>{
  const h=harness();await h.api.importEngine(h.document);
  assert.deepEqual(h.runtimeCalls,['/tmp/engine.zip']);assert.equal(h.calls.length,2);assert.ok(h.calls.every(c=>c.url.endsWith("/v1/profiles")||c.url.endsWith("/default")));
  assert.match(h.nodes.get('zlb-install-status').textContent,/离线导入成功/);
});
test('cancelled offline picker neither starts installation nor leaves disabled controls',async()=>{
  const h=harness({zipChoice:null});await h.api.importEngine(h.document);
  assert.equal(h.runtimeCalls.length,0);assert.equal(h.calls.length,0);
  assert.match(h.nodes.get('zlb-install-status').textContent,/已取消/);
  assert.equal(h.nodes.get('zlb-import-engine-button').disabled,false);
});
test('online failure keeps offline alternative available with adjacent feedback',async()=>{
  const h=harness({engineFails:true});await h.api.downloadEngine(h.document);
  assert.match(h.nodes.get('zlb-online-install-status').textContent,/HTTP 503/);
  assert.match(h.nodes.get('zlb-online-install-status').textContent,/离线导入/);
  assert.equal(h.nodes.get('zlb-import-engine-button').disabled,false);
  assert.equal(h.calls.length,0);
});
test('release page action opens only the project release page without model calls',()=>{
  const h=harness();h.api.openEngineRelease(h.document);
  assert.match(h.urls[0],/^https:\/\/github\.com\/Makashibata02\/zotero-local-bilingual\/releases\/tag\/v/);
  assert.equal(h.runtimeCalls.length,0);assert.equal(h.calls.length,0);
});
