// Actual templates/controller, isolated simulated profiles and no provider traffic.
import assert from 'node:assert/strict';
import {readFile,mkdir,writeFile,copyFile} from 'node:fs/promises';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {join} from 'node:path';
import {transform} from 'esbuild';
const {chromium}=await import(pathToFileURL(process.env.ZLB_BROWSER_MODULE).href);
const root=fileURLToPath(new URL('../../',import.meta.url));
const out=join(root,'output/ui-101-management');await mkdir(out,{recursive:true});
const source=await readFile(new URL('../src/main.ts',import.meta.url),'utf8');
const {code}=await transform(source.replace(/^import[^\n]*\r?\n/,''),{loader:'ts',format:'iife',define:{__PLUGIN_VERSION__:'"1.0.1"'}});
const settings=(await readFile(new URL('../addon/content/preferences.xhtml',import.meta.url),'utf8')).replace(/ onload="[^"]*"/,'').replace(/(<\/?)(html:)/g,'$1');
const confirmation=(await readFile(new URL('../addon/content/confirm.xhtml',import.meta.url),'utf8')).replace(/ onload="[^"]*"/,'');
const browser=await chromium.launch({executablePath:process.env.ZLB_BROWSER_EXE,headless:true});
const checks=[];
try {
 for(const [label,width,height,zoom] of [['normal',560,640,1],['narrow',480,560,1],['zoom125',560,640,1.25]]) {
  const page=await browser.newPage({viewport:{width,height}});const errors=[];page.on('pageerror',e=>errors.push(String(e)));
  await page.setContent(settings);
  await page.evaluate(()=>{
   const prefs={serviceToken:'LOCAL-MOCK-ONLY',profileId:'p03',provider:'openai',profileName:'Example 03',model:'mock',pdfSelftestPassed:'true'};
   window._profiles=Array.from({length:40},(_,i)=>({id:'p'+String(i).padStart(2,'0'),name:'Example '+String(i).padStart(2,'0')+' · 很长的学术翻译配置名称',model:'mock-model-with-a-long-name',base_url:'https://example.invalid/v1',provider:'openai',has_api_key:true,is_default:i===8,can_delete:true}));
   window._calls=[];window._deleteMode='ok';window._consent=false;window._providerCalls=0;
   window.Zotero={Prefs:{get:k=>prefs[k.split('.').pop()],set:(k,v)=>prefs[k.split('.').pop()]=v},getMainWindow:()=>({confirm:message=>{window._confirmationMessage=message;return window._consent;}}),logError(){}};
   window.RuntimeNative={};window.runtimeManager={ensure:async()=>({url:'http://127.0.0.1:17865',token:'LOCAL-MOCK-ONLY'})};
   window.fetch=async (url,init={})=>{
    window._calls.push({url,method:init.method||'GET'});
    if(url.endsWith('/v1/health'))return Response.json({version:'1.0.1',deployment_verified:true});
    if(url.endsWith('/v1/profiles'))return Response.json(window._profiles);
    if(url.endsWith('/default')) {
     const id=url.split('/').at(-2),body=init.body?JSON.parse(init.body):{};
     if(!body.initialize_only)window._profiles.forEach(p=>p.is_default=p.id===id);
     return Response.json({default_profile_id:window._profiles.find(p=>p.is_default)?.id||''});
    }
    if(init.method==='DELETE') {
     if(window._deleteMode==='error')return Response.json({detail:'无法删除 Windows 凭据中的 API Key，配置未删除'},{status:503});
     const id=url.split('/').at(-1),p=window._profiles.find(p=>p.id===id);
     window._profiles=window._profiles.filter(p=>p.id!==id);
     if(p.is_default && window._profiles.length)window._profiles[0].is_default=true;
     return Response.json({deleted:true,default_profile_id:window._profiles.find(p=>p.is_default)?.id||''});
    }
    window._providerCalls++;throw Error('Unexpected provider call');
   };
  });
  await page.addScriptTag({content:code});
  await page.evaluate(z=>{Zotero.LocalBilingual.preferences.onLoad(document);document.body.style.zoom=String(z);},zoom);
  await page.waitForFunction(()=>document.querySelectorAll('#zlb-profile-select option').length===40);
  assert.equal(await page.locator('#zlb-profile-select').inputValue(),'p03');
  await page.locator('#zlb-profile-select').selectOption('p09');
  assert.equal(await page.evaluate(()=>window._profiles.find(p=>p.is_default).id),'p08');
  await page.locator('#zlb-default-profile-button').click();
  await page.waitForFunction(()=>document.getElementById('zlb-default-profile-status').textContent.includes('已保存'));
  assert.equal(await page.evaluate(()=>window._profiles.find(p=>p.is_default).id),'p09');
  const before=await page.evaluate(()=>window._calls.filter(c=>c.method==='DELETE').length);
  await page.locator('#zlb-delete-profile-button').click();
  assert.match(await page.locator('#zlb-delete-profile-status').innerText(),/取消删除/);
  assert.equal(await page.evaluate(()=>window._calls.filter(c=>c.method==='DELETE').length),before);
  await page.evaluate(()=>{window._consent=true;window._deleteMode='error';});
  await page.locator('#zlb-delete-profile-button').click();
  await page.waitForFunction(()=>document.getElementById('zlb-delete-profile-status').textContent.includes('删除失败'));
  assert.equal(await page.locator('#zlb-profile-select option').count(),40);
  await page.evaluate(()=>{window._deleteMode='ok';});
  await page.locator('#zlb-delete-profile-button').click();
  await page.waitForFunction(()=>document.getElementById('zlb-delete-profile-status').textContent.includes('已删除'));
  assert.equal(await page.locator('#zlb-profile-select option').count(),39);
  assert.match(await page.evaluate(()=>window._confirmationMessage),/默认 API 改为/);
  assert.equal(await page.locator('#zlb-profile-select').inputValue(),'p00');
  const bounds=await page.evaluate(()=>({overflow:document.documentElement.scrollWidth>innerWidth+1,feedback:['default','delete'].map(type=>{const button=document.getElementById(`zlb-${type}-profile-button`).getBoundingClientRect(),status=document.getElementById(`zlb-${type}-profile-status`).getBoundingClientRect();return{below:status.top>=button.bottom,gap:status.top-button.bottom};})}));
  assert.ok(!bounds.overflow);assert.ok(bounds.feedback.every(p=>p.below&&p.gap<=15));
  await page.locator('#zlb-profile-select').scrollIntoViewIfNeeded();
  const screenshot=join(out,`profiles-${label}.png`);await page.screenshot({path:screenshot});
  if(label==='normal')await copyFile(screenshot,join(root,'docs/images/profiles-101.png'));
  assert.equal(await page.evaluate(()=>window._providerCalls),0);assert.deepEqual(errors,[]);
  checks.push({kind:'settings',label,width,height,zoom,ok:true,bounds,provider_requests:0});
  await page.close();

  const confirm=await browser.newPage({viewport:{width,height}});
  await confirm.setContent(confirmation);
  await confirm.evaluate(()=>{
   window.Zotero={Prefs:{get:k=>k.endsWith('profileId')?'p03':undefined,set(){}}};window.runtimeManager={};window._requests=0;
   window.fetch=()=>{window._requests++;throw Error('No calls permitted');};
   const ids=['zh-CN','zh-TW','ja','ko','fr','de','es'],names=['简体中文','繁体中文','日语','韩语','法语','德语','西班牙语'];
   window.arguments=[{profiles:Array.from({length:40},(_,i)=>({id:'p'+String(i).padStart(2,'0'),name:'Example '+i+' · 长配置名称',model:'long-model-name',provider:'openai',has_api_key:true,is_default:i===8})),parallel:1,entries:[{attachment:{},metadata:{title:'A long academic paper title with 中文路径示例 and preserved figures',authors_year:'Example 等 · 2025',filename:'Example - 2025 - paper.pdf'}}],capabilities:{languages:ids.map((id,i)=>({id,name:names[i]})),providers:{openai:Object.fromEntries(ids.map(id=>[id,{supported:true}])),bing:{'zh-CN':{supported:true}},tencent:{fr:{supported:true}}}},resolve:v=>window._choice=v}];window.close=()=>{};
  });
  await confirm.addScriptTag({content:code});
  await confirm.evaluate(z=>{window.arguments[0].plugin=Zotero.LocalBilingual;Zotero.LocalBilingual.confirmation.onLoad(window);document.body.style.zoom=String(z);},zoom);
  assert.equal(await confirm.locator('#model').inputValue(),'p08');assert.equal(await confirm.locator('#language').inputValue(),'zh-CN');
  await confirm.locator('#language').selectOption('ja');await confirm.locator('#model').selectOption('free-bing');
  assert.equal(await confirm.locator('#language').inputValue(),'zh-CN');assert.match(await confirm.locator('#language-note').innerText(),/已切换/);
  await confirm.locator('#model').selectOption('free-tencent');assert.ok(await confirm.locator('#confirm').isDisabled());
  await confirm.locator('#language').selectOption('fr');assert.ok(!await confirm.locator('#confirm').isDisabled());
  await confirm.locator('#model').selectOption('p08');await confirm.locator('#language').selectOption('zh-CN');
  const layout=await confirm.evaluate(()=>({overflow:document.documentElement.scrollWidth>innerWidth+1,buttonBottom:document.getElementById('confirm').getBoundingClientRect().bottom,selectCount:document.querySelectorAll('select').length}));
  assert.ok(!layout.overflow);assert.ok(layout.buttonBottom<=height+1);assert.equal(layout.selectCount,2);
  const shot=join(out,`confirm-${label}.png`);await confirm.screenshot({path:shot});if(label==='normal')await copyFile(shot,join(root,'docs/images/confirm-101.png'));
  await confirm.locator('button').filter({hasText:'取消'}).click();assert.equal(await confirm.evaluate(()=>window._choice),null);assert.equal(await confirm.evaluate(()=>window._requests),0);
  checks.push({kind:'confirmation',label,width,height,zoom,ok:true,layout,provider_requests:0});await confirm.close();
 }
 await writeFile(join(out,'browser-results.json'),JSON.stringify({ok:true,checks},null,2));
 console.log(JSON.stringify({ok:true,cases:checks.length,output:out}));
}finally{await browser.close();}
