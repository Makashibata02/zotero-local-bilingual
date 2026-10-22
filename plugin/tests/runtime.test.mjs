import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFile} from 'node:fs/promises';
import {webcrypto} from 'node:crypto';
import {transform} from 'esbuild';
const source = await readFile(new URL('../src/runtime.ts',import.meta.url),'utf8');
const {code} = await transform(source, {loader:'ts',format:'cjs'});
const context={module:{exports:{}},exports:{}, crypto:webcrypto,fetch,AbortSignal};
vm.runInNewContext(code,context);
const {RuntimeManager,safeEntry,selectManifest}=context.module.exports;

function harness({badHash=false,interrupt=false,lowDisk=false,legacy=false,preflightFails=false,oldService=null,shutdownDenied=false,neverExits=false,queued=0,platform='windows-x64',metadataMismatch=false}={}) {
  const files=new Map(),events=[],requests=[];
  const manifest={version:'0.2.0',protocol_version:2,platform,url:'https://github.com/owner/repo/engine.zip',size:4,installed_size:20,sha256:'a'.repeat(64)};
  let interrupted=false,started=0,shutdowns=0,oldUp=true;
  if(oldService) {
    files.set('/state/managed-service.json',{url:'http://127.0.0.1:12400',pid:99});
    files.set('/state/runtimes/0.2.0/.ready.json',{sha256:manifest.sha256});
  }
  const io={
    makeDirectory:async()=>{},exists:async path=>files.has(path),
    readJSON:async path=>{if(!files.has(path))throw Error('missing');return files.get(path);},
    writeJSON:async(path,data)=>files.set(path,data),
    readUTF8:async()=> 'private-local-token', stat:async path=>({size:files.get(path)?.length||0}),
    write:async(path,bytes,options)=>files.set(path,options?.mode==='append' ? [...(files.get(path)||[]),...bytes]:[...bytes]),
    remove:async path=>{files.delete(path);events.push(['remove',path]);},
    move:async(a,b)=>{for(const [path,v]of [...files])if(path===a||path.startsWith(a+'/')){files.set(path.replace(a,b),v);files.delete(path);}events.push(['move',a,b]);}
  };
  const native={checkSystem:()=>{},platform:()=>platform,samePath:(a,b)=>a===b,secureDirectory:async()=>{},baseDir:()=>'/state',legacyURL:()=> 'http://127.0.0.1:17865',freeSpace:()=>lowDisk?0:1000,
    sha256:()=>badHash?'b'.repeat(64):manifest.sha256,delay:async()=>{if(oldService && (shutdowns || oldService.shutdown_requested) && !neverExits)oldUp=false;},
    extract:async(_,dst,check)=>{assert.ok(check('python/python.exe'));files.set(dst+'/python/python.exe',[1]);files.set(dst+'/runtime.json',{version:'0.2.0',protocol_version:2,platform:metadataMismatch?'other':platform});events.push(['extract']);},
    preflight:async()=>{events.push(['preflight']);if(preflightFails)throw Error('resource corrupted');},
    start:async()=>{started++; files.set('/state/managed-service.json',{url:'http://127.0.0.1:12345'});return{exited:false};}
  };
  const request=async(url,options={})=>{
    if(url.startsWith('chrome:'))return Response.json(manifest);
    if(url.includes('12400') && url.endsWith('/health'))return oldUp ? Response.json({managed:true,version:'0.1.9',protocol_version:2,data_dir:'/state',process_id:99,supports_idle_shutdown:true,active_jobs:[],...oldService}):new Response('',{status:404});
    if(url.endsWith('/v1/scheduler'))return Response.json({counts:queued?{queued}:{failed:1}});
    if(url.endsWith('/shutdown-if-idle')) {
      shutdowns++;requests.push(options);
      assert.equal(options.headers['X-Local-Bilingual-Token'],'private-local-token');
      assert.equal(JSON.parse(options.body).expected_pid,99);
      return shutdownDenied ? Response.json({detail:'旧后台有请求处理中，未停止'},{status:409}):Response.json({accepted:true});
    }
    if(url.endsWith('/health'))return legacy&&url.includes('17865')?Response.json({version:'0.1.9',data_dir:'/state'}):url.includes('12345')&&started?Response.json({managed:true,version:'0.2.0',protocol_version:2}):new Response('',{status:404});
    requests.push(options);
    if(interrupt&&!interrupted){interrupted=true;return new Response(new ReadableStream({start(c){c.enqueue(new Uint8Array([1,2]));},pull(c){c.error(Error('network disconnected'));}}));}
    return options.headers?.Range ? new Response(new Uint8Array([3,4]),{status:206,headers:{'Content-Range':'bytes 2-3/4'}}):new Response(new Uint8Array([1,2,3,4]));
  };
  return{manager:new RuntimeManager(native,io,{join:(...p)=>p.join('/')},request),files,events,requests,get started(){return started;},get shutdowns(){return shutdowns;}};
}

