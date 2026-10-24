var handle;
function install() {} function uninstall() {} function shutdown() {if(handle)handle.destruct();}
function startup({rootURI}) {
 const service=Components.classes['@mozilla.org/addons/addon-manager-startup;1'].getService(Components.interfaces.amIAddonManagerStartup);
 handle=service.registerChrome(Services.io.newURI(rootURI+'manifest.json'),[['content','zlb-ui-test',rootURI+'content/'],['content','local-bilingual',rootURI+'content/']]);
 void Zotero.initializationPromise.then(async()=>{
  const win=Zotero.getMainWindow(),io=win.IOUtils,paths=win.PathUtils,base=__BASE__;
  const report={version:Zotero.version,os:Services.appinfo.OS,checks:[],requests:[]};
  let popup;
  const check=(ok,message)=>{if(!ok)throw Error(message);};
  const delay=ms=>Zotero.Promise.delay(ms);
  const mockZotero={isMac:Services.appinfo.OS === "Darwin",Prefs:{get:k=>k.endsWith('serviceToken')?'test-token':undefined,set(){}},logError:e=>report.checks.push({loggedError:String(e)})};
  const context={Zotero:mockZotero,URLSearchParams:win.URLSearchParams,fetch:async(url)=>{
   report.requests.push(String(url));
   if(String(url).includes('/v1/jobs?'))return new win.Response(JSON.stringify([{id:'fixture',status:'running',stage:'Translate Paragraphs',progress:42,metadata:{title:'Generated test paper with a long title for layout verification',filename:'生成的测试文件.pdf'},service_name:'测试翻译服务 · deepseek-flash',target_language:'zh-CN',metrics:{total_tokens:1234,request_count:4}}]));
   if(String(url).endsWith('/v1/scheduler'))return new win.Response(JSON.stringify({max_parallel:1,counts:{running:1}}));
   throw Error('Unexpected request: '+url);
  }};
  Services.scriptloader.loadSubScript('chrome://zlb-ui-test/content/plugin.js',context);
  const plugin=mockZotero.LocalBilingual;
  const ids=['zh-CN','zh-TW','ja','ko','fr','de','es'];
  const names=['简体中文','繁体中文','日语','韩语','法语','德语','西班牙语'];
  const supported=Object.fromEntries(ids.map(id=>[id,{supported:true}]));
  function args(){return {plugin,profiles:[{id:'default',name:'ds',model:'deepseek-flash',provider:'openai',is_default:true,has_api_key:true},{id:'unconfigured',name:'尚未配置密钥',model:'test',provider:'openai',has_api_key:false}],parallel:1,
   entries:[{attachment:{},metadata:{title:'Generated test paper — layout verification',authors_year:'Example · 2026',filename:'生成的测试文件.pdf'}}],
   capabilities:{languages:ids.map((id,i)=>({id,name:names[i]})),providers:{openai:supported,bing:supported,tencent:{'zh-CN':{supported:true}}}},resolve:value=>{report.selection=value;}};}
  async function open(name,width,height,zoom=1) {
   popup=win.openDialog('chrome://zlb-ui-test/content/'+name+'.xhtml','zlb-ui-test','chrome,dialog,resizable,width='+width+',height='+height,name==='confirm'?args():plugin);
   await new Promise(resolve=>popup.addEventListener('load',resolve,{once:true}));
   if(zoom!==1)popup.document.body.style.zoom=String(zoom);
   popup.focus();try{Services.focus.activeWindow=popup;}catch{}
   await delay(400);
   return popup.document;
  }
  async function snapshot(label) {
   const d=popup.document;
   const bitmap=await popup.browsingContext.currentWindowGlobal.drawSnapshot(new popup.DOMRect(0,0,popup.innerWidth,popup.innerHeight),2,'white');
   const canvas=d.createElementNS('http://www.w3.org/1999/xhtml','canvas');canvas.width=bitmap.width;canvas.height=bitmap.height;canvas.getContext('2d').drawImage(bitmap,0,0);bitmap.close();
   await io.write(paths.join(base,label+'.png'),new Uint8Array(await (await win.fetch(canvas.toDataURL('image/png'))).arrayBuffer()));
  }
  async function menu(id,label,expectedCount,fixed=true,keys=[]) {
   const d=popup.document,s=d.getElementById(id);s.scrollIntoView({block:'nearest'});s.focus();
   await delay(200);
   popup.focus();try{Services.focus.activeWindow=popup;}catch{};await delay(150);
   const input=popup.windowUtils.setHandlingUserInput(true);try{s.showPicker();}finally{input.destruct();}
   await delay(250);
   const mp=d.getElementById('ContentSelectDropdownPopup');check(mp,'Missing native popup for '+label);
   // Require the actual HTML select actor to open the popup, not a manual
   // openPopup fallback that could hide an activation or interaction failure.
   const forced=false;
   check(mp.state==='open','Native select did not open '+label);
   const items=[...mp.querySelectorAll('menuitem')].map(m=>({label:m.getAttribute('label'),disabled:m.disabled,rect:m.getBoundingClientRect().toJSON(),color:popup.getComputedStyle(m).color,
     labels:[...m.querySelectorAll('.menu-text,.menu-highlightable-text')].map(l=>({class:l.className,display:popup.getComputedStyle(l).display,rect:l.getBoundingClientRect().toJSON()}))}));
   const result={label,state:mp.state,forced,focused:d.hasFocus(),items};report.checks.push(result);
   check(items.length===expectedCount,'Wrong menu count '+label);
   if(fixed){
    check(items.every(m=>m.labels.filter(l=>l.display!=='none').length===1),'Duplicate menu labels '+label);
    check(mp.state==='open','Popup is not open '+label);
    check(items.every(m=>m.rect.height>0),'Invisible menu items '+label);
    check(items.every((m,i)=>!i||m.rect.y>=items[i-1].rect.bottom-1),'Overlapping menu rows '+label);
   }else check(items.some(m=>m.labels.filter(l=>l.display!=='none').length===2),'Baseline did not reproduce duplicate labels');
   result.keyboard=[];
   const tip=Components.classes['@mozilla.org/text-input-processor;1'].createInstance(Components.interfaces.nsITextInputProcessor);
   if(keys.length)check(tip.beginInputTransactionForTests(popup),'Cannot send native keyboard input');
   for(const key of keys){
    const codes={ArrowDown:40,ArrowUp:38,Enter:13,Escape:27,End:35,Home:36};
    const init={key,code:key,keyCode:codes[key],bubbles:true,cancelable:true};
    // macOS blinks a chosen menu item before firing its command.
    tip.keydown(new popup.KeyboardEvent('keydown',init));tip.keyup(new popup.KeyboardEvent('keyup',init));await delay(key==='Enter'?600:150);
    result.keyboard.push({key,active:mp.querySelector('[_moz-menuactive="true"]')?.getAttribute('label'),state:mp.state,value:s.value});
   }
   result.valueAfterKeys=s.value;result.keys=keys;
   mp.hidePopup();await delay(200);
   return result;
  }
  function change(id,value){const s=popup.document.getElementById(id);s.value=value;s.dispatchEvent(new popup.Event('change',{bubbles:true}));}
  function close(){if(popup&&!popup.closed){popup.clearTimeout(popup._zlbTimer);popup.close();}popup=null;}
  try {
   await open('confirm',680,760);popup.document.getElementById('zlb-native-menu-style').remove();await delay(200);await menu('model','baseline-model',4,false);close();
   for(const [label,width,height,zoom] of [['normal',680,760,1],['narrow',480,560,1],['zoom125',680,760,1.25]]) {
    const d=await open('confirm',width,height,zoom);
    check(d.querySelectorAll('#zlb-native-menu-style').length===1,'Missing or duplicate stylesheet');
    await snapshot('confirm-'+label);
    await menu('model','model-'+label,4);
    await menu('language','language-'+label,7);
    if(label==='normal') {
     await menu('model','keyboard-skip-disabled',4,true,['ArrowDown','Enter']);
     check(d.getElementById('model').value==='free-bing','Keyboard did not skip unavailable model');
     await menu('language','keyboard-language',7,true,['ArrowDown','ArrowDown','Enter']);
     check(d.getElementById('language').value==='ja','Keyboard language selection failed');
     const escaped=await menu('language','keyboard-escape',7,true,['ArrowDown','Escape']);
     check(escaped.keyboard.at(-1).state==='closed','Escape did not close the native menu');
     // Windows commits arrow-key changes immediately; macOS commits with Enter.
     const expectedEscape=Services.appinfo.OS==='WINNT'?escaped.keyboard[0].value:'ja';
     check(d.getElementById('language').value===expectedEscape,'Escape differs from native platform selection behavior');
    }
    change('language','ja');change('model','free-tencent');
    check(d.getElementById('language').value==='zh-CN','Unsupported language not reset');
    check(d.getElementById('language').options[2].disabled,'Unsupported language enabled');
    const disabled=await menu('language','disabled-language-'+label,7);
    check(disabled.items.filter(m=>m.disabled).length===6,'Disabled menu items missing');
    change('model','default');change('language','ja');
    check(!d.getElementById('confirm').disabled,'Supported selection disabled');
    const b=d.getElementById('confirm').getBoundingClientRect();check(b.bottom<=popup.innerHeight+1,'Confirm button outside viewport');
    check(d.documentElement.scrollWidth<=popup.innerWidth,'Confirmation horizontal overflow');
    plugin.confirmation.accept(popup);check(report.selection.target==='ja'&&report.selection.profile==='default','Selection payload changed');popup=null;
   }
   for(const [label,width,height,zoom] of [['normal',900,640,1],['narrow',480,600,1],['zoom125',800,600,1.25]]) {
    const d=await open('workbench',width,height,zoom);
    check(d.querySelectorAll('.job-row').length===1,'Workbench job missing');
    await menu('zlb-filter','filter-'+label,7);
    await menu('zlb-parallel','parallel-'+label,2);
    d.querySelector('.details-toggle').click();
    check(!d.querySelector('.job-details').hidden,'Details failed to expand');
    check(d.documentElement.scrollWidth<=popup.innerWidth,'Workbench horizontal overflow');
    await snapshot('workbench-'+label);close();
   }
   const settings=await open('settings',680,900);
   if(mockZotero.isMac)settings.getElementById('zlb-settings').setAttribute('data-platform','macos');
   settings.getElementById('zlb-version').textContent='本地双语 PDF __VERSION__ · 本地适配验证';
   const profiles=settings.getElementById('zlb-profile-select');
   for(const [value,title] of [['default','默认模型 · deepseek-flash'],['test','另一个测试模型']]) {
    const option=settings.createElementNS('http://www.w3.org/1999/xhtml','option');option.value=value;option.textContent=title;profiles.append(option);
   }
   await menu('zlb-profile-select','settings-profile',2);
   await menu('zlb-provider','settings-provider',4);
   settings.querySelector('details').open=true;
   await menu('zlb-thinking','settings-thinking',4);
   await menu('zlb-font-family','settings-font',3);
   const buttons=[...settings.querySelectorAll('button')].map(b=>{const rect=b.getBoundingClientRect(),range=settings.createRange();range.selectNodeContents(b);const text=range.getBoundingClientRect();return {id:b.id,visible:rect.height>0,contained:text.top>=rect.top-1&&text.bottom<=rect.bottom+1};});
   check(buttons.every(b=>!b.visible||b.contained),'Settings button text clipped');
   report.checks.push({label:'settings-buttons',buttons});
   settings.getElementById('zlb-settings').scrollIntoView({block:'start'});await snapshot('settings');close();
   report.ok=true;
  }catch(e){report.error=String(e);report.stack=e.stack;}
  finally{close();}
  await io.writeJSON(paths.join(base,'results.json'),report);
  Services.startup.quit(Components.interfaces.nsIAppStartup.eForceQuit);
 }).catch(e=>Components.utils.reportError(e));
}
