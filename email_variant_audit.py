"""Render genEmail's actual branch selection under Node and score fabricated variants. No sends."""
import json
from pathlib import Path
import subprocess
import disclaimer
import foreclosure_leads as F
from deliverability import score_content

ROOT = Path(__file__).resolve().parent


def variants(template=None):
    source = (Path(template) if template else ROOT / 'tracker_template.html').read_text()
    for lang in ('en', 'es'):
        source = source.replace('__IDENT_' + lang.upper() + '__', disclaimer.identity(lang, as_html=False))
    source = F._bake_alex_email(source, 'synthetic email audit')
    js = r'''
const input=JSON.parse(require('fs').readFileSync(0,'utf8')); const src=input.source;
function extract(name){
 const start=src.indexOf('function '+name+'(');
 if(start<0)throw Error('missing '+name);
 for(let j=src.indexOf('}',start);j>=0;j=src.indexOf('}',j+1)){
  const part=src.slice(start,j+1);try{new Function(part);return part;}catch(e){}
 }
 throw Error('unbalanced '+name);
}
let sender={name:'Synthetic Advisor',llc:'Example Company',phone:'305-555-0100',addr:'1 Example Street',email:'',web:''};
const window={}; const SUBJECT_STYLE='plain';
function _textContactBlocked(){return '';}
function _mailableList(){return [];}
let selectedStage='cold';function _mailStage(){return selectedStage;}
// These name formatters do not affect the copy/routing being measured.
function _ownerName(){return 'Synthetic';} function _firstName(){return 'Synthetic';}
eval(extract('_safeAddr'));eval(extract('_safeStreet'));eval(extract('_subjLine'));
// Read the actual baked opt-out globals and body override instead of substituting test copies.
eval(src.slice(src.indexOf('var _ALEXMAIL_EN'),src.indexOf('function genEmail(')));
const start=src.indexOf('function genEmail('),end=src.indexOf('  const Djson',start);
eval(src.slice(start,end)+'return window.__lastEmail; }');
const out=[];
for(const kind of ['early','tax','dated'])for(const stage of ['cold','follow','final']){
 selectedStage=stage;
 const r={case:'SYNTHETIC',owners:'SYNTHETIC PERSON',addr:'1 Example Street, Example City, FL',
 st:kind==='tax'?'TD':'FC',auction:kind==='early'?'':'12/31/2099',emails:[]};
 const m=genEmail(r,true);
 for(const lang of ['en','es'])out.push({kind,stage,lang,subject:lang==='en'?m.subj:m.subjES,body:lang==='en'?m.body:m.bodyES});
}
console.log(JSON.stringify(out));
'''
    proc = subprocess.run(['node', '-e', js], input=json.dumps({'source':source}), check=True, capture_output=True, text=True)
    out = json.loads(proc.stdout)
    for row in out:
        row['content_score'] = score_content(row['subject'], row['body'])
    return out


if __name__ == '__main__':
    print(json.dumps(variants(), ensure_ascii=False, indent=2))