test('safe zip paths include Chinese but reject traversal, drive and Windows device names',()=>{
  for(const p of ['../x','/x','python/../../x','C:/x','dir\\x','NUL.txt','dir/a.'])assert.equal(safeEntry(p),false,p);
  assert.equal(safeEntry('assets/字体.ttf'),true);
});
test('concurrent preparation installs and starts once',async()=>{
  const h=harness();await Promise.all([h.manager.ensure(),h.manager.ensure()]);
  assert.equal(h.started,1);assert.equal(h.events.filter(e=>e[0]==='extract').length,1);
  assert.ok(h.files.has('/state/runtimes/0.2.0/.ready.json'));
});
test('interrupted download resumes range without activating half an installation',async()=>{
  const h=harness({interrupt:true});await assert.rejects(h.manager.ensure(),/disconnected/);
  assert.equal(h.started,0);await h.manager.ensure();
  assert.equal(h.requests[1].headers.Range,'bytes=2-');assert.equal(h.started,1);
});
test('hash error does not replace or extract runtime',async()=>{
  const h=harness({badHash:true});await assert.rejects(h.manager.ensure(),/SHA-256/);
  assert.equal(h.started,0);assert.ok(!h.events.some(e=>e[0]==='extract'));
});
test('disk shortage and old terminal service stop before install',async()=>{
  for(const config of [{lowDisk:true},{legacy:true}]){
    const h=harness(config);await assert.rejects(h.manager.ensure());assert.equal(h.started,0);assert.equal(h.requests.length,0);
  }
});
test('failed resource selfcheck never activates staged files',async()=>{
  const h=harness({preflightFails:true});await assert.rejects(h.manager.ensure(),/corrupted/);
  assert.equal(h.started,0);assert.ok(!h.events.some(e=>e[0]==='move'));
});

test('public engine downloads omit browser credentials and contain no authorization header',async()=>{
  const h=harness();await h.manager.ensure();
  assert.equal(h.requests.length,1);
  assert.equal(h.requests[0].credentials,'omit');
  assert.ok(!('Authorization' in h.requests[0].headers));
});

test('idle managed old version exits cooperatively before the matching service starts',async()=>{
  const h=harness({oldService:{}});await h.manager.ensure();
  assert.equal(h.shutdowns,1);assert.equal(h.started,1);
  assert.equal(h.requests.filter(r=>r.method==='POST').length,1);
});
test('running tasks block handoff without stopping or starting services',async()=>{
  const h=harness({oldService:{active_jobs:['job']}});
  await assert.rejects(h.manager.ensure(),/任务/);assert.equal(h.started,0);assert.equal(h.shutdowns,0);
});
test('pending tasks and in-flight requests block handoff',async()=>{
  for(const oldService of [{pending_jobs:1},{business_requests:1}]) {
    const h=harness({oldService});await assert.rejects(h.manager.ensure(),/任务或请求/);assert.equal(h.started,0);assert.equal(h.shutdowns,0);
  }
});
test('legacy idle service is described as idle rather than falsely described as translating',async()=>{
  const h=harness({oldService:{supports_idle_shutdown:false}});
  await assert.rejects(h.manager.ensure(),/当前空闲.*不支持自动退出/);assert.equal(h.started,0);assert.equal(h.shutdowns,0);
});
test('legacy queued task is protected using its authenticated scheduler',async()=>{
  const h=harness({oldService:{supports_idle_shutdown:false},queued:1});
  await assert.rejects(h.manager.ensure(),/运行或排队/);assert.equal(h.started,0);assert.equal(h.shutdowns,0);
});
test('a denied shutdown never starts a second service',async()=>{
  const h=harness({oldService:{},shutdownDenied:true});await assert.rejects(h.manager.ensure(),/请求处理中/);assert.equal(h.started,0);
});
test('a shutdown timeout never starts a second service',async()=>{
  const h=harness({oldService:{},neverExits:true});await assert.rejects(h.manager.ensure(),/尚未完成/);assert.equal(h.started,0);
});
test('already shutting-down service is waited for without another POST',async()=>{
  const h=harness({oldService:{shutdown_requested:true}});await h.manager.ensure();assert.equal(h.shutdowns,0);assert.equal(h.started,1);
});
test('offline reimport can safely stop an idle service of the same version',async()=>{
  const h=harness({oldService:{version:'0.2.0'}});h.files.set('/offline.zip',[1,2,3,4]);
  await h.manager.ensure(()=>{},'/offline.zip');assert.equal(h.shutdowns,1);assert.equal(h.started,1);assert.equal(h.events.filter(e=>e[0]==='extract').length,1);
});
test('unmanaged or foreign-data-dir services are never automatically stopped',async()=>{
  for(const oldService of [{managed:false},{data_dir:'/elsewhere'},{process_id:100}]){
    const h=harness({oldService});await assert.rejects(h.manager.ensure());assert.equal(h.shutdowns,0);assert.equal(h.started,0);
  }
});


test('both Mac architectures install into isolated platform directories',async()=>{
  for(const platform of ['macos-arm64','macos-x64']) {
    const h=harness({platform});await h.manager.ensure();
    assert.equal(h.started,1);assert.ok(h.files.has(`/state/runtimes/0.2.0-${platform}/.ready.json`));
  }
});
test('platform catalog refuses missing, cross-platform or malformed manifests',()=>{
  const good={version:'1.1.0',protocol_version:2,platform:'macos-arm64',sha256:'a'.repeat(64),size:4,installed_size:20};
  const catalog={version:'1.1.0',protocol_version:2,engines:{'macos-arm64':good}};
  assert.equal(selectManifest(catalog,'macos-arm64'),good);
  assert.throws(()=>selectManifest(catalog,'macos-x64'),/尚未包含/);
  assert.throws(()=>selectManifest({...catalog,version:'1.0.0'},'macos-arm64'),/无效/);
  assert.throws(()=>selectManifest({...good,size:0},'macos-arm64'),/无效/);
});
test('wrong runtime metadata is rejected before executing preflight',async()=>{
  const h=harness({platform:'macos-arm64',metadataMismatch:true});
  await assert.rejects(h.manager.ensure(),/平台或版本不符/);
  assert.equal(h.started,0);assert.ok(!h.events.some(e=>e[0]==='preflight'));
});
