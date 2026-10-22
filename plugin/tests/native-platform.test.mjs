import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFile} from 'node:fs/promises';
const source=await readFile(new URL('../addon/runtime-native.js',import.meta.url),'utf8');
function native(os,abi) {
  const calls=[];
  const services={sysinfo:{getProperty:()=> '23.0.0'},appinfo:{OS:os,XPCOMABI:abi},env:{get:()=>'/LocalAppData'},dirsvc:{get:()=>({path:'/Users/Example User'})}};
  services.env.get=n=>n==='LOCALAPPDATA'?'/LocalAppData':'';
  const sandbox={Components:{classes:{},interfaces:{nsIFile:{}}},Services:services,Zotero:{Prefs:{get:()=>null},Promise:{delay:()=>new Promise(()=>{})}},ChromeUtils:{importESModule:()=>({Subprocess:{call:async opts=>{calls.push(opts);return {stdout:{readString:async()=>''},stderr:{readString:async()=>''},wait:()=>new Promise(()=>{})};}}})}};
  vm.runInNewContext(source,sandbox);
  const api=sandbox.createRuntimeNative({PathUtils:{join:(...s)=>s.join('/'),parent:p=>p.slice(0,p.lastIndexOf('/'))},IOUtils:{setPermissions:async(...a)=>calls.push(a),makeDirectory:async()=>{}}});
  return {api,calls,services};
}
test('OS/ABI selection covers native Apple Silicon, Intel/Rosetta and Windows',()=>{
  for(const [os,abi,want]of [['Darwin','aarch64-gcc3','macos-arm64'],['Darwin','arm64-gcc3','macos-arm64'],['Darwin','x86_64-gcc3','macos-x64'],['WINNT','x86_64-msvc','windows-x64'],['Linux','x86_64-gcc3',null]]) assert.equal(native(os,abi).api.platform(),want);
});
test('Mac stores data under Application Support and respects case-sensitive volumes',()=>{
  const h=native('Darwin','aarch64-gcc3');
  assert.equal(h.api.baseDir(),'/Users/Example User/Library/Application Support/ZoteroLocalBilingual');
  assert.equal(h.api.samePath('/Users/A','/users/a'),false);
  assert.equal(native('WINNT','x86_64-msvc').api.samePath('C:/Users/A','c:/users/a'),true);
});
test('Mac interpreter is invoked without shell expansion even for paths with spaces',async()=>{
  const h=native('Darwin','aarch64-gcc3');await h.api.secureDirectory('/data');
  await h.api.start('/Users/Example User/引擎','/data');
  const launch=h.calls.find(c=>c.command);
  assert.equal(launch.command,'/Users/Example User/引擎/python/bin/python3.12');
  assert.ok(launch.arguments.includes('/Users/Example User/引擎'));
  assert.deepEqual(h.calls[0],['/data',0o700]);
});

test('Mac version floor rejects older systems before downloading an engine',()=>{
  const h=native('Darwin','aarch64-gcc3');assert.doesNotThrow(()=>h.api.checkSystem());
  h.services.sysinfo.getProperty=()=> '22.6.0';assert.throws(()=>h.api.checkSystem(),/macOS 14/);
});
test('streaming Mac ZIP extraction restores interpreter permissions and refuses symlinks',async()=>{
  const modes=[];let active=0;let symbolic=false;
  const names=['python/bin/python3.12','python/lib/libpython3.12.dylib','assets/font.ttf'];
  const zip={open:()=>{},close:()=>{},findEntries:()=>({hasMore:()=>active<names.length,getNext:()=>names[active++]}),getEntry:()=>({realSize:1,permissions:symbolic?0o120777:0o100644,isDirectory:false}),getInputStream:()=>({})};
  const sandbox={Components:{classes:{
    '@mozilla.org/file/local;1':{createInstance:()=>({initWithPath:()=>{}})},
    '@mozilla.org/libjar/zip-reader;1':{createInstance:()=>zip},
    '@mozilla.org/binaryinputstream;1':{createInstance:()=>{let unread=true;return{setInputStream:()=>{},available:()=>unread?1:0,readByteArray:()=>{unread=false;return[0];},close:()=>{}};}}
  },interfaces:{}},Services:{appinfo:{OS:'Darwin',XPCOMABI:'aarch64-gcc3'}},ChromeUtils:{importESModule:()=>({Subprocess:{}})}};
  vm.runInNewContext(source,sandbox);
  const api=sandbox.createRuntimeNative({PathUtils:{join:(...s)=>s.join('/'),parent:p=>p.slice(0,p.lastIndexOf('/'))},IOUtils:{makeDirectory:async()=>{},write:async()=>{},setPermissions:async(p,m)=>modes.push([p,m])}});
  await api.extract('/test.zip','/stage',()=>true,100);
  assert.deepEqual(modes,[['/stage/python/bin/python3.12',0o700],['/stage/python/lib/libpython3.12.dylib',0o600],['/stage/assets/font.ttf',0o600]]);
  active=0;symbolic=true;await assert.rejects(api.extract('/test.zip','/stage',()=>true,100),/符号链接/);
});
