"""Optional, manual MFCase PDF combiner with verifiable original-file lineage.

Usage: python MFCase_Combine_And_Trace.py "<roll>/COMPLETED"
Outputs are <COMPLETED>/COMBINED/08-N.pdf. Source PDFs are never removed.
This tool does not run automatically or alter OCR/scan behavior.
"""
from __future__ import annotations
import argparse,json,os,re,tempfile
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path
import pymupdf

CASE_SUFFIX=re.compile(r'(?:^|_)(08-[0-9]{1,7})(?:\s+\([0-9]+\))?$',re.I)

def canonical_case(filename):
    match=CASE_SUFFIX.search(Path(filename).stem)
    if not match:return None
    case=match.group(1)
    return '08-'+(case[3:].lstrip('0') or '0')

def natural_key(path):
    return [int(v) if v.isdigit() else v.lower() for v in re.split(r'(\d+)',Path(path).name)]

def _append_line(paths,entry):
    data=json.dumps(entry,ensure_ascii=False,separators=(',',':'))+'\n'
    for dest in paths:
        dest.parent.mkdir(parents=True,exist_ok=True)
        with dest.open('a',encoding='utf8',newline='\n') as stream:
            stream.write(data)
            stream.flush()

def merge_completed(completed, replace=False):
    """Merge source files of each case and record provenance for every source."""
    completed=Path(completed).resolve()
    if not completed.is_dir():raise FileNotFoundError(completed)
    output=completed/'COMBINED';output.mkdir(exist_ok=True)
    local=Path(os.getenv('LOCALAPPDATA') or str(Path.home()))/'MFCase'/'logs'
    history_file=completed.parent/'.case_ocr_sent_history.json'
    try:history=json.loads(history_file.read_text('utf8')).get('sent',{})
    except (OSError,ValueError,TypeError):history={}
    reverse={entry.get('destination_name'):origin
             for origin,entry in history.items() if isinstance(entry,dict)}
    groups=defaultdict(list)
    for file in completed.iterdir():
        if not file.is_file() or file.suffix.lower()!='.pdf':continue
        case=canonical_case(file.name)
        if case:groups[case].append(file)
    results=[]
    for case in sorted(groups,key=natural_key):
        destination=output/(case+'.pdf')
        if destination.exists() and not replace:
            results.append((case,'skipped: output already exists'))
            continue
        sources=sorted(groups[case],key=natural_key)
        tmp=None
        try:
            merged=pymupdf.open()
            contributions=[]
            for source in sources:
                with pymupdf.open(source) as pdf:
                    count=len(pdf)
                    merged.insert_pdf(pdf)
                contributions.append({'original_name':reverse.get(source.name,source.name),
                                     'sent_name':source.name,'sent_path':str(source),
                                     'pages':count})
            if not len(merged):raise ValueError(f'No pages for {case}')
            fd,name=tempfile.mkstemp(prefix='.mfcase_merge_',suffix='.pdf',dir=output)
            os.close(fd);tmp=Path(name)
            merged.save(tmp,garbage=3,deflate=True)
            merged.close()
            with pymupdf.open(tmp) as verified:
                assert len(verified)==sum(e['pages'] for e in contributions)
            # Never overwrite an existing combined file unless explicitly requested.
            if destination.exists() and not replace:
                tmp.unlink(missing_ok=True)
                results.append((case,'skipped: output appeared during merge'))
                continue
            if replace and destination.exists():
                backup=destination.with_suffix('.previous.pdf')
                if backup.exists():raise FileExistsError(f'Backup exists: {backup}')
                os.replace(destination,backup)
            os.replace(tmp,destination)
            timestamp=datetime.now(timezone.utc).isoformat(timespec='milliseconds')
            entry={'at':timestamp,'action':'CASE_COMBINED','case':case,
                   'combined_name':destination.name,'combined_path':str(destination),
                   'input_pages':sum(e['pages'] for e in contributions),
                   'inputs':contributions}
            _append_line([local/'MFCase_Work_Log.txt',
                          local/'MFCase_File_Trace.jsonl',
                          output/'MFCase_Combination_Trace.jsonl'],entry)
            results.append((case,f'merged {len(sources)} input PDFs'))
        except Exception as exc:
            if tmp:tmp.unlink(missing_ok=True)
            results.append((case,f'ERROR: {exc}'))
    return results

if __name__=='__main__':
    parser=argparse.ArgumentParser(description='Combine completed case PDFs and retain exact source lineage.')
    parser.add_argument('completed',type=Path,help='Path to COMPLETED folder')
    parser.add_argument('--replace',action='store_true',help='Rebuild existing combined file; preserve a .previous.pdf backup')
    args=parser.parse_args()
    for case,result in merge_completed(args.completed,args.replace):
        print(case,result)
