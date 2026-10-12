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
const {RuntimeManager,safeEntry}=context.module.exports;

function harness({badHash=false,interrupt=false,lowDisk=false,legacy=false,preflightFails=false}={}) {
  const files=new Map(),events=[],requests=[];
  const manifest={version:'0.2.0',protocol_version:2,platform:'windows-x64',url:'https://github.com/owner/repo/engine.zip',size:4,installed_size:20,sha256:'a'.repeat(64)};
  let interrupted=false,started=0;
  const io={
    makeDirectory:async()=>{},exists:async path=>files.has(path),
    readJSON:async path=>{if(!files.has(path))throw Error('missing');return files.get(path);},
    writeJSON:async(path,data)=>files.set(path,data),
    readUTF8:async()=> 'private-local-token', stat:async path=>({size:files.get(path)?.length||0}),
    write:async(path,bytes,options)=>files.set(path,options?.mode==='append' ? [...(files.get(path)||[]),...bytes]:[...bytes]),
    remove:async path=>{files.delete(path);events.push(['remove',path]);},
    move:async(a,b)=>{for(const [path,v]of [...files])if(path===a||path.startsWith(a+'/')){files.set(path.replace(a,b),v);files.delete(path);}events.push(['move',a,b]);}
  };
  const native={windowsX64:()=>true,baseDir:()=>'/state',legacyURL:()=> 'http://127.0.0.1:17865',freeSpace:()=>lowDisk?0:1000,
    sha256:()=>badHash?'b'.repeat(64):manifest.sha256,delay:async()=>{},
    extract:async(_,dst,check)=>{assert.ok(check('python/python.exe'));files.set(dst+'/python/python.exe',[1]);events.push(['extract']);},
    preflight:async()=>{events.push(['preflight']);if(preflightFails)throw Error('resource corrupted');},
    start:async()=>{started++; files.set('/state/managed-service.json',{url:'http://127.0.0.1:12345'});return{exited:false};}
  };
  const request=async(url,options={})=>{
    if(url.startsWith('chrome:'))return Response.json(manifest);
    if(url.endsWith('/health'))return legacy&&url.includes('17865')?Response.json({version:'0.1.9',data_dir:'/state'}):url.includes('12345')&&started?Response.json({managed:true,version:'0.2.0',protocol_version:2}):new Response('',{status:404});
    requests.push(options);
    if(interrupt&&!interrupted){interrupted=true;return new Response(new ReadableStream({start(c){c.enqueue(new Uint8Array([1,2]));},pull(c){c.error(Error('network disconnected'));}}));}
    return options.headers?.Range ? new Response(new Uint8Array([3,4]),{status:206,headers:{'Content-Range':'bytes 2-3/4'}}):new Response(new Uint8Array([1,2,3,4]));
  };
  return{manager:new RuntimeManager(native,io,{join:(...p)=>p.join('/')},request),files,events,requests,get started(){return started;}};
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
