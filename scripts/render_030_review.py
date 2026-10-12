"""Render explicitly supplied acceptance outputs; no private workspace paths."""
from pathlib import Path
import argparse
import json
import pymupdf
from PIL import Image, ImageDraw


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--results',type=Path,required=True,help='Language acceptance JSON')
    parser.add_argument('--pdf',type=Path,action='append',default=[],help='Additional output PDF to render')
    parser.add_argument('--out',type=Path,default=Path('output/review'))
    args=parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=True)
    results=json.loads(args.results.read_text('utf8'))
    for provider in ('bing','tencent'):
        for group,languages in (('cjk',['zh-CN','zh-TW','ja','ko']),('latin',['fr','de','es'])):
            cols=2 if group=='cjk' else 3
            sheet=Image.new('RGB',(cols*620,((len(languages)+cols-1)//cols)*590),'#eef2f7')
            draw=ImageDraw.Draw(sheet)
            for i,language in enumerate(languages):
                result=results[provider+'-'+language]
                if not result['ok']:
                    raise ValueError('Failed acceptance: '+provider+' '+language)
                pdf=Path(result['pdf'])
                if not pdf.is_absolute(): pdf=args.results.parent/pdf
                with pymupdf.open(pdf) as document:
                    pix=document[0].get_pixmap(matrix=pymupdf.Matrix(1,1),clip=pymupdf.Rect(0,0,595,540))
                    rendered=Image.frombytes('RGB',(pix.width,pix.height),pix.samples)
                x=(i%cols)*620; y=(i//cols)*590
                draw.text((x+10,y+10),provider+' '+language,fill='black')
                sheet.paste(rendered,(x+10,y+35))
            sheet.save(args.out/f'{provider}-{group}.png')
    for index,pdf in enumerate(args.pdf,1):
        with pymupdf.open(pdf) as document:
            document[0].get_pixmap(matrix=pymupdf.Matrix(1.2,1.2)).save(args.out/f'sample-{index}.png')
    print(args.out)


if __name__=='__main__':
    main()
