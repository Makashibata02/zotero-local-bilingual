// Actual workbench DOM/controller with a mock local API. No paid calls.
import assert from 'node:assert/strict';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {pathToFileURL,fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {transform} from 'esbuild';
const {chromium}=await import(pathToFileURL(process.env.ZLB_BROWSER_MODULE).href);
const root=fileURLToPath(new URL('../../',import.meta.url));
const output=join(root,'output/workbench-compact-030');await mkdir(output,{recursive:true});
const html=(await readFile(new URL('../addon/content/workbench.xhtml',import.meta.url),'utf8')).replace(/ onload="[^"]*"/,'');
const source=await readFile(new URL('../src/main.ts',import.meta.url),'utf8');
const {code}=await transform(source.replace(/^import[^\n]*\r?\n/,''),{loader:'ts',format:'iife'});
const browser=await chromium.launch({executablePath:process.env.ZLB_BROWSER_EXE,headless:true});
try {
  const page=await browser.newPage({viewport:{width:1120,height:720}});
  const pageErrors=[];page.on('pageerror',error=>pageErrors.push(String(error)));
  await page.setContent(html);
  await page.evaluate(()=> {
    window._errors=[];window._mockRequests=[];window._mockPercent=12;
    window.Zotero={Utilities:{Internal:{copyTextToClipboard:text=>window._copied=text}},Prefs:{get:key=>key.endsWith('serviceToken')?'test-token':key.endsWith('workbenchColumnWidths')?'{':undefined,set(){}},
      Items:{getAsync:async()=>({libraryID:1,getField:()=> 'Original'})},logError:error=>window._errors.push(String(error))};
    window.runtimeManager={ensure:async()=>({url:'http://127.0.0.1:17865',token:'test-token'})};
    window.fetch=async url=> {
      window._mockRequests.push(String(url));
      if(String(url).includes('/v1/jobs?')) return Response.json([
        {id:'old',status:'completed',progress:100,source_title:'旧论文',metadata:{title:'旧论文',filename:'old.pdf',attachment_id:'1',library_id:'1'},artifacts:[{kind:'dual',name:'missing.pdf'},{kind:'mono',name:'missing-mono.pdf'}]},
        {id:'current',status:'running',stage:'Translate Paragraphs',progress:window._mockPercent,source_title:'UNSURE: self-supervised learning with Unknown Noise level',
          metadata:{title:'UNSURE: self-supervised learning with Unknown Noise level',filename:"Tachella 等 - 2025 - UNSURE self-supervised learning with Unknown Noise level and Stein's Unbiased Risk Estimate.pdf"},target_language:'zh-CN',service_name:'自定义长配置名称 · deepseek-chat-academic-translation-latest' ,
          metrics:{usage_kind:'tokens',total_tokens:1278,request_count:2}}]);
      if(String(url).endsWith('/cancel')) return Response.json({ok:true});
      if(String(url).endsWith('/v1/scheduler')) return Response.json({max_parallel:1,counts:{running:1,completed:1}});
      if(String(url).includes('/artifacts/')) return Response.json({detail:'测试旧产物不存在'},{status:404});
      throw Error(`Unexpected mock request: ${url}`);
    };
  });
  await page.addScriptTag({content:code});
  await page.evaluate(async()=> {window.arguments=[window.Zotero.LocalBilingual];await window.Zotero.LocalBilingual.workbench.onLoad(window);});
  assert.equal(await page.locator('#zlb-jobs .job-row').count(),2);
  assert.match(await page.locator('#zlb-jobs').innerText(),/UNSURE/);
  assert.match(await page.locator('#zlb-jobs').innerText(),/翻译正文/);
  await page.waitForFunction(()=>document.getElementById('zlb-jobs').textContent.includes('附件导入失败'));
  await page.locator('[data-job-id=old] .details-toggle').click();
  assert.match(await page.locator('#zlb-detail-old').innerText(),/附件导入失败/);
  assert.equal(await page.locator('#zlb-detail-old button').filter({hasText:'打开译文 PDF'}).count(),1);
  assert.equal(await page.locator('[aria-label^="UNSURE"]').getAttribute('aria-valuenow'),'12');
  assert.ok(await page.evaluate(()=>!!window._zlbTimer));
  await page.locator('[data-job-id=old] .details-toggle').click();
  await page.locator('[data-job-id=current] .details-toggle').click();
  await page.evaluate(async()=> {window._mockPercent=47;await window.Zotero.LocalBilingual.workbench.refresh(window,false);});
  assert.equal(await page.locator('[aria-label^="UNSURE"]').getAttribute('aria-valuenow'),'47');
  assert.ok(await page.locator('#zlb-detail-current').isVisible());
  assert.match(await page.locator('#zlb-detail-current').innerText(),/Stein's Unbiased Risk Estimate/);
  await page.locator('#zlb-detail-current button').filter({hasText:'复制任务 ID'}).click();
  assert.equal(await page.evaluate(()=>window._copied),'current');
  assert.match(await page.locator('#zlb-detail-current .action-feedback').first().innerText(),/操作成功/);
  await page.locator('[data-job-id=current] .details-toggle').click();
  await page.evaluate(()=> {
    document.getElementById('zlb-search').value='旧论文';document.getElementById('zlb-filter').value='completed';window._zlbOffset=25;
    window.Zotero.LocalBilingual.workbench.showCurrent(window);
  });
  await page.waitForFunction(()=>!window._zlbRefreshing);
  assert.equal(await page.locator('#zlb-search').inputValue(),'');
  assert.equal(await page.locator('#zlb-filter').inputValue(),'');
  assert.equal(await page.evaluate(()=>window._zlbOffset),0);
  assert.deepEqual(pageErrors,[]);
  const viewports=[];
  for(const [width,height,zoom] of [[900,640,1],[800,600,1],[680,560,1],[480,600,1],[380,560,1],[800,600,1.25]]) {
    await page.setViewportSize({width,height});await page.evaluate(z=>document.body.style.zoom=String(z),zoom);
    const measure=await page.evaluate(()=> {
      const list=document.getElementById('table-scroll'),bar=document.querySelector('[aria-label^="UNSURE"]');
      const title=document.querySelector('[data-job-id=current] .job-document .muted');
      return {viewport:innerWidth,page_width:document.documentElement.scrollWidth,list_width:list.clientWidth,list_content:list.scrollWidth,
        progress_visible:bar.getBoundingClientRect().right<=innerWidth,title_ellipsized:title.scrollWidth>title.clientWidth};
    });
    assert.ok(measure.page_width<=width,JSON.stringify(measure));
    assert.ok(measure.list_content<=measure.list_width+1,JSON.stringify(measure));assert.ok(measure.progress_visible);
    assert.ok(measure.title_ellipsized);
    await page.evaluate(()=>document.getElementById('table-scroll').scrollTop=0);
    await page.screenshot({path:join(output,`workbench-${width}-${zoom}.png`)});
    await page.locator('[data-job-id=current] .details-toggle').click();
    assert.ok(await page.locator('#zlb-detail-current').isVisible());
    assert.match(await page.locator('#zlb-detail-current').innerText(),/Stein's Unbiased/);
    const expanded=await page.evaluate(()=>({page:document.documentElement.scrollWidth,list:document.getElementById('table-scroll').scrollWidth,width:document.getElementById('table-scroll').clientWidth}));
    assert.ok(expanded.page<=width && expanded.list<=expanded.width+1,JSON.stringify(expanded));
    await page.locator('[data-job-id=current] .details-toggle').click();
    viewports.push({width,height,zoom,...measure});
  }
  await page.locator('[data-job-id=current] button').filter({hasText:'取消任务'}).click();
  assert.match(await page.locator('[data-job-id=current] .action-feedback').innerText(),/操作成功/);
  assert.ok(await page.evaluate(()=>window._mockRequests.some(url=>url.endsWith('/current/cancel'))));
  assert.deepEqual(pageErrors,[]);
  const result={ok:true,viewports,details_persist:true,copy_id:true,cancel_feedback:true,dual_artifacts_accessible:true,historical_error_does_not_block:true,paid_requests:0};
  await writeFile(join(output,'browser-results.json'),JSON.stringify(result,null,2));
  await page.evaluate(()=>clearTimeout(window._zlbTimer));
  console.log(JSON.stringify(result));
} finally {await browser.close();}
