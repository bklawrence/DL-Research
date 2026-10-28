#!/usr/bin/env python3
"""Collect PUBLIC school-aggregate ACCESS data and link to a Midwest school inventory.

Python 3.10+, standard library only. No browser cookies, logins, or student-level data.
Produces candidate matches with ORIGINAL source field names; does not infer exit rates.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
import hashlib
import io
import json
import os
from pathlib import Path
import posixpath
import re
import shutil
import sys
import time
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
import xml.etree.ElementTree as ET
import zipfile

HOME = Path(__file__).resolve().parent
DEFAULT_SOURCES = {
    "IL": {
        "url": "https://www.isbe.net/_layouts/Download.aspx?SourceUrl=%2FDocuments%2F2025-Report-Card-Public-Data-Set.xlsx",
        "filename": "IL_2025_report_card_public.xlsx",
        "landing": "https://www.isbe.net/ilreportcarddata",
        "note": "Report card has several data subjects; only appropriately labeled ACCESS/EL fields are extracted.",
    },
    "MN": {
        "url": "https://education.mn.gov/mdeprod/groups/educ/documents/basic/cm9k/mdg3/~edisp/prod087014.xlsx",
        "filename": "MN_2025_ACCESS_public.xlsx",
        "landing": "https://public.education.mn.gov/MDEAnalytics/DataTopic.jsp?TOPICID=1",
        "note": "MDE's 2025 ACCESS Public summary assessment workbook; not individual-level scores.",
    },
    "WI": {
        "url": None,
        "filename": None,
        "landing": "https://dpi.wi.gov/wisedash/download-files",
        "note": "Use publicly downloadable certified ACCESS/WIDA file; exact downloadable resource not confirmed for unattended retrieval.",
    },
    "MI": {
        "url": None,
        "filename": None,
        "landing": "https://www.mischooldata.org/wida-proficiency/",
        "note": "Interactive report/export. Put public school-level export in incoming/MI or supply --source MI=FILE.",
    },
    "IN": {"url": None, "landing": "https://www.in.gov/doe/", "note": "Await verified public school-level ACCESS URL."},
    "MO": {"url": None, "landing": "https://dese.mo.gov/", "note": "Await verified public school-level ACCESS URL."},
    "SD": {"url": None, "landing": "https://doe.sd.gov/", "note": "Await verified public school-level ACCESS URL."},
    "ND": {"url": None, "landing": "https://www.nd.gov/dpi/", "note": "Await verified public school-level ACCESS URL."},
}

# Identifies headings that MIGHT represent ACCESS/EL metrics; always preserve exact source label.
ACADEMIC_HEADING = re.compile(r"\b(?:IAR|ELA|Math|Mathematics)\b.*(?:proficiency|proficient|tested|participation|count)", re.I)

def eligible_candidate(item):
    status = str(item.get('Screening_status', '')).upper()
    return not (status.startswith('EXCLUDE') or status.startswith('NEW/INCOMPLETE'))

METRIC_HEADING = re.compile(
    r"access|wida|english.?language.?proficien|english.?learner.*(?:progress|exit|proficien)|"
    r"\bel\b.*(?:progress|exit|proficien)|reclassif|english.?proficien.*(?:progress|exit)|"
    r"overall.?composite|proficiency.?level|\bexited\b", re.I,
)
# The MN ACCESS workbook may label every measure only by the domain, not by ACCESS.
ACCESS_FILE_METRIC = re.compile(
    r"composite|proficien|scaled?.?score|\breading\b|\bwriting\b|\blistening\b|"
    r"\bspeaking\b|\bprogress\b|\bexit\b|\bpercent\b|\brate\b|count|tested", re.I,
)
SENSITIVE_HEADER = re.compile(r"student.?id|birth.?date|date.?of.?birth|social.security|student.?first.name|student.?last.name", re.I)
SCHOOL_FIELD = re.compile(r"^(school|school name|schoolname|site name|site|building|building name|organization name|org name|school name and number)$", re.I)
DISTRICT_FIELD = re.compile(r"^(district|district name|districtname|district/charter|lea name|district/agency name)$", re.I)
GRADE_FIELD = re.compile(r"^(grade|grade level|gradelevel|tested grade|assessment grade)$", re.I)
GROUP_FIELD = re.compile(r"^(group|subgroup|student group|report description|report category|population|demographic group)$", re.I)
YEAR_FIELD = re.compile(r"^(year|school year|assessment year|fiscal year|testing year|test year)$", re.I)
TEST_FIELD = re.compile(r"^(test name|assessment name|test|assessment)$", re.I)
SUBJECT_FIELD = re.compile(r"^(subject|test subject|test domain|domain)$", re.I)
SCHOOL_ID_FIELD = re.compile(r"^(school number|school id|school code|school identifier|rcdts|rcdts code)$", re.I)
DISTRICT_ID_FIELD = re.compile(r"^(district number|district id|district code|district identifier|lea id)$", re.I)
INCLUDED_FIELD = re.compile(r"^(students included|included students|tested count type|number tested type)$", re.I)


def normalize(s: object) -> str:
    s = (str(s or "")).lower()
    s = s.replace("&", " and ")
    s = re.sub(r"[\W_]+", " ", s)
    s = re.sub(r"\b(the|school|elementary|middle|academy|community|public|magnet)\b", " ", s)
    return " ".join(s.split())


def strict_norm(s: object) -> str:
    s = str(s or "").lower().replace("&", " and ")
    return " ".join(re.sub(r"[\W_]+", " ", s).split())


def best_field(headers: list[str], pat: re.Pattern[str]) -> str | None:
    for h in headers:
        if pat.fullmatch(" ".join(h.lower().split())):
            return h
    return None


def load_inventory(path: Path) -> dict[str, list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        entries = list(csv.DictReader(f))
    if not entries or not all(k in entries[0] for k in ("State", "School")):
        raise ValueError("Inventory must contain State and School columns")
    by_state: dict[str, list[dict[str, str]]] = {}
    for item in entries:
        by_state.setdefault(item['State'].strip().upper(), []).append(item)
    return by_state


def incompatible_school_levels(source: str, target: str) -> bool:
    """Never match an elementary school to a separate middle/high school.

    Other name variation still goes through the existing conservative review rule.
    """
    def level(name: str) -> str | None:
        x = strict_norm(name)
        if re.search(r"\b(middle|junior high)\b", x):
            return "middle"
        if re.search(r"\b(elementary|elem|primary)\b", x):
            return "elementary"
        if re.search(r"\b(high school|senior high)\b", x):
            return "high"
        return None
    a, b = level(source), level(target)
    return bool(a and b and a != b)


def match_school(school: str, district: str, candidates: list[dict[str, str]]):
    exact = strict_norm(school)
    loose = normalize(school)
    scored = []
    for item in candidates:
        target = item['School']
        if not exact or not strict_norm(target) or incompatible_school_levels(school, target):
            continue
        if exact == strict_norm(target):
            score = 1.0
        else:
            score = SequenceMatcher(None, loose, normalize(target)).ratio()
        # District is a tie breaker, not a substitute for school-name agreement.
        dd = strict_norm(district)
        listed_dd = strict_norm(item.get('District'))
        if dd and listed_dd and (dd == listed_dd or normalize(district) == normalize(item.get('District'))):
            score = min(1.0, score + .035)
        scored.append((score, item))
    if not scored:
        return None, 0.0, "unmatched"
    scored.sort(key=lambda p: p[0], reverse=True)
    score, item = scored[0]
    runner = scored[1][0] if len(scored) > 1 else -1
    # A very high match is still review-only if a nearly equal candidate exists.
    if score >= .985 and score - runner >= .07:
        status = "high_confidence_name_match"
    elif score >= .80:
        status = "review_required"
    else:
        status = "unmatched"
    return item, round(score, 4), status


def fetch(url: str, dest: Path, max_mb: int = 400) -> dict[str, str]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 1024:
        return {"status": "cached", "path": str(dest), "bytes": str(dest.stat().st_size)}
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; public-education-data-research/1.0)", "Accept": "application/octet-stream,*/*"})
    temp = dest.with_name(dest.name + '.partial')
    try:
        with urlopen(request, timeout=70) as res, temp.open('wb') as out:
            ct = res.headers.get("Content-Type", "")
            if 'text/html' in ct.lower():
                return {"status": "error", "message": "Endpoint served HTML instead of a data file; browser/manual step may be needed", "url": url}
            size = 0
            while True:
                block = res.read(1024 * 512)
                if not block: break
                size += len(block)
                if size > max_mb * 1024 * 1024:
                    raise ValueError(f"Download exceeds {max_mb}MB safety limit")
                out.write(block)
        if temp.stat().st_size < 128:
            raise ValueError("Downloaded file unexpectedly small")
        # XLSX/ZIP are both ZIP containers; signatures guard against login/error pages.
        if dest.suffix.lower() in ('.xlsx', '.zip'):
            if not zipfile.is_zipfile(temp):
                raise ValueError("Response is not a valid Excel/ZIP archive")
        temp.replace(dest)
        return {"status": "downloaded", "path": str(dest), "bytes": str(size), "url": url}
    except (HTTPError, URLError, OSError, ValueError, TimeoutError) as e:
        return {"status": "error", "message": str(e), "url": url}
    finally:
        if temp.exists(): temp.unlink()


def col_number(address: str) -> int:
    letters = re.match(r"[A-Z]+", address.upper())
    if not letters: return 0
    n = 0
    for x in letters.group(0): n = n * 26 + ord(x) - 64
    return n - 1


NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
RID = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'
REL_NS = '{http://schemas.openxmlformats.org/package/2006/relationships}'


def xml_path(target: str) -> str:
    if target.startswith('/'):
        return target.lstrip('/')
    if target.startswith('xl/'):
        return target
    return posixpath.normpath(posixpath.join('xl', target))


def excel_shared_strings(z: zipfile.ZipFile) -> list[str]:
    if 'xl/sharedStrings.xml' not in z.namelist(): return []
    data = []
    with z.open('xl/sharedStrings.xml') as src:
        for event, element in ET.iterparse(src, events=('end',)):
            if element.tag == NS + 'si':
                data.append(''.join(t.text or '' for t in element.iter(NS+'t')))
                element.clear()
    return data


def xlsx_tables(path: Path):
    with zipfile.ZipFile(path) as z:
        if 'xl/workbook.xml' not in z.namelist():
            raise ValueError('Invalid XLSX: workbook.xml missing')
        shared = excel_shared_strings(z)
        book = ET.fromstring(z.read('xl/workbook.xml'))
        rels = ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
        targets = {r.get('Id'): xml_path(r.get('Target', '')) for r in rels if r.tag == REL_NS+'Relationship'}
        for ws in book.iter(NS+'sheet'):
            name = ws.get('name', 'unknown')
            target = targets.get(ws.get(RID))
            if not target or target not in z.namelist(): continue
            def sheet_rows():
                with z.open(target) as src:
                    for event, row in ET.iterparse(src, events=('end',)):
                        if row.tag != NS+'row': continue
                        values = {}
                        for c in row.findall(NS+'c'):
                            idx = col_number(c.get('r', 'A1'))
                            typ = c.get('t', '')
                            v = c.find(NS+'v')
                            inline = c.find(NS+'is')
                            val = v.text if v is not None and v.text is not None else ''
                            if typ == 's' and val:
                                try: val = shared[int(val)]
                                except (ValueError, IndexError): pass
                            elif typ == 'inlineStr' and inline is not None:
                                val = ''.join(t.text or '' for t in inline.iter(NS+'t'))
                            if len(str(val)) > 5000: val = str(val)[:5000]
                            values[idx] = str(val)
                        if values: yield values
                        row.clear()
            yield name, sheet_rows()


def csv_tables(path: Path):
    if path.suffix.lower() == '.zip':
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if re.search(r'\.csv$|\.tsv$|\.txt$', name, re.I) and not name.endswith('/'):
                    with z.open(name) as f:
                        raw = f.read(4 * 1024 * 1024)
                    try: sample = raw.decode('utf-8-sig', errors='replace')
                    except Exception: continue
                    delimiter = '\t' if name.lower().endswith('.tsv') else ';' if sample.count(';') > sample.count(',') else ','
                    def parse_zip_member(member=name, delim=delimiter):
                        with z.open(member) as sub:
                            io_text = io.TextIOWrapper(sub, encoding='utf-8-sig', errors='replace')
                            for fields in csv.reader(io_text, delimiter=delim):
                                yield {i: v for i, v in enumerate(fields)}
                    yield name, parse_zip_member()
        return
    with path.open(encoding='utf-8-sig', errors='replace', newline='') as fp:
        sample = fp.read(8192)
        fp.seek(0)
        delimiter = '\t' if path.suffix.lower() in ('.tsv', '.txt') else ';' if sample.count(';') > sample.count(',') else ','
        # The caller exhausts each iterator before continuing, so the file remains open.
        def rows():
            for cells in csv.reader(fp, delimiter=delimiter):
                yield {i: x for i, x in enumerate(cells)}
        yield path.name, rows()


def iter_tables(path: Path):
    if path.suffix.lower() == '.xlsx': yield from xlsx_tables(path)
    elif path.suffix.lower() in ('.csv', '.tsv', '.txt', '.zip'): yield from csv_tables(path)


def choose_header(first: list[dict[int, str]]):
    best = None
    for i, row in enumerate(first):
        cells = {idx: str(v).strip() for idx,v in row.items() if str(v).strip()}
        vals = list(cells.values())
        if any(SENSITIVE_HEADER.search(v) for v in vals):
            continue
        has_school = bool(best_field(vals, SCHOOL_FIELD))
        count_relevant = sum(bool(METRIC_HEADING.search(v)) for v in vals)
        score = 10*has_school + int(bool(best_field(vals,DISTRICT_FIELD))) + min(count_relevant, 7) + len(vals)/1000
        if not best or score > best[0]: best = (score, i, cells)
    if not best or best[0] < 10: return None
    return best[1], best[2]


def extract_file(path: Path, state: str, inventory: list[dict[str, str]], output: Path, source_uri: str = ""):
    manifest = {"state":state,"file":str(path),"sheets":[],"high_confidence":0,"review_rows":0,"access_fields":0,"warnings":[]}
    matches, fields, audit, headers_audit = [], [], [], []
    is_access_file = 'access' in path.name.lower() or 'wida' in path.name.lower()
    try:
        for sheet, rows in iter_tables(path):
            initial = []
            try:
                for _ in range(32):
                    initial.append(next(rows))
            except StopIteration: pass
            found = choose_header(initial)
            if not found:
                manifest['sheets'].append({"name":sheet,"status":"no recognizable school column"})
                continue
            header_index, heads = found
            ordered = [(idx, txt) for idx,txt in heads.items()]
            names = [name for _,name in ordered]
            # All public header text is useful for diagnosing state files with
            # coded/stacked headings (especially Illinois). No student rows.
            headers_audit.extend({"state":state, "file":path.name, "sheet":sheet,
                                  "column_index":idx + 1, "header_AS_PUBLISHED":name,
                                  "looks_ACCESS_related":bool(re.search(r"access|wida|\belp\b|english|learner|language.?proficien|reclass|exit",name,re.I))}
                                 for idx,name in ordered)
            if any(SENSITIVE_HEADER.search(h) for h in names):
                manifest['sheets'].append({"name":sheet,"status":"skipped: student-level identifiers appear present"})
                continue
            sfield = best_field(names, SCHOOL_FIELD)
            dfield = best_field(names, DISTRICT_FIELD)
            gfield = best_field(names, GRADE_FIELD)
            groupfield = best_field(names, GROUP_FIELD)
            yearfield = best_field(names, YEAR_FIELD)
            name_to_idx = {v:k for k,v in ordered}
            sidx = name_to_idx[sfield]
            didx = name_to_idx[dfield] if dfield else None
            gidx = name_to_idx[gfield] if gfield else None
            groupidx = name_to_idx[groupfield] if groupfield else None
            yearidx = name_to_idx[yearfield] if yearfield else None
            def optional_header_value(vals, pat):
                name = best_field(names, pat)
                return vals.get(name_to_idx[name], '') if name else ''
            def row_context(vals):
                return {"source_test":optional_header_value(vals,TEST_FIELD),
                        "source_subject_or_domain":optional_header_value(vals,SUBJECT_FIELD),
                        "source_school_id":optional_header_value(vals,SCHOOL_ID_FIELD),
                        "source_district_id":optional_header_value(vals,DISTRICT_ID_FIELD),
                        "source_students_included":optional_header_value(vals,INCLUDED_FIELD)}
            metric_cols = [(i,n) for i,n in ordered if METRIC_HEADING.search(n) or
                           (is_access_file and ACCESS_FILE_METRIC.search(n)) or
                           (state == 'IL' and ACADEMIC_HEADING.search(n))]
            for i,n in metric_cols:
                fields.append({"state":state,"file":path.name,"sheet":sheet,"source_field":n,"source_column_index":i+1,"classification":"candidate_NOT_validated", "metric_family":("academic" if ACADEMIC_HEADING.search(n) and not METRIC_HEADING.search(n) else "access")})
            field_count = len(metric_cols)
            processed, candidates_count = 0,0
            for vals in initial[header_index+1:]:
                # initial rows after header must not be lost
                processed += 1
                school = vals.get(sidx, '')
                if school:
                    item, score, status = match_school(school, vals.get(didx,'') if didx is not None else '', inventory)
                    if item and status != 'unmatched':
                        candidates_count += 1
                        audit.append({"state":state,"source_file":path.name,"sheet":sheet,"reported_school":school,"reported_district":vals.get(didx,'') if didx is not None else '',"inventory_id":item.get('Row_ID',''),"inventory_school":item['School'],"model":item.get('Entry_Model_Revised','') or item.get('Model_2024_25',''),"match_score":score,"match_status":status,"eligibility_screen":("candidate" if eligible_candidate(item) else "excluded_from_primary")})
                        if status == 'high_confidence_name_match': manifest['high_confidence'] += 1
                        else: manifest['review_rows'] += 1
                        for idx, metric in metric_cols:
                            val = vals.get(idx,'')
                            if str(val).strip():
                                matches.append({"state":state,"school":item['School'],"inventory_id":item.get('Row_ID',''),"reported_school":school,"reported_district":vals.get(didx,'') if didx is not None else '',"grade":vals.get(gidx,'') if gidx is not None else '',"subgroup":vals.get(groupidx,'') if groupidx is not None else '',"reported_year":vals.get(yearidx,'') if yearidx is not None else '',"field_name_AS_PUBLISHED":metric,"value_AS_PUBLISHED":val,"source_sheet":sheet,"source_file":path.name,"source_url":source_uri,"match_status":status,"match_score":score,"metric_status":"unvalidated_candidate_not_standardized", "metric_family":("academic" if ACADEMIC_HEADING.search(metric) and not METRIC_HEADING.search(metric) else "access"), "eligibility_screen":("candidate" if eligible_candidate(item) else "excluded_from_primary"), **row_context(vals)})
            for vals in rows:
                processed += 1
                school = vals.get(sidx, '')
                if not school: continue
                item, score, status = match_school(school, vals.get(didx,'') if didx is not None else '', inventory)
                if not item or status == 'unmatched': continue
                candidates_count += 1
                audit.append({"state":state,"source_file":path.name,"sheet":sheet,"reported_school":school,"reported_district":vals.get(didx,'') if didx is not None else '',"inventory_id":item.get('Row_ID',''),"inventory_school":item['School'],"model":item.get('Entry_Model_Revised','') or item.get('Model_2024_25',''),"match_score":score,"match_status":status,"eligibility_screen":("candidate" if eligible_candidate(item) else "excluded_from_primary")})
                if status == 'high_confidence_name_match': manifest['high_confidence'] += 1
                else: manifest['review_rows'] += 1
                for idx, metric in metric_cols:
                    val = vals.get(idx,'')
                    if not str(val).strip(): continue
                    matches.append({"state":state,"school":item['School'],"inventory_id":item.get('Row_ID',''),"reported_school":school,"reported_district":vals.get(didx,'') if didx is not None else '',"grade":vals.get(gidx,'') if gidx is not None else '',"subgroup":vals.get(groupidx,'') if groupidx is not None else '',"reported_year":vals.get(yearidx,'') if yearidx is not None else '',"field_name_AS_PUBLISHED":metric,"value_AS_PUBLISHED":val,"source_sheet":sheet,"source_file":path.name,"source_url":source_uri,"match_status":status,"match_score":score,"metric_status":"unvalidated_candidate_not_standardized", "metric_family":("academic" if ACADEMIC_HEADING.search(metric) and not METRIC_HEADING.search(metric) else "access"), "eligibility_screen":("candidate" if eligible_candidate(item) else "excluded_from_primary"), **row_context(vals)})
            manifest['access_fields'] += field_count
            manifest['sheets'].append({"name":sheet,"status":"processed","school_rows_checked":processed,"potential_school_rows_matched":candidates_count,"candidate_metric_fields":field_count})
    except (ValueError, OSError, ET.ParseError, zipfile.BadZipFile, EOFError, KeyError) as e:
        manifest['warnings'].append(f"Processing error: {e}")
    return matches, fields, audit, headers_audit, manifest


def write_csv(path: Path, rows: list[dict], cols: list[str]):
    with path.open('w', newline='', encoding='utf-8-sig') as f:
        wr=csv.DictWriter(f, fieldnames=cols, extrasaction='ignore')
        wr.writeheader()
        wr.writerows(rows)


def main():
    ap = argparse.ArgumentParser(description="Public, school-aggregate ACCESS results collector; no third-party packages")
    ap.add_argument('--inventory', type=Path, default=HOME/'midwest_school_inventory.csv')
    ap.add_argument('--output', type=Path, default=HOME/'output')
    ap.add_argument('--no-download', action='store_true', help='Process previously downloaded files only')
    ap.add_argument('--source', action='append', default=[], metavar='STATE=URL_OR_FILE', help='Override/add public state file or HTTPS URL')
    ap.add_argument('--states', default='IL,MN,WI,MI,IN,MO,SD,ND', help='Comma-separated states to process')
    args=ap.parse_args()
    states=[s.strip().upper() for s in args.states.split(',') if s.strip()]
    by_state=load_inventory(args.inventory)
    cfg=json.loads(json.dumps(DEFAULT_SOURCES))
    local_override={}
    for spec in args.source:
        if '=' not in spec: ap.error('--source must look like MN=https://.../file.xlsx or MN=/path/file.xlsx')
        state, loc=spec.split('=',1)
        state=state.strip().upper()
        if loc.startswith('https://'):
            cfg.setdefault(state,{})['url']=loc
            cfg[state]['filename']=state+'_override'+(Path(loc.split('?')[0]).suffix or '.xlsx')
        elif loc.startswith('http://'):
            ap.error('Only HTTPS downloads supported. Download the HTTP file yourself into incoming/STATE.')
        else:
            local_override.setdefault(state,[]).append(Path(loc).expanduser().resolve())
        if state not in states: states.append(state)
    out=args.output.resolve()
    raw=out/'raw'
    incoming=HOME/'incoming'
    out.mkdir(parents=True, exist_ok=True)
    events=[]
    paths={s:[] for s in states}
    for s in states:
        source=cfg.get(s, {})
        url=source.get('url')
        if not args.no_download and url:
            filename=source.get('filename') or (s+'_data'+Path(url.split('?')[0]).suffix)
            dest=raw/s/filename
            result=fetch(url, dest)
            events.append({'state':s,'source':url, **result})
            if result['status'] in ('cached','downloaded'): paths[s].append(dest)
        elif not url:
            events.append({'state':s,'source':source.get('landing',''),'status':'no_verified_direct_download','message':source.get('note','')})
        for p in local_override.get(s,[]):
            if p.is_file(): paths[s].append(p)
            else: events.append({'state':s,'source':str(p),'status':'missing_local_file'})
        folder=incoming/s
        if folder.exists():
            paths[s] += [p for p in sorted(folder.iterdir()) if p.suffix.lower() in ('.csv','.tsv','.txt','.xlsx','.zip')]
        if args.no_download and not paths[s]:
            p=raw/s
            if p.exists(): paths[s] += [f for f in p.iterdir() if f.suffix.lower() in ('.csv','.tsv','.txt','.xlsx','.zip')]
        paths[s] = list(dict.fromkeys(paths[s]))
    all_matches, all_fields, all_audit, all_headers, runs=[],[],[],[],[]
    for state in states:
        for f in paths.get(state,[]):
            print(f'Processing {state}: {f.name}', flush=True)
            direct = cfg.get(state, {}).get('url') or ''
            auto_name = cfg.get(state, {}).get('filename') or ''
            provenance = direct if f.parent.resolve() == (raw/state).resolve() and f.name == auto_name else ''
            ms, fs, au, ha, run=extract_file(f, state, by_state.get(state,[]), out, provenance)
            all_matches+=ms; all_fields+=fs; all_audit+=au; all_headers+=ha; runs.append(run)
    write_csv(out/'access_metric_candidates.csv', [r for r in all_matches if r['metric_family']=='access' and r['eligibility_screen']=='candidate'], [
        'state','school','inventory_id','reported_school','reported_district','grade','subgroup','reported_year','field_name_AS_PUBLISHED',
        'value_AS_PUBLISHED','source_sheet','source_file','source_url','match_status','match_score','metric_status',
        'source_test','source_subject_or_domain','source_school_id','source_district_id','source_students_included', 'metric_family','eligibility_screen'])
    write_csv(out/'academic_metric_candidates.csv', [r for r in all_matches if r['metric_family']=='academic' and r['eligibility_screen']=='candidate'], [
        'state','school','inventory_id','reported_school','reported_district','grade','subgroup','reported_year','field_name_AS_PUBLISHED',
        'value_AS_PUBLISHED','source_sheet','source_file','source_url','match_status','match_score','metric_status',
        'source_test','source_subject_or_domain','source_school_id','source_district_id','source_students_included','metric_family','eligibility_screen'])
    write_csv(out/'match_audit.csv', all_audit, [
        'state','source_file','sheet','reported_school','reported_district','inventory_id','inventory_school','model','match_score','match_status','eligibility_screen'])
    write_csv(out/'field_inventory.csv', all_fields, [
        'state','file','sheet','source_field','source_column_index','classification','metric_family'])
    write_csv(out/'source_column_inventory.csv', all_headers, [
        'state','file','sheet','column_index','header_AS_PUBLISHED','looks_ACCESS_related'])
    school_count = {}
    access_count = {}
    academic_count = {}
    for r in all_matches:
        key = (r['state'], r['inventory_id'], r['school'])
        school_count[key] = school_count.get(key, 0) + 1
        counter = access_count if r['metric_family']=='access' else academic_count
        counter[key] = counter.get(key, 0) + 1
    school_coverage = []
    for state in states:
        for item in by_state.get(state, []):
            row = dict(item)
            row['collection_eligibility_screen'] = 'candidate' if eligible_candidate(item) else 'excluded_from_primary'
            row['academic_raw_metric_cells_found'] = academic_count.get((state, item.get('Row_ID',''), item['School']), 0)
            row['ACCESS_raw_metric_cells_found'] = access_count.get((state, item.get('Row_ID',''), item['School']), 0)
            row['all_raw_metric_cells_found'] = school_count.get((state, item.get('Row_ID',''), item['School']), 0)
            row['ACCESS_extraction_status'] = ('raw_candidates_available_require_review' if row['ACCESS_raw_metric_cells_found'] else 'not_retrieved_or_not_matched')
            school_coverage.append(row)
    if school_coverage:
        names = list(dict.fromkeys(x for row in school_coverage for x in row.keys()))
        write_csv(out/'school_inventory_with_collection_status.csv', school_coverage, names)
    summary={'created_utc': datetime.now(timezone.utc).isoformat(),
             'purpose':'school-level aggregate ACCESS retrieval; no inferred comparability or exit rates',
             'downloads':events,'parsed_files':runs,
             'total_inventory_schools':sum(len(v) for v in by_state.values()),
             'matched_candidate_records':len(all_audit),
             'distinct_inventory_entries_with_matches':len(set((a['state'],a['inventory_id']) for a in all_audit)),
             'distinct_inventory_entries_with_metric_values':len(set((a['state'],a['inventory_id']) for a in all_matches)),
             'candidate_metric_values':len(all_matches),
             'note':'All extracted values are candidate raw fields; verify metric/denominator/year/grade before adding to analysis.'}
    (out/'collection_report.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding='utf-8')
    print(f'Done. Saved outputs to {out}; candidate metric cells: {len(all_matches)}.')
    if any(x.get('status')=='error' for x in events):
        print('One or more downloads failed. Use incoming/STATE or --source STATE=/path/file.xlsx for manual fallback.')
    return 0

if __name__ == '__main__':
    sys.exit(main())