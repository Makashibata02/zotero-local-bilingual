import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFile} from 'node:fs/promises';
import {transform} from 'esbuild';

const source=await readFile(new URL('../src/main.ts',import.meta.url),'utf8');
const {code}=await transform(source.replace(/^import[^\n]*\r?\n/,''),{loader:'ts',format:'iife'});

// Layout and popup rendering are checked in real Zotero. This small DOM fixture
// exercises both actual controllers without starting a service or browser.
class Node {
  constructor(localName='div') {
    this.localName=localName;this.children=[];this.dataset={};
    this.value='';this.textContent='';this.disabled=false;this.checked=false;
  }
  append(...children) {this.children.push(...children);}
  replaceChildren(...children) {this.children=[...children];}
  addEventListener() {}
  get selectedOptions() {return this.children.filter(child=>child.selected);}
  querySelector(selector) {
    for(const child of this.children) {
      if(selector==='input:checked'&&child.localName==='input'&&child.checked)return child;
      const match=child.querySelector(selector);if(match)return match;
    }
    return null;
  }
}

function harness(isMac) {
  const nodes=new Map();
  for(const id of ['model','language','output','parallel','files','model-note','language-note','limits','confirm','error',
    'zlb-jobs','zlb-engine-status','zlb-workbench-status','zlb-search','zlb-filter','zlb-hidden','zlb-parallel','zlb-counts','zlb-page']) {
    const node=new Node(['model','language','zlb-filter','zlb-parallel'].includes(id)?'select':'div');
    node.id=id;nodes.set(id,node);
  }
  const theme=new Node('link');theme.id='existing-theme';theme.href='chrome://example/content/theme.css';
  const head=new Node('head');head.append(theme);
  const document={head,getElementById:id=>nodes.get(id)||head.children.find(node=>node.id===id)||null,
    createElementNS:(namespace,tag)=>Object.assign(new Node(tag),{namespaceURI:namespace}),
    querySelectorAll:selector=>{assert.equal(selector,'#files input');return [];}};
  const prefs={serviceUrl:'http://127.0.0.1:17865',serviceToken:'test-token'};
  const Zotero={isMac,Prefs:{get:key=>prefs[key.split('.').pop()],set:(key,value)=>prefs[key.split('.').pop()]=value}};
  const requests=[];
  vm.runInNewContext(code,{Zotero,Headers,URLSearchParams,fetch:async url=>{
    requests.push(String(url));
    if(String(url).includes('/v1/jobs?'))return Response.json([]);
    assert.ok(String(url).endsWith('/v1/scheduler'));
    return Response.json({max_parallel:1,counts:{}});
  }});
  const window={document,clearTimeout(){},setTimeout(){return 1;},arguments:[{
    profiles:[{id:'api-default',name:'测试模型',model:'model-a',provider:'openai',is_default:true,has_api_key:true}],
    entries:[],parallel:1,
    capabilities:{languages:[{id:'zh-CN',name:'简体中文'}],providers:{openai:{'zh-CN':{supported:true}}}}
  }]};
  return {api:Zotero.LocalBilingual,window,document,nodes,head,theme,requests};
}

function assertNativeTheme(h) {
  assert.equal(h.head.children.length,2);
  assert.equal(h.head.children[0],h.theme);
  const link=h.head.children[1];
  assert.equal(link.localName,'link');
  assert.equal(link.namespaceURI,'http://www.w3.org/1999/xhtml');
  assert.equal(link.rel,'stylesheet');
  assert.equal(link.href,'chrome://local-bilingual/content/native-menus.css');
}

for(const isMac of [true,false]) {
  const platform=isMac?'Mac':'Windows';
  test(`${platform} confirmation loads the shared native theme and retains HTML selections`,()=>{
    const h=harness(isMac),model=h.nodes.get('model'),language=h.nodes.get('language');
    h.api.confirmation.onLoad(h.window);
    h.api.confirmation.onLoad(h.window);
    assertNativeTheme(h);
    assert.equal(h.document.getElementById('model'),model);
    assert.equal(h.document.getElementById('language'),language);
    assert.equal(model.localName,'select');assert.equal(language.localName,'select');
    assert.equal(h.api.confirmation.choice(h.window,'model').value,'api-default');
    assert.equal(h.api.confirmation.choice(h.window,'language').value,'zh-CN');
    assert.deepEqual(model.children.map(option=>option.value),['api-default','free-bing','free-tencent']);
    assert.equal(h.window.arguments[0].ready,true);
  });

  test(`${platform} workbench initializes its menus with one shared native theme`,async()=>{
    const h=harness(isMac),filter=h.nodes.get('zlb-filter'),parallel=h.nodes.get('zlb-parallel');
    await h.api.workbench.onLoad(h.window);
    await h.api.workbench.onLoad(h.window);
    // Also exercise the stylesheet's own idempotence if the controller reloads.
    h.window._zlbLoaded=false;
    await h.api.workbench.onLoad(h.window);
    assertNativeTheme(h);
    assert.equal(h.document.getElementById('zlb-filter'),filter);
    assert.equal(h.document.getElementById('zlb-parallel'),parallel);
    assert.equal(filter.localName,'select');assert.equal(parallel.localName,'select');
    assert.equal(parallel.value,'1');
    assert.match(h.nodes.get('zlb-workbench-status').textContent,/还没有任务/);
    assert.equal(h.requests.length,4);
  });
}

// These are the actual chrome documents; loading the theme before `load` avoids
// a first-click race. Unit DOM mocks alone do not verify native popup rendering.
test('both standalone documents preload the native popup theme', async()=>{
  for(const name of ['confirm.xhtml','workbench.xhtml']) {
    const html=await readFile(new URL('../addon/content/'+name,import.meta.url),'utf8');
    assert.match(html,/<head><link id="zlb-native-menu-style" rel="stylesheet" href="chrome:\/\/local-bilingual\/content\/native-menus\.css"\/>/);
  }
});
test('isolated UI profiles skip Word auto-installation', async()=>{
  const fixture=await readFile(new URL('../../scripts/prepare_macos_ui_check.py',import.meta.url),'utf8');
  assert.match(fixture,/"extensions\.zoteroWinWordIntegration\.skipInstallation": True/);
});
