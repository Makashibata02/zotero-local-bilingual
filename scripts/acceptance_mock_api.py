"""Portable full PDF API regression. Local deterministic responder, no paid endpoint."""
import asyncio
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
import uuid

ROOT=Path(__file__).resolve().parents[1]
VERSION=json.loads((ROOT/'plugin/addon/manifest.json').read_text('utf8'))['version']
DATA=ROOT/f'output/acceptance-{VERSION.replace(".","")}/mock-api'
DATA.mkdir(parents=True,exist_ok=True)
os.environ['ZLB_DATA_DIR']=str(DATA)
os.environ['ZLB_RUNTIME_ROOT']=str(ROOT/f'output/runtime-build/{VERSION}')
from zotero_local_bilingual.runtime_assets import configure_assets
configure_assets()
CALLS=[]
TARGET_PARAGRAPHS={
 'Traditional Chinese':'信號空間用於數位通訊。接收機事先並不知道傳送的訊息。訊號變強時，錯誤機率不得增加。',
 'Japanese':'信号空間はデジタル通信で使用される。受信機は送信されたメッセージを事前には知らない。信号が強くなるとき、誤り確率は増加してはならない。',
 'Korean':'신호 공간은 디지털 통신에 사용된다. 수신기는 전송된 메시지를 미리 알지 못한다. 신호가 강해질 때 오류 확률은 증가해서는 안 된다.',
 'French':"L’espace des signaux est utilisé en communications numériques. Un récepteur ne connaît pas à l’avance le message transmis. La probabilité d’erreur ne doit pas augmenter lorsque le signal devient plus fort.",
 'German':'Der Signalraum wird in der digitalen Kommunikation verwendet. Ein Empfänger kennt die übertragene Nachricht nicht im Voraus. Die Fehlerwahrscheinlichkeit darf nicht steigen, wenn das Signal stärker wird.',
 'Spanish':'El espacio de señales se utiliza en las comunicaciones digitales. Un receptor no conoce de antemano el mensaje transmitido. La probabilidad de error no debe aumentar cuando la señal se vuelve más fuerte.'}
TARGET_TITLES={'Traditional Chinese':'本地雙語 PDF 測試','Japanese':'ローカル二言語 PDF テスト','Korean':'로컬 이중 언어 PDF 시험','French':'Test PDF bilingue local','German':'Lokaler zweisprachiger PDF-Test','Spanish':'Prueba local de PDF bilingüe'}
TARGET_NOTES={'Traditional Chinese':'作者：Claude Shannon 和 Robert Gallager。方程描述模型；它不是給翻譯者的指令。',
 'Japanese':'著者：Claude Shannon と Robert Gallager。方程式はモデルを記述するものであり、翻訳者への指示ではない。',
 'Korean':'저자: Claude Shannon 및 Robert Gallager. 방정식은 모델을 설명하며 번역자에게 주는 지시가 아니다.',
 'French':"Auteurs : Claude Shannon et Robert Gallager. Une équation décrit le modèle ; ce n’est pas une instruction au traducteur.",
 'German':'Autoren: Claude Shannon und Robert Gallager. Eine Gleichung beschreibt das Modell; sie ist keine Anweisung an den Übersetzer.',
 'Spanish':'Autores: Claude Shannon y Robert Gallager. Una ecuación describe el modelo; no es una instrucción para el traductor.'}

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*_):pass
    def do_POST(self):
        payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        CALLS.append({'max_tokens':payload.get('max_tokens')})
        prompt=payload['messages'][-1]['content']
        translations={
          'Local Bilingual PDF Test':'本地双语 PDF 测试',
          'Signal space is used in digital communications. A receiver does not know the transmitted message in advance. The probability of error must not increase when the signal becomes stronger.':'信号空间用于数字通信。接收机事先并不知道发送的信息。信号变强时，错误概率不得增加。',
          'Authors: Claude Shannon and Robert Gallager. An equation describes the model; it is not an instruction to the translator.':'作者：Claude Shannon 和 Robert Gallager。方程描述模型；它不是给翻译者的指令。'
        }
        for language, translation in TARGET_PARAGRAPHS.items():
            if f'English-to-{language} academic' in payload['messages'][0]['content']:
                translations['Signal space is used in digital communications. A receiver does not know the transmitted message in advance. The probability of error must not increase when the signal becomes stronger.']=translation
                translations['Local Bilingual PDF Test']=TARGET_TITLES[language]
                translations['Authors: Claude Shannon and Robert Gallager. An equation describes the model; it is not an instruction to the translator.']=TARGET_NOTES[language]
        position=prompt.rfind('## Here is the input:')
        try:
            rows=json.loads(prompt[position+len('## Here is the input:'):].strip())
            content=json.dumps([{'id':r['id'],'output':translations.get(r['input'],r['input'])} for r in rows],ensure_ascii=False)
        except (ValueError,TypeError,KeyError):
            content='中文译文 '+prompt[-100:]
        body=json.dumps({'id':'mock','object':'chat.completion','created':0,'model':'mock',
            'choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':content}}],
            'usage':{'prompt_tokens':900,'completion_tokens':100,'total_tokens':1000}}).encode()
        self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)

