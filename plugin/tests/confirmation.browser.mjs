// Render the actual confirmation document/controller without a service or API.
// ZLB_BROWSER_MODULE points to an existing Playwright installation; no install.
import assert from 'node:assert/strict';
import {readFile, mkdir, writeFile} from 'node:fs/promises';
import {pathToFileURL, fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {transform} from 'esbuild';

const {chromium}=await import(pathToFileURL(process.env.ZLB_BROWSER_MODULE).href);
const root=fileURLToPath(new URL('../../',import.meta.url));
const output=join(root,'output/ui-fix-030');
await mkdir(output,{recursive:true});
const html=(await readFile(new URL('../addon/content/confirm.xhtml',import.meta.url),'utf8'))
  .replace(/ onload="[^"]*"/,'');
const source=await readFile(new URL('../src/main.ts',import.meta.url),'utf8');
const {code}=await transform(source.replace(/^import[^\n]*\r?\n/,''),{loader:'ts',format:'iife'});
const browser=await chromium.launch({executablePath:process.env.ZLB_BROWSER_EXE,headless:true});
const checks=[];
try {
  for(const [label,width,height,zoom] of [['normal',680,760,1],['narrow',480,560,1],['zoom125',680,760,1.25]]) {
    const page=await browser.newPage({viewport:{width,height}});
    const errors=[];page.on('pageerror',error=>errors.push(String(error)));
    await page.setContent(html);
    await page.evaluate(()=>{
      window.Zotero={Prefs:{get:key=>key.endsWith('profileId')?'default':undefined,set(){}}};
      window.runtimeManager={};
      window._requestCount=0;window.fetch=()=>{window._requestCount++;throw Error('No network allowed in confirmation test');};
      const names=['简体中文','繁体中文','日语','韩语','法语','德语','西班牙语'];
      const ids=['zh-CN','zh-TW','ja','ko','fr','de','es'];
      const supported=Object.fromEntries(ids.map(id=>[id,{supported:true}]));
      window.arguments=[{profiles:[{id:'default',name:'默认翻译服务',model:'deepseek-chat',provider:'openai'}],parallel:1,
        entries:[{attachment:{},metadata:{title:"UNSURE: self-supervised learning with Unknown Noise level and Stein's Unbiased Risk Estimate",authors_year:'Tachella 等 · 2025-02-11',filename:"Tachella 等 - 2025 - UNSURE self-supervised learning with Unknown Noise level and Stein's Unbiased Risk Estimate.pdf"}},
          {attachment:{_zlbDefault:false},metadata:{title:'补充材料',authors_year:'Tachella 等 · 2025',filename:'含中文路径的补充材料.pdf'}}],
        capabilities:{languages:ids.map((id,i)=>({id,name:names[i]})),providers:{openai:supported,bing:supported,tencent:{'zh-CN':{supported:true}}}},
        resolve:value=>{window._choice=value;}}];
      window.close=()=>{window._closed=true;};
    });
    await page.addScriptTag({content:code});
    await page.evaluate(z=>{
      window.arguments[0].plugin=window.Zotero.LocalBilingual;
      window.Zotero.LocalBilingual.confirmation.onLoad(window);
      document.body.style.zoom=String(z);
    },zoom);
    assert.equal(await page.locator('select').count(),0);
    assert.equal(await page.locator('#model input').count(),3);
    assert.equal(await page.locator('#language input').count(),7);
    assert.equal(await page.locator('#language input:checked').inputValue(),'zh-CN');
    assert.equal(await page.locator('#output input:checked').inputValue(),'dual');
    assert.equal(await page.locator('#files input:checked').count(),1);
    await page.screenshot({path:join(output,`confirmation-${label}-top.png`)});
    for(const [group,value] of [['language','ja'],['output','dual_and_mono'],['parallel','2']]) {
      await page.locator(`#${group} input[value="${value}"]`).check();
      assert.equal(await page.locator(`#${group} input:checked`).inputValue(),value);
    }
    const language=await page.locator('#language input:checked').elementHandle();
    await page.locator('#files input[value="1"]').check();
    assert.equal(await page.evaluate(element=>element.isConnected,language),true);
    assert.match(await page.locator('#limits').innerText(),/两篇并行/);
    assert.match(await page.locator('#limits').innerText(),/2000000 tokens/);
    await page.locator('#parallel').scrollIntoViewIfNeeded();
    await page.screenshot({path:join(output,`confirmation-${label}-options.png`)});
    const bounds=await page.evaluate(()=>{
      const footer=document.querySelector('footer').getBoundingClientRect();
      const button=document.getElementById('confirm').getBoundingClientRect();
      const main=document.querySelector('main');
      const cards=[...document.querySelectorAll('.choice')].map(card=>{
        const box=card.getBoundingClientRect(),radio=card.querySelector('input').getBoundingClientRect();
        return{left:box.left,right:box.right,top:box.top,bottom:box.bottom,radioInside:radio.left>=box.left&&radio.right<=box.right&&radio.top>=box.top&&radio.bottom<=box.bottom};
      });
      return{footerBottom:footer.bottom,buttonBottom:button.bottom,height:innerHeight,scrollable:main.scrollHeight>main.clientHeight,
        cardsInside:cards.every(card=>card.left>=0&&card.right<=innerWidth&&card.radioInside),
        overlap:cards.some((a,i)=>cards.slice(i+1).some(b=>Math.min(a.right,b.right)-Math.max(a.left,b.left)>1&&Math.min(a.bottom,b.bottom)-Math.max(a.top,b.top)>1))};
    });
    assert.ok(bounds.cardsInside);assert.ok(!bounds.overlap);
    assert.ok(bounds.buttonBottom<=height+1);assert.ok(bounds.footerBottom<=height+1);
    await page.locator('#model input[value="free-tencent"]').check();
    assert.equal(await page.locator('#language input:checked').inputValue(),'zh-CN');
    assert.ok(await page.locator('#language input[value="ja"]').isDisabled());
    await page.locator('#model input[value="default"]').check();
    await page.locator('#language input[value="ja"]').check();
    await page.locator('#files input[value="0"]').uncheck();
    await page.locator('#files input[value="1"]').uncheck();
    assert.ok(await page.locator('#confirm').isDisabled());
    await page.locator('#files input[value="1"]').check();
    await page.locator('#confirm').click();
    const choice=await page.evaluate(()=>window._choice);
    assert.deepEqual(choice,{indices:[1],profile:'default',provider:'openai',target:'ja',output:'dual_and_mono',parallel:2});
    assert.equal(await page.evaluate(()=>window._requestCount),0);
    assert.deepEqual(errors,[]);
    checks.push({label,width,height,zoom,ok:true,bounds,choice,network_requests:0});
    await page.close();
  }
  await writeFile(join(output,'browser-results.json'),JSON.stringify({ok:true,checks},null,2));
  console.log(JSON.stringify({ok:true,cases:checks.length,output}));
} finally {await browser.close();}
