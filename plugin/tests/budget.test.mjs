import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFile} from 'node:fs/promises';
import {webcrypto} from 'node:crypto';
import {transform} from 'esbuild';
const source=await readFile(new URL('../src/main.ts',import.meta.url),'utf8');
const {code}=await transform(source.replace(/^import[^\n]*\r?\n/,''),{loader:'ts',format:'iife'});
const job={id:'a'.repeat(32),budget:{can_extend:true,max_total_tokens:60000,max_requests:100},
  metrics:{total_tokens:47306,request_count:34,unconfirmed_tokens:0}};
function harness(answers,confirmed=true,fail=false) {
  const calls=[],prefs={serviceToken:'local-only'},confirms=[];
  const Zotero={Prefs:{get:key=>prefs[key.split('.').pop()],set:(key,val)=>prefs[key.split('.').pop()]=val}};
  vm.runInNewContext(code,{Zotero,Headers,crypto:webcrypto,runtimeManager:{},
    fetch:async(url,init)=>{calls.push({url,body:JSON.parse(init.body),headers:init.headers});if(fail)throw Error('lost response');return Response.json({});}});
  const window={prompt:()=>answers.shift(),confirm:text=>{confirms.push(text);return confirmed;}};
  return{run:()=>Zotero.LocalBilingual.workbench.extendBudget(window,job),calls,confirms,prefs};
}
test('cancel before consent sends no API request',async()=>{
  for(const answers of [[null],['120000',null]]) {const h=harness(answers);await assert.rejects(h.run(),/未确认/);assert.equal(h.calls.length,0);}
  const h=harness(['120000','200'],false);await assert.rejects(h.run(),/未确认/);assert.equal(h.calls.length,0);
});
test('confirmed extension shows spend and sends bounded lifetime limits',async()=>{
  const h=harness(['120000','200']);await h.run();
  assert.match(h.confirms[0],/47306/);assert.match(h.confirms[0],/继续扣费/);
  assert.equal(h.calls[0].body.confirmed,true);assert.equal(h.calls[0].body.max_total_tokens,120000);
  assert.equal(h.calls[0].body.expected_max_total_tokens,60000);
  assert.match(h.calls[0].url,/extend-budget$/);
  assert.equal(h.calls[0].headers.get('Content-Type'),'application/json');
});
test('lost approval response preserves idempotency key',async()=>{
  const h=harness(['120000','200','120000','200'],true,true);
  await assert.rejects(h.run());await assert.rejects(h.run());
  assert.equal(h.calls[0].body.client_request_id,h.calls[1].body.client_request_id);
});
test('invalid or unchanged limits do not send a request',async()=>{
  for(const answers of [['60000','100'],['2000001','200'],['120000','0']]) {
    const h=harness(answers);await assert.rejects(h.run(),/上限/);assert.equal(h.calls.length,0);
  }
});
