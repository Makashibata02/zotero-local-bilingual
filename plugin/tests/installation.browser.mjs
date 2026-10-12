// Actual settings template and controller, with simulated runtime and no provider calls.
// XHTML namespace prefixes are removed for Chromium; native Zotero remains manual.
import assert from 'node:assert/strict';
import {readFile,mkdir,writeFile,copyFile} from 'node:fs/promises';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {join} from 'node:path';
import {transform} from 'esbuild';
const {chromium}=await import(pathToFileURL(process.env.ZLB_BROWSER_MODULE).href);
const root=fileURLToPath(new URL('../../',import.meta.url));
const output=join(root,'output/ui-100');await mkdir(output,{recursive:true});
const html=(await readFile(new URL('../addon/content/preferences.xhtml',import.meta.url),'utf8')).replace(/ onload="[^"]*"/,'').replace(/(<\/?)(html:)/g,'$1');
const source=await readFile(new URL('../src/main.ts',import.meta.url),'utf8');
const {code}=await transform(source.replace(/^import[^\n]*\r?\n/,''),{loader:'ts',format:'iife',define:{__PLUGIN_VERSION__:'"1.0.0"'}});
const browser=await chromium.launch({executablePath:process.env.ZLB_BROWSER_EXE,headless:true});
const checks=[];
try {
 for(const [label,width,height,zoom] of [['normal',700,950,1],['narrow',480,850,1],['zoom125',700,950,1.25]]) {
  const page=await browser.newPage({viewport:{width,height}});const errors=[];page.on('pageerror',e=>errors.push(String(e)));
  await page.setContent(html);
  await page.evaluate(()=>{
   const values={provider:'bing'};
   window.Zotero={Prefs:{get:k=>values[k.split('.').pop()],set:(k,v)=>values[k.split('.').pop()]=v},launchURL:u=>window._url=u,logError(){}};
   window._mode='ok';window._path=null;window._calls=[];window._providerCalls=0;
   window.RuntimeNative={chooseZip:async()=>window._path};
   window.runtimeManager={ensure:async(feedback,path)=>{window._calls.push(path??'online');feedback('下载 100% · 正在校验 SHA-256…');await new Promise(r=>setTimeout(r,80));if(window._mode==='fail')throw Error('HTTP 503');return{url:'http://127.0.0.1:17865',token:'test-only-local-token'};}};
   window.fetch=async u=>{if(u.endsWith('/v1/health'))return Response.json({version:'1.0.0'});if(u.endsWith('/v1/profiles'))return Response.json([]);window._providerCalls++;throw Error('Unexpected provider request');};
  });
  await page.addScriptTag({content:code});
  await page.evaluate(z=>{Zotero.LocalBilingual.preferences.onLoad(document);document.body.style.zoom=String(z);},zoom);
  await page.waitForTimeout(100);
  assert.match(await page.locator('#zlb-version').innerText(),/1\.0\.0/);
  const bounds=await page.evaluate(()=>{const box=id=>document.getElementById(id).getBoundingClientRect();return{engineBottom:box('zlb-engine-setup').bottom,profileTop:box('zlb-profile-select').top,overflow:document.documentElement.scrollWidth>innerWidth+1,buttonVisible:box('zlb-import-engine-button').bottom<=innerHeight};});
  assert.ok(bounds.engineBottom<bounds.profileTop);assert.ok(!bounds.overflow);assert.ok(bounds.buttonVisible);
  await page.locator('#zlb-download-engine-button').click();
  await page.waitForFunction(()=>document.getElementById('zlb-online-install-status').textContent.includes('成功'));
  assert.match(await page.locator('#zlb-online-install-status').innerText(),/未调用翻译模型/);
  assert.equal(await page.evaluate(()=>window._providerCalls),0);
  const screenshot=join(output,`settings-${label}.png`);await page.screenshot({path:screenshot});
  if(label==='normal')await copyFile(screenshot,join(root,'docs/images/settings-100.png'));
  await page.locator('#zlb-import-engine-button').click();
  assert.match(await page.locator('#zlb-install-status').innerText(),/已取消/);
  assert.equal(await page.evaluate(()=>window._calls.length),1);
  await page.evaluate(()=>{window._path='offline-example.zip';});
  await page.locator('#zlb-import-engine-button').click();
  await page.waitForFunction(()=>document.getElementById('zlb-install-status').textContent.includes('成功'));
  await page.evaluate(()=>{window._mode='fail';});
  await page.locator('#zlb-download-engine-button').click();
  await page.waitForFunction(()=>document.getElementById('zlb-online-install-status').textContent.includes('HTTP 503'));
  assert.match(await page.locator('#zlb-online-install-status').innerText(),/离线导入/);
  assert.equal(await page.locator('#zlb-import-engine-button').isDisabled(),false);
  await page.locator('#zlb-release-page-button').click();
  assert.equal(await page.evaluate(()=>window._url),'https://github.com/Makashibata02/zotero-local-bilingual/releases/tag/v1.0.0');
  assert.equal(await page.evaluate(()=>window._providerCalls),0);assert.deepEqual(errors,[]);
  const feedbackBounds=await page.evaluate(()=>{const b=document.getElementById('zlb-download-engine-button').getBoundingClientRect(),p=document.getElementById('zlb-online-install-status').getBoundingClientRect();return{below:p.top>=b.bottom,gap:p.top-b.bottom};});
  assert.ok(feedbackBounds.below);assert.ok(feedbackBounds.gap<=10);
  checks.push({label,width,height,zoom,bounds,feedbackBounds,translation_requests:0});await page.close();
 }
 await writeFile(join(output,'results.json'),JSON.stringify({ok:true,checks},null,2));console.log(JSON.stringify({ok:true,cases:checks.length}));
} finally {await browser.close();}
