"""Public short sentence + real PDF tests; no user Key and no paid requests."""
import json
import os
from pathlib import Path
import subprocess
import time
import argparse
parser=argparse.ArgumentParser();parser.add_argument('--providers',nargs='+',default=['bing','tencent']);parser.add_argument('--languages',nargs='+');args=parser.parse_args()
ROOT=Path(__file__).resolve().parents[1]
VERSION=json.loads((ROOT/'plugin/addon/manifest.json').read_text('utf8'))['version']
RUNTIME=ROOT/f'output/runtime-build/{VERSION}'
DATA=ROOT/f'output/acceptance-{VERSION.replace(".","")}'/('languages-punctuation' if args.languages else 'languages-final');DATA.mkdir(parents=True,exist_ok=True)
os.environ['ZLB_RUNTIME_ROOT']=str(RUNTIME);os.environ['ZLB_DATA_DIR']=str(DATA)
from zotero_local_bilingual.runtime_assets import configure_assets
configure_assets()
from zotero_local_bilingual.languages import LANGUAGES
from zotero_local_bilingual.free_translate import WebProvider
from zotero_local_bilingual.translation_policy import TranslationOptions
from zotero_local_bilingual.selftest import make_test_pdf
import pymupdf
results={};evidence=json.loads((ROOT/'service/src/zotero_local_bilingual/language_validation.json').read_text('utf8')) if args.languages else {provider:{language:{'supported':False,'status':'新版保护规则待验收'} for language in LANGUAGES} for provider in ['bing','tencent']};source=DATA/'input.pdf';make_test_pdf(source)
for provider in args.providers:
    for language in (args.languages or LANGUAGES):
        label=provider+'-'+language;folder=DATA/label;folder.mkdir(exist_ok=True)
        result={'provider':provider,'target_language':language,'paid_api_calls':0};started=time.monotonic()
        try:
            preview=WebProvider(provider,target_language=language).translate('A receiver does not know the message in advance.')
            if preview.strip()=='A receiver does not know the message in advance.':raise ValueError('service returned original English')
            result['preview']=preview
            spec={**TranslationOptions(target_language=language,free_max_requests=20,max_characters=10000).model_dump(),
                'provider':provider,'source_path':str(source),'engine_output_dir':str(folder),'engine_working_dir':str(folder/'work'), 'pages':'1','ignore_cache':True}
            (folder/'work').mkdir(exist_ok=True);spec_path=folder/'spec.json';spec_path.write_text(json.dumps(spec),'utf8')
            env=os.environ.copy();env.pop('ZLB_API_KEY',None)
            process=subprocess.run([str(RUNTIME/'python/python.exe'),'-I','-B','-m','zotero_local_bilingual.engine_worker',str(spec_path)],
                env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=subprocess.CREATE_NO_WINDOW,timeout=180)
            (folder/'stderr.log').write_bytes(process.stderr);(folder/'events.jsonl').write_bytes(process.stdout)
            events=[json.loads(line) for line in process.stdout.splitlines() if line.startswith(b'{')]
            final=next((e for e in events if e['type']=='result'),None)
            if process.returncode or not final:raise RuntimeError(next((e.get('error') for e in events if e['type']=='fatal'),'worker failed'))
            with pymupdf.open(final['mono_pdf_path']) as doc:
                text='\n'.join(p.get_text() for p in doc)
                if '\ufffd' in text or not text.strip():raise ValueError('unreadable PDF text')
                if not all(''.join(name.split()) in ''.join(text.split()) for name in ['Claude Shannon','Robert Gallager']):raise ValueError('author names were changed')
                if 'E=mc^2' not in ''.join(text.split()):raise ValueError('formula text changed')
                (folder/'extracted.txt').write_text(text,'utf8')
            result.update(ok=True,usage=final['usage'],quality=final['quality'],pdf=final['mono_pdf_path'])
            evidence[provider][language]={'supported':True,'status':'本机直连短句＋一页 PDF 验收通过'}
        except Exception as exc:
            result.update(ok=False,error=str(exc)[:1000]);evidence[provider][language]={'supported':False,'status':'未支持／未验收：'+str(exc)[:100]}
        result['seconds']=round(time.monotonic()-started,3);results[label]=result
        (DATA/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),'utf8')
        (ROOT/'service/src/zotero_local_bilingual/language_validation.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),'utf8')
        print(label,result['ok'],result['seconds'],flush=True)
print('Language acceptance complete; 0 paid API calls',flush=True)
