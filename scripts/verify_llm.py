"""Explicit live eval against the configured model. No emails or downloads."""
from datetime import datetime,timezone
import json
from pathlib import Path
from dotenv import load_dotenv
from regulatory_agent.requests import parse_request,ClarificationNeeded


def main():
    load_dotenv(Path.cwd()/'.env')
    cases=[
        ('Hi Agent, Other Documents files from M12205 please','Other Documents'),
        ('Please retrieve the miscellaneous filings for M12205','Other Documents'),
        ('M12205: could you collect the main filings?','Key Documents'),
        ('Please send the written hearing records for M12205','Transcripts'),
        ('M12205 hearing audio please','Recordings'),
        ('Get the exhibits for M12205','Exhibits'),
        ('M12205: get all documents',None),
        ('M12205: can you help me?',None),
        ('M12205 and M12383 Exhibits',None),
    ]
    results=[]
    for text,expected in cases:
        try:
            parsed=parse_request(text,allow_fallback=False,log=lambda _:None)
            ok=expected is not None and parsed.category==expected and parsed.parser=='openai-compatible'
            actual={'matter':parsed.matter,'category':parsed.category,'parser':parsed.parser,'model':parsed.model}
        except ClarificationNeeded:
            ok=expected is None
            actual={'clarification':True}
        results.append({'request':text,'expected':expected,'actual':actual,'passed':ok})
        print(('PASS' if ok else 'FAIL')+': '+text,flush=True)
    Path('output').mkdir(exist_ok=True)
    Path('output/llm-check.json').write_text(json.dumps({'checked_at':datetime.now(timezone.utc).isoformat(),
        'passed':sum(item['passed'] for item in results),'total':len(results),'cases':results},indent=2),encoding='utf8')
    if not all(item['passed'] for item in results):
        raise SystemExit(1)
    print(f'Live model evaluation passed: {len(results)}/{len(results)} cases',flush=True)


if __name__=='__main__': main()