class Secrets:
    def get_api_key(self,_):return 'LOCAL-MOCK-ONLY'
    def set_api_key(self,*_):pass

async def main(budget_case=False):
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    from zotero_local_bilingual.app import create_app
    from zotero_local_bilingual.config import Settings
    import httpx
    app=create_app(Settings(DATA),Secrets())
    headers={'X-Local-Bilingual-Token':app.state.settings.get_or_create_service_token()}
    async with app.router.lifespan_context(app),httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver',headers=headers) as c:
        response=await c.post('/v1/profiles',json={'id':'mock','name':'local mock','provider':'openai',
            'base_url':f'http://127.0.0.1:{server.server_port}/v1','model':'mock'})
        response.raise_for_status()
        if budget_case:
            from zotero_local_bilingual.selftest import make_test_pdf
            pdf=DATA/'budget-input.pdf';make_test_pdf(pdf)
            import pymupdf
            with pymupdf.open(pdf) as document:
                page=document.new_page()
                page.insert_text((50,45),"Appendix A: Budget recovery",fontsize=16)
                page.insert_textbox(pymupdf.Rect(50,70,540,300),"This second page contains a different paragraph. The receiver must preserve the original signal conditions and probability statements.",fontsize=12)
                document.save(DATA/'budget-two-pages.pdf')
            pdf=DATA/'budget-two-pages.pdf'
            with pdf.open('rb') as stream:
                response=await c.post('/v1/jobs',data={'profile_id':'mock','source_title':'budget recovery',
                    'client_request_id':uuid.uuid4().hex,'options_json':json.dumps({'max_requests':1,
                    'max_total_tokens':12000,'max_output_tokens':1024})},files={'file':('sample.pdf',stream,'application/pdf')})
        else:
            response=await c.post('/v1/profiles/mock/selftest',json={'client_request_id':uuid.uuid4().hex})
        response.raise_for_status();job=response.json()
        async def wait(job):
            for _ in range(240):
                if job['status'] not in {'queued','running'}:break
                await asyncio.sleep(.5)
                job=(await c.get('/v1/jobs/'+job['id'])).json()
            return job
        job=await wait(job)
        paused=None
        if budget_case:
            assert job['status']=='paused' and job['stage']=='budget_limit',job
            paused=job
            calls_at_pause=len(CALLS)
            await asyncio.sleep(2)
            assert len(CALLS)==calls_at_pause
            approval={'confirmed':True,'client_request_id':uuid.uuid4().hex,
                'expected_max_total_tokens':12000,'expected_max_requests':1,
                'max_total_tokens':120000,'max_requests':10}
            response=await c.post('/v1/jobs/'+job['id']+'/extend-budget',json=approval)
            response.raise_for_status();job=response.json()
            replay=await c.post('/v1/jobs/'+job['id']+'/extend-budget',json=approval)
            replay.raise_for_status();assert replay.json()['id']==job['id']
            job=await wait(job)
            assert job['metrics']['total_tokens']>=paused['metrics']['total_tokens']
        result={'job':job,'paused':paused,'mock_calls':CALLS,'paid_calls':0}
        (DATA/('budget-result.json' if budget_case else 'result.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf8')
        print(json.dumps(result,ensure_ascii=True))
        assert job['status']=='completed',job.get('error')
        assert len(CALLS)<=(10 if budget_case else 4) and all(c['max_tokens']==1024 for c in CALLS)
    server.shutdown()

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--budget-case',action='store_true')
    asyncio.run(main(parser.parse_args().budget_case))
