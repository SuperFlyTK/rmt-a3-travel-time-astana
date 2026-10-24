"""Verify report inputs and pre-registration order without reading credentials."""
import csv
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
def main():
    git=sys.argv[1] if len(sys.argv)>1 else 'git'
    initial=subprocess.check_output([git,'rev-list','--max-parents=0','HEAD'],cwd=ROOT,text=True).strip()
    committed=subprocess.check_output([git,'show','-s','--format=%cI',initial],cwd=ROOT,text=True).strip()
    for filename in ['docs/expected_results.md','docs/expected_results.json','config/study.json','data/routes.csv']:
        original=subprocess.check_output([git,'show',f'{initial}:{filename}'],cwd=ROOT)
        current=(ROOT/filename).read_bytes().replace(b'\r\n',b'\n')
        assert original.replace(b'\r\n',b'\n')==current, f'Preregistered input changed: {filename}'
    rows=[]
    for path in sorted((ROOT/'data/raw').glob('*.csv')):
        with path.open(encoding='utf-8-sig',newline='') as f:rows.extend(csv.DictReader(f))
    assert rows, 'No observations'
    first=min(datetime.fromisoformat(row['timestamp_utc']) for row in rows)
    assert datetime.fromisoformat(committed)<first,'Collection predates registration'
    config=json.loads((ROOT/'config/study.json').read_text(encoding='utf-8'))
    assert len(config['rq'].split())<=25
    assert {row['provider'] for row in rows}=={'ors'},'Unapproved primary provider was collected'
    successful=[row for row in rows if row['status']=='ok']
    for row in successful:
        for column in ['raw_file','geometry_file']:
            assert (ROOT/row[column]).is_file(), f'Missing evidence {column}'
    report=ROOT/'RMT_A3_Team_AkhanovYerdaulet_ShadiyarbekSyrym_SabyrzhanAkylbek.docx'
    if report.exists():
        with zipfile.ZipFile(report) as z:
            root=ET.fromstring(z.read('word/document.xml'))
        ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        first_p=root.find('.//w:p',ns)
        line=''.join(t.text or '' for t in first_p.findall('.//w:t',ns))
        assert line=='Group SE-2424 | Assignment 3 | Akhanov Yerdaulet, Shadiyarbek Syrym, Sabyrzhan Akylbek'
    result={'pre_registration_commit':initial,'commit_time':committed,'first_raw_request':first.isoformat(),
            'raw_requests':len(rows),'successful_requests':len(successful),'rq_words':len(config['rq'].split()),
            'preregistered_files_unchanged':True,'observed_providers':['ors'],'primary_comparison_performed':False}
    (ROOT/'docs/evidence_verification.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result))
if __name__=='__main__':main()
