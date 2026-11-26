"""
Case Number OCR & PDF Renamer - local Windows app
Case format: two-digit year + hyphen + 1-7 digits.
The program NEVER silently renames an uncertain case number.
"""
from __future__ import annotations
import os, re, shutil, threading, queue, json, logging, time, math, hashlib
import sys, subprocess, tempfile, urllib.request, urllib.error, zipfile
from contextlib import contextmanager, nullcontext
from bisect import bisect_left, bisect_right
from time import perf_counter
from pathlib import Path
from datetime import datetime
import tkinter as tk
"""MFCase handwriting safety, v5.6.18.

This file deliberately has NO network access and never changes learned field
coordinates. Case data and quarantine reports remain in the learning directory.
"""
import hashlib
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

CASE = re.compile(r"08-[0-9]{1,7}\Z")


def identity(value):
    value = str(value or "").strip()
    if not CASE.fullmatch(value):
        return None
    return '08-' + (value[3:].lstrip('0') or '0')


def number_only(image, label_rects=(), require_separation=False):
    """Return an ink-localized number image, a relative bbox, and confidence of isolation.

    Printed labels are excluded only when their actual box is known. Without a
    label box, significant whitespace between right-hand and left-hand groups
    permits a conservative split. Uncertain full fields are NOT fed to TrOCR.
    """
    rgb = np.asarray(image.convert('RGB') if isinstance(image, Image.Image) else image)
    if rgb.ndim != 3 or rgb.shape[2] < 3 or rgb.shape[0] < 11 or rgb.shape[1] < 35:
        return None, None, 'unusable_crop'
    h, w = rgb.shape[:2]
    gray = np.array(Image.fromarray(rgb[:, :, :3]).convert('L'))
    import cv2
    # Remove long flat form rules; their ink is not part of a digit.
    dark = (gray < min(215, int(np.percentile(gray, 80)) - 25)).astype(np.uint8)
    horizontals = cv2.morphologyEx(dark, cv2.MORPH_OPEN, np.ones((1, max(30, w//4)),np.uint8))
    ink = np.where(horizontals > 0, 0, dark)
    xx = np.flatnonzero(ink.sum(axis=0) >= 2)
    yy = np.flatnonzero(ink.sum(axis=1) >= 2)
    if len(xx) < 20 or len(yy) < 7:
        return None, None, 'no_number_strokes'
    cut = 0
    confirmed_split = False
    for r in label_rects:
        if len(r) == 4:
            end = int(r[2])
            if 3 < end < w - 28 and end > cut:
                cut = end
                confirmed_split = True
    if not confirmed_split:
        # A wide empty vertical gap is robust evidence of a separate printed
        # caption on the left. Never blindly crop off a fixed percentage.
        gaps=[]
        for a,b in zip(xx[:-1],xx[1:]):
            width = int(b-a-1)
            if width >= max(12,int(w*.055)) and 0.25*w<a<0.82*w:
                left=int((xx<=a).sum());right=int((xx>=b).sum())
                if left>=12 and right>=18:
                    gaps.append((width,a,b))
        if gaps:
            _,a,b=max(gaps)
            cut=int(b)
            confirmed_split=True
    if require_separation and not confirmed_split:
        return None, None, 'field_not_separable'
    # Restrict to the actual ink after the caption. Outer padding keeps thin
    # first and last strokes without the label; no resizing of the original ROI.
    segment=ink[:,cut:]
    col=np.flatnonzero(segment.sum(axis=0)>=2)
    row=np.flatnonzero(segment.sum(axis=1)>=2)
    if len(col)<18 or len(row)<7:
        return None,None,'number_not_visible'
    x0=max(cut,cut+int(col[0])-max(4,int(w*.015)))
    x1=min(w,cut+int(col[-1])+max(6,int(w*.025))+1)
    y0=max(0,int(row[0])-max(3,int(h*.045)))
    y1=min(h,int(row[-1])+max(4,int(h*.06))+1)
    if x1-x0<30 or y1-y0<10:
        return None,None,'too_small'
    out=rgb[y0:y1,x0:x1].copy()
    # The last stroke at the exact learned-field edge makes an incomplete read
    # plausible; the caller must keep any result in REVIEW.
    clipped=bool((ink[:,max(cut,w-3):].sum()>=3) and x1==w)
    return out,(x0,y0,x1,y1),('edge_clipped' if clipped else 'isolated' if confirmed_split else 'number_only')


def image_variants(image):
    src=Image.fromarray(np.asarray(image)).convert('RGB')
    w,h=src.size
    pad=max(6,int(h*.15))
    yield np.asarray(ImageOps.expand(src,border=pad,fill='white'))
    contrast=ImageOps.autocontrast(src.convert('L'),cutoff=1).convert('RGB')
    yield np.asarray(ImageOps.expand(contrast,border=pad,fill='white'))


def missing_end_conflict(readings):
    """True if two valid 08 readings differ by one or more *trailing* digits."""
    values=list({identity(v) for v in readings if identity(v)})
    return any(a!=b and (a.startswith(b) or b.startswith(a)) for a in values for b in values)


def suspect_training_label(example):
    """Conservative label audit; no OCR prediction is treated as ground truth."""
    truth=identity(example.get('confirmed_case_number'))
    if not truth:
        return 'invalid_08_label'
    if example.get('training_quarantined'):
        return 'previously_quarantined'
    candidate=identity(example.get('ocr_candidate'))
    score=float(example.get('ocr_score') or 0)
    # A human may assign a case number for filing that is not what the crop
    # visibly says. We cannot teach the neural model from such a conflict.
    if candidate and candidate!=truth and score>=75:
        return 'ocr_label_conflict_needs_visual_verification'
    if not candidate or score<85:
        return 'no_independent_high_confidence_label_evidence'
    if candidate!=truth:
        return 'label_not_corroborrated'
    if example.get('crop_origin')=='manual-field' and not example.get('number_only_verified'):
        return 'manual_field_may_contain_caption'
    return None


def audit_examples(examples, learning_dir):
    """Non-destructive audit; return safe examples and quarantine report."""
    safe=[]; quarantine=[]
    learning_dir=Path(learning_dir)
    for ex in examples:
        reason=suspect_training_label(ex)
        rel=str(ex.get('image') or '')
        path=(learning_dir/rel)
        if not rel or path.suffix.lower() not in ('.png','.jpg','.jpeg'):
            reason=reason or 'invalid_image_name'
        if not path.is_file():
            reason=reason or 'image_missing'
        elif not reason:
            try:
                with Image.open(path) as image:
                    im=np.asarray(image.convert('RGB'))
                    number,box,why=number_only(im,require_separation=False)
                    if number is None or why=='edge_clipped':
                        reason='crop_incomplete_or_unreadable'
                    elif not ex.get('number_only_verified'):
                        # Legacy crops may include the caption. In the absence
                        # of physical separation evidence, do not trust them.
                        _,_,strict_reason=number_only(im,require_separation=True)
                        if strict_reason=='field_not_separable' and image.width>image.height*6:
                            reason='wide_legacy_crop_requires_review'
            except (OSError,ValueError):
                reason='image_unreadable'
        if reason:
            quarantine.append({'example_id':ex.get('example_id'),'image':rel,'reason':reason})
        else:
            safe.append(ex)
    return safe,quarantine


def write_quarantine_report(folder, quarantine):
    folder=Path(folder)
    target=folder/'handwriting_quarantine_v5618.json'
    payload={'version':'5.6.18','generated_at':datetime.now().isoformat(timespec='seconds'),
             'excluded_from_training':quarantine,
             'note':'Only handwriting training is affected. No source images, labels, or location history were deleted.'}
    temp=target.with_name(target.name+'.tmp')
    temp.write_text(json.dumps(payload,indent=2),encoding='utf8')
    os.replace(temp,target)
    return target


def backup_learning_once(folder):
    """Create immutable-in-practice copies BEFORE model changes; refuse if failure."""
    folder=Path(folder)
    destination=folder/'v5618_safety_backup'
    marker=destination/'COMPLETE.json'
    if marker.is_file():
        return destination
    destination.mkdir(parents=True,exist_ok=True)
    # Never modify original learning images. Back up metadata and both model
    # directories. This can require several GB and intentionally aborts if the
    # volume cannot hold the safety copy.
    for name in ('learning_examples_v3.json','learning_examples_v3.last_good.json',
                 'learning_examples_v2.json','learning_examples.json'):
        src=folder/name
        if src.is_file():
            shutil.copy2(src,destination/name)
    for name in ('trocr_v2_finetuned','trocr_v2_previous'):
        src=folder/name
        if src.is_dir():
            shutil.copytree(src,destination/name,dirs_exist_ok=True)
    for name in ('examples_v3',):
        src=folder/name
        if src.is_dir():
            shutil.copytree(src,destination/name,dirs_exist_ok=True)
    tmp=marker.with_suffix('.tmp')
    tmp.write_text(json.dumps({'version':'5.6.18','created_at':datetime.now().isoformat(),
                               'note':'Preserved original examples, database, and available checkpoints.'}),encoding='utf8')
    os.replace(tmp,marker)
    return destination


def following_ink(region, rect):
    """Test whether the OCR bounding box omitted adjacent right-hand digit ink."""
    import cv2
    image=np.asarray(region)
    if image.ndim==3:
        image=np.asarray(Image.fromarray(image).convert('L'))
    x0,y0,x1,y1=map(int,rect)
    h,w=image.shape[:2]
    charh=y1-y0
    if charh<9 or x1>w-5:
        return False
    xend=min(w,x1+max(15,int(charh*1.5)))
    scan=image[max(0,y0-3):min(h,y1+3),x1+1:xend]
    if scan.shape[1]<5 or scan.shape[0]<7:
        return False
    threshold=min(205,int(np.percentile(scan,80))-20)
    mask=(scan<threshold).astype(np.uint8)
    cnt,labels,stats,cent=cv2.connectedComponentsWithStats(mask,8)
    for x,y,ww,hh,area in stats[1:]:
        # Minimum digit-like component; exclude table grid lines.
        if (ww>=2 and hh>=max(8,int(charh*.35)) and area>=max(9,int(charh*.45))
                and ww<max(8,int(charh*1.5)) and x<max(6,int(charh*.4))):
            return True
    return False

from types import SimpleNamespace as _MFCaseSafetyNamespace
hw5618=_MFCaseSafetyNamespace(identity=identity,number_only=number_only,image_variants=image_variants,missing_end_conflict=missing_end_conflict,suspect_training_label=suspect_training_label,audit_examples=audit_examples,write_quarantine_report=write_quarantine_report,backup_learning_once=backup_learning_once,following_ink=following_ink)

from tkinter import ttk, filedialog, messagebox

# Operational default: OFFLINE. No startup model discovery or HF telemetry.
# Only the separately clicked PREPARE OFFLINE MODELS action temporarily
# overrides HF offline flags for public model download; never for PDFs.
os.environ["HF_HUB_OFFLINE"]="1"
os.environ["TRANSFORMERS_OFFLINE"]="1"
os.environ["HF_HUB_DISABLE_TELEMETRY"]="1"
os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"]="1"
os.environ["DO_NOT_TRACK"]="1"
os.environ["WANDB_DISABLED"]="true"
os.environ["DISABLE_TELEMETRY"]="1"

try:
    import pymupdf
except ImportError:
    pymupdf = None
try:
    import easyocr
except ImportError:
    easyocr = None
try:
    import numpy as np
except ImportError:
    np = None

REQUIRED_CASE_YEAR = "08"
CASE_RE = re.compile(r"(?<![A-Za-z0-9|])(08-[0-9]{1,7})(?![A-Za-z0-9|]|\s+(?!08\s*-)[0-9]|\s+[Il|](?![A-Za-z]))")

def normalize_case_number(value):
    """Return the OCR reading without adding, padding, or inferring digits.

    Only whitespace around the hyphen and OCR dash glyphs are normalized;
    the digits and their lengths are preserved exactly as read.
    """
    text = str(value or "").strip().replace("—", "-").replace("–", "-")
    text = re.sub(r"\s* - \s*", "-", text)
    text = re.sub(r"\s*-\s*", "-", text)
    return text

def _case_identity(value):
    """Compare valid case readings without leading padding; retain raw labels."""
    case = normalize_case_number(value)
    if not CASE_RE.fullmatch(case):
        return None
    return "08-" + (case[3:].lstrip("0") or "0")


LABEL_RE = re.compile(r"(?:Case\s+Number|INCIDENT\s+N[O0]\s*\.?)", re.IGNORECASE)
INCIDENT_LABEL_RE = re.compile(r"\bINCIDENT\s*(?:N[O0]|NUM(?:BER)?|#)\s*[.:#]?", re.IGNORECASE)
CASE_FIELD_LABEL_RE = re.compile(
    r"(?:CASE\s*(?:NO\.?|NUM(?:BER)?|#)|INCIDENT\s*(?:N[O0]\s*\.?|NUM(?:BER)?|#))",
    re.IGNORECASE)
INCIDENT_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9|])(08\s*-\s*[0-9]{1,7})(?![A-Za-z0-9|]|\s+(?!08\s*-)[0-9]|\s+[Il|](?![A-Za-z]))")


def _same_physical_case_box(a,b):
    if not a or not b or len(a)!=4 or len(b)!=4:
        return False
    ax,ay,bx,by=map(float,a);cx,cy,dx,dy=map(float,b)
    aw,ah=bx-ax,by-ay;bw,bh=dx-cx,dy-cy
    if min(aw,ah,bw,bh)<=0:
        return False
    overlap=max(0,min(bx,dx)-max(ax,cx))*max(0,min(by,dy)-max(ay,cy))
    return (overlap/min(aw*ah,bw*bh)>=.65 and
            abs((ay+by-cy-dy)/2)<=.55*min(ah,bh) and
            max(aw,bw)/min(aw,bw)<=4 and max(ah,bh)/min(ah,bh)<=3)


def _select_primary_case_hit(hits, source, image_shape):
    """Identify the case field, then retain its strongest complete reading."""
    if not hits:
        raise ValueError("Cannot select from an empty candidate list")
    def ranking(hit):
        case=normalize_case_number(hit.get("number"))
        rect=hit.get("box_rect") or hit.get("rect")
        located=bool(hit.get("box_verified") and rect and len(rect)==4)
        try:score=float(hit.get("score") or 0)
        except (ValueError,TypeError):score=0.
        if not math.isfinite(score):score=0.
        # All readings of the SAME physical field share its label evidence.
        labeled=located and any(h.get("label_type") in ("incident","case")
            and h.get("box_verified") and _same_physical_case_box(rect,h.get("box_rect") or h.get("rect"))
            for h in hits)
        return (bool(located and CASE_RE.fullmatch(case)),bool(labeled),
                -float(rect[1]) if located else float('-inf'),score>=95.,score)
    field=max(hits,key=ranking)
    rect=field.get('box_rect') or field.get('rect')
    same=[h for h in hits if h.get('box_verified')
          and CASE_RE.fullmatch(normalize_case_number(h.get('number')))
          and _same_physical_case_box(rect,h.get('box_rect') or h.get('rect'))]
    return max(same,key=lambda h:ranking(h)[-1]) if same else field


def _original_pdf_sequence_key(path):
    """A sequential scan ID must be the FINAL numeric run in the original stem.

    Group only otherwise-identical filename templates; never infer across a
    missing filename, other roll, or different suffix/prefix.
    """
    name = Path(path).stem
    match = re.fullmatch(r"(.*?)(\d+)([^\d]*)", name)
    if not match:
        return None
    prefix, digits, suffix = match.groups()
    return (prefix.casefold(), suffix.casefold(), int(digits))


import hashlib



def _mfcase_header_field_candidates(hits,page_shape):
    """Candidate readings in the physical right-margin case-number location."""
    ph,pw=page_shape[:2]
    span=int(round(4*72*2.2))
    xmin=pw-min(span,pw)
    expected=re.compile(r"08-\d{1,7}\Z")
    candidates=[]
    for h in hits:
        rect=h.get("rect")
        value=str(h.get("number") or "")
        if not rect or not expected.fullmatch(value):
            continue
        try:
            x0,y0,x1,y1=map(float,rect)
        except (ValueError,TypeError):
            continue
        if (x0>=xmin-4 and x1<=pw+4 and y0>=0 and
                y1<=min(ph,span)+4 and y0<y1):
            candidates.append(h)
    if not candidates:
        return []
    margin=[h for h in candidates
            if (float(h["rect"][0])+float(h["rect"][2]))/2>=pw*.82
            and float(h["rect"][1])<=ph*.24]
    if not margin:
        return [] # Not a well-located header; do not authorize a fax page number.
    # Limit independent candidates to the physical right-margin word row.
    first=min(float(h["rect"][1]) for h in margin)
    return [h for h in margin if abs(float(h["rect"][1])-first)<=max(24.,span*.06)]


def _mfcase_export_case_name(value):
    """Format a filename only; never alter OCR, confirmed or training labels."""
    original=normalize_case_number(value)
    match=re.fullmatch(r"(08)-(\d{1,7})", original)
    if not match:
        raise ValueError(f"Invalid case number for export: {value!r}")
    year,case_digits=match.groups()
    case_digits = case_digits.lstrip("0") or "0"
    return f"{year}-{case_digits}"




def _learned_crop_clusters(learning_data):
    """Cluster ALL retained verified crop boxes in normalized full-page space.

    Historical field_regions used a physical upper-right 4-inch square rather
    than full-page coordinates.  New crops are full-page normalized.  Preserve
    both; never rewrite or discard legacy examples to build this index.
    """
    data=learning_data if isinstance(learning_data,dict) else {}
    full=data.get("field_regions_full_page",[]) or []
    old=data.get("field_regions",[]) or []
    source_has_full={str(x.get("source_pdf") or "") for x in full if isinstance(x,dict)
                     and x.get("source_pdf")}
    samples=[]
    seen_for_source=set()
    for collection,legacy in ((full,False),(old,True)):
        for item in collection:
            if not isinstance(item,dict):continue
            if legacy and item.get("source_pdf") in source_has_full:
                continue  # The same manual box is already stored precisely.
            coords=item.get("rect_norm")
            if not isinstance(coords,(list,tuple)) or len(coords)!=4:continue
            try:x0,y0,x1,y1=(float(v) for v in coords)
            except (TypeError,ValueError):continue
            if not all(math.isfinite(v) for v in (x0,y0,x1,y1)):continue
            if not (0<=x0<x1<=1 and 0<=y0<y1<=1):continue
            if legacy:
                # Old coordinates refer to a 4 x 4 inch window on the upper
                # right of a standard 8.5 x 11 inch scanned report.  This is
                # a best-effort migration; old records did not save page size.
                rw=min(1.,4./8.5);rh=min(1.,4./11.)
                x0,x1=1.-rw+rw*x0,1.-rw+rw*x1
                y0,y1=rh*y0,rh*y1
            cx,cy=(x0+x1)/2,(y0+y1)/2
            width,height=x1-x0,y1-y0
            if width<.007 or height<.004 or width>.9 or height>.55:continue
            # Saving a correction repeatedly for one PDF must not skew the
            # learned mean by duplicating the same selection.
            source=str(item.get("source_pdf") or "")
            if source:
                key=(source,round(cx,2),round(cy,2))
                if key in seen_for_source:continue
                seen_for_source.add(key)
            samples.append((cx,cy,width,height,source))
    # Deterministic nearest-center grouping: all examples contribute, not just
    # the most recent one.  Different form types produce different clusters.
    groups=[]
    for sample in samples:
        cx,cy,w,h,_=sample
        choices=[]
        for index,group in enumerate(groups):
            mx=sum(x[0] for x in group)/len(group)
            my=sum(x[1] for x in group)/len(group)
            mw=sorted(x[2] for x in group)[len(group)//2]
            mh=sorted(x[3] for x in group)[len(group)//2]
            delta=((cx-mx)**2+(cy-my)**2)**.5
            similar=(max(w,mw)/max(.001,min(w,mw)) <= 3.0 and
                     max(h,mh)/max(.001,min(h,mh)) <= 3.0)
            if delta<=.08 and similar:choices.append((delta,index))
        if choices:groups[min(choices)[1]].append(sample)
        else:groups.append([sample])
    output=[]
    for group in groups:
        # Trim extreme sizes inside a group, without throwing away positions
        # or counting only a sample subset.
        def stable_average(values):
            ordered=sorted(values)
            cut=int(len(ordered)*.10) if len(ordered)>=10 else 0
            trimmed=ordered[cut:len(ordered)-cut] if cut else ordered
            return sum(trimmed)/len(trimmed)
        x=stable_average([v[0] for v in group])
        y=stable_average([v[1] for v in group])
        w=stable_average([v[2] for v in group])
        h=stable_average([v[3] for v in group])
        rect=(max(0.,x-w/2),max(0.,y-h/2),min(1.,x+w/2),min(1.,y+h/2))
        compact=sum((v[0]-x)**2+(v[1]-y)**2 for v in group)/len(group)
        output.append(dict(rect_norm=rect,count=len(group),variance=compact))
    output.sort(key=lambda v:(-v['count'],v['variance'],v['rect_norm'][1]))
    automatic=data.get("auto_field_regions_full_page",[]) or []
    if automatic:
        auto_groups=_learned_crop_clusters({"field_regions_full_page":automatic})
        # Manual crop geometry always leads; automatic observations never
        # shift its average or outvote it by volume.
        for item in auto_groups:
            a,b,c,d=item['rect_norm']
            if not any(abs((a+c-r['rect_norm'][0]-r['rect_norm'][2])/2)<.08
                       and abs((b+d-r['rect_norm'][1]-r['rect_norm'][3])/2)<.08
                       for r in output):
                output.append(item)
    return output


def _learned_crop_rectangles(learning_data,width,height):
    """Translate every ranked crop cluster into a padded full-page scan box."""
    boxes=[]
    for item in _learned_crop_clusters(learning_data):
        a,b,c,d=item['rect_norm']
        dx=max(8.,(c-a)*width*.10)
        dy=max(5.,(d-b)*height*.18)
        rect=(max(0,int(a*width-dx)),max(0,int(b*height-dy)),
              min(width,int(math.ceil(c*width+dx))),
              min(height,int(math.ceil(d*height+dy))))
        if rect[2]-rect[0]>=35 and rect[3]-rect[1]>=10:
            boxes.append(dict(rect=rect,count=item['count'],rect_norm=item['rect_norm']))
    return boxes

def _public_scan_event(event, aliases):
    """Allowlist only: unknown strings/keys never leave the computer."""
    enum_keys={
        'event':{'run_start','run_end','row_final','ocr_candidates','ocr_decision',
                 'performance_file','bounded_ocr_pass','crop_read_selection',
                 'model_crop_rejected','scan_error','approval_decision','sequence_update'},
        'source':{'incident','case','fallback','header','learned','unknown'},
        'label_type':{'incident','case'},
        'outcome':{'candidate_ready','manual_review','auto_approved'},
        'engine':{'EasyOCR','TrOCR'},
        'inference_method':{'carry_forward','before_after'},
        'reason':{'non_case_word_detected','visual_number_line_evidence'}}
    numbers={'score','confidence','page','count','ms','calls','total_ms','handwriting_calls','printed_calls',
             'candidates','auto_approved','manual_review','no_case_number','scan_error',
             'incident_candidate','incident_low_or_uncalibrated_confidence','unverified_fallback_location','conflicting_readings'}
    flags={'ambiguous','box_verified','label_context','learned_location','auto_approved','printed_available'}
    rectangles={'rect','bbox','box_rect','selected_bbox'}
    cases={'number','primary','candidate','case','confirmed','original','selected'}
    containers={'all_candidates','hits','per_page','readings','engine_scores','stages','summary'}
    engines={'EasyOCR','EasyOCR-joined','TrOCR','TrOCR-learned'}
    def alias(kind,value):
        mapping=aliases.setdefault(kind,{})
        if value not in mapping:mapping[value]=kind+str(len(mapping)+1)
        return mapping[value]
    def clean(obj):
        if not isinstance(obj,dict):return {}
        out={}
        for key,value in obj.items():
            if key=='file' and isinstance(value,str):out['file_id']=alias('file',value)
            elif key in cases and isinstance(value,str):
                valid=bool(re.fullmatch(r'08-[0-9]{1,7}',value))
                out[key]={'valid_08':valid}
                if valid:
                    out[key].update(identity=alias('case',_case_identity(value)),digits=len(value)-3,
                                    leading_zeros=len(value[3:])-len(value[3:].lstrip('0')))
            elif key=='version' and isinstance(value,str) and re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+',value):out[key]=value
            elif key in enum_keys and isinstance(value,str) and value in enum_keys[key]:out[key]=value
            elif key in flags and isinstance(value,bool):out[key]=value
            elif key in numbers and isinstance(value,(int,float)) and math.isfinite(value):out[key]=value
            elif key in rectangles and isinstance(value,(list,tuple)) and len(value)==4 and all(isinstance(x,(int,float)) and math.isfinite(x) for x in value):out[key]=list(value)
            elif key=='engines' and isinstance(value,list):out[key]=[x for x in value if isinstance(x,str) and x in engines]
            elif key=='engine_scores' and isinstance(value,dict):out[key]={k:v for k,v in value.items() if k in engines and isinstance(v,(int,float)) and math.isfinite(v)}
            elif key=='stages' and isinstance(value,dict):
                allowed={'trocr','easyocr','pdf_render','network_io_pdf_open','learned_zone','learned_handwriting','learned_printed','bounded_upper_right_quarter'}
                out[key]={k:clean(v) for k,v in value.items() if k in allowed}
            elif key in containers:
                if isinstance(value,dict):out[key]=clean(value)
                elif isinstance(value,list):out[key]=[clean(v) for v in value if isinstance(v,dict)]
        return out
    if event.get('event') not in enum_keys['event']:return None
    return clean(event)


def _github_log_settings_path():
    return Path(os.environ.get('LOCALAPPDATA') or str(Path.home()/'.local/share'))/'MFCase'/'github_logs.json'


def _protect_github_token(value, decrypt=False):
    """Windows DPAPI, current user only; never persist a plaintext token."""
    if os.name!='nt':raise RuntimeError('Credential storage requires Windows; use MFCASE_GITHUB_LOG_TOKEN instead.')
    import ctypes
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]
    buffer=ctypes.create_string_buffer(value)
    source=Blob(len(value),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)));result=Blob()
    crypt=ctypes.windll.crypt32
    if decrypt:
        ok=crypt.CryptUnprotectData(ctypes.byref(source),None,None,None,None,1,ctypes.byref(result))
    else:
        ok=crypt.CryptProtectData(ctypes.byref(source),'MFCase GitHub logs',None,None,None,1,ctypes.byref(result))
    if not ok:raise RuntimeError('Windows could not protect/unlock the GitHub credential.')
    try:return ctypes.string_at(result.data,result.size)
    finally:
        ctypes.windll.kernel32.LocalFree.argtypes=[ctypes.c_void_p]
        ctypes.windll.kernel32.LocalFree(ctypes.cast(result.data,ctypes.c_void_p))


def _read_github_log_settings():
    path=_github_log_settings_path()
    if not path.exists():return {'enabled':True}
    return json.loads(path.read_text(encoding='utf-8'))


def _github_token_is_expired(settings):
    if settings.get('token_rejected'):return True
    value=settings.get('token_expires_at')
    if not value:return False
    try:
        from datetime import timezone
        expiry=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        if expiry.tzinfo is None:expiry=expiry.replace(tzinfo=timezone.utc)
        return expiry<=datetime.now(timezone.utc)
    except (ValueError,TypeError):return False


def _save_github_log_settings(settings, enabled, token=''):
    import base64
    data=dict(settings,enabled=bool(enabled))
    if token.strip():
        data['protected_token']=base64.b64encode(_protect_github_token(token.strip().encode())).decode()
        data.pop('token_expires_at',None);data.pop('token_rejected',None)
    path=_github_log_settings_path();path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(data),encoding='utf-8');os.replace(temp,path)
    return data



class _MFCaseCapsLabel(ttk.Label):
    """Uppercase generated status messages without modifying saved data."""
    def configure(self, cnf=None, **kwargs):
        if 'text' in kwargs:
            kwargs['text']=App._ui_message_caps(kwargs['text'])
        return super().configure(cnf,**kwargs)
    config=configure

class _MFCasePieAction:
    """Small Button-compatible state proxy for the existing scan callbacks."""
    def __init__(self, canvas, key):
        self.canvas, self.key = canvas, key
    def config(self, **kwargs):
        item=self.canvas.actions[self.key]
        if 'state' in kwargs: item['state']=kwargs['state']
        if 'text' in kwargs: item['text']=kwargs['text'].upper()
        self.canvas.redraw()
    configure=config
    def cget(self, key): return self.canvas.actions[self.key].get(key)
    def __getitem__(self,key): return self.cget(key)
    def invoke(self): return self.canvas.invoke_action(self.key)
    def winfo_exists(self): return self.canvas.winfo_exists()

class MFCasePieTransport(tk.Canvas):
    """Four keyboard-accessible circular sectors; preserves Button API states."""
    COLORS={'folder':'#123f79','scan':'#167a3d','pause':'#2489bd','stop':'#b92e32'}
    ORDER=('folder','scan','pause','stop')
    ANGLES={'folder':(90,90),'scan':(0,90),'pause':(180,90),'stop':(270,90)}
    def __init__(self, master, on_activate, **kwargs):
        super().__init__(master,bg='#edf2f8',highlightthickness=0,
                         takefocus=1,**kwargs)
        self.on_activate=on_activate;self.actions={}
        self.selected=None;self.focused='folder';self.radius=1;self.center=(0,0)
        self.bind('<Configure>',lambda _ev:self.redraw())
        self.bind('<Button-1>',self._click)
        self.bind('<Return>',self._activate_keyboard)
        self.bind('<space>',self._activate_keyboard)
        self.bind('<Left>',lambda _ev:self._change_focus(-1))
        self.bind('<Right>',lambda _ev:self._change_focus(1))
        self.bind('<Up>',lambda _ev:self._change_focus(-1))
        self.bind('<Down>',lambda _ev:self._change_focus(1))
        self.bind('<FocusIn>',lambda _ev:self.redraw())
        self.bind('<FocusOut>',lambda _ev:self.redraw())
    def register(self,key,text,callback,state):
        self.actions[key]={'text':text,'command':callback,'state':state}
        return _MFCasePieAction(self,key)
    def _change_focus(self,delta):
        self.focused=self.ORDER[(self.ORDER.index(self.focused)+delta)%4]
        self.redraw();return 'break'
    def _activate_keyboard(self,_event=None):
        self.invoke_action(self.focused);return 'break'
    def _click(self,event):
        self.focus_set()
        x,y=event.x-self.center[0],event.y-self.center[1]
        if x*x+y*y>self.radius*self.radius:return
        if y<0:key='folder' if x<0 else 'scan'
        else:key='pause' if x<0 else 'stop'
        self.focused=key;self.invoke_action(key)
    def invoke_action(self,key):
        item=self.actions.get(key)
        if not item or item['state']=='disabled':return
        self.on_activate('pie:'+key)
        item['command']()
    def redraw(self):
        if not self.winfo_exists():return
        self.delete('all')
        width=max(1,self.winfo_width());height=max(1,self.winfo_height())
        diameter=min(width-18,height-18)
        if diameter<20:return
        cx,cy=width/2,height/2;r=diameter/2
        self.center=(cx,cy);self.radius=r
        x0,y0,x1,y1=cx-r,cy-r,cx+r,cy+r
        for key in self.ORDER:
            if key not in self.actions:continue
            item=self.actions[key];pressed=self.selected=='pie:'+key
            color=self.COLORS[key]
            if pressed:
                color={'folder':'#08274e','scan':'#095526','pause':'#0e5883','stop':'#8e1c22'}[key]
            if item['state']=='disabled':
                color={'folder':'#4e6e94','scan':'#4a9f70',
                       'pause':'#68b6db','stop':'#d46d70'}[key]
            start,extent=self.ANGLES[key]
            self.create_arc(x0,y0,x1,y1,start=start,extent=extent,
                            fill=color,outline='#e5edf5',width=4,style='pieslice')
            dx=(-1 if key in ('folder','pause') else 1)*r*.46
            dy=(-1 if key in ('folder','scan') else 1)*r*.46
            ix,iy=cx+dx,cy+dy-r*.07
            a=max(8,r*.18)
            if key=='folder':
                self.create_rectangle(ix-a*.95,iy-a*.15,ix+a*.95,iy+a*.65,
                                      outline='white',width=2)
                self.create_rectangle(ix-a*.95,iy-a*.55,ix-a*.05,iy-a*.12,
                                      outline='white',fill='white')
            elif key=='scan':
                self.create_polygon(ix-a*.55,iy-a*.8,ix+a*.8,iy,
                                    ix-a*.55,iy+a*.8,fill='white',outline='white')
            elif key=='pause':
                for sign in (-.45,.28):
                    self.create_rectangle(ix+a*sign,iy-a*.72,ix+a*(sign+.25),iy+a*.72,
                                          fill='white',outline='white')
            else:
                self.create_rectangle(ix-a*.63,iy-a*.63,ix+a*.63,iy+a*.63,
                                      fill='white',outline='white')
            label={'folder':'FOLDER','scan':'SCAN','pause':item['text'],
                   'stop':'STOP'}[key]
            self.create_text(ix,iy+a*1.55,text=label,fill='white',
                             font=('Segoe UI',max(8,int(r*.106)),'bold'))
            if pressed:self.create_arc(x0+5,y0+5,x1-5,y1-5,start=start,extent=extent,
                                      outline='#fff4b2',width=3,style='arc')
            if key==self.focused and self.focus_get()==self:
                self.create_text(ix,iy-a*1.5,text='•',fill='white',
                                 font=('Segoe UI',11,'bold'))
        self.create_oval(cx-4,cy-4,cx+4,cy+4,fill='#e5edf5',outline='#e5edf5')

class App:
    def __init__(self, root):
        self.root = root
        root.title("M.F. CASE NUMBER OCR — V5.6.18")
        root.geometry("1180x780")
        root.minsize(1040, 720)
        self.folder = None
        self.reader = None
        self.trocr_processor = None
        self.trocr_model = None
        self.trocr_training = False
        self.queue = queue.Queue()
        self.busy = False
        self.paused = False
        self.pause_event = threading.Event()
        self.pause_event.set()
        self.stop_event = threading.Event()
        self.learning_pause_event = threading.Event()
        self.learning_pause_event.set()
        self.learning_stop_event = threading.Event()
        self.records = {}
        self.sent_history = {}
        self.completed_photo_refs = {}
        self.completed_edit_widgets = {}
        self.completed_edit_drafts = {}
        self.completed_unverify_buttons = {}
        # Checkbox selection is independent of Treeview's review-row highlighting.
        self.completed_checked_iids = set()
        # Scan checkboxes do not change the Treeview review highlight.
        self.scan_checked_iids = set()
        # Display-only sorts; sequence approval always uses original filenames.
        self._table_sort_state = {}
        self._table_column_titles = {}
        # Never export before a running scan's sequence check has completed.
        self.export_busy = False
        self._export_cancel_event=threading.Event()
        self._stop_all_requested=False
        self._auto_export_after = None
        self._export_reschedule = False
        self._export_in_flight_iids = set()
        self.scan_items = []
        self.learning_dir = None
        self.learning_data = {"examples": [], "corrections": {}, "field_regions_full_page":[],
                              "trocr_v2_trained_count": 0,
                              "trocr_v2_attempted_count": 0,
                              "trocr_v2_validation": {}}
        self.data_lock = threading.RLock()
        self.trocr_training = False
        self.torch_device = "cpu"
        self.trocr_error = ""
        self.training_popup = None
        self.training_progressbar = None
        self.training_percent_label = None
        self.training_stage_label = None
        self.training_close_button = None
        self.training_pause_button = None
        self.training_stop_button = None
        self.auto_label_var = tk.BooleanVar(value=True)
        self.auto_threshold_var = tk.StringVar(value="95")
        self.log_path = None
        self.scan_year_prefix = REQUIRED_CASE_YEAR
        self.update_busy = False
        self.update_release = None
        self._startup_auto_update = False
        self._startup_dialog = None
        self._startup_phase_label = None
        self._startup_progressbar = None
        self._startup_learning_pending = False
        self._offline_prepare_busy = False
        self._auto_learning_started = False
        self._crop_ocr_generation = 0
        self._network_indicator_after = None
        self._learning_connection_source = None
        self._learning_auto_run = False
        self._auto_learning_interrupted = False
        self._post_scan_learning_after = None
        self._last_activity_time = time.monotonic()
        self._training_model_ready_on_restart = False
        # All animation is driven by Tk's own event loop; file and model I/O
        # happens in workers so the loading indicator remains responsive.
        self._loading_jobs = set()
        self._loader_panel = None
        self._loader_arc = None
        self._loader_canvas = None
        self._loader_label = None
        self._loader_tick_id = None
        self._loader_angle = 0
        self._folder_load_generation = 0
        self._folder_load_timeout_id = None
        self.app_version = "5.6.18"
        self._diag_file=None
        self._diag_run_id=None
        self._diag_current_file=None
        self._diag_lock=threading.RLock()
        self._perf_stages={}
        self._training_worker_done=threading.Event()
        self._training_worker_done.set()
        self._scan_waiting_for_training=False
        self._startup_ready = False
        self._sequence_review_pending = False
        self._startup_error = None
        self._learning_revision = None
        self._cached_model_dir = None
        self._run_outcomes = {}
        self._active_scan_iid = None
        self._worker_current_iid = None
        self._rescan_force_iids = set()
        self._audit_lock = threading.RLock()
        self._audit_root = Path(os.getenv("LOCALAPPDATA") or str(Path(__file__).resolve().parent)) / "MFCase" / "logs"
        self._audit_file = self._audit_root / "MFCase_Work_Log.txt"
        self.auto_send_var = tk.BooleanVar(master=root, value=False)
        self.show_all_files_var = tk.BooleanVar(master=root, value=False)
        self._load_user_settings()
        self.app_dir = Path(__file__).resolve().parent
        self.update_config_path = self.app_dir / "update_config.json"
        self._ui()
        # Start maximized so the complete queue and review controls have as much
        # usable screen space as possible. On Windows, "zoomed" is the normal
        # maximized-window state (taskbar and title bar remain accessible).
        self.root.after_idle(self._maximize_window)
        self.auto_label_var.trace_add("write", lambda *_: self._refresh_auto_eligibility_status())
        self.auto_threshold_var.trace_add("write", lambda *_: self._refresh_auto_eligibility_status())
        self.year_var.trace_add("write", lambda *_: self._refresh_auto_eligibility_status())
        self.auto_label_var.set(True)
        self.auto_threshold_var.set("95")
        self._refresh_auto_eligibility_status()
        root.bind_all("<ButtonPress>", self._mark_user_activity, add="+")
        root.bind_all("<KeyPress>", self._mark_user_activity, add="+")
        root.bind_all('<ButtonRelease-1>',self._on_ui_button_released,add='+')
        root.protocol("WM_DELETE_WINDOW",self._request_close)
        root.after(150, self._poll)
        # Production is local-only. Network access is possible only after an
        # explicit click on UPDATE or PREPARE OFFLINE MODELS.
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY","1")
        os.environ.setdefault("DO_NOT_TRACK","1")
        if os.environ.get("MFCASE_SKIP_STARTUP_MAINTENANCE") == "1":
            self._startup_ready = True  # headless test harness
        else:
            root.after(250,self._begin_offline_startup)
            root.after(650,lambda: self.check_for_updates(automatic=True))

    def _loader_start(self, job, description):
        """Show a centered, animated, quarter-screen-scale spinner (UI thread only)."""
        self._loading_jobs.add(job)
        if self._loader_panel is None:
            self.root.update_idletasks()
            # Diameter is one quarter of the shorter visible window dimension.
            # A minimum keeps the indicator readable on small displays.
            diameter = max(128, min(300, int(min(
                self.root.winfo_width(), self.root.winfo_height()) * 0.25)))
            panel = tk.Frame(self.root, bg="#f4f7fc", relief="solid",
                             borderwidth=2, highlightthickness=2,
                             highlightbackground="#1456a8")
            self._loader_panel = panel
            panel.place(relx=0.5, rely=0.5, anchor="center")
            canvas = tk.Canvas(panel, width=diameter + 36, height=diameter + 36,
                               bg="#f4f7fc", highlightthickness=0)
            canvas.pack(padx=18, pady=(16, 2))
            edge = 23
            far = diameter + 13
            canvas.create_oval(edge, edge, far, far, outline="#dce5f2", width=12)
            self._loader_arc = canvas.create_arc(
                edge, edge, far, far, start=0, extent=115,
                outline="#1456a8", width=14, style=tk.ARC)
            self._loader_canvas = canvas
            self._loader_label = tk.Label(
                panel, text=description, font=("Segoe UI", 12, "bold"),
                bg="#f4f7fc", fg="#12345a", wraplength=diameter + 46)
            self._loader_label.pack(padx=14, pady=(0, 18))
            self._loader_tick()
        else:
            self._loader_label.configure(text=description)
        self._loader_panel.lift()

    def _loader_tick(self):
        if self._loader_panel is None:
            return
        self._loader_angle = (self._loader_angle - 9) % 360
        self._loader_canvas.itemconfigure(self._loader_arc, start=self._loader_angle)
        self._loader_tick_id = self.root.after(45, self._loader_tick)

    def _loader_finish(self, job):
        self._loading_jobs.discard(job)
        if self._loading_jobs:
            if self._loader_label is not None:
                if "rolls" in self._loading_jobs:
                    self._loader_label.configure(text="LOADING PDF ROLLS…")
                elif "learning" in self._loading_jobs:
                    self._loader_label.configure(text="LOADING SHARED LEARNING…")
            return
        if self._loader_tick_id is not None:
            self.root.after_cancel(self._loader_tick_id)
            self._loader_tick_id = None
        if self._loader_panel is not None:
            self._loader_panel.destroy()
        self._loader_panel = None
        self._loader_canvas = None
        self._loader_arc = None
        self._loader_label = None

    def _folder_load_worker(self, folder, generation):
        """Enumerate rolls and read folder state without blocking animation."""
        try:
            logging.basicConfig(
                filename=str(folder / ".case_ocr_log.txt"), level=logging.INFO,
                format="%(asctime)s %(levelname)s %(message)s",
                encoding="utf-8", force=True)
            settings_path = folder / ".case_ocr_settings.json"
            year = "08"
            if settings_path.exists():
                try:
                    year = str(json.loads(settings_path.read_text(encoding="utf-8")).get("year", "")).strip() or "08"
                except Exception:
                    logging.exception("Could not read folder settings")
            year = REQUIRED_CASE_YEAR  # Historical settings cannot change the required 08 prefix.
            saved = {}
            state_error = False
            state_path = folder / ".case_ocr_state.json"
            if state_path.exists():
                try:
                    saved = json.loads(state_path.read_text(encoding="utf-8")).get("files", {})
                except Exception:
                    logging.exception("Could not read saved job state; rebuilding queue")
                    state_error = True
            with os.scandir(folder) as entries:
                pdfs=sorted((Path(entry.path) for entry in entries
                             if entry.name.lower().endswith(".pdf") and entry.is_file()),
                            key=self._natural_pdf_sort_key)
            self.queue.put(("folder_load_result", generation, year, saved, pdfs, state_error))
        except Exception as exc:
            self.queue.put(("folder_load_error", generation, str(exc)))

    def _sent_history_path(self):
        return Path(self.folder) / ".case_ocr_sent_history.json" if self.folder else None

    def _load_sent_history(self, previous_state=None):
        with self._perf_measure("network_io_history_load"):
            return self._load_sent_history_impl(previous_state=previous_state)

    def _load_sent_history_impl(self, previous_state=None):
        """Restore verified sent-file anchors using real COMPLETED PDFs only."""
        self.sent_history = {}
        if not self.folder:
            return
        completed=Path(self.folder)/"COMPLETED"
        path=self._sent_history_path()
        try:
            payload=json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
            rows=payload.get("sent",{})
        except (OSError,ValueError,TypeError):
            rows={}
        if not isinstance(rows,dict):
            rows={}
        # Backfill v5.5.1 state entries that were exported but not yet tracked.
        for value in (previous_state or {}).values():
            if not isinstance(value,dict) or not value.get("exported"):
                continue
            origin=str(value.get("original_name") or "")
            target=str(value.get("export_name") or "")
            case=normalize_case_number(value.get("confirmed"))
            if (origin and target and CASE_RE.fullmatch(case) and
                    (value.get("manual_approved") or value.get("auto_labeled"))):
                rows.setdefault(origin,{"original_name":origin,"destination_name":target,
                    "confirmed":case,"source":"prior_state",
                    "manual_approved":bool(value.get("manual_approved")),
                    "auto_labeled":bool(value.get("auto_labeled")),
                    "sequence_inferred":bool(value.get("sequence_inferred"))})
        if completed.is_dir():
            # Recovery of older sent files that v5.5.1 forgot after restarting.
            # The strict _08-number filename suffix is created by MFCase SEND FILES.
            # We validate that the physical destination exists before anchoring.
            try:
                for pdf in completed.iterdir():
                    if not pdf.is_file() or pdf.suffix.lower()!=".pdf":
                        continue
                    match=re.fullmatch(r"(.+)_(08-[0-9]{1,7})(?: \(\d+\))?",pdf.stem)
                    if match:
                        origin=match.group(1)+".pdf"
                        rows.setdefault(origin,{"original_name":origin,
                            "destination_name":pdf.name,"confirmed":match.group(2),
                            "source":"completed_filename_recovery","manual_approved":True,
                            "auto_labeled":False})
            except OSError as exc:
                self._log("Could not inspect COMPLETED history: %s",exc)
        for origin,value in rows.items():
            if not isinstance(value,dict):
                continue
            name=str(value.get("destination_name") or "")
            case=normalize_case_number(value.get("confirmed"))
            if (not origin or Path(origin).name!=origin or not name
                    or Path(name).name!=name or not CASE_RE.fullmatch(case)
                    ):
                continue
            path=completed/name
            if not path.is_file():
                continue
            row=dict(value)
            row.update(original_name=origin,destination_name=name,confirmed=case)
            self.sent_history[origin]=row
        self._save_sent_history()

    def _save_sent_history(self):
        with self._perf_measure("network_io_history_save"):
            return self._save_sent_history_impl()

    def _save_sent_history_impl(self):
        path=self._sent_history_path()
        if path is None:
            return False
        try:
            payload={"version":1,"updated_at":datetime.now().isoformat(timespec="seconds"),
                     "sent":self.sent_history}
            temp=path.with_suffix(".json.tmp")
            temp.write_text(json.dumps(payload,indent=2),encoding="utf-8")
            os.replace(temp,path)
            return True
        except OSError as exc:
            self._log("Could not persist sent-file anchor history: %s",exc)
            return False

    def _sequence_order_with_sent_history(self, include_unscanned=True):
        """Original PDF order, including independently confirmed SENT anchors."""
        if not self.folder:
            return []
        folder=Path(self.folder)
        order=[]
        active=set()
        for iid,rec in self.records.items():
            if not rec.get("path") or Path(rec["path"]).parent!=folder:
                continue
            if not include_unscanned and str(rec.get("status") or "") in ("Not scanned","Scanning"):
                continue
            original=Path(rec.get("original_name") or rec["path"]).name
            active.add(original.casefold())
            order.append((iid,rec))
        # History is validated against actual COMPLETED PDFs ONCE at folder load.
        # Do not re-stat every historical file on each scan row over SMB.
        for original,row in getattr(self,"sent_history",{}).items():
            if original.casefold() in active or row.get("sequence_inferred"):
                continue
            target=folder/"COMPLETED"/str(row.get("destination_name") or "")
            case=normalize_case_number(row.get("confirmed"))
            if not CASE_RE.fullmatch(case):
                continue
            rec={"path":target,"original_name":original,"confirmed":case,
                 "manual_approved":bool(row.get("manual_approved",True)),
                 "auto_labeled":bool(row.get("auto_labeled",False)),
                 "sequence_inferred":False,"sequence_conflict":False,
                 "scan_ambiguous":False,"exported":True,
                 "score":float(row.get("score",95. if row.get("auto_labeled") else 0.) or 0.),
                 "status":"Sent to COMPLETED (history)"}
            order.append(("sent-history::"+original,rec))
        return sorted(order,key=lambda item:self._natural_pdf_sort_key(
            Path(item[1].get("original_name") or item[1]["path"])))

    def _apply_folder_load(self, year, saved, pdfs, state_error):
        """Install the folder listing onto Tk controls, preserving saved state."""
        # A prior process may have been closed mid-scan. Its finished
        # decisions remain on disk; do not carry the export lock across runs.
        self._sequence_review_pending=False
        self.year_var.set(REQUIRED_CASE_YEAR)
        for tree in (self.tree, self.completed_tree):
            for item in tree.get_children():
                tree.delete(item)
        for editing_iid in list(self.completed_edit_widgets):
            self._close_completed_edit(editing_iid)
        self.completed_edit_drafts.clear()
        self.completed_checked_iids.clear()
        self.scan_checked_iids.clear()
        self.records = {}
        self.completed_photo_refs.clear()
        if state_error:
            messagebox.showwarning(
                "Saved queue could not be read",
                "The saved queue file could not be read. The PDFs are still intact; the queue will be rebuilt.")
        self._bulk_scan_rows=True
        for pdf in pdfs:
            old=saved.get(pdf.name,{})
            sent=False  # A PDF still in the selected root has not been sent to COMPLETED.
            item = {
                "path": pdf, "candidate": (
                    normalize_case_number(old.get("candidate"))
                    if CASE_RE.fullmatch(normalize_case_number(old.get("candidate"))) else ""),
                "confirmed": old.get("confirmed", ""), "status": old.get("status", "Not scanned"),
                "snippet": None, "handwritten": bool(old.get("handwritten", False)),
                "score": float(old.get("score", 0.0) or 0.0),
                "engines": list(old.get("engines", [])),
                "engine_agreement": bool(old.get("engine_agreement", False)),
                "engine_scores": dict(old.get("engine_scores") or {}),
                "auto_labeled": bool(old.get("auto_labeled", False)),
                "approval_rule_version": old.get("approval_rule_version",""),
                "manual_approved": bool(old.get("manual_approved", False)),
                "send_after_manual_approval": bool(old.get("send_after_manual_approval", False)),
                 "completed_iid": None, "exported": sent,
                "case_draft": old.get("case_draft", ""),
                "original_name": old.get("original_name") or
                                 (pdf.name if not sent else old.get("source_name",pdf.name)),
                "original_path": old.get("original_path",""),
                "export_name": old.get("export_name", ""),
                "preview_meta": old.get("preview_meta", {}),
                "pending_auto_location": old.get("pending_auto_location"),
                "manual_crop_rect": tuple(old.get("manual_crop_rect", [])) if old.get("manual_crop_rect") else None,
                "manual_crop_origin": old.get("manual_crop_origin",""),
                "training_type": old.get("training_type", "unsure"),
                "scan_source": old.get("scan_source", ""),
                "scan_ambiguous": bool(old.get("scan_ambiguous", False)),
                "sequence_inferred": bool(old.get("sequence_inferred", False)),
                "sequence_conflict": bool(old.get("sequence_conflict", False)),
                "sequence_anchors": list(old.get("sequence_anchors", [])),
                "sequence_suggestion": old.get("sequence_suggestion", ""),
                "sequence_previous_prefill":bool(old.get("sequence_previous_prefill",False)),
                "sequence_original_candidate":old.get("sequence_original_candidate",""),
                "sequence_original_score":old.get("sequence_original_score",old.get("score",0)),
                "sequence_original_ambiguous":bool(old.get("sequence_original_ambiguous",False)),
                "sequence_excluded": bool(old.get("sequence_excluded", False)),
                "scan_selected": bool(old.get("scan_selected", True)),
                "learning_pending": bool(old.get("learning_pending", False)),
                "export_error": str(old.get("export_error", ""))
            }
            if not item.get("case_draft") and not str(item.get("status", "")).startswith("Edited —"):
                item.pop("case_draft",None)
            if item["auto_labeled"] and item["approval_rule_version"]!="5.3.2":
                item.update(confirmed="",auto_labeled=False,manual_approved=False,
                            sequence_inferred=False,sequence_conflict=False,
                            status="REVIEW — rescan needed for updated two-engine and sequence rules")
            if item["confirmed"] and not CASE_RE.fullmatch(
                    normalize_case_number(item["confirmed"])):
                # Historical non-08 approval is not valid in this release.
                # Keep PDFs and learning; mark the root record for correction.
                previous_case=str(item["confirmed"])
                item.update(confirmed="",manual_approved=False,
                            auto_labeled=False,send_after_manual_approval=False,
                            sequence_inferred=False,sequence_conflict=False,
                            sequence_anchors=[],sequence_suggestion="",
                            status=f"REVIEW — saved {previous_case} rejected; 08- required")
                self._diag_event("saved_case_year_rejected",file=pdf.name,
                    case=previous_case,reason="required_literal_08_prefix")
            if sent:
                item["status"]=f"Renamed in place: {pdf.name}"
                item["scan_selected"]=False
            iid = f"pdf::{pdf.name}"
            if iid in self.records:
                iid=f"pdf::COMPLETED::{pdf.name}"
            self.records[iid] = item
            item["scan_selected"]=False  # Selection is for review/rescan, not the scan queue.
            if item["confirmed"] and not item["exported"]:
                self._insert_completed_row(iid,item)
                item["completed_iid"]=iid
            else:
                item["completed_iid"]=None
            self._insert_scan_row(iid,item)
        self._bulk_scan_rows=False
        self._load_sent_history(saved)
        self.status.config(
            text=f"{len(pdfs):,} PDFs queued. SCAN processes pending files; the list shows items needing review.")
        self._refresh_scan_button()
        self._refresh_learning_button_state()
        self._refresh_review_controls()
        self._update_completed_count()
        self._update_scan_file_count()
        self._save_state()
        self._recheck_after_decision()
        self._update_completed_count()
        self._schedule_auto_export()

    @staticmethod
    def _ui_message_caps(value):
        """Uppercase generated status copy, never filesystem identifiers."""
        value=str(value or '')
        if re.search(r'(?i)\b[^\s]+\.(?:pdf|zip|jsonl?)\b|https?://|\\\\|[A-Za-z]:[/\\]',value):
            return value
        return value.upper()

    def _on_ui_button_released(self,event):
        """Visual-only pressed selection for ordinary review/settings buttons.

        Never invokes a command or changes review/approval semantics.
        """
        widget=getattr(event,'widget',None)
        if not isinstance(widget,(tk.Button,ttk.Button)):
            return
        if widget in getattr(self,'_ui_action_buttons',{}).values():
            return  # Shared dashboard controls already handle their own state.
        try:
            if str(widget.cget('state'))=='disabled':return
        except tk.TclError:return
        previous=getattr(self,'_generic_pressed_widget',None)
        if previous is not None:
            try:
                if previous.winfo_exists():
                    if isinstance(previous,tk.Button):previous.config(relief='raised',bd=2)
                    else:previous.config(style=getattr(previous,'_mfcase_original_style','MFCase.Action.TButton'))
            except tk.TclError:pass
        self._generic_pressed_widget=None
        self._pressed_visual('generic:'+str(widget))
        if previous is widget:return
        self._generic_pressed_widget=widget
        try:
            if isinstance(widget,tk.Button):widget.config(relief='sunken',bd=4)
            else:
                widget._mfcase_original_style=widget.cget('style')
                widget.config(style='MFCase.Pressed.TButton')
        except tk.TclError:pass

    def _pressed_visual(self,key):
        """One depressed selection at a time; repeat-click releases it."""
        if not key.startswith('generic:'):
            previous=getattr(self,'_generic_pressed_widget',None)
            if previous is not None:
                try:
                    if previous.winfo_exists():
                        if isinstance(previous,tk.Button):previous.config(relief='raised',bd=2)
                        else:previous.config(style=getattr(previous,'_mfcase_original_style','MFCase.Action.TButton'))
                except tk.TclError:pass
                self._generic_pressed_widget=None
        old=getattr(self,'_pressed_ui_key',None)
        self._pressed_ui_key=None if old==key else key
        for name,button in getattr(self,'_ui_action_buttons',{}).items():
            if button.winfo_exists():
                active=(self._pressed_ui_key==name)
                button.configure(relief='sunken' if active else 'raised',
                                 bd=4 if active else 2)
        pie=getattr(self,'pie_transport',None)
        if pie is not None:
            pie.selected=self._pressed_ui_key
            pie.redraw()

    def _action_button(self,parent,key,label,callback,color='#123f79',**pack):
        def run():
            self._pressed_visual(key)
            callback()
        button=tk.Button(parent,text=label.upper(),command=run,
            bg=color,fg='white',activebackground='#082e59',activeforeground='white',
            font=('Segoe UI',10,'bold'),relief='raised',bd=2,
            padx=12,pady=9,takefocus=1,cursor='hand2',
            disabledforeground='#cad0d6')
        self._ui_action_buttons[key]=button
        if pack:button.pack(**pack)
        return button

    def _toggle_list_display(self):
        self.show_all_files_var.set(not self.show_all_files_var.get())
        self._pressed_visual('list')
        self._toggle_full_list()
        self._refresh_list_toggle()

    def _refresh_list_toggle(self):
        button=getattr(self,'list_toggle_btn',None)
        if button is not None:
            button.configure(relief='sunken' if self.show_all_files_var.get() else 'raised',
                             bd=4 if self.show_all_files_var.get() else 2)

    def _select_all_visible(self):
        tree=getattr(self,'tree',None)
        if tree is None:return
        visible=set(tree.get_children(''))
        selected=bool(visible) and visible.issubset(self.scan_checked_iids)
        desired=bool(self.ui_select_all_var.get())
        if desired==selected:return
        # Preserve the existing checkbox rules and guards.
        if self.busy or self._training_blocks_work():
            self.ui_select_all_var.set(selected)
            return
        self._toggle_scan_select_all()

    def _short_folder_name(self,folder):
        if not folder:return 'NO FOLDER SELECTED'
        return '…/'+Path(str(folder)).name

    def _dashboard_resize(self,event=None):
        dashboard=getattr(self,'dashboard',None)
        if dashboard is None or not dashboard.winfo_exists():return
        available=max(0,self.review_tab.winfo_height())
        desired=max(240,min(450,int(available*.485)))
        if getattr(self,'_dashboard_last_height',None)!=desired:
            self._dashboard_last_height=desired
            dashboard.configure(height=desired)

    def _format_run_clock(self,seconds):
        seconds=max(0,int(seconds))
        return f'{seconds//3600:02d}:{seconds//60%60:02d}:{seconds%60:02d}'

    def _scan_clock_actual(self):
        if getattr(self,'_scan_clock_start',None) is None:
            return getattr(self,'_scan_clock_final_elapsed',0.0)
        now=time.monotonic()
        paused=getattr(self,'_scan_clock_pause_seconds',0.0)
        pause_start=getattr(self,'_scan_clock_pause_start',None)
        if pause_start is not None:paused+=max(0.0,now-pause_start)
        return max(0.0,now-self._scan_clock_start-paused)

    def _update_run_timers(self):
        label=getattr(self,'scan_run_time_label',None)
        if label is None:return
        actual=self._scan_clock_actual()
        done=getattr(self,'_scan_clock_done',0)
        total=len(getattr(self,'scan_items',[]) or [])
        if done and total:
            per=max(.01,actual/done)
            estimate=per*total
            remaining=max(0.0,per*(total-done))
            estimated=self._format_run_clock(estimate)
            remaining=self._format_run_clock(remaining)
        else:
            estimated='--:--:--';remaining='--:--:--'
        label.config(text=('RUN TIME: ESTIMATED '+estimated+'  |  ACTUAL '+
                           self._format_run_clock(actual)+'  |  REMAINING '+remaining))

    def _tick_ui_clock(self):
        try:
            if self.root.winfo_exists():
                self._update_run_timers()
                self.root.after(800,self._tick_ui_clock)
        except tk.TclError:pass

    def _ui(self):
        self._pressed_ui_key=None
        self._generic_pressed_widget=None
        self._ui_action_buttons={}
        styles=ttk.Style(self.root)
        styles.configure('MFCase.Pressed.TButton',relief='sunken',borderwidth=3,
                         padding=(4,6),font=('Segoe UI',10,'bold'))
        self._scan_clock_start=None
        self._scan_clock_pause_start=None
        self._scan_clock_pause_seconds=0.
        self._scan_clock_final_elapsed=0.
        self._scan_clock_done=0
        main=ttk.Frame(self.root,padding=7)
        main.pack(fill='both',expand=True)
        header=ttk.Frame(main)
        header.pack(fill='x',pady=(0,4))
        ttk.Label(header,text='M.F.',font=('Segoe UI',16,'bold'),
                  foreground='white',background='#123f79',padding=(10,3)).pack(side='left',padx=(0,9))
        title_stack=ttk.Frame(header)
        title_stack.pack(side='left',fill='x',expand=True)
        ttk.Label(title_stack,text=f'M.F. CASE NUMBER OCR & RENAMER — V{self.app_version}',
                  font=('Segoe UI',15,'bold')).pack(anchor='w')
        self.settings_nav_btn=self._action_button(header,'settings','SETTINGS',
            self._open_settings_window)
        self.settings_nav_btn.pack(side='right',padx=(6,0))
        self.learning_activity=ttk.Frame(header)
        self.learning_activity_label=ttk.Label(self.learning_activity,
            text='LEARNING',font=('Segoe UI',9,'bold'))
        self.learning_activity_label.pack(side='left',padx=(0,4))
        self.learning_spinner=tk.Canvas(self.learning_activity,width=78,height=15,
            bg='#e6efe7',highlightthickness=1,highlightbackground='#a3c6ac')
        self.learning_spinner.pack(side='left')
        self._learning_spinner_job=None
        ttk.Separator(main,orient='horizontal').pack(fill='x',pady=(4,5))
        self.view_container=ttk.Frame(main)
        self.view_container.pack(fill='both',expand=True)
        self.review_tab=ttk.Frame(self.view_container,padding=5)
        self.completed_tab=ttk.Frame(self.view_container,padding=8)
        self.settings_tab=ttk.Frame(self.view_container,padding=14)
        self.update_btn=ttk.Button(self.settings_tab,text='UPDATE',command=self.check_for_updates)
        self.finish_batch_btn=ttk.Button(self.settings_tab,text='UPDATE LEARNING',
                                          command=self._finish_import_batch,state='disabled')
        self.update_btn.pack(anchor='w',pady=5)
        self.finish_batch_btn.pack(anchor='w',pady=5)
        self.review_tab.place(relx=0,rely=0,relwidth=1,relheight=1)
        self.year_var=tk.StringVar(value='08')
        self.auto_label_var.set(True)
        self.auto_threshold_var.set('95')

        self.dashboard=tk.Frame(self.review_tab,bg='#edf2f8',height=320,
                                highlightthickness=1,highlightbackground='#afbfce')
        self.dashboard.place(relx=0,rely=0,relwidth=1,relheight=.48)
        self.dashboard.grid_propagate(False)
        self.lower_panel=ttk.Frame(self.review_tab,padding=(0,5,0,0))
        self.lower_panel.place(relx=0,rely=.48,relwidth=1,relheight=.52)
        self.dashboard.grid_columnconfigure(0,weight=1,uniform='dashboard')
        self.dashboard.grid_columnconfigure(1,weight=1,uniform='dashboard')
        self.dashboard.grid_rowconfigure(0,weight=1)
        self.action_bar=tk.Frame(self.dashboard,bg='#edf2f8')
        self.action_bar.grid(row=0,column=0,sticky='nsew',padx=(3,1),pady=3)
        self.pie_transport=MFCasePieTransport(self.action_bar,self._pressed_visual)
        self.pie_transport.pack(fill='both',expand=True)
        self.choose_folder_btn=self.pie_transport.register('folder','FOLDER',self.choose_folder,'normal')
        self.scan_btn=self.pie_transport.register('scan','SCAN',self.scan,'disabled')
        self.pause_btn=self.pie_transport.register('pause','PAUSE',self.toggle_pause,'disabled')
        self.restart_btn=self.pie_transport.register('stop','STOP',self.stop_or_restart_scan,'disabled')
        self.pie_transport.redraw()

        right=tk.LabelFrame(self.dashboard,text=' FILE ACTIONS ',
              fg='#123f79',bg='#edf2f8',font=('Segoe UI',12,'bold'),
              bd=2,relief='groove',padx=10,pady=10)
        right.grid(row=0,column=1,sticky='nsew',padx=(3,8),pady=8)
        for c in range(3):right.grid_columnconfigure(c,weight=1,uniform='actions')
        self.refresh_root_btn=self._action_button(right,'refresh','REFRESH',self.refresh_root_folder)
        self.refresh_root_btn.grid(row=0,column=0,sticky='ew',padx=3,pady=10)
        self.clear_root_btn=self._action_button(right,'clear','CLEAR',self.clear_scan_screen)
        self.clear_root_btn.grid(row=0,column=1,sticky='ew',padx=3,pady=10)
        self.send_selected_btn=self._action_button(right,'send','SEND FILES',self.export_completed)
        self.send_selected_btn.grid(row=0,column=2,sticky='ew',padx=3,pady=10)
        self.send_selected_btn.config(state='disabled')
        tk.Label(right,text='SCAN • REVIEW • SEND',bg='#edf2f8',fg='#647a94',
                 font=('Segoe UI',11,'bold')).grid(row=1,column=0,columnspan=3,pady=(18,4))

        toolbar=ttk.Frame(self.lower_panel)
        toolbar.pack(fill='x',pady=(0,4))
        self.list_toggle_btn=tk.Button(toolbar,text='☰',fg='white',bg='#c99b00',
             activebackground='#a67c00',activeforeground='white',
             font=('Segoe UI',17,'bold'),relief='raised',bd=2,takefocus=1,
             command=self._toggle_list_display,width=3,cursor='hand2')
        self.list_toggle_btn.pack(side='left',padx=(0,9))
        self._ui_action_buttons['list']=self.list_toggle_btn
        self.scan_file_count_label=ttk.Label(toolbar,text='0 OF 0 SELECTED',width=17)
        self.scan_file_count_label.pack(side='left',padx=(0,4))
        self.ui_select_all_var=tk.BooleanVar(master=self.root,value=False)
        self.ui_select_all=ttk.Checkbutton(toolbar,text='SELECT ALL',
                           variable=self.ui_select_all_var,command=self._select_all_visible)
        self.ui_select_all.pack(side='left',padx=(0,12))
        self.folder_label=ttk.Label(toolbar,text='NO FOLDER SELECTED',width=21,anchor='w')
        self.folder_label.pack(side='left',padx=(0,10))
        self.rescan_btn=self._action_button(toolbar,'rescan','RESCAN ALL',
                                            self.rescan_all,color='#123f79')
        self.rescan_btn.pack(side='right',padx=(8,0))
        self.rescan_btn.config(state='disabled')

        self.review_btn=ttk.Button(self.settings_tab,text='REVIEW SELECTED',
                                   command=self.review_selected,state='disabled')
        self.review_all_btn=self.review_btn
        self.work_log_btn=ttk.Button(self.settings_tab,text='EXPORT WORK LOG',command=self._diag_export)
        self.before_after_btn=ttk.Button(self.settings_tab,text='BEFORE/AFTER',command=self._run_before_after)
        scan_progress_row=ttk.Frame(self.lower_panel)
        scan_progress_row.pack(fill='x',pady=(0,3))
        self.scan_progress_label=ttk.Label(scan_progress_row,
            text='SCAN READY — 0 OF 0 PDF FILES',width=42,anchor='w')
        self.scan_progress_label.pack(side='left',padx=(0,10))
        self.progress=ttk.Progressbar(scan_progress_row,mode='determinate',maximum=1,value=0)
        self.progress.pack(side='left',fill='x',expand=True)
        self.scan_progress_pct=ttk.Label(scan_progress_row,text='0%',width=6,anchor='e')
        self.scan_progress_pct.pack(side='right',padx=(8,0))
        self.scan_run_time_label=ttk.Label(self.lower_panel,
            text='RUN TIME: ESTIMATED --:--:--  |  ACTUAL 00:00:00  |  REMAINING --:--:--',
            font=('Segoe UI',10,'bold'))
        self.scan_run_time_label.pack(anchor='w',pady=(0,3))
        self.scan_displayed_count_label=ttk.Label(self.lower_panel,text='0 FILES DISPLAYED')
        self.scan_displayed_count_label.pack(side='bottom',anchor='w',pady=(4,2))
        self.root.after(800,self._tick_ui_clock)

        pane = ttk.Frame(self.lower_panel)
        pane.pack(fill="both", expand=True)
        self._scan_preview_photos={}
        self._scan_preview_widgets={}
        self._scan_approve_widgets={}
        self._scan_review_widgets={}
        self._scan_case_widgets={}
        self._scan_case_sync=False
        self._scan_preview_keys={}
        self._scan_preview_refresh_id=None
        ttk.Style().configure("MFCase.Scan.Treeview",rowheight=74)
        cols = ("scan_select", "file", "preview", "candidate", "approve", "review", "score", "workflow", "status")
        self.tree = ttk.Treeview(pane, columns=cols, show="headings",
                                 style="MFCase.Scan.Treeview",selectmode="extended")
        self.tree.heading("scan_select", text="☑ ALL", command=self._toggle_scan_select_all)
        self.tree.column("scan_select", width=76, minwidth=65, stretch=False, anchor="center")
        self._set_sortable_heading("scan", self.tree, "file", "PDF FILE")
        self.tree.column("file",width=210,minwidth=140,stretch=True)
        self.tree.heading("preview",text="CASE NUMBER THUMBNAIL")
        self.tree.column("preview",width=230,minwidth=210,stretch=False,anchor="center")
        self._set_sortable_heading("scan", self.tree, "candidate", "CASE NUMBER")
        self.tree.column("candidate", width=135, minwidth=110, stretch=False, anchor="center")
        self.tree.heading("approve", text="APPROVE")
        self.tree.column("approve", width=60, minwidth=60, stretch=False, anchor="center")
        self.tree.heading("review", text="REVIEW")
        self.tree.column("review", width=58, minwidth=58, stretch=False, anchor="center")
        self._set_sortable_heading("scan", self.tree, "score", "EASY / TrOCR")
        self.tree.column("score", width=135, minwidth=120, stretch=False, anchor="center")
        self._set_sortable_heading("scan", self.tree, "workflow", "STATUS")
        self.tree.column("workflow",width=114,minwidth=98,stretch=False,anchor="center")
        self._set_sortable_heading("scan", self.tree, "status", "OCR DETAILS")
        self.tree.column("status", width=220, minwidth=120, stretch=True)
        # Make OCR-generated case-number suggestions visible in the main queue.
        self.tree["displaycolumns"] = ("scan_select", "file", "preview", "candidate", "approve", "review", "score", "workflow", "status")
        self.tree.tag_configure("scanning_now",background="#fff2d5")
        sy = ttk.Scrollbar(pane, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=lambda first,last:(
             sy.set(first,last),self._schedule_scan_preview_refresh()))
        self.tree.pack(side="left", fill="both", expand=True)
        sy.pack(side="right", fill="y")
        self.tree.bind("<Button-1>", self._on_scan_checkbox_click)
        self.tree.bind("<Double-1>", self._unified_double_click)
        self.tree.bind("<<TreeviewSelect>>", self._sync_scan_selection)
        self.tree.bind("<Configure>",lambda e:self._schedule_scan_preview_refresh(),add="+")
        self.tree.bind("<KeyRelease>",lambda e:self._schedule_scan_preview_refresh(),add="+")
        # Ctrl+A works from the list or its surrounding controls in SELECT FILES.
        self.root.bind("<Control-a>", self._select_all_for_rescan, add="+")
        self.tree.bind("<Control-a>", self._select_all_for_rescan)
        self.tree.bind("<Control-A>", self._select_all_for_rescan)

        # The scan progressbar is always visible above the list.
        self.status = _MFCaseCapsLabel(self.lower_panel, text="SELECT A FOLDER, THEN CLICK SCAN.")
        self.status.pack(anchor="w")

        # Internal status labels remain available to the existing diagnostics,
        # but are not placed on the coworker screen.
        self.learning_stats_label = ttk.Label(self.settings_tab, text="")
        self.model_status_label = ttk.Label(self.settings_tab, text="")
        self.learning_type_stats_label = ttk.Label(self.settings_tab, text="")
        self.auto_eligibility_label = ttk.Label(self.settings_tab, text="")
        self.learning_stats_label.pack(anchor="w",pady=5)
        self.model_status_label.pack(anchor="w",pady=5)

        completed_header = ttk.Frame(self.completed_tab)
        completed_header.pack(fill="x", pady=(0, 5))
        ttk.Label(completed_header, text="READY FOR REVIEW / EXPORT",
                  font=("Segoe UI", 12, "bold")).pack(side="left")
        self.export_btn = ttk.Button(
            completed_header, text="SEND FILES", command=self.export, state="disabled",
            style="MFCase.Action.TButton")
        self.export_btn.pack(side="right", padx=(8, 0))
        self.completed_refresh_btn = ttk.Button(
            completed_header, text="REFRESH", command=self.refresh_completed_list,
            style="MFCase.Action.TButton")
        self.completed_refresh_btn.pack(side="right", padx=(8, 0))
        ttk.Label(
            self.completed_tab,
            text="ENTER A CORRECTED CASE NUMBER IN CHANGE AND CLICK SAVE, THEN SEND FILES. "
                 "APPROVED FILES REMAIN AVAILABLE FOR CORRECTION. "
                 "SEND FILES MOVES APPROVED FILES TO COMPLETED; REVIEW FILES REMAIN.",
            wraplength=1100).pack(anchor="w", pady=(0, 5))
        cpane = ttk.Frame(self.completed_tab)
        cpane.pack(fill="both", expand=True)
        ccols = ("case", "edit_case", "file", "original_path", "status", "unverify", "select")
        style = ttk.Style(self.root)
        try:
            one_inch = max(72, int(self.root.winfo_fpixels("1i")))
            extra_half_inch = int(self.root.winfo_fpixels("0.5i"))
        except Exception:
            one_inch = 80
            extra_half_inch = 48
        style.configure("Completed.Treeview", rowheight=one_inch)
        self.completed_tree = ttk.Treeview(
            cpane, columns=ccols, show=("tree", "headings"),
            selectmode="extended", style="Completed.Treeview")
        self.completed_tree.heading("#0", text="IMAGE")
        image_width = 210 + extra_half_inch
        self.completed_tree.column("#0", width=image_width, minwidth=image_width,
                                   stretch=False, anchor="center")
        for col, title, width, min_width in [
            ("case", "CASE #", 130, 105),
            ("edit_case", "CHANGE", 210, 185),
            ("file", "ORIGINAL NAME", 240, 160),
            ("original_path", "ORIGINAL PATH", 220, 120),
            ("status", "QUEUE STATUS", 185, 140),
            ("unverify", "UNVERIFY", 94, 82),
            ("select", "☐ SELECT ALL", 112, 96)]:
            if col in ("case", "file", "original_path", "status"):
                self._set_sortable_heading("completed", self.completed_tree, col, title)
            else:
                self.completed_tree.heading(col, text=title)
            self.completed_tree.column(col, width=width, minwidth=min_width,
                                       anchor="center", stretch=col in ("file", "original_path", "status"))
        self.completed_tree.heading("select", command=self._toggle_completed_select_all)
        self.completed_tree.bind("<Button-1>", self._on_completed_click)
        self.completed_tree.bind("<Double-1>", self.on_completed_double_click)
        self.completed_tree.bind("<<TreeviewSelect>>", lambda _event: self._update_completed_count())
        self.completed_tree.bind("<MouseWheel>", self._completed_mousewheel)
        self.completed_tree.bind("<Button-4>", lambda e: self._scroll_completed(-3))
        self.completed_tree.bind("<Button-5>", lambda e: self._scroll_completed(3))
        self.completed_tree.bind("<Configure>", lambda _e: self._schedule_completed_layout())
        csy = ttk.Scrollbar(cpane, orient="vertical", command=self._completed_scrollbar_command)
        self.completed_scrollbar = csy
        self.completed_tree.configure(yscrollcommand=self._completed_scrollbar_update)
        self.completed_tree.pack(side="left", fill="both", expand=True)
        csy.pack(side="right", fill="y")
        # Keep the rightmost selection column accessible on narrower monitors.
        self.completed_xscrollbar = ttk.Scrollbar(
            self.completed_tab, orient="horizontal", command=self._completed_xscroll_command)
        self.completed_tree.configure(xscrollcommand=self._completed_xscrollbar_update)
        self.completed_xscrollbar.pack(fill="x")
        cbar = ttk.Frame(self.completed_tab)
        cbar.pack(fill="x", pady=(10, 0))
        self.completed_count = ttk.Label(cbar, text="0 FILES READY TO SEND")
        self.completed_count.pack(side="left")
        self.review_completed_btn = ttk.Button(
            cbar, text="REVIEW / APPROVE SELECTED", command=self.review_completed_selected, state="disabled")
        # Review selected is removed; double-click any row to open review.
        self.unverify_selected_btn = ttk.Button(
            cbar, text="UNVERIFY SELECTED", command=self._unverify_completed_selected,
            state="disabled",style="MFCase.Action.TButton")
        self.unverify_selected_btn.pack(side="right", padx=(0, 8))
        # Transfer outcomes must be visible while the user is on COMPLETED;
        # the generic status label lives on a different tab.
        self.completed_send_status = _MFCaseCapsLabel(
            self.completed_tab, text="APPROVED FILES ARE READY FOR SEND FILES.",
            wraplength=1100)
        self.completed_send_status.pack(anchor="w", pady=(5, 0))
        # The SETTINGS widget is an unplaced parent for internal status labels.
        # Users see four options in a compact pop-out instead.
        self._show_main_view("select")

    def _show_main_view(self, view_name="select"):
        """Always show the single combined list, even for legacy callbacks."""
        self._active_main_view="select"
        self.review_tab.lift()

    def _background_learning_active(self):
        return bool(self.trocr_training and getattr(self,"_learning_auto_run",False))

    def _training_blocks_work(self):
        """Only deliberate/manual training may block a competing task."""
        return bool(self.trocr_training and not getattr(self,"_learning_auto_run",False))

    def _spin_learning_indicator(self):
        self._learning_spinner_job=None
        if not getattr(self,'_learning_indicator_active',False):return
        canvas=self.learning_spinner
        position=(getattr(self,'_learning_spinner_position',0)+4)%82
        self._learning_spinner_position=position
        canvas.delete('all')
        canvas.create_rectangle(1,1,76,13,outline='#9fbea6',fill='#e7f0e7')
        x=2+min(position,82-position)*1.55
        canvas.create_rectangle(x,2,x+13,12,fill='#168447',outline='#0d6531')
        self.learning_activity_label.config(text='LEARNING')
        self._learning_spinner_job=self.root.after(65,self._spin_learning_indicator)

    def _set_background_learning_indicator(self,enabled):
        """Animate only while the existing local learning worker is running."""
        frame=getattr(self,'learning_activity',None)
        if frame is None:return
        self._learning_indicator_active=bool(enabled)
        job=getattr(self,'_learning_spinner_job',None)
        if job is not None:
            self.root.after_cancel(job)
            self._learning_spinner_job=None
        if enabled:
            if not frame.winfo_manager():frame.pack(side='right',padx=8)
            self._spin_learning_indicator()
        else:frame.pack_forget()

    def _yield_background_learning(self, reason="user work"):
        """Let selection/scanning/sending proceed; abort idle fine-tuning safely.

        Training uses a private model instance and checks stop_event between
        steps. It must not tear down the OCR model currently used by scanning.
        Any unfinished examples remain in local learning and can be retried.
        """
        if not self._background_learning_active():
            return False
        self._auto_learning_interrupted=True
        self.learning_stop_event.set()
        self.learning_pause_event.set()
        # Keep the spinner visible until the worker reports completion.
        self._log("Background learning yielding to %s; saved examples retained",reason)
        return True

    def _open_settings_window(self):
        existing=getattr(self,"_settings_popup",None)
        if existing is not None and existing.winfo_exists():
            existing.lift();existing.focus_force();return
        win=tk.Toplevel(self.root)
        self._settings_popup=win
        win.title(f"MFCASE SETTINGS — V{self.app_version}")
        win.transient(self.root)
        win.resizable(False,False)
        win.geometry("480x420")
        panel=ttk.Frame(win,padding=20)
        panel.pack(fill="both",expand=True)
        ttk.Label(panel,text=f"SETTINGS  •  V{self.app_version}",
                  font=("Segoe UI",15,"bold")).pack(pady=(0,14))
        for label,func in (
            ("USER GUIDE",self._open_instructions),
            ("EXPORT WORK LOG",self._diag_export),
            ("GITHUB SETTINGS",self._configure_github_logs),
            ("UPDATE",lambda: self.check_for_updates(automatic=False)),
            ("UPDATE LEARNING",self._finish_import_batch)):
            self._action_button(panel,'settings:'+label,label,func).pack(fill='x',pady=5)
        ttk.Separator(panel).pack(fill="x",pady=(12,6))
        ttk.Checkbutton(panel,text="AUTO SEND COMPLETED — AFTER SCAN FINISHES",
            variable=self.auto_send_var,command=self._save_user_settings).pack(anchor="w",pady=5)
        ttk.Label(panel,text="OFF BY DEFAULT. SENDS ONLY APPROVED FILES AFTER BEFORE/AFTER CHECKS. "
            "STOP NEVER TRIGGERS AUTO SEND. PDFS AND FULL LOGS STAY LOCAL; "
            "CONFIGURED SANITIZED DIAGNOSTICS MAY UPLOAD AFTER SCANS.",
            wraplength=425,justify="left").pack(anchor="w",pady=(4,0))

    def _network_online_passive(self):
        """Passive OS check, never a network probe or claim of zero data leakage."""
        if os.name=="nt":
            try:
                import ctypes
                flags=ctypes.c_ulong(0)
                return bool(ctypes.windll.wininet.InternetGetConnectedState(
                    ctypes.byref(flags),0))
            except Exception:
                return True  # Fail closed: do not declare safe without evidence.
        try:
            import psutil
            return any(name.lower() not in ("lo","loopback") and status.isup
                       for name,status in psutil.net_if_stats().items())
        except Exception:
            return True


    def _startup_notice(self, text):
        label = getattr(self, "_startup_phase_label", None)
        if label is not None:
            try:
                if label.winfo_exists():
                    label.configure(text=text)
            except tk.TclError:
                pass

    def _close_startup_notice(self):
        dialog = self._startup_dialog
        self._startup_dialog = None
        self._startup_phase_label = None
        bar = self._startup_progressbar
        self._startup_progressbar = None
        if bar is not None:
            try:
                bar.stop()
            except tk.TclError:
                pass
        if dialog is not None:
            try:
                dialog.destroy()
            except tk.TclError:
                pass


    def _begin_offline_startup(self):
        """Initialize from user-local disks. No HTTP or SMB is accessed."""
        if self._startup_ready or self._loading_jobs:
            return
        try:
            self._startup_learning_pending=False
            directory=Path(os.environ.get("LOCALAPPDATA") or
                           str(Path.home()/".local"/"share"))/"MFCase"/"learning"
            directory.mkdir(parents=True,exist_ok=True)
            self.learning_dir=directory
            self._load_learning_data(refresh_ui=True)
            self._diag_event("offline_startup",
                model_downloads_allowed=False,software_updates_automatic=False,
                learning_updates_automatic=False,diagnostic_sync=False)
            self.status.config(text="OFFLINE STARTUP: LOADING CACHED OCR MODELS. "
                 "No internet or shared drive connection is required.")
            self._begin_model_preload()
        except Exception as exc:
            self._fail_startup(str(exc))

    def _begin_startup_updates(self):
        """Legacy entry point is offline-only; no background update checks."""
        self._begin_offline_startup()

    def _retry_startup(self):
        if self._startup_ready or self.update_busy or self._loading_jobs:
            return
        self._startup_error = None
        self._startup_learning_pending = False
        self._offline_prepare_busy = False
        self._auto_learning_started = False
        self.learning_dir = None
        self.reader = None
        self.trocr_processor = None
        self.trocr_model = None
        self.trocr_error = ""
        self._startup_retry_btn.config(state="disabled")
        self._startup_learning_check()

    def _startup_learning_check(self):
        """Load shared learning on launch; train pending changes only while idle.

        Training and scanning are deliberately serialized: both use the OCR
        model/checkpoints, and concurrent runs can corrupt validation or exhaust
        RAM. The automatic training thread is limited to two CPU workers.
        """
        if self._learning_connection_source == "startup":
            return
        self._startup_notice("Connecting to shared learning; the initial load may take a while…")
        self._learning_connection_source = "startup"
        self._loader_start("learning", "Loading shared learning…")
        learning = Path(r"\\172.16.1.231\MicroficheNew\2008") / ".case_ocr_learning"
        threading.Thread(target=self._learning_connect_worker,
                         args=(learning, None), name="MFCase-StartupLearning", daemon=True).start()

    def _startup_software_complete(self,error=None):
        self._startup_auto_update=False
        self._loader_finish("startup")
        if not self._startup_ready and not self._loading_jobs:
            self._begin_offline_startup()

    def _start_automatic_learning_if_ready(self):
        """Run pending local training only while idle."""
        if getattr(self,"_stop_all_requested",False):return
        if not self._startup_ready:
            return False
        if self.busy or self.export_busy or self.trocr_training:
            # Do not permanently lose automatic learning when someone starts
            # a scan before the idle-learning timer fires.
            if self.busy or self.export_busy:
                self.root.after(30000,self._start_automatic_learning_if_ready)
            return False
        self._startup_learning_pending=False
        try:
            pending=self._pending_learning_examples()
            if not pending:
                return False
            # v5.6.18: never fine-tune while idle; UPDATE LEARNING is explicit.
            return False
        except Exception as exc:
            self._log("Automatic learning skipped: %s",exc)
            return False

    def _read_update_config(self):
        try:
            cfg = json.loads(self.update_config_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError(f"Update configuration could not be read: {exc}")
        repo = str(cfg.get("repository", "")).strip()
        asset_name = str(cfg.get("asset_name", "CaseNumberOCR_Update.zip")).strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise RuntimeError(
                "GitHub updater is not configured yet. Create the dedicated CaseNumberOCR-Updates repository, "
                "then set 'repository' in update_config.json to OWNER/REPOSITORY.")
        return repo, asset_name

    @staticmethod
    def _version_tuple(value):
        parts = re.findall(r"\d+", str(value))
        return tuple(int(part) for part in parts[:4]) if parts else (0,)

    def check_for_updates(self, automatic=False):
        if self.update_busy or (automatic and (self.busy or self.export_busy or self.trocr_training)):
            return
        self._startup_auto_update = bool(automatic)
        try:
            repo, asset_name = self._read_update_config()
        except Exception as exc:
            if automatic:
                self._startup_software_complete(str(exc))
            else:
                messagebox.showinfo("Updater setup", str(exc), parent=self.root)
            return
        self.update_busy = True
        self.update_btn.config(state="disabled", text="CHECKING…")
        threading.Thread(
            target=self._update_check_worker,
            args=(repo, asset_name), daemon=True).start()

    def _update_check_worker(self, repo, asset_name):
        try:
            url = f"https://api.github.com/repos/{repo}/releases/latest"
            request = urllib.request.Request(url, headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "MF-CaseNumberOCR-Updater/5.0.10"})
            with urllib.request.urlopen(request, timeout=25) as response:
                release = json.loads(response.read().decode("utf-8"))
            tag = str(release.get("tag_name") or release.get("name") or "")
            assets = release.get("assets") or []
            asset = next((item for item in assets if item.get("name") == asset_name), None)
            if self._version_tuple(tag) <= self._version_tuple(self.app_version):
                self.queue.put(("update_check_result", tag, None, None))
                return
            if not asset:
                raise RuntimeError(
                    f"GitHub has release {tag}, but it does not contain the required update asset '{asset_name}'. "
                    "Attach the update ZIP to the published release and try again.")
            payload = {
                "version": tag,
                "download_url": asset.get("browser_download_url"),
                "digest": asset.get("digest"),
                "asset_name": asset_name,
                "html_url": release.get("html_url", "")}
            if not payload["download_url"]:
                raise RuntimeError("The GitHub release asset has no download URL.")
            self.queue.put(("update_check_result", tag, payload, None))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                detail = "GitHub repository or latest release was not found. Create the dedicated repository and publish a release first."
            else:
                detail = f"GitHub returned HTTP {exc.code}: {exc.reason}"
            self.queue.put(("update_check_result", "", None, detail))
        except Exception as exc:
            self.queue.put(("update_check_result", "", None, str(exc)))

    def _download_update(self, release):
        if self.update_busy:
            return
        self.update_busy = True
        self.update_btn.config(state="disabled", text="DOWNLOADING…")
        threading.Thread(target=self._update_download_worker, args=(release,), daemon=True).start()

    def _update_download_worker(self, release):
        try:
            temp_path = Path(tempfile.gettempdir()) / f"MF_CaseNumberOCR_Update_{os.getpid()}.zip"
            request = urllib.request.Request(
                release["download_url"],
                headers={"User-Agent": "MF-CaseNumberOCR-Updater/5.0.10"})
            digest = hashlib.sha256()
            with urllib.request.urlopen(request, timeout=90) as response, temp_path.open("wb") as output:
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    output.write(block)
                    digest.update(block)
            actual_digest = digest.hexdigest()
            expected = str(release.get("digest") or "")
            if not re.fullmatch(r"sha256:[0-9a-fA-F]{64}",expected):
                raise RuntimeError("No SHA-256 digest in the GitHub release. Update blocked.")
            if actual_digest.lower() != expected.split(":", 1)[1].lower():
                raise RuntimeError("The downloaded update failed GitHub's SHA-256 digest check. No files were changed.")
            with zipfile.ZipFile(temp_path, "r") as archive:
                bad = archive.testzip()
                if bad:
                    raise RuntimeError(f"The update ZIP failed its integrity check at {bad}.")
                allowed = {"case_number_ocr.py", "updater_apply.py", "RUN_CASE_OCR.bat", "MF.ico", "Launch_Case_OCR.vbs", "README.txt", "README_FIRST.txt", "SETUP.bat", "verify_package.py", "update_config.json", "CREATE_DESKTOP_SHORTCUT.bat", "Create_Desktop_Shortcut.vbs", "RUN_CASE_OCR_DEBUG.bat", "CHANGELOG_V5_0_2.txt", "CHANGELOG_V5_0_3.txt", "CHANGELOG_V5_0_4.txt"}
                found = {Path(name).name for name in archive.namelist() if not name.endswith("/")}
                if not {"case_number_ocr.py", "updater_apply.py"}.issubset(found):
                    raise RuntimeError("The update ZIP does not contain the expected application files.")
                for name in archive.namelist():
                    if name.endswith("/"):
                        continue
                    path = Path(name)
                    if (len(path.parts) != 1 or path.name != name or
                            (path.name not in allowed and not re.fullmatch(
                                r"CHANGELOG_V[0-9]+(?:_[0-9]+){1,3}\.txt",path.name))):
                        raise RuntimeError(f"The update ZIP contains an unexpected path: {name}")
            self.queue.put(("update_download_ready", str(temp_path), release.get("version", "new version")))
        except Exception as exc:
            self.queue.put(("update_error", str(exc)))

    def _launch_update_installer(self, archive_path):
        if self._background_learning_active():
            self._yield_background_learning("software update")
        if self.busy or self.export_busy or self.trocr_training:
            self.status.config(text="UPDATE VERIFIED; INSTALLATION DEFERRED UNTIL WORK IS IDLE.")
            self.root.after(3000,lambda path=archive_path:self._launch_update_installer(path))
            return
        helper = self.app_dir / "updater_apply.py"
        if not helper.is_file():
            if self._startup_auto_update:
                self._startup_software_complete("updater_apply.py is missing")
            else:
                messagebox.showerror("Updater unavailable", "updater_apply.py is missing from the application folder.")
            return
        try:
            staged_helper = Path(tempfile.gettempdir()) / f"mf_case_ocr_apply_{os.getpid()}.py"
            shutil.copy2(helper, staged_helper)
            flags = 0
            if os.name == "nt":
                flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
            self._log("Launching external updater for %s from %s", archive_path, self.app_version)
            subprocess.Popen([
                sys.executable, str(staged_helper),
                "--pid", str(os.getpid()),
                "--app-dir", str(self.app_dir),
                "--zip", str(archive_path)],
                cwd=str(self.app_dir), creationflags=flags,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.root.destroy()
        except Exception as exc:
            if self._startup_auto_update:
                self._startup_software_complete(str(exc))
            else:
                messagebox.showerror("Could not start updater", str(exc), parent=self.root)

    @staticmethod
    def _natural_pdf_sort_key(path):
        """Sort filenames so Report 2 comes before Report 10 for sequence review."""
        return tuple((0, int(part)) if part.isdigit() else (1, part.casefold())
                     for part in re.split(r"(\d+)", Path(path).name))

    def _maximize_window(self):
        """Open the application in the operating system's maximized state."""
        try:
            self.root.state("zoomed")
        except tk.TclError:
            # Fallback for Tk builds/platforms that do not expose the Windows
            # zoomed state. Keep the regular window decorations enabled.
            try:
                width = self.root.winfo_screenwidth()
                height = self.root.winfo_screenheight()
                self.root.geometry(f"{width}x{height}+0+0")
            except tk.TclError:
                pass

    def _update_scan_file_count(self):
        label = getattr(self, "scan_file_count_label", None)
        tree = getattr(self, "tree", None)
        if label is None or tree is None:
            return
        try:
            visible = set(tree.get_children(""))
            checked = len(visible & self.scan_checked_iids)
            label.config(text=f"{checked:,} OF {len(visible):,} SELECTED")
            toggle=getattr(self,"ui_select_all_var",None)
            if toggle is not None:toggle.set(bool(visible) and visible.issubset(self.scan_checked_iids))
            footer=getattr(self,"scan_displayed_count_label",None)
            if footer is not None:
                footer.config(text=f"{len(visible):,} FILES DISPLAYED • {len(self.records):,} IN FOLDER • "
                              f"{sum(1 for r in self.records.values() if r.get('status')=='Not scanned' and not r.get('exported')):,} PENDING • "
                              f"{sum(1 for r in self.records.values() if r.get('confirmed') and r.get('manual_approved') and not r.get('exported')):,} COMPLETED")
            self._refresh_scan_checkbox_heading()
        except (tk.TclError, AttributeError):
            pass


    @staticmethod
    def _valid_ocr_box_candidates(record):
        """Only a real, complete 08- OCR hit can authorize a red/selectable box.

        Unverified historic snippets and unlocated model guesses may be retained
        as diagnostic data but never appear as candidate boxes in the UI.
        """
        snippet = record.get("snippet") if record else None
        if not snippet and record and record.get("preview_meta"):
            meta=record["preview_meta"]
            snippet=(meta.get("page_num",0),None,meta.get("label",""),meta.get("rect"),meta.get("candidates",[]))
        if not snippet or len(snippet) < 5 or not isinstance(snippet[4], list):
            return []
        valid = []
        for hit in snippet[4]:
            if not isinstance(hit, dict):
                continue
            number = normalize_case_number(hit.get("number"))
            rect = hit.get("rect")
            # A TrOCR/strip guess can say 08- even when the boxed handwriting
            # is only a suffix (e.g. "234"). Never draw that unproven box.
            if (not hit.get("box_verified") or not CASE_RE.fullmatch(number)
                    or not isinstance(rect, (list, tuple)) or len(rect) != 4):
                continue
            try:
                x0, y0, x1, y1 = map(float, rect)
            except (ValueError, TypeError):
                continue
            if not (all(math.isfinite(v) for v in (x0,y0,x1,y1)) and
                    x0 >= 0 and y0 >= 0 and x1 > x0 and y1 > y0):
                continue
            if snippet[1] is not None and hasattr(snippet[1], "shape"):
                h,w=snippet[1].shape[:2]
                if x1>w+4 or y1>h+4:
                    continue
            valid.append(hit)
        # Multiple OCR passes may report overlapping versions of the same
        # printed field. Display ONE compact, trustworthy box per location.
        preferred = _case_identity(record.get("confirmed") or record.get("candidate"))
        def rank(hit):
            x0,y0,x1,y1=map(float,hit["rect"])
            area=(x1-x0)*(y1-y0)
            return (_case_identity(hit.get("number"))==preferred,
                    float(hit.get("score") or 0),-area)
        kept=[]
        for hit in sorted(valid,key=rank,reverse=True):
            x0,y0,x1,y1=map(float,hit["rect"])
            duplicate=False
            for old in kept:
                a0,b0,a1,b1=map(float,old["rect"])
                overlap=max(0,min(x1,a1)-max(x0,a0))*max(0,min(y1,b1)-max(y0,b0))
                smaller=min((x1-x0)*(y1-y0),(a1-a0)*(b1-b0))
                if smaller>0 and overlap/smaller>=0.55:
                    duplicate=True
                    break
            if not duplicate:
                kept.append(hit)
        return kept

    def _sort_table_column(self, table_name, column):
        """Sort the visible table without changing the before/after scan order."""
        tree = self.tree if table_name == "scan" else self.completed_tree
        previous = self._table_sort_state.get(table_name)
        reverse = bool(previous and previous[0] == column and not previous[1])
        self._table_sort_state[table_name] = (column, reverse)
        self._apply_table_sort(table_name)

    def _apply_table_sort(self, table_name):
        tree = self.tree if table_name == "scan" else self.completed_tree
        state = self._table_sort_state.get(table_name)
        if not state:
            return
        column, reverse = state
        ids = list(tree.get_children(""))
        def row_key(iid):
            raw = str(tree.set(iid, column) or "")
            if column == "score":
                match = re.match(r"\s*(\d+(?:\.\d+)?)%", raw)
                if match:
                    return (0, float(match.group(1)))
                return (1, 0)
            if not raw or raw in ("—", "☐", "☑"):
                return (1, ())
            return (0, self._natural_pdf_sort_key(raw))
        for index, iid in enumerate(sorted(ids, key=row_key, reverse=reverse)):
            tree.move(iid, "", index)
        for col, title in self._table_column_titles.get(table_name, {}).items():
            tree.heading(col, text=title + (
                (" ▼" if reverse else " ▲") if col == column else ""))
        if table_name == "scan":
            self._schedule_scan_preview_refresh()
        else:
            self.root.after_idle(self._position_completed_unverify_buttons)

    def _set_sortable_heading(self, table_name, tree, column, title):
        self._table_column_titles.setdefault(table_name, {})[column] = title
        tree.heading(column, text=title,
                     command=lambda table_name=table_name, column=column:
                         self._sort_table_column(table_name, column))

    @staticmethod
    def _workflow_status(record):
        if record.get("exported"):
            return "RENAMED"
        if record.get("confirmed") and record.get("manual_approved"):
            return "COMPLETED"
        if record.get("status")=="Not scanned":
            return "NOT SCANNED"
        return "REVIEW"


    def _scan_preview_cache_file(self, record):
        import hashlib as _hashlib
        base=Path(os.getenv("LOCALAPPDATA") or str(self.app_dir))/"MFCase"/"preview_cache"
        name=str(record.get("path") or "")
        try:
            stamp=Path(name).stat().st_mtime_ns
        except OSError:
            stamp=0
        key=(name,stamp,str(record.get("candidate") or ""),str(record.get("confirmed") or ""))
        token=_hashlib.sha256(repr(key).encode("utf-8")).hexdigest()[:25]
        return base/(token+".png")

    def _make_scan_preview(self,iid,record):
        from PIL import Image,ImageDraw,ImageFont,ImageTk
        cache_path=self._scan_preview_cache_file(record)
        if not record.get("snippet") and record.get("preview_meta") and not cache_path.is_file():
            self._ensure_preview(record)
        snippet=record.get("snippet")
        existing=self._scan_preview_keys.get(iid)
        key=(str(cache_path),str(record.get("preview_meta")),record.get("confirmed"),record.get("candidate"),str(record.get("manual_crop_rect")),record.get("sequence_inferred"))
        if existing==key and iid in self._scan_preview_photos:
            return self._scan_preview_photos[iid]
        tile=None
        if snippet and len(snippet)>1 and snippet[1] is not None:
            try:
                page=Image.fromarray(snippet[1]).convert("RGB")
                candidates=self._valid_ocr_box_candidates(record)
                case=str(record.get("confirmed") or record.get("candidate") or "")
                def same_case(value):
                    try:return _case_identity(value)==_case_identity(case) and _case_identity(case) is not None
                    except (ValueError,TypeError):return False
                chosen=next((h for h in candidates if h.get("rect") and same_case(h.get("number"))),None)
                rect=(record.get("manual_crop_rect") if record.get("manual_crop_origin")=="drawn"
                      else chosen.get("rect") if chosen else None)
                # A bare snippet rect cannot authorize a box: it must belong
                # to an actual, valid 08- OCR candidate for this case.
                if not rect and not record.get("sequence_inferred"):
                    match=next((h for h in candidates if same_case(h.get("number"))),None)
                    rect=match.get("rect") if match else None
                if rect:
                    x0,y0,x1,y1=map(int,map(float,rect))
                    w=max(10,x1-x0);h=max(10,y1-y0)
                    # Include the label to the LEFT of the number.
                    left=max(3,int(w*.03))
                    box=(max(0,x0-left),max(0,y0-max(11,int(h*.4))),
                         min(page.width,x1+max(12,int(w*.14))),
                         min(page.height,y1+max(11,int(h*.4))))
                else:
                    raise ValueError("No verified 08- number crop; manual review required")
                crop=page.crop(box)
                crop.thumbnail((216,64),Image.Resampling.LANCZOS)
                tile=Image.new("RGB",(222,68),"white")
                tile.paste(crop,((222-crop.width)//2,(68-crop.height)//2))
            except Exception as exc:
                self._log("Case preview crop failed: %s",exc)
        if tile is None and cache_path.is_file() and not snippet and CASE_RE.fullmatch(str(record.get("candidate") or "")):
            try:
                tile=Image.open(cache_path).convert("RGB")
                tile.thumbnail((222,68),Image.Resampling.LANCZOS)
            except Exception:
                tile=None
        if tile is None:
            tile=Image.new("RGB",(222,68),"white")
            draw=ImageDraw.Draw(tile)
            msg="No 08- crop — review" if not record.get("candidate") else "08- crop unavailable"
            draw.text((9,25),msg,fill="#555555")
        ImageDraw.Draw(tile).rectangle((0,0,221,67),outline="#999999",width=1)
        if snippet and snippet[1] is not None:
            try:
                cache_path.parent.mkdir(parents=True,exist_ok=True)
                tile.save(cache_path)
            except OSError:
                pass
        photo=ImageTk.PhotoImage(tile)
        self._scan_preview_photos[iid]=photo
        self._scan_preview_keys[iid]=key
        self._release_page_pixels(record)
        return photo

    def _schedule_scan_preview_refresh(self):
        if not hasattr(self,"tree") or self._scan_preview_refresh_id is not None:
            return
        try:
            self._scan_preview_refresh_id=self.root.after_idle(self._refresh_scan_previews)
        except tk.TclError:
            self._scan_preview_refresh_id=None

    def _refresh_scan_previews(self):
        self._scan_preview_refresh_id=None
        if not hasattr(self,"tree") or not self.tree.winfo_exists():
            return
        visible=set()
        height=max(self.tree.winfo_height(),75)
        for y in range(0,height+74,36):
            iid=self.tree.identify_row(y)
            if iid and iid in self.records and iid not in visible:
                visible.add(iid)
                bbox=self.tree.bbox(iid,"preview")
                if not bbox:
                    continue
                x,top,w,h=bbox
                button=self._scan_approve_widgets.get(iid)
                if button is None:
                    button=tk.Button(self.tree,text="✓",bg="#188038",fg="white",
                        activebackground="#116329",activeforeground="white",
                        font=("Segoe UI",15,"bold"),relief="flat",cursor="hand2",
                        command=lambda key=iid:self._quick_approve(key))
                    self._scan_approve_widgets[iid]=button
                accept_box=self.tree.bbox(iid,"approve")
                if accept_box:
                    ax,ay,aw,ah=accept_box
                    button.configure(state="disabled" if self._scan_row_locked(iid) else "normal")
                    button.place(x=ax+(aw-34)//2,y=ay+(ah-32)//2,width=34,height=32)
                review_button=self._scan_review_widgets.get(iid)
                if review_button is None:
                    review_button=tk.Button(self.tree,text="R",bg="#1456a8",fg="white",
                        activebackground="#0d4388",activeforeground="white",
                        font=("Segoe UI",15,"bold"),relief="flat",cursor="hand2",
                        command=lambda key=iid:self._open_review_iid(key))
                    self._scan_review_widgets[iid]=review_button
                review_box=self.tree.bbox(iid,"review")
                if review_box:
                    rx,ry,rw,rh=review_box
                    review_button.configure(state="disabled" if self._scan_row_locked(iid) else "normal")
                    review_button.place(x=rx+(rw-34)//2,y=ry+(rh-32)//2,width=34,height=32)
                self._place_scan_case_entry(iid)
                photo=self._make_scan_preview(iid,self.records[iid])
                widget=self._scan_preview_widgets.get(iid)
                if widget is None:
                    widget=tk.Label(self.tree,borderwidth=0,highlightthickness=0,
                                    cursor="hand2")
                    widget.bind("<Button-1>",lambda e,key=iid:self._select_preview_row(key,e))
                    widget.bind("<Double-1>",lambda e,key=iid:self._open_review_iid(key))
                    self._scan_preview_widgets[iid]=widget
                widget.configure(image=photo)
                width=min(222,max(0,w-6));image_height=min(68,max(0,h-4))
                if width>0 and image_height>0:
                    widget.place(x=x+(w-width)//2,y=top+(h-image_height)//2,
                                 width=width,height=image_height)
        for iid,widget in list(self._scan_preview_widgets.items()):
            if iid not in visible or not self.tree.exists(iid):
                try:widget.place_forget()
                except tk.TclError:pass
            if not self.tree.exists(iid):
                try:widget.destroy()
                except tk.TclError:pass
                self._scan_preview_widgets.pop(iid,None)
                self._scan_preview_photos.pop(iid,None)
                self._scan_preview_keys.pop(iid,None)

        for iid,button in list(self._scan_review_widgets.items()):
            if iid not in visible or not self.tree.exists(iid):
                button.place_forget()
            if not self.tree.exists(iid):
                button.destroy()
                self._scan_review_widgets.pop(iid,None)

        for iid,button in list(self._scan_approve_widgets.items()):
            if iid not in visible or not self.tree.exists(iid):
                button.place_forget()
            if not self.tree.exists(iid):
                button.destroy()
                self._scan_approve_widgets.pop(iid,None)

        for iid,(entry,var) in list(self._scan_case_widgets.items()):
            if iid not in visible or not self.tree.exists(iid):
                entry.place_forget()
            if not self.tree.exists(iid):
                entry.destroy()
                self._scan_case_widgets.pop(iid,None)

    def _place_scan_case_entry(self, iid):
        bounds=self.tree.bbox(iid,"candidate")
        if not bounds:
            return
        record=self.records[iid]
        value=(record.get("case_draft","") if record.get("case_draft") or record.get("sequence_excluded")
               else record.get("confirmed") or record.get("candidate") or "")
        pair=self._scan_case_widgets.get(iid)
        if pair is None:
            var=tk.StringVar(master=self.tree,value=value)
            entry=ttk.Entry(self.tree,textvariable=var,font=("Segoe UI",11))
            self._scan_case_widgets[iid]=(entry,var)
            var.trace_add("write",lambda *_args,key=iid:self._inline_case_changed(key))
            entry.bind("<Return>",lambda _event,key=iid:(self._quick_approve(key),"break")[1])
            entry.bind("<Double-1>",lambda event,key=iid:(self._open_review_iid(key),"break")[1])
            entry.bind("<FocusIn>",lambda _event:key_focus(iid))
            entry.bind("<FocusOut>",lambda _event:self._save_state())
            def key_focus(key):
                self._yield_background_learning("case-number edit")
                self.tree.focus(key)
        else:
            entry,var=pair
            if var.get()!=value:
                self._scan_case_sync=True
                try:var.set(value)
                finally:self._scan_case_sync=False
        disabled=self._scan_row_locked(iid)
        entry.configure(state="disabled" if disabled else "normal")
        x,y,w,h=bounds
        entry.place(x=x+2,y=y+(h-28)//2,width=max(20,w-4),height=28)

    def _inline_case_changed(self, iid):
        if self._scan_case_sync or iid not in self.records:
            return
        record=self.records[iid]
        if self._scan_row_locked(iid):
            return
        text=self._scan_case_widgets[iid][1].get()
        # Blank and invalid edits are retained as drafts, never an old approved value.
        record.update(case_draft=text,confirmed="",manual_approved=False,
                      auto_labeled=False,sequence_inferred=False,sequence_conflict=False,
                      sequence_excluded=True,status="Edited — click the green check to approve")
        if self.completed_tree.exists(iid):
            self.completed_tree.delete(iid)
        record["completed_iid"]=None
        self.tree.set(iid,"candidate",text)
        self.tree.set(iid,"workflow","REVIEW")
        self.tree.set(iid,"status",record["status"])
        self._update_completed_count()

    def _quick_approve(self, iid):
        if self._scan_row_locked(iid):
            self.status.config(text="THIS FILE IS BEING SCANNED, SENT OR IS NOT EDITABLE.")
            return
        self._yield_background_learning("file approval")
        record=self.records.get(iid)
        if not record or record.get("exported"):
            return
        value=(record.get("case_draft","") if record.get("case_draft") or record.get("sequence_excluded")
               else record.get("confirmed") or record.get("candidate") or "")
        value=normalize_case_number(value)
        if (not record.get("case_draft") and record.get("confirmed")==value
                and record.get("manual_approved") and not record.get("auto_labeled")
                and not record.get("learning_pending") and not record.get("sequence_conflict")):
            self.status.config(text=f"{record['path'].name} is already COMPLETED and ready to send.")
            self._insert_scan_row(iid,record)
            return  # Never duplicate learning or rename a previously approved file.
        if not CASE_RE.fullmatch(normalize_case_number(value)):
            self.status.config(text="ENTER A COMPLETE 08- CASE NUMBER BEFORE ACCEPTING.")
            self._start_scan_case_edit(iid)
            return
        class Value:
            def get(self): return value
        if self._save_completed_case_edit(iid,Value()):
            self._audit_event("CHECK_APPROVED",original_name=record.get("original_name") or record["path"].name,
                              confirmed=value)
            record.pop("case_draft",None)
            self._save_state()
            self._insert_scan_row(iid,record)

    def _save_scan_draft(self, iid, entry):
        record=self.records[iid]
        value=normalize_case_number(entry.get())
        if not CASE_RE.fullmatch(value):
            return False
        self._audit_event("DRAFT_SAVED",original_name=record.get("original_name") or record["path"].name,
                          proposed_case=value)
        record.update(case_draft=value,confirmed="",manual_approved=False,exported=False,
                      auto_labeled=False,sequence_inferred=False,sequence_conflict=False,
                      sequence_excluded=True,status="Edited — click the green check to approve")
        if self.completed_tree.exists(iid):
            self.completed_tree.delete(iid)
        record["completed_iid"]=None
        self._insert_scan_row(iid,record)
        self._update_completed_count()
        saved=self._save_state()
        if saved:
            self._recheck_after_decision()
        return saved

    def _select_preview_row(self,iid,event=None):
        if self.tree.exists(iid):
            rows=list(self.tree.get_children(""))
            anchor=getattr(self,"_scan_selection_anchor",self.tree.focus())
            state=getattr(event,"state",0)
            if state & 0x0001 and anchor in rows and iid in rows:
                lo,hi=sorted((rows.index(anchor),rows.index(iid)))
                self.tree.selection_set(rows[lo:hi+1])
            elif state & 0x0004:
                if iid in self.tree.selection(): self.tree.selection_remove(iid)
                else: self.tree.selection_add(iid)
                self._scan_selection_anchor=iid
            else:
                self.tree.selection_set(iid)
                self._scan_selection_anchor=iid
            self.tree.focus(iid)
            self.tree.focus_set()
            self._sync_scan_selection()
            self._refresh_review_controls()

    def _open_preview_review(self,iid):
        return self._open_review_iid(iid)

    def _show_in_scan_list(self, record):
        if record.get("exported") or record.get("status")=="Moved to BLANKS":
            return False
        option=getattr(self,"show_all_files_var",None)
        if option is not None and option.get():
            return True
        return (record.get("status")!="Not scanned" and
                (bool(record.get("case_draft")) or self._workflow_status(record)!="COMPLETED"
                 or bool(record.get("sequence_conflict")) or bool(record.get("export_error"))))

    def _toggle_full_list(self):
        self._rebuild_scan_list()
        self._refresh_list_toggle()

    def _rebuild_scan_list(self):
        # Batch all row changes; never sort/count the whole table per row.
        self._bulk_scan_rows=True
        try:
            for iid,record in self.records.items():
                self._insert_scan_row(iid,record)
        finally:
            self._bulk_scan_rows=False
        if self._table_sort_state.get("scan"):
            self._apply_table_sort("scan")
        self._update_scan_file_count()
        self._schedule_scan_preview_refresh()

    def _pending_scan_items(self, only_iids=None):
        """The scan queue is independent of visible rows and selection."""
        force=getattr(self,"_rescan_force_iids",set())
        return [(iid,rec) for iid,rec in self.records.items()
                if (only_iids is None or iid in only_iids)
                and (not rec.get("confirmed") or iid in force)
                and not rec.get("exported") and rec.get("status")!="Moved to BLANKS"
                and (iid in force or rec.get("status")=="Not scanned"
                     or str(rec.get("status","")).startswith("Scan error:"))
                and Path(rec["path"]).parent==Path(self.folder)]

    @staticmethod
    def _release_page_pixels(record):
        snippet=record.get("snippet")
        if snippet:
            record["preview_meta"]={"page_num":snippet[0],"label":snippet[2],
                "rect":list(snippet[3]) if snippet[3] else None,
                "candidates":snippet[4] if len(snippet)>4 else []}
            record["snippet"]=None

    def _insert_scan_row(self, iid, record, candidate_override=None, score_override=None):
        """Keep case number and PDF visible until explicit user transfer."""
        if not hasattr(self,"tree"):return
        if self.folder and Path(record["path"]).parent != Path(self.folder):
            if self.tree.exists(iid):
                self.tree.delete(iid)
            return
        if not self._show_in_scan_list(record):
            if self.tree.exists(iid):
                self.tree.delete(iid)
                self._schedule_scan_preview_refresh()
            if not getattr(self,"_bulk_scan_rows",False):
                self._update_scan_file_count()
            return
        proposed=(record.get("case_draft") or
                  (record.get("confirmed") if record.get("manual_approved") and record.get("confirmed")
                   else record.get("candidate", "")))
        if not CASE_RE.fullmatch(normalize_case_number(proposed)):
            proposed=""
        values=("☑" if iid in self.scan_checked_iids else "☐",
                record["path"].name,"",
                proposed if candidate_override is None else
                (candidate_override if CASE_RE.fullmatch(normalize_case_number(candidate_override)) else ""),
                "", "R",
                (" / ".join(f"{record['engine_scores'][engine]:.1f}%"
                    if engine in record.get("engine_scores",{}) else "—"
                    for engine in ("EasyOCR","TrOCR"))) if score_override is None else score_override,
                self._workflow_status(record),
                self._ui_message_caps(record.get("status","Not scanned")))
        if self.tree.exists(iid):
            self.tree.item(iid,values=values,tags=("scanning_now",) if iid==getattr(self,"_active_scan_iid",None) else ())
        else:
            self.tree.insert("","end",iid=iid,values=values,
                             tags=("scanning_now",) if iid==getattr(self,"_active_scan_iid",None) else ())
        if not getattr(self,"_bulk_scan_rows",False) and iid in self.scan_checked_iids and iid not in self.tree.selection():
            self.tree.selection_add(iid)

        if getattr(self,"_bulk_scan_rows",False):
            return
        btn=getattr(self,"send_selected_btn",None)
        if btn is not None:
            allowed=(not self.busy and not self.export_busy and not self._training_blocks_work()
                     and any(self._eligible_for_kurt(x,self.records[x])
                             for x in self.records))
            btn.config(state="normal" if allowed else "disabled")
        if self._table_sort_state.get("scan"):
            self._apply_table_sort("scan")
        self._schedule_scan_preview_refresh()
        self._update_scan_file_count()

    def export_completed(self):
        """Export every approved unsent record; review rows are left in place."""
        return self.export()

    def _send_all_selected(self):
        """Compatibility callback now follows SEND FILES semantics."""
        return self.export_completed()

    def _unified_double_click(self,event):
        """Review opens from any row column, not just the thumbnail."""
        iid=self.tree.identify_row(event.y)
        if iid in self.records:
            self._open_review_iid(iid)
        return "break"

    def _open_review_iid(self,iid):
        """Open the full-page manual editor without withdrawing an existing approval."""
        if iid not in self.records or self._scan_row_locked(iid):
            return
        self._mark_user_activity()
        self._yield_background_learning("manual review")
        rec=self.records[iid]
        if rec.get("exported"):
            self.status.config(text="SENT FILES CANNOT BE EDITED FROM THE ROOT QUEUE. RESTORE THE PDF FIRST.")
            return
        try:
            self.review_queue=[iid]
            self._show_review_item(0)
            self._audit_event("REVIEW_OPENED",original_name=rec.get("original_name") or rec['path'].name,
                              current_case=rec.get("confirmed") or rec.get("candidate") or "")
        except Exception as exc:
            self._log("Manual review failed for %s: %s",iid,exc)
            messagebox.showerror("Review unavailable",str(exc),parent=self.root)

    def _start_scan_case_edit(self,iid):
        if self._scan_row_locked(iid):
            return
        self._place_scan_case_entry(iid)
        pair=self._scan_case_widgets.get(iid)
        if pair:
            pair[0].focus_set()
            pair[0].selection_range(0,"end")

    def _open_instructions(self):
        win=tk.Toplevel(self.root)
        win.title(f'MFCASE V{self.app_version} — USER GUIDE')
        win.geometry('880x680')
        win.transient(self.root)
        frame=ttk.Frame(win,padding=18)
        frame.pack(fill='both',expand=True)
        text=tk.Text(frame,wrap='word',font=('Segoe UI',10),spacing3=7)
        text.pack(fill='both',expand=True)
        instructions=(
            f'MFCASE V{self.app_version} USER GUIDE\n\n'
            '1. FOLDER: SELECT THE ROOT FOLDER CONTAINING YOUR PDF FILES. THE FOLDER NAME '            'APPEARS IN THE TOOLBAR.\n'
            '2. SCAN: PRESS THE GREEN PLAY BUTTON. THE PROGRAM CHECKS LEARNED CASE-NUMBER '            'LOCATIONS AND THE UPPER-RIGHT QUARTER. HANDWRITING AND PRINTED OCR ARE '            'AVAILABLE. THE AUTOMATIC APPROVAL THRESHOLD REMAINS FIXED AT 95%.\n'
            '3. PAUSE/RESUME: PRESS THE LIGHT-BLUE BUTTON. STOP: PRESS THE RED SQUARE. '            'STOP DOES NOT DELETE COMPLETED APPROVALS OR TRIGGER AUTO SEND.\n'
            '4. LIST: PRESS THE YELLOW THREE-LINE BUTTON TO SHOW ALL FILES. PRESS IT AGAIN '            'TO RETURN TO THE FILTERED REVIEW LIST. THE DEFAULT VIEW IS FILTERED. '            'SELECT ALL APPLIES TO VISIBLE ROWS.\n'
            '5. PROGRESS: THE TOOLBAR SHOWS SELECTED FILES, CURRENT PDF, SCAN PROGRESS, '            'ESTIMATED RUN TIME, ACTUAL ELAPSED TIME, AND ESTIMATED REMAINING TIME. '            'ESTIMATES BEGIN AFTER A PDF IS PROCESSED AND CAN CHANGE.\n'
            '6. REVIEW: DOUBLE-CLICK A FILE OR USE R. DRAW A BOX ANYWHERE ON THE PDF '            'AND VERIFY/CORRECT ITS 08- CASE NUMBER. THE GREEN CHECK MARKS IT COMPLETED. '            'A CORRECTED CROP AND ITS LOCATION ARE SAVED FOR LOCAL LEARNING.\n'
            '7. BEFORE/AFTER: MATCHING INDEPENDENT COMPLETIONS CAN CONFIRM FILES BETWEEN '            'THEM. CONFIRMED MANUAL CASE NUMBERS ARE PROTECTED. AUTO APPROVALS MAY BE '            'CORRECTED BY THE EXISTING ANCHOR STRATEGY.\n'
            '8. PREVIOUS CASE: A SINGLE PRECEDING 95%+ CASE MAY PREFILL A LOW-CONFIDENCE CASE FIELD, BUT IT NEVER APPROVES, RENAMES, OR SENDS THAT PDF. THE USER MUST VERIFY AND PRESS THE GREEN CHECK. MATCHING BEFORE/AFTER ANCHORS STILL COMPLETE THE FILE AS BEFORE.\n'
            '9. SEND FILES: MOVES ELIGIBLE COMPLETED PDFS TO THE COMPLETED SUBFOLDER. '            'AUTO SEND COMPLETED IS OPTIONAL AND OFF BY DEFAULT; STOP NEVER AUTO SENDS. '            'REFRESH RELOADS THE FOLDER; CLEAR ONLY CLEARS THE DISPLAY.\n'
            '10. SETTINGS: USER GUIDE, EXPORT WORK LOG, GITHUB SETTINGS, UPDATE AND '            'UPDATE LEARNING ARE AVAILABLE. BACKGROUND LOCAL TRAINING CAN START AFTER '            'THE PROGRAM IS IDLE; THE SMALL GREEN INDICATOR APPEARS WHILE IT RUNS.\n'
            '11. EXPORT WORK LOG: WRITES A UNIQUELY NAMED ZIP DIRECTLY TO THE SELECTED '            'ROOT FOLDER\'S COMPLETED SUBFOLDER, WITHOUT MOVING OR ALTERING PDFS. '            'WORK LOGS CAN INCLUDE SENSITIVE FILENAMES, CASE NUMBERS AND OCR TEXT; '            'REVIEW DEPARTMENT RULES BEFORE SHARING.\n'
            '12. UPDATE: GITHUB MAY BE CONTACTED FOR SOFTWARE RELEASES. OCR AND PDF '            'PROCESSING REMAIN LOCAL. GITHUB SETTINGS CONTROLS THE EXISTING OPTIONAL '            'SANITIZED DIAGNOSTIC UPLOAD SETTINGS.\n')
        text.insert('1.0',instructions.upper())
        text.configure(state='disabled')
        ttk.Button(frame,text='CLOSE',command=win.destroy).pack(anchor='e',pady=8)

    def _check_connection_status(self):
        """Report LOCAL state without HTTP, SMB probes or other network I/O."""
        label=getattr(self,"connection_status_label",None)
        if label is None:
            return
        models=bool(self.reader is not None and self.trocr_model is not None)
        learning=bool(self.learning_dir and
                      Path(self.learning_dir).is_dir())
        label.config(text=(
            f"OCR: {'OFFLINE READY' if models else 'LOCAL MODELS NOT READY'}   |   "
            f"Learning: {'LOCAL' if learning else 'NOT READY'}   |   "
            "Internet: NOT CONTACTED   |   "
            "Updates: MANUAL BUTTONS ONLY   |   "
            "Work logs: LOCAL ONLY"))
    def _obsolete_insert_scan_row(self, iid, record, candidate_override=None, score_override=None):
        """Insert/update a single scan row with an independent checkbox."""
        if not hasattr(self, "tree"):
            return
        values = (
            "☑" if iid in self.scan_checked_iids else "☐",
            record["path"].name,
            record.get("candidate", "") if candidate_override is None else candidate_override,
            (f'{float(record.get("score") or 0):.1f}%' if record.get("score") else "—")
                if score_override is None else score_override,
            record.get("status", "Not scanned"))
        if self.tree.exists(iid):
            self.tree.item(iid, values=values)
        else:
            self.tree.insert("", "end", iid=iid, values=values)
        if iid in self.scan_checked_iids and iid not in self.tree.selection():
            self.tree.selection_add(iid)

    def _sync_scan_selection(self, event=None):
        selected = set(self.tree.selection())
        for iid in self.tree.get_children(""):
            self._set_scan_checkbox(iid, iid in selected)
        self._refresh_scan_checkbox_heading()
        self._update_scan_file_count()
        self._refresh_scan_button()
        self._refresh_rescan_button()
        self._refresh_review_controls()
        self._save_state()

    def _refresh_scan_checkbox_heading(self):
        if not hasattr(self, "tree"):
            return
        visible = set(self.tree.get_children(""))
        all_selected = bool(visible) and visible.issubset(self.scan_checked_iids)
        self.tree.heading("scan_select", text="☑ ALL" if all_selected else "☐ ALL")

    def _set_scan_checkbox(self, iid, checked):
        if iid not in self.records:
            return
        self.records[iid]["scan_selected"] = bool(checked)
        if checked:
            self.scan_checked_iids.add(iid)
        else:
            self.scan_checked_iids.discard(iid)
        if self.tree.exists(iid):
            self.tree.set(iid, "scan_select", "☑" if checked else "☐")
            highlighted = iid in self.tree.selection()
            if checked and not highlighted:
                self.tree.selection_add(iid)
            elif not checked and highlighted:
                self.tree.selection_remove(iid)

    def _toggle_scan_select_all(self):
        if self.busy or self._training_blocks_work():
            return
        visible = set(self.tree.get_children(""))
        make_checked = not (bool(visible) and visible.issubset(self.scan_checked_iids))
        for iid in visible:
            self._set_scan_checkbox(iid, make_checked)
        self._refresh_scan_checkbox_heading()
        self._update_scan_file_count()
        self._refresh_scan_button()
        self._save_state()

    def _on_scan_checkbox_click(self, event):
        if self.tree.identify_region(event.x,event.y)=="cell":
            iid=self.tree.identify_row(event.y)
            if iid in self.records:
                self._select_preview_row(iid,event)
                if self.tree.identify_column(event.x)=="#4":
                    # Schedule after Tk finishes selection (single click).
                    self.root.after_idle(lambda item=iid:
                        self._start_scan_case_edit(item)
                        if item in self.records else None)
                return "break"

    def _select_all_for_rescan(self, event=None):
        """Ctrl+A highlights every displayed root-level PDF row.

        Do not override Ctrl+A in text-entry widgets or the COMPLETED view.
        Selection is synchronized with scan checkboxes; documents are unchanged.
        """
        if getattr(self, "_active_main_view", "select") != "select":
            return None
        widget = getattr(event, "widget", None)
        if widget is not None and isinstance(widget, (tk.Entry, tk.Text, ttk.Entry, ttk.Combobox, tk.Spinbox)):
            return None
        if not self._startup_ready or self.busy or self._training_blocks_work() or self.export_busy:
            return "break"
        selected = tuple(self.tree.get_children(""))
        if selected:
            self.tree.selection_set(selected)
            self.tree.focus(selected[0])
            self._sync_scan_selection()
            self._refresh_rescan_button()
            self.status.config(text=f"{len(selected):,} PDF(s) selected.")
        else:
            self.tree.selection_remove(self.tree.selection())
            self._refresh_rescan_button()
            self.status.config(text="NO ELIGIBLE UNRESOLVED PDFS TO RESCAN.")
        return "break"

    def _rescan_eligible(self, iid):
        """Only unresolved PDFs still in the selected root are eligible.

        Completed, approved, exported and BLANKS records must not be altered by
        a targeted rescan, even if a stale UI selection contains their IDs.
        """
        rec = self.records.get(iid)
        if not rec or not self.folder or not self.tree.exists(iid):
            return False
        if (rec.get("confirmed") or rec.get("exported")
                or rec.get("manual_approved") or rec.get("auto_labeled")
                or rec.get("status") == "Moved to BLANKS"):
            return False
        path = Path(rec.get("path", ""))
        try:
            return (path.is_file() and path.suffix.lower() == ".pdf"
                    and path.parent.resolve() == self.folder.resolve())
        except OSError:
            return False

    def _refresh_rescan_button(self):
        btn=getattr(self,"rescan_btn",None)
        if btn is None:
            return
        enabled=(self._startup_ready and not self.busy and not self.export_busy
                 and not self._training_blocks_work() and bool(self._rescan_all_candidates()))
        btn.config(state="normal" if enabled else "disabled")

    def rescan_selected(self):
        """Rescan ONLY highlighted, unresolved rows; retain all original PDFs.

        This never resets completed files and never touches learning examples.
        Checkbox state governs ordinary SCAN; highlight state governs RESCAN.
        """
        if not self._startup_ready or self.busy or self._training_blocks_work() or self.export_busy:
            return
        selected = [iid for iid in self.tree.selection() if self._rescan_eligible(iid)]
        if not selected:
            messagebox.showinfo("Rescan files", "Highlight one or more unresolved files in the scan list first.\nUse Ctrl or Shift to select multiple rows.")
            return
        if not messagebox.askyesno(
                "Rescan selected files",
                f"Rescan {len(selected):,} highlighted PDF(s)?\n\n"
                "Previous OCR suggestions for only these files will be replaced. "
                "Other files, completed approvals, originals, and shared learning will be preserved."):
            return
        for iid in selected:
            rec = self.records[iid]
            rec.update({
                "candidate": "", "confirmed": "", "status": "Not scanned",
                "snippet": None, "preview_meta": {}, "manual_crop_rect": None,
                "handwritten": False, "score": 0.0, "engines": [],
                "engine_agreement": False, "auto_labeled": False,
                "manual_approved": False, "scan_source": "",
                "scan_ambiguous": False, "sequence_inferred": False,
                "sequence_conflict": False, "sequence_anchors": [],
                "sequence_suggestion": "", "sequence_excluded": False,
                "export_error": "", "scan_selected": True
            })
            self.scan_checked_iids.add(iid)
            self._insert_scan_row(iid, rec)
        self._save_state()
        self._update_scan_file_count()
        self._log("Targeted rescan requested for %d selected PDF(s)", len(selected))
        self.scan(only_iids=set(selected))

    def _rescan_all_candidates(self):
        if not self.folder:
            return []
        root=Path(self.folder)
        items=[]
        for iid,rec in self.records.items():
            if rec.get("exported") or rec.get("status")=="Moved to BLANKS":
                continue
            path=Path(rec.get("path") or "")
            if path.suffix.lower()!=".pdf":
                continue
            try:
                if path.parent==root:
                    items.append(iid)
            except OSError:
                pass
        return items

    def rescan_all(self):
        """Rescan every remaining root PDF, including unsent COMPLETED rows.

        Explicit human corrections remain authoritative. Automatic approvals
        are re-evaluated without deleting local learning or provenance records.
        """
        if (not self._startup_ready or self.busy or self._training_blocks_work()
                or self.export_busy):
            return
        eligible=self._rescan_all_candidates()
        if not eligible:
            self.status.config(text="NO REMAINING ROOT PDFS TO RESCAN.")
            return
        if not messagebox.askyesno("RESCAN ALL",
              f"Rescan all {len(eligible)} remaining PDFs, including unsent COMPLETED rows?\n\n"
              "Manual corrections stay protected. Automatic OCR approvals will be recalculated;"
              " sent files, learning data and audit logs will not be modified.",parent=self.root):
            return
        self._rescan_force_iids=set(eligible)
        self.scan_checked_iids.update(eligible)
        self.tree.selection_set(tuple(eligible))
        for iid in eligible:
            rec=self.records[iid]
            rec["scan_selected"]=True
            # Human corrections must survive a speculative rescan.
            human=bool(rec.get("manual_approved")) and not bool(rec.get("auto_labeled"))
            if not human:
                rec.update(candidate="",confirmed="",status="Not scanned",
                    snippet=None,preview_meta={},manual_crop_rect=None,
                    handwritten=False,score=0.0,engines=[],engine_scores={},
                    engine_agreement=False,auto_labeled=False,manual_approved=False,
                    scan_source="",scan_ambiguous=False,sequence_inferred=False,
                    sequence_conflict=False,sequence_anchors=[],sequence_suggestion="",
                    sequence_excluded=False,export_error="")
                if self.completed_tree.exists(iid):
                    self.completed_tree.delete(iid)
                rec["completed_iid"]=None
            self._insert_scan_row(iid,rec)
        self._audit_event("RESCAN_ALL",count=len(eligible),manual_decisions_preserved=True)
        self._save_state()
        self.scan(only_iids=set(eligible))

    def _refresh_scan_button(self):
        if not hasattr(self, "scan_btn"):
            return
        if self.busy or self._training_blocks_work() or self.export_busy:
            self.scan_btn.config(state="disabled")
            return
        if not self._startup_ready:
            self.scan_btn.config(state="disabled")
            return
        candidates = bool(self._pending_scan_items())
        self.scan_btn.config(state="normal" if candidates or getattr(self,"_sequence_review_pending",False) else "disabled")
        self._refresh_rescan_button()
        button=getattr(self,"send_selected_btn",None)
        if button is not None:
            allowed=(self._startup_ready and not self.busy and
                    not self.export_busy and not self._training_blocks_work() and
                    any(self._eligible_for_kurt(iid,self.records[iid])
                        for iid in self.records))
            button.config(state="normal" if allowed else "disabled")

    def clear_scan_screen(self):
        """Clear only visible scan rows; never remove PDF files or saved learning."""
        if getattr(self,"_scan_case_editor",None) is not None:
            self.status.config(text="SAVE THE EDITED CASE WITH ENTER OR CANCEL WITH ESC FIRST.")
            self._scan_case_editor.focus_set()
            return
        if self.busy or self._training_blocks_work() or self.export_busy:
            self.status.config(text="CLEAR IS AVAILABLE AFTER SCANNING, LEARNING, OR SENDING FINISHES.")
            return
        for iid in self.tree.get_children(""):
            self.tree.delete(iid)
        self._refresh_scan_checkbox_heading()
        self._update_scan_file_count()
        self._refresh_scan_button()
        self._schedule_scan_preview_refresh()
        self.status.config(text="SCAN LIST CLEARED. NO PDFS, APPROVALS, OR LEARNING WERE DELETED. CLICK REFRESH TO RESTORE IT.")


    def _refresh_timeout(self,generation):
        """Never keep the Tk loading overlay indefinitely on an SMB delay.

        The worker may still finish and update the list later. Existing saved
        decisions remain intact; the UI stays usable during slow network I/O.
        """
        self._folder_load_timeout_id=None
        if generation!=self._folder_load_generation:
            return
        self._loader_finish("rolls")
        self.refresh_root_btn.config(state="normal")
        self.choose_folder_btn.config(state="normal")
        self.status.config(
            text="FOLDER REFRESH IS TAKING LONGER THAN EXPECTED. CURRENT PDFS "
                 "remain visible; delayed network results will load when ready.")
        self._diag_event("folder_refresh_wait",
            outcome="overlay_released_while_network_worker_pending",
            generation=generation)

    def refresh_root_folder(self):
        """Refresh direct root PDFs only, retaining all saved manual decisions."""
        if getattr(self,"_scan_case_editor",None) is not None:
            self.status.config(text="SAVE THE EDITED CASE WITH ENTER OR CANCEL WITH ESC FIRST.")
            self._scan_case_editor.focus_set()
            return
        if not self._startup_ready or self.busy or self._training_blocks_work() or self.export_busy:
            return
        if "rolls" in self._loading_jobs:
            self.status.config(text="FOLDER REFRESH IS ALREADY IN PROGRESS.")
            return
        if not self.folder or not self.folder.is_dir():
            messagebox.showinfo("Choose a folder","Choose a folder first.")
            return
        self._save_state()
        self._folder_load_generation+=1
        generation=self._folder_load_generation
        self.refresh_root_btn.config(state="disabled")
        self._loader_start("rolls","Refreshing root-folder PDFs…")
        if self._folder_load_timeout_id is not None:
            self.root.after_cancel(self._folder_load_timeout_id)
        self._folder_load_timeout_id=self.root.after(
            16000,lambda generation=generation:self._refresh_timeout(generation))
        threading.Thread(target=self._folder_load_worker,
                         args=(self.folder,generation),daemon=True).start()

    def choose_folder(self, folder=None):
        if getattr(self,"_scan_case_editor",None) is not None:
            self.status.config(text="SAVE THE EDITED CASE WITH ENTER OR CANCEL WITH ESC FIRST.")
            self._scan_case_editor.focus_set()
            return
        if not self._startup_ready or self._loading_jobs:
            return
        if self.busy or self.export_busy or self._training_blocks_work():
            messagebox.showinfo(
                "Processing in progress",
                "Stop the current scan or send before changing folders.")
            return
        self._yield_background_learning("folder selection")
        folder = folder or filedialog.askdirectory(title="Select folder containing scanned PDFs",
                            initialdir=getattr(self,"_last_folder",None) or None)
        if not folder:
            return
        self.folder = Path(folder)
        self._last_folder=str(self.folder)
        self._save_user_settings()
        self.folder_label.config(text=self._short_folder_name(self.folder))
        self.log_path = self.folder / ".case_ocr_log.txt"
        # Folder I/O and shared learning access both run off the Tk thread.
        # The indicator stays animated until BOTH tasks report completion.
        self._folder_load_generation += 1
        generation = self._folder_load_generation
        self.scan_btn.config(state="disabled")
        self.choose_folder_btn.config(state="disabled")
        self.refresh_root_btn.config(state="disabled")
        self._loader_start("rolls", "Loading PDFs from the selected root folder…")
        self.status.config(text="LOADING PDF ROLLS (EXISTING SHARED LEARNING REMAINS LOADED)…")
        threading.Thread(target=self._folder_load_worker, args=(self.folder, generation),
                         daemon=True, name="MFCase-LoadRolls").start()

    def _save_state(self):
        with self._perf_measure("network_io_state_save"):
            return self._save_state_impl()

    def _save_state_impl(self):
        if not self.folder:
            return
        payload = {"version": 2, "updated_at": datetime.now().isoformat(timespec="seconds"), "files": {}}
        for rec in self.records.values():
            path = rec.get("path")
            if not path:
                continue
            payload["files"][path.name] = {
                "sequence_review_pending": bool(getattr(self,"_sequence_review_pending",False)),
                "candidate": rec.get("candidate", ""),
                "pending_auto_location": rec.get("pending_auto_location"),
                "case_draft": rec.get("case_draft", ""),
                "confirmed": rec.get("confirmed", ""),
                "status": rec.get("status", ""),
                "handwritten": bool(rec.get("handwritten", False)),
                "score": float(rec.get("score", 0.0) or 0.0),
                "engines": rec.get("engines", []),
                "engine_agreement": bool(rec.get("engine_agreement", False)),
                "engine_scores": dict(rec.get("engine_scores") or {}),
                "auto_labeled": bool(rec.get("auto_labeled", False)),
                "approval_rule_version": rec.get("approval_rule_version",""),
                "manual_approved": bool(rec.get("manual_approved", False)),
                "send_after_manual_approval": bool(rec.get("send_after_manual_approval", False)),
                "exported": bool(rec.get("exported", False)),
                "export_name": rec.get("export_name", ""),
                "original_name": rec.get("original_name",""),
                "original_path": rec.get("original_path",""),
                "preview_meta": (
                    {"page_num": rec["snippet"][0], "label": rec["snippet"][2],
                     "rect": list(rec["snippet"][3]) if rec["snippet"][3] else None,
                     "candidates": rec["snippet"][4] if len(rec["snippet"]) > 4 else []}
                    if rec.get("snippet") else rec.get("preview_meta", {})
                ),
                "manual_crop_rect": list(rec["manual_crop_rect"]) if rec.get("manual_crop_rect") else None,
                "manual_crop_origin": rec.get("manual_crop_origin",""),
                "training_type": rec.get("training_type", "unsure"),
                "scan_source": rec.get("scan_source", ""),
                "scan_ambiguous": bool(rec.get("scan_ambiguous", False)),
                "sequence_inferred": bool(rec.get("sequence_inferred", False)),
                "sequence_conflict": bool(rec.get("sequence_conflict", False)),
                "sequence_anchors": list(rec.get("sequence_anchors", [])),
                "sequence_suggestion": rec.get("sequence_suggestion", ""),
                "sequence_previous_prefill": bool(rec.get("sequence_previous_prefill", False)),
                "sequence_original_candidate":rec.get("sequence_original_candidate",""),
                "sequence_original_score":rec.get("sequence_original_score",rec.get("score",0)),
                "sequence_original_ambiguous":bool(rec.get("sequence_original_ambiguous",False)),
                "sequence_excluded": bool(rec.get("sequence_excluded", False)),
                "scan_selected": bool(rec.get("scan_selected", True)),
                "learning_pending": bool(rec.get("learning_pending", False)),
                "export_error": str(rec.get("export_error", ""))
            }
        target = self.folder / ".case_ocr_state.json"
        temp = target.with_suffix(".json.tmp")
        try:
            temp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(temp, target)
            return True
        except Exception:
            logging.exception("Failed to save queue state")
            return False


    def _diag_event(self, event, **details):
        """Append an analysis-ready JSON record without interrupting OCR."""
        try:
            import json as _json
            from datetime import datetime as _dt, timezone as _tz
            filepath=getattr(self,"_diag_file",None)
            if filepath is None:
                # A work log is permanent even outside an active scan run.
                if event in ("scan_error","manual_case_confirmation","transfer_result"):
                    self._audit_event("SCAN_"+event.upper(),**details)
                return
            payload={"schema":"mfcase.diagnostic.v1",
                     "utc":_dt.now(_tz.utc).isoformat(timespec="milliseconds"),
                     "run_id":getattr(self,"_diag_run_id",None),
                     "version":getattr(self,"app_version","unknown"),
                     "event":event}
            if getattr(self,"_diag_current_file",None):
                payload["file"]=self._diag_current_file
            payload.update(details)
            with self._diag_lock:
                with open(filepath,"a",encoding="utf-8",newline="\n") as handle:
                    handle.write(_json.dumps(payload,ensure_ascii=False,default=str,
                                             separators=(",",":"))+"\n")
            if event in ("scan_error","manual_case_confirmation","approval_decision",
                         "run_end","row_final","transfer_result","auto_send_error"):
                small={key:value for key,value in payload.items()
                       if key in ("file","candidate","confirmed","case","score",
                                  "status","outcome","error","source_file",
                                  "destination_file","auto_approved","run_id")}
                self._audit_event("SCAN_"+event.upper(),**small)
        except Exception as exc:
            # Diagnostics must never block evidence processing.
            try: logging.warning("Diagnostic logging failed: %s",exc)
            except Exception: pass


    def _configure_github_logs(self, replacement=False, retry_log=None):
        try:settings=_read_github_log_settings()
        except Exception:
            messagebox.showerror('GitHub logs','Could not read GitHub log settings.');return
        existing=getattr(self,'_github_log_dialog',None)
        if existing is not None and existing.winfo_exists():
            existing.lift();return
        win=tk.Toplevel(self.root);self._github_log_dialog=win
        win.title('GitHub scan diagnostics');win.transient(self.root)
        panel=ttk.Frame(win,padding=18);panel.pack(fill='both',expand=True)
        enabled=tk.BooleanVar(value=bool(settings.get('enabled',True)))
        ttk.Checkbutton(panel,text='UPLOAD SANITIZED DIAGNOSTICS AFTER EVERY SCAN',variable=enabled).pack(anchor='w')
        saved=bool(settings.get('protected_token') or os.environ.get('MFCASE_GITHUB_LOG_TOKEN'))
        needs_token=replacement or _github_token_is_expired(settings) or not saved
        ttk.Label(panel,text=('Token expired or rejected. Create a replacement below.' if replacement or _github_token_is_expired(settings)
                  else 'Token saved securely — reused automatically; no re-entry needed.' if saved
                  else 'One-time setup: save a GitHub token to enable uploads.'),wraplength=480).pack(pady=10)
        ttk.Label(panel,text='PUBLIC REPOSITORY: CHRISBROOK0127/CASENUMBEROCR-UPDATES\n'
            'Only sanitized diagnostics upload. Documents and full logs stay local.\n'
            'Use a fine-grained token for this repository with Contents: Read and write.',justify='left').pack(pady=6)
        def create_token():
            import webbrowser
            webbrowser.open('https://github.com/settings/personal-access-tokens/new?name=MFCase+Scan+Logs&target_name=chrisbrook0127&expires_in=90&contents=write')
        ttk.Button(panel,text='CREATE REPLACEMENT TOKEN',command=create_token).pack(anchor='w',pady=5)
        editor=ttk.Frame(panel)
        token=tk.StringVar()
        ttk.Label(editor,text='NEW GITHUB TOKEN:').pack(anchor='w')
        entry=ttk.Entry(editor,textvariable=token,show='*',width=60);entry.pack(fill='x')
        def show_editor():
            editor.pack(fill='x',pady=8);entry.focus_set()
        if needs_token:show_editor()
        else:ttk.Button(panel,text='CHANGE SAVED TOKEN',command=show_editor).pack(anchor='w')
        def save():
            if (replacement or _github_token_is_expired(settings)) and not token.get().strip():
                messagebox.showerror('GitHub logs','Enter the replacement token before saving.',parent=win);return
            try:
                _save_github_log_settings(settings,enabled.get(),token.get())
                token.set('');win.destroy();self._github_log_dialog=None
                self.status.config(text='GITHUB TOKEN/SETTINGS SAVED. SAVED CREDENTIALS WILL BE REUSED AFTER EVERY SCAN.')
                if retry_log and enabled.get():
                    threading.Thread(target=self._diag_sync_shared,args=(retry_log,),daemon=True).start()
            except Exception:
                messagebox.showerror('GitHub logs','Could not save the protected credential/settings. Existing credential retained.',parent=win)
        ttk.Button(panel,text='SAVE',command=save).pack(pady=10)


    def _diag_sync_shared(self, logfile=None):
        """Authorized post-scan upload of an allowlisted, anonymous snapshot."""
        import base64,gzip,io,uuid
        # Legacy review/training hooks call this without a path. Only the
        # completed-scan hook is authorized to start an automatic upload.
        if logfile is None or getattr(self,"_stop_all_requested",False):return
        logfile=Path(logfile)
        def status(message):self.queue.put(('diagnostic_upload_status',message))
        try:
            settings=_read_github_log_settings()
            if not settings.get('enabled',True):return
            if _github_token_is_expired(settings):
                status('GitHub token expired or rejected. Enter a replacement to resume uploads.')
                self.queue.put(('diagnostic_token_needed',str(logfile)));return
            token=''
            if settings.get('protected_token'):
                try:
                    token=_protect_github_token(base64.b64decode(settings['protected_token']),decrypt=True).decode()
                except Exception:
                    status('Saved GitHub token cannot be unlocked for this Windows user. Enter a replacement.')
                    self.queue.put(('diagnostic_token_needed',str(logfile)));return
            if not token:token=os.environ.get('MFCASE_GITHUB_LOG_TOKEN','').strip()
            if not token:
                status('GitHub logs not uploaded: configure a token in SETTINGS > GITHUB SETTINGS.');return
            aliases={};output=io.BytesIO();count=0
            with logfile.open(encoding='utf-8') as source,gzip.GzipFile(fileobj=output,mode='wb',mtime=0) as archive:
                for line in source:
                    try:event=json.loads(line)
                    except (ValueError,TypeError):continue
                    safe=_public_scan_event(event,aliases)
                    if safe:
                        archive.write((json.dumps(safe,separators=(',',':'),allow_nan=False)+'\n').encode());count+=1
                    if output.tell()>8_000_000:
                        status('GitHub logs not uploaded: sanitized log exceeds upload size limit. Full log retained locally.');return
            if not count:
                status('GitHub logs not uploaded: no scan events found.');return
            data=output.getvalue()
            if len(data)>8_000_000:
                status('GitHub logs not uploaded: size limit. Full log retained locally.');return
            name='scan-diagnostics/'+datetime.now().strftime('%Y-%m-%d')+'/'+uuid.uuid4().hex+'.jsonl.gz'
            url='https://api.github.com/repos/chrisbrook0127/CaseNumberOCR-Updates/contents/'+name
            body=json.dumps({'message':'Add sanitized MFCase scan diagnostics','content':base64.b64encode(data).decode(),'branch':'main'}).encode()
            if getattr(self,"_stop_all_requested",False):return
            request=urllib.request.Request(url,data=body,method='PUT',headers={
                'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
                'Content-Type':'application/json','User-Agent':'MFCase-Diagnostics','X-GitHub-Api-Version':'2022-11-28'})
            with urllib.request.urlopen(request,timeout=30) as response:
                if response.status!=201:raise RuntimeError('Unexpected upload response')
                expiry=response.headers.get('GitHub-Authentication-Token-Expiration')
            if isinstance(expiry,str) and expiry:
                try:
                    current=_read_github_log_settings()
                    if current.get('protected_token')==settings.get('protected_token'):
                        current['token_expires_at']=expiry
                        _save_github_log_settings(current,current.get('enabled',True))
                except Exception:pass  # A successful upload is not a credential-save failure.
            status('Sanitized scan diagnostics uploaded to GitHub: '+name)
        except urllib.error.HTTPError as exc:
            if exc.code==401:
                try:
                    current=_read_github_log_settings()
                    if current.get('protected_token')==settings.get('protected_token'):
                        current['token_rejected']=True
                        _save_github_log_settings(current,current.get('enabled',True))
                except Exception:pass
                status('GitHub token expired or rejected. Enter a replacement; full log retained locally.')
                self.queue.put(('diagnostic_token_needed',str(logfile)))
            else:
                status(f'GitHub logs not uploaded (HTTP {exc.code}). Check permissions or rate limits; saved token retained.')
        except Exception:
            # Never display exception text: it may include paths or credentials.
            status('GitHub logs not uploaded: connection or credential error. Full log retained locally.')


    def _diag_start_run(self):
        """Create a fresh, time-stamped run in local-only diagnostic storage."""
        try:
            import uuid
            from datetime import datetime as _dt, timezone as _tz
            directory=Path(os.getenv("LOCALAPPDATA") or str(self.app_dir))/"MFCase"/"diagnostics"
            directory.mkdir(parents=True,exist_ok=True)
            run_id=_dt.now(_tz.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
            self._diag_run_id=run_id
            self._diag_file=directory/("mfcase-"+run_id+".jsonl")
            self._diag_current_file=None
            self._diag_event("run_start",
                 selected_files=[Path(x[1]["path"]).name for x in self.scan_items],
                 scan_folder=str(self.folder),
                 year_prefix=self.scan_year_prefix,
                 scan_region_fraction=[0.5,0.0,1.0,0.5],
                 either_engine_min_inclusive=self._get_auto_threshold(),
                 auto_label_enabled=bool(self.auto_label_var.get()),
                 application_version=self.app_version)
            # Retain EVERY diagnostic run. Do not prune historical work logs.
            self._audit_event("SCAN_STARTED", selected_count=len(self.scan_items),
                              folder=str(self.folder))
        except Exception as exc:
            self._diag_file=None
            self._log("Could not initialize detailed diagnostic log: %s",exc)

    @staticmethod
    def _diag_hits(hits):
        return [{
            "number":str(h.get("number") or ""),
            "score":round(float(h.get("score") or 0),3),
            "bbox":list(h.get("rect")) if h.get("rect") is not None else None,
            "engines":list(h.get("engines") or []),
                    "engine_scores":dict(h.get("engine_scores") or {}),
            "label_type":h.get("label_type"),
            "label_context":bool(h.get("label_context")),
            "learned_location":bool(h.get("learned_location"))
        } for h in (hits or [])[:100]]

    def _diag_export(self):
        """User-controlled ZIP export. No automatic upload or transmission."""
        import zipfile as _zipfile
        from datetime import datetime as _dt
        from tkinter import messagebox as _messagebox
        if not self.folder:
            _messagebox.showwarning('SELECT FOLDER',
                'SELECT A ROOT FOLDER BEFORE EXPORTING A WORK LOG.')
            return
        active=getattr(self,"_diag_file",None)
        directory=Path(os.getenv("LOCALAPPDATA") or str(self.app_dir))/"MFCase"/"diagnostics"
        logs=sorted(directory.glob("mfcase-*.jsonl"),reverse=True)[:10] if directory.is_dir() else []
        if active and Path(active).exists() and Path(active) not in logs:
            logs.insert(0,Path(active))
        if not logs:
            _messagebox.showinfo("Work log","No recorded scans yet. Run a scan first.")
            return
        output_dir=Path(self.folder)/'COMPLETED'
        try:
            output_dir.mkdir(parents=True,exist_ok=True)
        except OSError as exc:
            _messagebox.showerror('WORK LOG EXPORT FAILED',str(exc))
            return
        suggested='MFCase_Work_Log_'+_dt.now().strftime('%Y%m%d_%H%M%S')+'.zip'
        target=output_dir/suggested
        number=2
        while target.exists():
            target=output_dir/(Path(suggested).stem+f'_{number}.zip')
            number+=1
        notes=(
            "MFCase OCR diagnostic work log, schema mfcase.diagnostic.v1\n"
            "Each JSONL line is a standalone JSON event. Filter on event/file/run_id.\n"
            "Events: run_start, ocr_label_pass, field_ocr_pass, candidate_pass, "
            "ocr_candidates, candidate_filter, ocr_decision, row_decision, "
            "sequence_decision, transfer_result, run_end.\n"
            "bbox is [left,top,right,bottom] in rendered page pixels.\n"
            "score is the raw OCR engine score, not a statistical guarantee.\n"
            "ignored contains candidate reads excluded and an explicit reason.\n"
            "Do not share externally without checking department data policies.\n"
            "Contains filenames, possible identifiers and OCR text in the 3-inch zone.\n"
            "No original PDFs or images are included.\n")
        try:
            with _zipfile.ZipFile(target,"x",compression=_zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("HOW_TO_READ.txt",notes)
                for path in logs:
                    archive.write(path,arcname=path.name)
                if self.log_path and Path(self.log_path).is_file():
                    archive.write(self.log_path,arcname="existing_ocr_log.txt")
                if self._audit_file.is_file():
                    archive.write(self._audit_file,arcname="MFCase_Work_Log.txt")
                provenance=self._audit_root / "MFCase_File_Trace.jsonl"
                if provenance.is_file():
                    archive.write(provenance,arcname="MFCase_File_Trace.jsonl")
            _messagebox.showinfo('WORK LOG SAVED',
                'DIAGNOSTIC ZIP SAVED IN THE COMPLETED FOLDER.\n\n'+str(target))
        except Exception as exc:
            _messagebox.showerror('WORK LOG EXPORT FAILED',str(exc))


    def _scan_progress_update(self,done=None,phase="SCANNING",filename=None,index=None):
        """Non-blocking Tk progress display reflecting actual PDF scan work."""
        total=len(getattr(self,"scan_items",[]) or [])
        if done is None:
            try:done=int(float(self.progress["value"]))
            except (ValueError,TypeError,tk.TclError):done=0
        done=max(0,min(total,int(done)))
        if getattr(self,'_scan_clock_start',None) is not None:
            self._scan_clock_done=max(getattr(self,'_scan_clock_done',0),done)
        self.progress.config(maximum=max(1,total),value=done)
        pct=int(round(100*done/total)) if total else 0
        label=getattr(self,"scan_progress_label",None)
        if label is not None:
            if filename:
                label.configure(text=f"{phase.upper()}: {index or done} OF {total} ({filename})")
            else:
                label.configure(text=f"{phase.upper()}: {done} OF {total} PDF FILES")
        pct_label=getattr(self,"scan_progress_pct",None)
        if pct_label is not None:
            pct_label.configure(text=f"{pct}%")
        self._update_run_timers()
        self._diag_event("scan_progress",done=done,total=total,
                         percentage=pct,phase=phase)



    @staticmethod
    def _is_corner_case_rect(rect,image):
        """Compatibility name: the complete upper-right QUARTER qualifies.

        Do not reimpose the earlier 82% x / 24% y extra restriction.
        """
        return App._is_in_4x4_rect(rect,image)

    @staticmethod
    def _is_in_4x4_rect(rect,image):
        """Verify an on-page OCR box lies within the actual upper-right quarter ROI."""
        if rect is None or image is None:
            return False
        try:
            height,width=image.shape[:2]
            span=width-width//2
            x0,y0,x1,y1=map(float,rect)
            return (all(math.isfinite(v) for v in (x0,y0,x1,y1))
                    and x0>=width-span and y0>=0
                    and x1<=width and y1<=height//2
                    and x1>x0 and y1>y0)
        except (TypeError,ValueError,AttributeError):
            return False

    def _prefer_case_margin(self,hits,kind,image):
        if kind not in ("header","fallback") or not hits:
            return hits
        strong_margin=[h for h in hits if
            self._is_corner_case_rect(h.get("rect"),image)]
        if not strong_margin:
            self._diag_event("margin_priority",source=kind,
                outcome="no_corner_case_read",
                candidates=self._diag_hits(hits))
            return hits
        # Only variants in the same PHYSICAL case-number zone are compared.
        # Any separate number to its left is outside the intended case field
        # and must not create a false conflict.
        ignored=[h for h in hits if h not in strong_margin]
        self._diag_event("margin_priority",source=kind,
            outcome="right_margin_priority",kept=self._diag_hits(strong_margin),
            ignored=[{"candidate":x,"reason":"outside_upper_right_case_field"}
                     for x in self._diag_hits(ignored)])
        return strong_margin

    def _resolve_ocr_alternates(self,hits,kind,best):
        """Conflicts require credible competing OCR of the *same* case field.

        OCR can hallucinate case-shaped strings from unrelated text elsewhere
        in the allowed 4x4. Those are diagnostic candidates, not vetoes of a
        physically located, high-confidence header number. A genuine second
        95%+ reading in the same field remains a manual-review conflict.
        """
        if not best:
            return hits
        chosen_rect=best.get("box_rect") or best.get("rect")
        if not chosen_rect or len(chosen_rect)!=4:
            return [best]
        x0,y0,x1,y1=map(float,chosen_rect)
        height=max(1.,y1-y0)
        best_case=_case_identity(best.get("number"))
        best_score=float(best.get("score") or 0.)
        primary_engines=set(best.get("engines") or [])
        # One verified literal printed 08- read at the locked 95% threshold
        # outranks a model-only alternative from exactly the same ink. Do not
        # make this shortcut for handwritten or inferred printed readings.
        exact_printed=(best_score>=95.0 and any(e in
            ("EasyOCR","EasyOCR-joined") for e in primary_engines))
        retained=[best]
        for hit in hits:
            if hit is best:
                continue
            if (_same_physical_case_box(chosen_rect,hit.get("box_rect") or hit.get("rect"))
                    and float(hit.get("score") or 0.)<best_score):
                # Lower confidence on the same ink cannot veto the winner.
                continue
            if _case_identity(hit.get("number"))==best_case:
                # Duplicate identity: selected field already has this value.
                continue
            rect=hit.get("box_rect") or hit.get("rect")
            if (not hit.get("box_verified") or not rect or len(rect)!=4 or
                    float(hit.get("score") or 0.)<95.):
                continue
            ax0,ay0,ax1,ay1=map(float,rect)
            another_h=max(1.,ay1-ay0)
            common_h=max(0.,min(y1,ay1)-max(y0,ay0))
            same_line=(common_h/min(height,another_h)>=.5)
            x_gap=max(0.,max(x0,ax0)-min(x1,ax1))
            # An independent form field below the case number is not an OCR
            # alternative. Similar or overlapping numbered ink on the SAME
            # physical line is a real conflict if both are high-confidence.
            same_field=same_line and x_gap<=max(18.,.8*max(height,another_h))
            if not same_field:
                continue
            alt_engines=set(hit.get("engines") or [])
            if exact_printed and not any(e in
                   ("EasyOCR","EasyOCR-joined") for e in alt_engines):
                self._diag_event("printed_shortcut_ignored_model_variant",
                                 chosen=best.get("number"),alternative=hit.get("number"))
                continue
            retained.append(hit)
        if len(retained)<len(hits):
            self._diag_event("case_field_alternates_filtered",source=kind,
                original=self._diag_hits(hits),kept=self._diag_hits(retained))
        return retained

    def _diag_field_filter(self,hits,source):
        """Keep valid 08- candidates without discarding competing readings."""
        filtered=[]
        ignored=[]
        expected=REQUIRED_CASE_YEAR
        for h in hits:
            number=str(h.get("number") or "").strip()
            match=re.fullmatch(r"(\d{1,2})\s*-\s*(\d{1,7})",number)
            if not match:
                ignored.append({"candidate":self._diag_hits([h])[0],"reason":"not_a_complete_case_number"})
                continue
            if match.group(1)!=REQUIRED_CASE_YEAR:
                ignored.append({"candidate":self._diag_hits([h])[0],
                                "reason":"required_literal_08_prefix_missing"})
                continue
            filtered.append(h)
        self._diag_event("candidate_filter",source=source,
              kept=self._diag_hits(filtered),ignored=ignored,
              expected_year=expected)
        return filtered

    def _load_user_settings(self):
        """Auto-send defaults OFF and stays OFF after a failed settings read."""
        self._user_settings_file=Path(os.getenv("LOCALAPPDATA") or str(Path.home())) / "MFCase" / "user_settings.json"
        try:
            cfg=json.loads(self._user_settings_file.read_text(encoding="utf-8"))
            self._last_folder=str(cfg.get("last_folder") or "")
            self.auto_send_var.set(cfg.get("auto_send_completed") is True)
            self.show_all_files_var.set(False)  # Always start in the filtered view.
        except (OSError,ValueError,TypeError):
            self.auto_send_var.set(False)
            self.show_all_files_var.set(False)

    def _save_user_settings(self):
        path=self._user_settings_file
        try:
            path.parent.mkdir(parents=True,exist_ok=True)
            tmp=path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps({"version":1,
                      "auto_send_completed":bool(self.auto_send_var.get()),
                      "full_list":False,
                      "last_folder":str(self.folder or getattr(self,"_last_folder",""))},indent=2),encoding="utf-8")
            os.replace(tmp,path)
            self._audit_event("AUTO_SEND_SETTING",enabled=bool(self.auto_send_var.get()))
        except OSError as exc:
            self.auto_send_var.set(False)  # No unsaved, silently active auto-send.
            messagebox.showerror("Settings not saved",str(exc),parent=self.root)

    def _mark_user_activity(self,event=None):
        self._last_activity_time=time.monotonic()
        # Idle learning is never permitted during scanning or while someone edits.
        return None

    def _scan_row_locked(self,iid):
        """Only the current OCR record and in-flight transfer are locked."""
        rec=self.records.get(iid)
        return (not rec or rec.get("exported") or self.export_busy
            or self._training_blocks_work() or
            iid in getattr(self,"_export_in_flight_iids",set()) or
            (self.busy and iid in (getattr(self,"_active_scan_iid",None),
                                   getattr(self,"_worker_current_iid",None))))

    def _schedule_post_scan_learning(self):
        """Run pending local training after startup or approval and 90 seconds idle."""
        if getattr(self,"_stop_all_requested",False):return
        old=getattr(self,"_post_scan_learning_after",None)
        if old is not None:
            try:self.root.after_cancel(old)
            except tk.TclError:pass
        self._last_activity_time=time.monotonic()
        # Location learning is still captured on approval. Model retraining is manual.
        self._post_scan_learning_after=None

    def _run_post_scan_learning_if_idle(self):
        self._post_scan_learning_after=None
        if getattr(self,"_stop_all_requested",False):return
        if self.export_busy:
            # The transfer can outlast the first idle interval; retry after it finishes.
            self._post_scan_learning_after=self.root.after(30000,self._run_post_scan_learning_if_idle)
            return
        if (self.busy or self.paused or self.trocr_training
                or self._loading_jobs or not self._startup_ready):
            self._post_scan_learning_after=self.root.after(30000,self._run_post_scan_learning_if_idle)
            return
        idle=time.monotonic()-getattr(self,"_last_activity_time",time.monotonic())
        if idle < 90:
            self._post_scan_learning_after=self.root.after(int(max(1000,(90-idle)*1000)),
                self._run_post_scan_learning_if_idle)
            return
        self._audit_event("IDLE_LEARNING_CHECK_SKIPPED_MANUAL_ONLY")

    def _audit_event(self, action, **data):
        """Append-only text and trace records. Never rotate, truncate or delete."""
        try:
            from datetime import timezone
            timestamp=datetime.now(timezone.utc).isoformat(timespec="milliseconds")
            line=json.dumps({"at":timestamp,"action":action,**data},ensure_ascii=False,
                            separators=(",",":"),default=str)
            with self._audit_lock:
                self._audit_root.mkdir(parents=True,exist_ok=True)
                with open(self._audit_file,"a",encoding="utf-8",newline="\n") as handle:
                    handle.write(line+"\n")
                    handle.flush()
                if action in ("FILE_SENT","FILE_RENAMED","FILE_COMBINED","FILE_RESTORED"):
                    trace=self._audit_root/"MFCase_File_Trace.jsonl"
                    with open(trace,"a",encoding="utf-8",newline="\n") as handle:
                        handle.write(line+"\n")
                        handle.flush()
        except Exception as exc:
            try:logging.warning("MFCase append-only audit failed: %s",exc)
            except Exception:pass

    def record_combination(self,combined_path,inputs):
        """Companion combiner may call this for proven page-level input lineage.

        The app cannot truthfully infer inclusion merely from matching filenames;
        the actual combining operation must provide its source file list.
        """
        output=Path(combined_path)
        for row in inputs:
            if isinstance(row,dict):
                origin=row.get("original_name") or row.get("input_name")
                sent=row.get("sent_path") or row.get("input_path")
            else:
                origin=Path(row).name
                sent=str(row)
            self._audit_event("FILE_COMBINED",original_name=origin,
                              sent_path=sent,combined_path=str(output),combined_name=output.name)

    def _log(self, message, *args):
        try:
            logging.info(message, *args)
            rendered=message % args if args else str(message)
            self._audit_event("MESSAGE",detail=rendered)
        except Exception:
            pass

    def _year_prefix(self):
        """This release processes only 2008 case numbers written as 08-NNN."""
        if self.year_var.get().strip()!=REQUIRED_CASE_YEAR:
            self.year_var.set(REQUIRED_CASE_YEAR)
        return REQUIRED_CASE_YEAR

    def _learning_example_id(self, example):
        existing = str(example.get("example_id") or "").strip()
        if existing:
            return existing
        image = str(example.get("image") or "").strip()
        source = str(example.get("source_pdf") or "").strip()
        confirmed = str(example.get("confirmed_case_number") or "").strip()
        raw = f"{image}|{source}|{confirmed}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest() if image and confirmed else ""

    def _merge_legacy_learning_data(self, legacy_dir):
        """Merge a prior folder-local library into the central library without deleting it."""
        if not self.learning_dir or not legacy_dir or not legacy_dir.is_dir():
            return
        try:
            if legacy_dir.resolve() == self.learning_dir.resolve():
                return
            central_db = self.learning_dir / "learning_examples_v3.json"
            local_db = next(
                (legacy_dir / name for name in (
                    "learning_examples_v3.json",
                    "learning_examples_v2.json",
                    "learning_examples.json")
                 if (legacy_dir / name).is_file()),
                None)
            if local_db is None:
                return

            central_source = next(
                (self.learning_dir / name for name in (
                    "learning_examples_v3.json",
                    "learning_examples_v2.json",
                    "learning_examples.json")
                 if (self.learning_dir / name).is_file()),
                None)
            central = (
                json.loads(central_source.read_text(encoding="utf-8"))
                if central_source else {})
            local = json.loads(local_db.read_text(encoding="utf-8"))
            central_examples = list(central.get("examples", []))
            known = {
                self._learning_example_id(ex)
                for ex in central_examples
                if self._learning_example_id(ex)
            }

            copied_images = 0
            for example in local.get("examples", []):
                example = dict(example)
                rel = str(example.get("image") or "").strip()
                if rel:
                    source_image = (legacy_dir / rel).resolve()
                    target_image = (self.learning_dir / rel).resolve()
                    try:
                        source_image.relative_to(legacy_dir.resolve())
                        target_image.relative_to(self.learning_dir.resolve())
                    except ValueError:
                        example["image"] = None
                    else:
                        if source_image.is_file() and not target_image.exists():
                            target_image.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(source_image, target_image)
                            copied_images += 1
                        if not target_image.is_file():
                            example["image"] = None
                example_id = self._learning_example_id(example)
                if example_id and example_id not in known:
                    example["example_id"] = example_id
                    central_examples.append(example)
                    known.add(example_id)

            central["examples"] = central_examples
            corrections = dict(central.get("corrections", {}))
            corrections.update(local.get("corrections", {}))
            central["corrections"] = corrections

            regions = list(central.get("field_regions", []))
            region_keys = {
                (tuple(r.get("rect_norm", [])), r.get("source_pdf"),
                 r.get("confirmed_case_number"))
                for r in regions
            }
            for region in local.get("field_regions", []):
                key = (tuple(region.get("rect_norm", [])), region.get("source_pdf"),
                       region.get("confirmed_case_number"))
                if key not in region_keys:
                    regions.append(region)
                    region_keys.add(key)
            central["field_regions"] = regions

            for key in (
                "trocr_v2_trained_count", "trocr_v2_attempted_count",
                "trocr_v2_validation", "training_type_validation",
                "last_training_summary"
            ):
                if key not in central and key in local:
                    central[key] = local[key]
            for key in ("trocr_v3_attempted_ids", "trocr_v3_trained_ids"):
                central[key] = sorted(set(central.get(key, []) or []) |
                                      set(local.get(key, []) or []))

            # Keep the central active model if one already exists. Copy a legacy
            # checkpoint only when the central share has no corresponding model.
            for model_name in ("trocr_v2_finetuned", "trocr_v2_previous"):
                local_model = legacy_dir / model_name
                central_model = self.learning_dir / model_name
                if local_model.is_dir() and not central_model.exists():
                    shutil.copytree(local_model, central_model)

            temp = central_db.with_suffix(".json.tmp")
            temp.write_text(json.dumps(central, indent=2), encoding="utf-8")
            os.replace(temp, central_db)
            self._log(
                "Merged legacy learning data from %s into central library: %s examples, %s image crops copied",
                legacy_dir, len(central_examples), copied_images)
        except Exception as exc:
            # The original local library is never deleted or modified.
            self._log("Legacy learning merge failed; original data preserved at %s: %s",
                      legacy_dir, exc)

    def _pending_learning_examples(self):
        """Return confirmed image crops that have not yet been used for training."""
        if not self.learning_dir:
            return []
        attempted = set(self.learning_data.get("trocr_v3_attempted_ids", []))
        pending = []
        for example in self.learning_data.get("examples", []):
            if (not example.get("image") or not CASE_RE.fullmatch(
                    str(example.get("confirmed_case_number") or ""))):
                continue
            example_id = self._learning_example_id(example)
            if not example_id or example_id in attempted:
                continue
            image_path = (self.learning_dir / str(example["image"])).resolve()
            try:
                image_path.relative_to(self.learning_dir.resolve())
            except ValueError:
                continue
            if image_path.is_file():
                item = dict(example)
                item["example_id"] = example_id
                pending.append(item)
        return pending

    @staticmethod
    def _even_sample(items, limit):
        """Select a deterministic, evenly spaced replay sample."""
        items = list(items or [])
        limit = max(0, int(limit))
        if limit == 0 or not items:
            return []
        if len(items) <= limit:
            return items
        if limit == 1:
            return [items[len(items) // 2]]
        indexes = [round(i * (len(items) - 1) / (limit - 1)) for i in range(limit)]
        return [items[index] for index in indexes]

    def _refresh_learning_button_state(self):
        """Keep UPDATE LEARNING enabled only when enough new confirmed crops exist."""
        button = getattr(self, "finish_batch_btn", None)
        if button is None:
            return
        if not self.learning_dir or self.busy or self.trocr_training:
            button.config(state="disabled")
            return
        attempted = bool(self.learning_data.get("trocr_v3_attempted_ids", []))
        required = 5 if attempted else 10
        pending = len(self._pending_learning_examples())
        button.config(
            state="normal" if pending >= required else "disabled",
            text=f"UPDATE LEARNING (ALL {pending} new; min {required})")

    def _learning_connect_worker(self, learning_dir, legacy_dir):
        """Connect/load/synchronize the central library without blocking Tk."""
        try:
            learning_dir.mkdir(parents=True, exist_ok=True)
            if not learning_dir.is_dir():
                raise OSError("The centralized learning location is not a directory.")
            self.learning_dir = learning_dir
            self._merge_legacy_learning_data(legacy_dir)
            self._load_learning_data(refresh_ui=False)
            self.queue.put(("learning_connect_result", True, "Shared learning library connected."))
        except Exception as exc:
            logging.exception("Background connection to shared learning library failed")
            self.learning_dir = None
            self.learning_data = {"examples": [], "corrections": {},
                                  "trocr_v2_trained_count": 0,
                                  "trocr_v2_attempted_count": 0,
                                  "trocr_v2_validation": {}, "field_regions": [],
                                  "training_type_validation": {}, "last_training_summary": {}, "field_regions_full_page": [],
                                  "trocr_v3_attempted_ids": [], "trocr_v3_trained_ids": []}
            self.queue.put(("learning_connect_result", False, str(exc)))

    @contextmanager
    def _shared_learning_write_lock(self, timeout=20):
        """Atomic SMB-compatible lock directory: never overwrite another worker's lock."""
        if not self.learning_dir:
            raise RuntimeError("Shared learning directory is unavailable")
        lock_dir = self.learning_dir / ".mfcase_write_lock"
        started = time.monotonic()
        while True:
            try:
                lock_dir.mkdir()
                break
            except FileExistsError:
                if time.monotonic() - started > timeout:
                    raise TimeoutError("Shared learning is being updated by another workstation. Retry after it finishes; the lock was not removed automatically.")
                time.sleep(0.2)
        try:
            (lock_dir / "owner.txt").write_text(f"{os.getpid()} {datetime.now().isoformat()}\n", encoding="utf-8")
            yield
        finally:
            try:
                (lock_dir / "owner.txt").unlink(missing_ok=True)
                lock_dir.rmdir()
            except OSError:
                self._log("Shared learning write lock cleanup needs attention: %s", lock_dir)

    def _persist_learning_data(self, append_example=None, strict_revision=False):
        """Serialize network writes, merge new samples, and retain a last-good DB backup."""
        with self._shared_learning_write_lock():
            db = self.learning_dir / "learning_examples_v3.json"
            previous = json.loads(db.read_text(encoding="utf-8")) if db.is_file() else {}
            if not isinstance(previous, dict):
                raise ValueError("Existing network learning DB is not a JSON object")
            if strict_revision and self._learning_revision is not None:
                db_revision = (db.name, db.stat().st_mtime_ns, db.stat().st_size) if db.exists() else None
                if db_revision != self._learning_revision:
                    raise RuntimeError("Shared learning changed on another computer; reload before changing or deleting existing examples")
            if append_example is not None:
                latest_examples = previous.get("examples", [])
                if not isinstance(latest_examples, list):
                    raise ValueError("Shared examples are malformed; refusing to overwrite")
                ids = {self._learning_example_id(ex) for ex in latest_examples}
                if self._learning_example_id(append_example) not in ids:
                    latest_examples.append(append_example)
                self.learning_data["examples"] = latest_examples
                latest_regions = previous.get("field_regions", [])
                local_regions = self.learning_data.get("field_regions", [])
                seen = {json.dumps(region, sort_keys=True) for region in latest_regions}
                for region in local_regions:
                    key = json.dumps(region, sort_keys=True)
                    if key not in seen:
                        latest_regions.append(region)
                        seen.add(key)
                self.learning_data["field_regions"] = latest_regions
                stored_full=list(previous.get("field_regions_full_page",[]) or [])
                known_full={json.dumps(entry,sort_keys=True) for entry in stored_full}
                for entry in self.learning_data.get("field_regions_full_page",[]):
                    key=json.dumps(entry,sort_keys=True)
                    if key not in known_full:
                        stored_full.append(entry)
                        known_full.add(key)
                self.learning_data["field_regions_full_page"]=stored_full
                auto={x["source_id"]:x for x in previous.get("auto_field_regions_full_page",[])
                      if isinstance(x,dict) and x.get("source_id")}
                auto.update({x["source_id"]:x for x in self.learning_data.get("auto_field_regions_full_page",[])
                             if isinstance(x,dict) and x.get("source_id")})
                self.learning_data["auto_field_regions_full_page"]=list(auto.values())
                self.learning_data["corrections"] = {**previous.get("corrections", {}), **self.learning_data.get("corrections", {})}
                for field in ("trocr_v3_attempted_ids", "trocr_v3_trained_ids"):
                    self.learning_data[field] = sorted(set(previous.get(field, [])) | set(self.learning_data.get(field, [])))
            if db.is_file():
                backup = db.with_name("learning_examples_v3.last_good.json")
                shutil.copy2(db, backup)
            temp = db.with_name(db.name + f".{os.getpid()}.tmp")
            try:
                temp.write_text(json.dumps(self.learning_data, indent=2), encoding="utf-8")
                os.replace(temp, db)
            finally:
                temp.unlink(missing_ok=True)
            self._learning_revision = (db.name, db.stat().st_mtime_ns, db.stat().st_size)
            self._sync_learning_type_databases()

    def _load_learning_data(self, refresh_ui=True):
        if not self.learning_dir:
            self.learning_data = {
                "examples": [], "corrections": {},
                "trocr_v2_trained_count": 0, "trocr_v2_attempted_count": 0,
                "trocr_v2_validation": {}, "field_regions": [],
                "field_regions_full_page":[],
                "training_type_validation": {}, "last_training_summary": {},
                "trocr_v3_attempted_ids": [], "trocr_v3_trained_ids": []
            }
            if refresh_ui:
                self._refresh_learning_stats()
            return
        db_v3 = self.learning_dir / "learning_examples_v3.json"
        db_v2 = self.learning_dir / "learning_examples_v2.json"
        legacy_db = self.learning_dir / "learning_examples.json"
        try:
            source_db = db_v3 if db_v3.exists() else (db_v2 if db_v2.exists() else legacy_db)
            loaded = json.loads(source_db.read_text(encoding="utf-8")) if source_db.exists() else {}
            if not isinstance(loaded, dict) or not isinstance(loaded.get("examples", []), list) or not isinstance(loaded.get("corrections", {}), dict):
                raise ValueError("Shared learning database has invalid structure")
            self.learning_data = {
                "examples": loaded.get("examples", []),
                "corrections": loaded.get("corrections", {}),
                "trocr_v2_trained_count": int(loaded.get("trocr_v2_trained_count", 0)),
                "trocr_v2_attempted_count": int(loaded.get("trocr_v2_attempted_count", 0)),
                "trocr_v2_validation": loaded.get("trocr_v2_validation", {}),
                "field_regions": loaded.get("field_regions", []),
                "field_regions_full_page": loaded.get("field_regions_full_page", []),
                "auto_field_regions_full_page": loaded.get("auto_field_regions_full_page", []),
                "training_type_validation": loaded.get("training_type_validation", {}),
                "last_training_summary": loaded.get("last_training_summary", {}),
                "trocr_v3_attempted_ids": list(loaded.get("trocr_v3_attempted_ids", [])),
                "trocr_v3_trained_ids": list(loaded.get("trocr_v3_trained_ids", []))
            }
            usable = []
            for example in self.learning_data["examples"]:
                example_id = self._learning_example_id(example)
                if example_id:
                    example["example_id"] = example_id
                    usable.append(example)
            attempted = set(self.learning_data["trocr_v3_attempted_ids"])
            trained = set(self.learning_data["trocr_v3_trained_ids"])
            legacy_attempted = max(
                self.learning_data["trocr_v2_attempted_count"],
                self.learning_data["trocr_v2_trained_count"])
            # Old versions tracked counts, not IDs. Mark the previously attempted
            # examples as used so the first V3.3.6 update does not re-run 200+ crops.
            if not attempted and legacy_attempted:
                attempted.update(self._learning_example_id(ex) for ex in usable[:legacy_attempted])
                attempted.discard("")
            if not attempted and usable and (
                self.learning_data.get("trocr_v2_validation") or
                self.learning_data.get("last_training_summary")
            ):
                attempted.update(self._learning_example_id(ex) for ex in usable)
                attempted.discard("")
            if not trained and self.learning_data["trocr_v2_trained_count"]:
                trained.update(self._learning_example_id(ex) for ex in usable[
                    :self.learning_data["trocr_v2_trained_count"]])
                trained.discard("")
            self.learning_data["trocr_v3_attempted_ids"] = sorted(attempted)
            self.learning_data["trocr_v3_trained_ids"] = sorted(trained)
            # A read must never rewrite the network DB: it can race with other workstations.
            self._learning_revision = (source_db.name, source_db.stat().st_mtime_ns, source_db.stat().st_size) if source_db.exists() else None
        except Exception as exc:
            logging.exception("Shared learning database could not be read; prior state preserved")
            raise RuntimeError(f"Shared learning is unreadable; scanning is blocked to protect corrections: {exc}") from exc
        if refresh_ui:
            self._refresh_learning_stats()

    @staticmethod
    def _normalized_training_type(value):
        value = str(value or "unsure").strip().lower()
        return value if value in {"handwritten", "printed"} else "unsure"

    def _sync_learning_type_databases(self):
        """Write independently inspectable category libraries from the V3 master set."""
        if not self.learning_dir:
            return
        examples = self.learning_data.get("examples", [])
        for category in ("handwritten", "printed", "unsure"):
            category_examples = [
                dict(ex) for ex in examples
                if self._normalized_training_type(ex.get("training_type")) == category]
            payload = {
                "category": category,
                "count": len(category_examples),
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "examples": category_examples
            }
            target = self.learning_dir / f"learning_{category}_examples.json"
            temp = target.with_suffix(".json.tmp")
            temp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(temp, target)

    def _show_learning_inspector(self):
        """Show what was saved, categorized, and actually tested during training."""
        if not self.learning_dir:
            messagebox.showinfo("Learning data", "Choose the source PDF folder first.")
            return
        win = tk.Toplevel(self.root)
        win.title("M.F. Learning Data Inspector")
        win.geometry("1050x650")
        win.transient(self.root)
        outer = ttk.Frame(win, padding=12)
        outer.pack(fill="both", expand=True)
        counts = {kind: sum(1 for ex in self.learning_data.get("examples", [])
                           if self._normalized_training_type(ex.get("training_type")) == kind
                           and ex.get("image"))
                  for kind in ("handwritten", "printed", "unsure")}
        ttk.Label(outer, text="SAVED LEARNING EXAMPLES", font=("Segoe UI", 14, "bold")).pack(anchor="w")
        ttk.Label(outer, text=(f"Handwritten: {counts['handwritten']}   •   Printed: {counts['printed']}   •   "
                               f"Unclassified: {counts['unsure']}"),
                  font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(3, 8))
        validation = self.learning_data.get("trocr_v2_validation", {})
        if validation:
            status_text = (f"Last model test: baseline {validation.get('baseline_accuracy_pct', 0):.1f}% → "
                           f"{validation.get('accuracy_pct', 0):.1f}% on held-out examples; "
                           f"{validation.get('training_examples', 0)} training examples; "
                           f"{validation.get('epochs_completed', 0)} passes; "
                           f"model {'activated' if validation.get('saved_as_active') else 'not replaced'}.")
        else:
            status_text = "No completed model validation run is recorded yet. Saving crops is not the same as updating model weights."
        ttk.Label(outer, text=status_text, wraplength=1000).pack(anchor="w", pady=(0, 8))
        ttk.Label(outer, text="Each row below is a saved confirmed crop. The category is chosen in Review; the OCR score is independent of this label.",
                  wraplength=1000).pack(anchor="w", pady=(0, 8))
        pane = ttk.Frame(outer); pane.pack(fill="both", expand=True)
        cols = ("type", "ocr", "confirmed", "source", "image")
        tree = ttk.Treeview(pane, columns=cols, show="headings", height=16)
        for col, title, width in (("type", "Learning library", 120), ("ocr", "Original OCR", 130),
                                  ("confirmed", "Confirmed answer", 140), ("source", "Source PDF", 310),
                                  ("image", "Saved crop", 250)):
            tree.heading(col, text=title); tree.column(col, width=width, anchor="w")
        sy = ttk.Scrollbar(pane, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sy.set)
        tree.pack(side="left", fill="both", expand=True); sy.pack(side="right", fill="y")
        for index, ex in enumerate(reversed(self.learning_data.get("examples", []))):
            if not ex.get("image"):
                continue
            category = self._normalized_training_type(ex.get("training_type"))
            tree.insert("", "end", iid=str(index), values=(category, ex.get("ocr_candidate", ""),
                        ex.get("confirmed_case_number", ""), ex.get("source_pdf", ""), ex.get("image", "")))
        bottom = ttk.Frame(outer); bottom.pack(fill="x", pady=(8, 0))
        def open_learning_folder():
            try:
                if os.name == "nt":
                    os.startfile(str(self.learning_dir))
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", str(self.learning_dir)])
                else:
                    subprocess.Popen(["xdg-open", str(self.learning_dir)])
            except Exception as exc:
                messagebox.showerror("Open learning folder", str(exc), parent=win)
        ttk.Button(bottom, text="OPEN LEARNING FOLDER", command=open_learning_folder).pack(side="left")
        ttk.Button(bottom, text="CLOSE", command=win.destroy).pack(side="right")

    def _learning_accuracy(self):
        by_source = {}
        for index, example in enumerate(self.learning_data.get("examples", [])):
            if not example.get("ocr_candidate") or not example.get("confirmed_case_number"):
                continue
            source = example.get("source_pdf") or example.get("image") or f"legacy-{index}"
            by_source[source] = example
        examples = list(by_source.values())
        if not examples:
            return 0, 0
        exact = sum(
            1 for e in examples
            if e.get("ocr_candidate") == e.get("confirmed_case_number"))
        return exact, len(examples)

    def _refresh_learning_stats(self):
        if not hasattr(self, "learning_stats_label"):
            return
        exact, total = self._learning_accuracy()
        if total:
            pct = 100.0 * exact / total
            self.learning_stats_label.config(
                text=f"Verified exact-match rate: {pct:.1f}% ({exact}/{total})")
        else:
            self.learning_stats_label.config(
                text="VERIFIED EXACT-MATCH RATE: NO CONFIRMED OCR PREDICTIONS YET")
        self._refresh_auto_eligibility_status()
        self._refresh_learning_button_state()
        if hasattr(self, "learning_type_stats_label"):
            by_type = {kind: sum(1 for ex in self.learning_data.get("examples", [])
                                 if self._normalized_training_type(ex.get("training_type")) == kind
                                 and ex.get("image"))
                       for kind in ("handwritten", "printed", "unsure")}
            self.learning_type_stats_label.config(
                text=(f"Learning examples: handwritten {by_type['handwritten']} • "
                      f"printed {by_type['printed']} • unsure {by_type['unsure']}"))
        if hasattr(self, "model_status_label"):
            validation = self.learning_data.get("trocr_v2_validation", {})
            detail = ""
            if validation:
                detail = (
                    f" | held-out TrOCR exact match: "
                    f"{validation.get('accuracy_pct', 0):.1f}% "
                    f"({validation.get('correct', 0)}/{validation.get('total', 0)})")
            trained = self.learning_data.get("trocr_v2_trained_count", 0)
            self.model_status_label.config(
                text=f"TrOCR: {'fine-tuned' if trained else 'base model'}"
                     f" • {trained} training samples{detail}")

    def _get_auto_threshold(self):
        """Fixed policy threshold, no user-adjustable confidence slider."""
        return 95.0

    def _save_learning_example(self, record, confirmed_number):
        confirmed_number=normalize_case_number(confirmed_number)
        if not CASE_RE.fullmatch(confirmed_number):
            raise ValueError("Learning accepts only 08- followed by 1–7 case digits.")
        if not self.learning_dir:
            raise OSError("The centralized learning share is unavailable; reconnect to the network before saving learning examples.")
        # Prefer the rectangle drawn by the reviewer; otherwise use the OCR rectangle.
        training_type = "handwritten"  # New examples only; retain all existing categories/data.
        examples_dir = self.learning_dir / "examples_v3" / training_type
        examples_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        image_rel = None
        snippet = record.get("snippet")
        # Keep two rectangles conceptually separate: the full green field box
        # teaches the app where INCIDENT NO. / CASE NUMBER and the number live, while the
        # neural OCR crop should contain the number itself whenever possible.
        field_rect = record.get("manual_crop_rect") or (
            snippet[3] if snippet and record.get("scan_source") == "incident" else None)
        training_rect = None
        candidates = self._valid_ocr_box_candidates(record)
        selected_candidate = next((c for c in candidates
                                   if str(c.get("number") or "").strip() == str(confirmed_number)
                                   and c.get("rect")), None)
        if record.get("manual_crop_origin") == "drawn":
            selected_candidate = None  # User's rectangle takes priority.
        elif selected_candidate is None:
            # A corrected label still belongs to the original number's image box.
            # Never select an unrelated candidate solely because it has a rectangle.
            selected_candidate = next((c for c in candidates
                if normalize_case_number(c.get("number") or "") ==
                   normalize_case_number(record.get("candidate") or "")
                and c.get("rect")), None)
        if (record.get("manual_crop_rect") and field_rect and
                (record.get("manual_crop_origin")=="drawn" or not selected_candidate)):
            training_rect=tuple(field_rect)
        elif selected_candidate:
            training_rect=selected_candidate.get("rect")
        if field_rect and not selected_candidate:
            training_rect = tuple(field_rect)
        elif not training_rect:
            training_rect = snippet[3] if snippet else None
        crop_origin=("manual-field" if record.get("manual_crop_rect") and
                     (record.get("manual_crop_origin")=="drawn" or not selected_candidate)
                     else "candidate-number-box" if selected_candidate else "ocr")
        if snippet and snippet[1] is not None and training_rect:
            from PIL import Image
            image = Image.fromarray(snippet[1]).convert("RGB")
            x0, y0, x1, y1 = [int(v) for v in training_rect]
            # A modest border preserves the first/last digit strokes without
            # adding the unrelated case-field caption to the training target.
            pad_x = max(8, int((x1 - x0) * 0.04))
            pad_y = max(5, int((y1 - y0) * 0.12))
            x0 -= pad_x; x1 += pad_x; y0 -= pad_y; y1 += pad_y
            x0 = max(0, min(image.width, x0))
            x1 = max(0, min(image.width, x1))
            y0 = max(0, min(image.height, y0))
            y1 = max(0, min(image.height, y1))
            if x1 <= x0 or y1 <= y0:
                raise ValueError("The selected crop box is empty. Draw a box around the case number.")
            crop = image.crop((x0, y0, x1, y1))
            if crop.width < 35 or crop.height < 10:
                raise ValueError("The selected crop is too small. Draw a larger box around the case-field label (INCIDENT NO. or CASE NUMBER) and the complete case number.")
            # The original full field is retained as location evidence only.
            isolated,local_box,isolation=hw5618.number_only(
                crop,require_separation=(crop_origin=='manual-field' and
                                         crop.width>crop.height*5.0))
            if isolated is None:
                crop=None
            else:
                from PIL import Image as _Image
                crop=_Image.fromarray(isolated)
            # Avoid nearly full-page crops: they teach the line recognizer the wrong task.
            if crop is not None and crop.width >= image.width * 0.85 and crop.height >= image.height * 0.35:
                raise ValueError("The crop covers too much of the page. Draw a tighter box around the case number.")
            if crop is not None:
                crop_path = examples_dir / f"{stamp}.png"
                crop.save(crop_path)
                image_rel = f"examples_v3/{training_type}/{stamp}.png"

        # A confirmed case remains valid for filing and LOCATION learning even
        # when its handwriting sample needs audit instead of model training.
        if not image_rel:
            self._log("No safe handwriting training image; location history remains available")

        candidate = str(record.get("candidate") or "").strip()
        # Remember where the reviewer selected/drew the field inside the same
        # top-right 4 x 4 inch search window. These normalized zones guide future
        # OCR passes; the full top-right search remains as a safety net.
        if snippet and snippet[1] is not None and field_rect:
            page_h, page_w = snippet[1].shape[:2]
            search_w = min(page_w, int(round(4 * 72 * 2.2)))
            search_h = min(page_h, int(round(4 * 72 * 2.2)))
            search_x = page_w - search_w
            x0, y0, x1, y1 = [float(v) for v in field_rect]
            x0 = max(float(search_x), min(float(page_w), x0))
            x1 = max(float(search_x), min(float(page_w), x1))
            y0 = max(0.0, min(float(search_h), y0))
            y1 = max(0.0, min(float(search_h), y1))
            if x1 > x0 and y1 > y0 and search_w > 0 and search_h > 0:
                region = {
                    "rect_norm": [
                        round((x0 - search_x) / search_w, 5),
                        round(y0 / search_h, 5),
                        round((x1 - search_x) / search_w, 5),
                        round(y1 / search_h, 5)],
                    "confirmed_case_number": confirmed_number,
                    "source_pdf": record.get("path").name if record.get("path") else "",
                    "saved_at": datetime.now().isoformat(timespec="seconds")
                }
                with self.data_lock:
                    regions = self.learning_data.setdefault("field_regions", [])
                    regions.append(region)
                    # Keep every verified crop: spatial planning groups them
                    # instead of discarding history after 200 examples.
                    self.learning_data["field_regions"] = regions

        # Preserve EVERY verified manual crop, both inside and outside the
        # default upper-right quarter, normalized to the ENTIRE original page.
        # This is coordinate learning only; neural model training stays separate.
        if (snippet and snippet[1] is not None and field_rect
                and record.get("manual_crop_origin")=="drawn"):
            ph,pw=snippet[1].shape[:2]
            bx0,by0,bx1,by1=map(float,field_rect)
            if 0<=bx0<bx1<=pw and 0<=by0<by1<=ph:
                region={"rect_norm":[round(bx0/pw,6),round(by0/ph,6),
                                      round(bx1/pw,6),round(by1/ph,6)],
                        "source_pdf":record.get("path").name if record.get("path") else "",
                        "confirmed_case_number":confirmed_number,
                        "saved_at":datetime.now().isoformat(timespec="seconds"),
                        "source":"reviewer-confirmed-manual-crop"}
                with self.data_lock:
                    regions=self.learning_data.setdefault("field_regions_full_page",[])
                    regions.append(region)

        example = {
            "image": image_rel,
            "crop_origin": crop_origin if image_rel else "none",
            "number_only_verified":bool(image_rel),
            "training_type": training_type,
            "ocr_candidate": candidate,
            "confirmed_case_number": confirmed_number,
            "source_pdf": record.get("path").name if record.get("path") else "",
            "ocr_score": float(record.get("score", 0.0) or 0.0),
            "engine_agreement": bool(record.get("engine_agreement", False)),
            "engines": record.get("engines", []),
            "confirmed_at": datetime.now().isoformat(timespec="seconds")
        }
        example["example_id"] = self._learning_example_id(example)
        with self.data_lock:
            if image_rel:
                self.learning_data.setdefault("examples", []).append(example)
            self._persist_learning_data(append_example=example if image_rel else None)
            (self.folder / ".case_ocr_settings.json").write_text(
                json.dumps({"year": REQUIRED_CASE_YEAR}, indent=2), encoding="utf-8")
        self._diag_event("learning_saved",file=Path(record["path"]).name,
               confirmed_case_number=confirmed_number,
               crop_origin=crop_origin,training_type=training_type,
               image_saved=bool(image_rel))
        self._diag_sync_shared()
        self._refresh_learning_stats()
        self._refresh_learning_button_state()
        self._save_state()
        return bool(image_rel)
        # Deliberately do not start training here. Training is deferred until the
        # user finishes the current import/review batch from the main window.

    def _auto_label_validation_examples(self, threshold, current_year):
        """Return distinct, manually verified reports in the existing calibration group."""
        validation_by_source = {}
        for example in self.learning_data.get("examples", []):
            source = example.get("source_pdf")
            if not (source and example.get("engine_agreement")
                    and float(example.get("ocr_score", 0.0) or 0.0) >= threshold
                    and example.get("ocr_candidate")
                    and example.get("confirmed_case_number")):
                continue
            if (not CASE_RE.fullmatch(str(example.get("confirmed_case_number") or ""))
                    or not CASE_RE.fullmatch(str(example.get("ocr_candidate") or ""))):
                continue
            validation_by_source[source] = example
        return list(validation_by_source.values())

    @staticmethod
    def _upper_error_bound(errors, total, confidence=0.95):
        """One-sided exact Clopper-Pearson upper confidence bound for error rate."""
        if total <= 0 or errors < 0 or errors >= total:
            return 1.0
        alpha = 1.0 - confidence
        if errors == 0:
            return 1.0 - alpha ** (1.0 / total)

        def binomial_cdf(k, n, p):
            # P(X <= k) for X~Binomial(n,p), evaluated recursively for stability.
            if p <= 0.0:
                return 1.0
            if p >= 1.0:
                return 0.0
            term = (1.0 - p) ** n
            terms = [term]
            for i in range(k):
                term *= ((n - i) / (i + 1.0)) * (p / (1.0 - p))
                terms.append(term)
            return min(1.0, math.fsum(terms))

        lo, hi = 0.0, 1.0
        for _ in range(70):
            mid = (lo + hi) / 2.0
            if binomial_cdf(errors, total, mid) > alpha:
                lo = mid
            else:
                hi = mid
        return hi

    def _auto_label_calibration_stats(self):
        try:
            threshold = self._get_auto_threshold()
            current_year = self.scan_year_prefix or self._year_prefix()
        except Exception:
            return 0, 0, 0.0, 1.0, None
        validation = self._auto_label_validation_examples(threshold, current_year)
        total = len(validation)
        correct = sum(
            1 for example in validation
            if example.get("ocr_candidate") == example.get("confirmed_case_number"))
        errors = total - correct
        pct = (100.0 * correct / total) if total else 0.0
        upper = self._upper_error_bound(errors, total, 0.95)
        return total, correct, pct, upper, threshold

    def _refresh_auto_eligibility_status(self):
        label = getattr(self, "auto_eligibility_label", None)
        if label is None:
            return
        threshold = self._get_auto_threshold()
        year = getattr(self, "scan_year_prefix", "") or (
            self.year_var.get().strip()[-2:] if hasattr(self, "year_var") else "08")
        message = (f"Auto-confirm ON: one complete localized 08- case at {threshold:g}% "
                   "or higher, with no competing high-confidence case.")
        label.config(text=message)

    def _auto_label_block_reason(self, score, engine_agreement, candidate, engine_scores=None):
        """Require a valid 08- case meeting the selected threshold in either engine."""
        normalized = normalize_case_number(candidate)
        if not CASE_RE.fullmatch(normalized):
            return "Case number must start with 08- followed by 1–7 case digits."
        observed_year=normalized.split("-",1)[0]
        if observed_year!=REQUIRED_CASE_YEAR:
            return "Case number must start with the literal 08- prefix."
        try:
            observed_score = float(score or 0.0)
        except Exception:
            observed_score = 0.0
        # score is this candidate's maximum OCR confidence across engines.
        if not math.isfinite(observed_score) or observed_score < self._get_auto_threshold():
            return f"At least one OCR engine must report {self._get_auto_threshold():g}%."
        return None

    def _auto_label_is_calibrated(self, score, engine_agreement, candidate, distinct_count=1):
        return self._auto_label_block_reason(score, engine_agreement, candidate) is None

    def _sequence_case_number(self, record):
        """Independent manual and machine completions have equal anchor priority."""
        if (not record or record.get("sequence_inferred") or
                record.get("case_draft") or
                str(record.get("status") or "").startswith("Unverified")):
            return None
        status=str(record.get("status") or "").lower()
        human_verified=(bool(record.get("manual_approved")) and
                        not bool(record.get("auto_labeled")))
        # Older verified automatic records may lack manual_approved=True.
        # A merely suggested/unapproved OCR number is never an anchor.
        machine_verified=bool(record.get("auto_labeled")) and (
            bool(record.get("manual_approved")) or
            bool(record.get("exported")) or
            "auto verified" in status or
            "automatically approved" in status or
            "ocr approved" in status or
            status.startswith("completed"))
        if not (human_verified or machine_verified):
            return None
        # Human corrections override stale OCR ambiguity flags. Machine
        # results must have no unresolved OCR conflicts or exclusions.
        if not human_verified and (record.get("scan_ambiguous") or
                                   record.get("sequence_conflict") or
                                   record.get("sequence_excluded")):
            return None
        number=normalize_case_number(record.get("confirmed") or "")
        if not CASE_RE.fullmatch(number):
            return None
        return _case_identity(number)

    @staticmethod
    def _initial_review_reading(record, candidate_values, sequence_values, sequence_message):
        """Use the scan first; a two-sided suggestion may prefill a blank read."""
        existing = record.get("confirmed")
        if existing:
            return str(existing)
        candidate = record.get("candidate")
        if candidate:
            return str(candidate)
        return str(sequence_values[0]) if len(sequence_values) == 1 else ""

    def _sequence_index(self):
        """Snapshot independent anchors across unsent and already-sent PDFs."""
        order=[]
        for iid,rec in self._sequence_order_with_sent_history(include_unscanned=False):
            order.append((len(order),iid,self._sequence_case_number(rec)))
        return {iid:(order,i) for i,(_n,iid,_case) in enumerate(order)}

    def _sequence_neighbors(self, iid, sequence_index=None):
        """Find independent, two-sided bounding anchors at any distance.

        Never use two preceding/next files as a substitute for a real
        before AND after pair, and never cascade inferred values.
        """
        indexed=sequence_index if sequence_index is not None else self._sequence_index()
        located=indexed.get(iid)
        if located is None:
            return None
        group,index=located
        before=next(((row[1],row[2]) for row in reversed(group[:index]) if row[2]),None)
        after=next(((row[1],row[2]) for row in group[index+1:] if row[2]),None)
        if before and after and _case_identity(before[1])==_case_identity(after[1]):
            return before,after
        return None

    def _sequence_hint_for_iid(self, iid):
        neighbors = self._sequence_neighbors(iid)
        if not neighbors:
            return [], ""
        left, right = neighbors
        if left[1] == right[1]:
            return [left[1]], (
                f"Both bounding anchors read {left[1]} in original file order. "
                "Sequence suggestion only; verify against this PDF.")
        return [], (
            f"Bounding independent anchors read {left[1]} / {right[1]}. "
            "Scan takes precedence; review this PDF manually.")

    def _run_before_after(self):
        """Explicit recheck; never release an unfinished scan's export barrier."""
        if self.busy or self.export_busy or self._training_blocks_work():
            messagebox.showinfo("BEFORE/AFTER", "Finish the current operation before checking.")
            return
        if not self.folder or not self.records:
            return
        if getattr(self, "_sequence_review_pending", False):
            messagebox.showinfo("BEFORE/AFTER", "Finish scanning all files before checking.")
            return
        commit = getattr(self, "_scan_case_commit", None)
        if commit:
            commit()
            if getattr(self,"_scan_case_editor",None) is not None:
                return
        changed = self._apply_sequence_consensus()
        self._log(f"BEFORE/AFTER complete: {changed} file(s) updated; unresolved conflicts remain for review.")
        anchors=sum(bool(self._sequence_case_number(r)) for r in self.records.values())
        message=f"BEFORE/AFTER: {changed} file(s) updated; {anchors} confirmed anchors."
        if not changed:
            message+=" Accept matching cases on BOTH sides using the green checks; unaccepted drafts cannot anchor."
        self.status.config(text=message)
        messagebox.showinfo("BEFORE/AFTER",message)

    def _finish_scan_sequence_review(self):
        """Only the worker's all-files-done event can release the export barrier."""
        self._sequence_review_pending=True
        self._scan_progress_update(len(self.scan_items),"CHECKING BEFORE / AFTER")
        self._apply_sequence_consensus()
        self._sequence_review_pending=False
        self._save_state()
        self._scan_progress_update(len(self.scan_items),"SCAN AND SEQUENCE CHECK COMPLETE")

    def _incremental_sequence_check(self, changed_iid=None):
        """Only recheck ranges affected by a new independent anchor.

        Matching manually confirmed boundaries can span hundreds of pages.
        Unrelated files do not need to be written again for each scanned PDF.
        The full recheck at SCAN completion and STOP remains authoritative.
        """
        if not self.folder:
            return 0
        order=self._sequence_order_with_sent_history()
        anchors=tuple((iid,self._sequence_case_number(rec))
                      for iid,rec in order if self._sequence_case_number(rec))
        if anchors==getattr(self,"_last_sequence_signature",None):
            return 0
        self._last_sequence_signature=anchors
        if changed_iid is None:
            return self._apply_sequence_consensus()
        order=self._sequence_order_with_sent_history()
        index=next((j for j,(iid,_r) in enumerate(order) if iid==changed_iid),None)
        if index is None:
            return self._apply_sequence_consensus()
        case=self._sequence_case_number(order[index][1])
        if not case:
            return self._apply_sequence_consensus()
        humans=[j for j,(_iid,r) in enumerate(order)
                if self._sequence_case_number(r) and r.get("manual_approved")
                and not r.get("auto_labeled")]
        previous=max((j for j in humans if j<index), default=None)
        following=min((j for j in humans if j>index), default=None)
        # Without a human on a side, use the closest matching independent
        # anchor; this covers legacy automatic-only before/after cases.
        if previous is None:
            previous=max((j for j in range(index)
                if self._sequence_case_number(order[j][1])==case),default=index)
        if following is None:
            following=min((j for j in range(index+1,len(order))
                if self._sequence_case_number(order[j][1])==case),default=index)
        left=min(previous,index)
        right=max(following,index)
        if left>=right:
            return 0
        return self._apply_sequence_consensus(affected_range=(left,right+1))

    def _sequence_consensus_plan(self, order, positions=None):
        """Use actual bounding anchors; never search past conflicts for a match.

        Two explicit human boundaries may correct automatic reads between them.
        Otherwise use the nearest independent anchor on each side.
        """
        anchors=[self._sequence_case_number(r) for _,r in order]
        independent=[i for i,a in enumerate(anchors) if a]
        human=[i for i in independent if order[i][1].get("manual_approved")
               and not order[i][1].get("auto_labeled")]
        human_set=set(human);plan={};conflicts=set()
        for pos in (positions if positions is not None else range(len(order))):
            if pos in human_set:continue
            iid,r=order[pos];status=str(r.get("status") or "")
            if (r.get("exported") or r.get("case_draft") or r.get("sequence_excluded") or
                status in ("Not scanned","Scanning","Moved to BLANKS") or status.startswith("Scan error:")):
                continue
            hl=bisect_left(human,pos)-1;hr=bisect_right(human,pos)
            if hl>=0 and hr<len(human) and anchors[human[hl]]==anchors[human[hr]]:
                left,right=human[hl],human[hr]
            else:
                lo=bisect_left(independent,pos)-1;hi=bisect_right(independent,pos)
                if lo<0 or hi>=len(independent):continue
                left,right=independent[lo],independent[hi]
            if anchors[left]!=anchors[right]:
                conflicts.add(pos);continue
            plan[pos]=(anchors[left],[order[left][0],order[right][0]])
        for pos in (positions if positions is not None else range(len(order))):
            if pos in plan or pos in conflicts or pos in human_set:continue
            iid,r=order[pos];status=str(r.get("status") or "")
            if (r.get("exported") or r.get("case_draft") or r.get("sequence_excluded")
                    or r.get("scan_ambiguous") or anchors[pos]
                    or status in ("Not scanned","Scanning","Moved to BLANKS")
                    or status.startswith("Scan error:")):
                continue
            score=float(r.get("sequence_original_score",r.get("score",0)) or 0)
            if not math.isfinite(score) or score>75:continue
            prior=bisect_left(independent,pos)-1
            if prior<0:continue
            left=independent[prior];anchor=order[left][1]
            strength=float(anchor.get("score") or 0)
            if not math.isfinite(strength) or strength<95:continue
            # Never skip a nearer conflicting/manual anchor or chain guesses.
            plan[pos]=(anchors[left],[order[left][0]])
        return plan,conflicts

    def _apply_sequence_consensus(self, affected_range=None):
        with self._perf_measure("before_after"):
            result=self._apply_sequence_consensus_impl(affected_range=affected_range)
        self._diag_event("performance_stage",stage="before_after",
                         ms=round(self._perf_stages.get("before_after",(0,0))[0],2))
        return result

    def _flag_context_outliers(self, order, protected_positions=()):
        """Review automatic outliers using at most ten original neighbors per side.

        Medians prevent one erroneous neighboring OCR result from becoming the
        boundary for an entire run. This is a review gate, never digit correction.
        Matching before/after decisions and explicit human approvals take priority.
        """
        from statistics import median
        strong=[]
        for _,r in order:
            case=self._sequence_case_number(r)
            strong.append(int(case.split("-")[1]) if case and
                          float(r.get("score") or 0)>=95 else None)
        flagged=[]
        for pos,(iid,r) in enumerate(order):
            if (iid not in self.records or r.get("exported") or
                    not r.get("auto_labeled") or r.get("sequence_inferred") or
                    pos in protected_positions or strong[pos] is None):
                continue
            left=[v for v in strong[max(0,pos-10):pos] if v is not None]
            right=[v for v in strong[pos+1:pos+11] if v is not None]
            if not left or not right:
                continue
            low,high=sorted((median(left),median(right)))
            if not low<=strong[pos]<=high:
                flagged.append((iid,r,low,high))
        for iid,r,low,high in flagged:
            r.update(confirmed="",manual_approved=False,auto_labeled=False,
                     sequence_conflict=True,
                     status=f"REVIEW: OCR outside nearby confirmed range 08-{low:g} to 08-{high:g}; verify case")
            if self.completed_tree.exists(iid):self.completed_tree.delete(iid)
            r["completed_iid"]=None
            self._insert_scan_row(iid,r)
            self._diag_event("sequence_context_review",file=str(r["path"]),
                             candidate=r.get("candidate"),score=r.get("score"),
                             neighbors_each_side=10)
        return len(flagged)

    def _apply_sequence_consensus_impl(self, affected_range=None):
        """Apply and save current anchors during scans, on stop, and at completion."""
        if not self.folder or self.export_busy:
            return 0
        order=self._sequence_order_with_sent_history()
        if not order:
            return 0
        allowed=range(*affected_range) if affected_range is not None else None
        plan,conflicted=self._sequence_consensus_plan(order,positions=allowed)
        # Wait for the full scan: future pages must be available before this gate.
        changed=self._flag_context_outliers(order,plan) if not self.busy else 0
        if changed:
            plan,conflicted=self._sequence_consensus_plan(order,positions=allowed)
        for pos,(iid,r) in enumerate(order):
            if affected_range is not None and pos not in allowed:
                continue
            if iid not in self.records or r.get("exported") or (r.get("manual_approved") and not r.get("auto_labeled")
                                     and not r.get("sequence_inferred")):
                continue
            desired=plan.get(pos)
            current=_case_identity(r.get("confirmed"))
            wanted=_case_identity(desired[0]) if desired else None
            # A previous-only anchor may SUGGEST a value, but it is never
            # sufficient evidence to approve/rename/send this PDF. If that
            # suggestion is no longer supported, restore the original read.
            if (r.get("sequence_previous_prefill") and
                    (not desired or len(desired[1]) != 1 or
                     wanted != _case_identity(r.get("candidate")))):
                if not desired:
                    r.update(candidate=r.get("sequence_original_candidate", ""),
                             score=r.get("sequence_original_score",r.get("score",0)),
                             sequence_previous_prefill=False,sequence_anchors=[],
                             status="Previous-case suggestion no longer supported — REVIEW")
                    self._insert_scan_row(iid,r)
                    changed+=1
            if r.get("sequence_inferred") and (not desired or wanted!=current):
                r.update(candidate=r.get("sequence_original_candidate", ""),
                         score=r.get("sequence_original_score",r.get("score",0)),
                         confirmed="",manual_approved=False,auto_labeled=False,
                         sequence_inferred=False,sequence_anchors=[],sequence_conflict=True,
                         scan_ambiguous=bool(r.get("sequence_original_ambiguous",False)),
                         status="Prior before/after result no longer supported — REVIEW")
                if self.completed_tree.exists(iid):self.completed_tree.delete(iid)
                r["completed_iid"]=None
                self._insert_scan_row(iid,r)
                changed+=1
            if pos in conflicted or not desired:
                continue
            if len(desired[1]) == 1:
                # The preceding high-confidence case only PREFILLS the case
                # number field. It cannot confirm the document, change its
                # filename or make it eligible for SEND FILES.
                if (r.get("sequence_previous_prefill") and not r.get("sequence_inferred")
                        and not r.get("manual_approved") and not r.get("confirmed")
                        and _case_identity(r.get("candidate")) == wanted
                        and r.get("sequence_anchors") == desired[1]):
                    continue
                if not r.get("sequence_inferred") and not r.get("sequence_previous_prefill"):
                    r["sequence_original_candidate"]=r.get("candidate", "")
                    r["sequence_original_score"]=r.get("score",0)
                    r["sequence_original_ambiguous"]=r.get("scan_ambiguous",False)
                prior=r.get("sequence_original_candidate",r.get("candidate",""))
                r.update(confirmed="",candidate=desired[0],manual_approved=False,
                         auto_labeled=False,sequence_inferred=False,
                         sequence_previous_prefill=True,sequence_conflict=False,
                         sequence_anchors=desired[1],
                         status=f"REVIEW — PREVIOUS CASE {desired[0]} PREFILLED; VERIFY AND APPROVE")
                if self.completed_tree.exists(iid):
                    self.completed_tree.delete(iid)
                r["completed_iid"]=None
                self._insert_scan_row(iid,r)
                self._diag_event("sequence_prefill",file=str(r["path"]),
                                 suggested=desired[0],original=prior,
                                 anchors=desired[1],auto_completed=False)
                changed+=1
                continue
            if r.get("sequence_previous_prefill"):
                # A genuine MATCHING before+after pair may still complete
                # the PDF. Preserve its pre-prefill OCR read for undo/recheck.
                r["candidate"]=r.get("sequence_original_candidate",r.get("candidate",""))
                r["sequence_previous_prefill"]=False
            if (r.get("sequence_inferred") and wanted==_case_identity(r.get("confirmed"))
                    and (not r.get("sequence_anchors") or r.get("sequence_anchors")==desired[1])):
                continue
            if current==wanted and r.get("manual_approved") and not r.get("sequence_inferred"):
                continue
            if not r.get("sequence_inferred"):
                r["sequence_original_candidate"]=r.get("candidate", "")
                r["sequence_original_score"]=r.get("score",0)
                r["sequence_original_ambiguous"]=r.get("scan_ambiguous",False)
            prior=r.get("confirmed") or r.get("candidate") or "unreadable"
            case=desired[0]
            r.update(confirmed=case,candidate=case,sequence_inferred=True,
                     sequence_conflict=False,scan_ambiguous=False,
                     sequence_anchors=desired[1],manual_approved=True,
                     auto_labeled=True,approval_rule_version="5.3.2")
            self._move_to_completed(iid,batch=True)
            method="Previous 95%+ anchor" if len(desired[1])==1 else "Before/after"
            r["status"]=(f"{method} {case}; replaced {prior} — ready for SEND FILES (inferred)")
            self._insert_scan_row(iid,r)
            self._diag_event("sequence_update",file=str(r["path"]),original=prior,
                             confirmed=case,anchors=desired[1],score=float(r.get("score") or 0),
                             inference_method="carry_forward" if len(desired[1])==1 else "before_after")
            changed+=1
        if changed:
            self._refresh_review_controls()
            self._update_completed_count()
            if not self._save_state():
                raise OSError("Could not persist before/after decisions.")
        if not self.busy:
            self._rename_completed_in_place()
        return changed

    def _rename_completed_in_place(self):
        """Rename only confirmed decisions after sequence review; never send here."""
        if self.busy or self.export_busy or not self.folder:
            return
        for iid,r in self.records.items():
            source=Path(r["path"]) if r.get("path") else None
            case=_case_identity(r.get("confirmed"))
            if (not source or source.parent!=Path(self.folder) or not source.is_file()
                    or r.get("exported") or not r.get("manual_approved") or not case
                    or r.get("case_draft") or r.get("sequence_conflict") or r.get("scan_ambiguous")):
                continue
            original=r.get("original_name") or source.name
            base=f"{Path(original).stem}_{case}"
            if re.fullmatch(re.escape(base)+r"(?: \([1-9][0-9]*\))?\.pdf",source.name,re.I):
                continue
            target=None
            reserved=False
            try:
                counter=0
                while True:
                    target=source.parent/(base+(f" ({counter})" if counter else "")+".pdf")
                    try:
                        fd=os.open(str(target),os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
                        os.close(fd);reserved=True;break
                    except FileExistsError:
                        counter+=1
                os.replace(source,target);reserved=False
                r["original_name"]=original
                r["original_path"]=r.get("original_path") or str(source)
                r["path"]=target
                r["status"]=f"Completed: {target.name} — ready for SEND FILES"
                r["export_error"]=""
                self._insert_scan_row(iid,r)
            except OSError as exc:
                if reserved and target is not None:
                    try: target.unlink()
                    except OSError: pass
                r["export_error"]=str(exc)
                r["status"]=f"Approved; rename failed: {exc} — retry SEND FILES"
                self._insert_scan_row(iid,r)
        if not self._save_state():
            raise OSError("Could not persist renamed file paths; check folder access.")

    def _recheck_after_decision(self):
        """Refresh dependent approvals after a human changes an anchor."""
        if self.export_busy or self._training_blocks_work():
            return
        try:
            self._last_sequence_signature=None
            self._apply_sequence_consensus()
        except Exception as exc:
            self._sequence_review_pending=True
            self._log("Before/after recheck failed: %s",exc)
            self.status.config(text="BEFORE/AFTER CHECK FAILED; RENAMING IS BLOCKED. FINISH SCAN TO RETRY.")

    def _configure_trocr_token_ids(self, model, processor):
        """
        VisionEncoderDecoderModel uses the OUTER composite config when shifting
        labels during training. Some Transformers releases/checkpoints do not
        expose pad_token_id on that config even though the tokenizer knows it.
        Copy the tokenizer's IDs onto the model and generation configs explicitly.
        """
        tokenizer = processor.tokenizer
        pad_token_id = getattr(tokenizer, "pad_token_id", None)
        if pad_token_id is None:
            raise RuntimeError(
                "TrOCR tokenizer has no pad_token_id. The handwriting model cannot be fine-tuned safely.")

        config = model.config
        config.pad_token_id = int(pad_token_id)

        eos_token_id = getattr(tokenizer, "eos_token_id", None)
        if eos_token_id is not None:
            config.eos_token_id = int(eos_token_id)

        decoder_start_token_id = getattr(config, "decoder_start_token_id", None)
        if decoder_start_token_id is None:
            for attr in ("bos_token_id", "cls_token_id", "eos_token_id"):
                candidate = getattr(tokenizer, attr, None)
                if candidate is not None:
                    decoder_start_token_id = candidate
                    break
        if decoder_start_token_id is None:
            decoder_config = getattr(config, "decoder", None)
            decoder_start_token_id = getattr(decoder_config, "bos_token_id", None)
        if decoder_start_token_id is None:
            raise RuntimeError(
                "TrOCR model has no decoder_start_token_id. Cannot prepare decoder inputs for training.")
        config.decoder_start_token_id = int(decoder_start_token_id)

        decoder_config = getattr(config, "decoder", None)
        if decoder_config is not None:
            decoder_config.pad_token_id = int(pad_token_id)
            if eos_token_id is not None:
                decoder_config.eos_token_id = int(eos_token_id)

        generation_config = getattr(model, "generation_config", None)
        if generation_config is not None:
            generation_config.pad_token_id = int(pad_token_id)
            generation_config.decoder_start_token_id = int(decoder_start_token_id)
            if eos_token_id is not None:
                generation_config.eos_token_id = int(eos_token_id)

        return int(pad_token_id), int(decoder_start_token_id)

    def _local_model_source(self):
        """Use only already-local checkpoints; no network discovery/copy."""
        roots=[]
        if self.learning_dir is not None:
            roots.extend([Path(self.learning_dir)/"trocr_v2_finetuned",
                          Path(self.learning_dir)/"trocr_v2_previous"])
        local_root=(Path(os.environ.get("LOCALAPPDATA") or
                    str(Path.home()/".local"/"share"))/"MFCaseOCR"/"models")
        if local_root.is_dir():
            old=sorted(
                [p for p in local_root.glob("trocr-*")
                 if (p/".mfcase_complete").is_file() and
                    (p/"config.json").is_file()],
                key=lambda p:p.stat().st_mtime,reverse=True)
            roots.extend(old)
            roots.append(local_root/"offline-base")
        for path in roots:
            if (path/"config.json").is_file():
                self._cached_model_dir=path
                return path
        return None


    def _prepare_offline_models(self):
        if (self.busy or self.trocr_training or self.update_busy or
                self.export_busy or self._offline_prepare_busy):
            messagebox.showinfo("Preparing offline models",
                "Finish scanning, training or updates before preparing models.")
            return
        if not messagebox.askyesno("Prepare offline models",
            "This action connects to the public model providers to DOWNLOAD "
            "the EasyOCR and TrOCR program models only. It never uploads "
            "PDFs, case numbers, learning images or logs. Model files may "
            "require over 1 GB of disk space. Continue?"):
            return
        self._offline_prepare_busy=True
        if getattr(self,"prepare_offline_btn",None) is not None:
            self.prepare_offline_btn.config(state="disabled",text="DOWNLOADING MODELS…")
        self.status.config(text="DOWNLOADING PUBLIC OCR MODELS BY EXPLICIT REQUEST; "
                                "no case files are transmitted.")
        threading.Thread(target=self._prepare_offline_models_worker,
            daemon=True,name="MFCase-ExplicitOfflineModelDownload").start()

    def _prepare_offline_models_worker(self):
        prior_env={k:os.environ.get(k) for k in
                   ("HF_HUB_OFFLINE","TRANSFORMERS_OFFLINE")}
        os.environ["HF_HUB_OFFLINE"]="0"
        os.environ["TRANSFORMERS_OFFLINE"]="0"
        try:
            import easyocr
            from transformers import TrOCRProcessor,VisionEncoderDecoderModel
            # Only PUBLIC model asset downloads, never content from PDFs.
            easyocr.Reader(["en"],gpu=False,download_enabled=True,verbose=False)
            name="microsoft/trocr-base-handwritten"
            processor=TrOCRProcessor.from_pretrained(name,local_files_only=False)
            model=VisionEncoderDecoderModel.from_pretrained(name,local_files_only=False)
            folder=(Path(os.environ.get("LOCALAPPDATA") or
                  str(Path.home()/".local"/"share"))/
                  "MFCaseOCR"/"models"/"offline-base")
            folder.mkdir(parents=True,exist_ok=True)
            # Save completely before marking the checkpoint available offline.
            model.save_pretrained(str(folder),safe_serialization=True)
            processor.save_pretrained(str(folder))
            (folder/".mfcase_complete").write_text("offline-base-v1",encoding="ascii")
            self.queue.put(("offline_models_ready",str(folder)))
        except Exception as exc:
            self.queue.put(("offline_models_error",str(exc)))
        finally:
            for key,previous in prior_env.items():
                if previous is None:
                    os.environ.pop(key,None)
                else:
                    os.environ[key]=previous

    def _preload_models_worker(self):
        try:
            if pymupdf is None or easyocr is None or np is None:
                raise RuntimeError("Required OCR packages are missing. Run SETUP.bat to repair them.")
            try:
                import torch
                gpu = bool(torch.cuda.is_available())
            except Exception:
                gpu = False
            try:
                reader = easyocr.Reader(["en"], gpu=gpu, verbose=False,
                                        download_enabled=False)
            except Exception:
                reader = easyocr.Reader(["en"], gpu=False, verbose=False,
                                        download_enabled=False)
                gpu = False
            self.reader = reader
            self.queue.put(("model_status", f"EasyOCR ready ({'GPU' if gpu else 'CPU'}); loading handwriting model…"))
            self._load_trocr()
            if self.trocr_model is None or self.trocr_processor is None:
                raise RuntimeError("Handwriting recognition model did not initialize")
            self.queue.put(("startup_models_ready",))
        except Exception as exc:
            logging.exception("Startup OCR preload failed")
            self.queue.put(("startup_models_error", str(exc)))

    def _begin_model_preload(self):
        if not self.learning_dir or self._startup_ready:
            return
        self._startup_notice("Loading OCR engines and trained recognition model before scanning…")
        self._loader_start("preload", "Loading recognition models before enabling the application…")
        threading.Thread(target=self._preload_models_worker, daemon=True,
                         name="MFCase-PreloadModels").start()

    def _complete_startup(self):
        self._startup_ready = True
        self._startup_error = None
        self._loader_finish("preload")
        self._close_startup_notice()
        self.choose_folder_btn.config(state="normal")
        self.refresh_root_btn.config(state="normal" if self.folder else "disabled")
        self._refresh_scan_button()
        self.status.config(text="OCR READY — UPDATES ARE CHECKED AT STARTUP; "
                                "background learning uses local confirmed examples.")
        self.root.after_idle(self._restore_last_folder)
        self._schedule_post_scan_learning()

    def _fail_startup(self, reason):
        self._startup_ready = False
        self._startup_error = reason
        self._loader_finish("preload")
        self.choose_folder_btn.config(state="disabled")
        self.refresh_root_btn.config(state="disabled")
        self.scan_btn.config(state="disabled")
        self._startup_notice("Startup blocked: " + reason + "  Check network/setup, then click RETRY STARTUP.")
        if getattr(self, "_startup_retry_btn", None) is not None:
            self._startup_retry_btn.config(state="normal")
        self.status.config(text="OFFLINE MODELS NOT READY — "+reason+
                            ". Restore the existing installed OCR model files, then click RETRY STARTUP.")
        self._log("Startup blocked: %s", reason)

    def _load_trocr(self):
        if self.trocr_model is not None:
            return
        self.queue.put(("model_load_start", "Loading handwriting recognition and learned model…"))
        try:
            import torch
            from transformers import TrOCRProcessor, VisionEncoderDecoderModel

            self.torch_device = "cuda" if torch.cuda.is_available() else "cpu"
            model_dir = self._local_model_source()
            if model_dir and (model_dir / "config.json").exists():
                model_source = str(model_dir)
                processor = TrOCRProcessor.from_pretrained(model_source, local_files_only=True)
                model = VisionEncoderDecoderModel.from_pretrained(model_source, local_files_only=True)
                model_status = "fine-tuned local model"
            else:
                model_source = "microsoft/trocr-base-handwritten"
                processor = TrOCRProcessor.from_pretrained(
                    model_source,local_files_only=True)
                model = VisionEncoderDecoderModel.from_pretrained(
                    model_source,local_files_only=True)
                model_status = "Microsoft base handwriting model"
            model.to(self.torch_device)
            self._configure_trocr_token_ids(model, processor)
            model.eval()
            self.trocr_processor = processor
            self.trocr_model = model
            self.trocr_error = ""
            self._log("Loaded %s on %s", model_status, self.torch_device)
            try:
                self.queue.put(("model_status", f"TrOCR: {model_status} • {self.torch_device.upper()}"))
            except Exception:
                pass
        finally:
            self.queue.put(("model_load_done",))

    def _trocr_read(self, image):
        return self._trocr_read_scored(image)[0]

    def _budgeted_ocr(self, engine, image, callback, empty):
        budget=getattr(self,"_page_ocr_budget",None)
        if budget is None or budget["thread"]!=threading.get_ident():
            return callback()
        arr=np.asarray(image)
        key=(engine,arr.shape,str(arr.dtype),hashlib.sha256(arr.tobytes()).digest())
        if key in budget["cache"]:
            return budget["cache"][key]
        limit=(1 if engine=="printed" else
               (5 if budget.get("handwriting_uncertain") else 3))
        if budget[engine]>=limit:
            return empty
        budget[engine]+=1
        result=callback()
        if engine=="handwriting":
            try:
                value,score=result
                if (not CASE_RE.fullmatch(normalize_case_number(value)) or
                    float(score or 0)<95):
                    budget["handwriting_uncertain"]=True
            except (TypeError,ValueError):
                budget["handwriting_uncertain"]=True
        budget["cache"][key]=result
        return result

    def _trocr_read_scored(self, image):
        with self._perf_measure("trocr"):
            return self._budgeted_ocr("handwriting",image,
                lambda:self._trocr_read_scored_impl(image),("",0.0))

    def _trocr_read_scored_impl(self, image):
        if self.trocr_error:
            return "", 0.0
        try:
            from PIL import Image
            import torch
            self._load_trocr()
            pil = Image.fromarray(image).convert("RGB") if isinstance(image, np.ndarray) else image.convert("RGB")
            pixels = self.trocr_processor(images=pil, return_tensors="pt").pixel_values.to(self.torch_device)
            with torch.inference_mode():
                output = self.trocr_model.generate(
                    pixels, max_new_tokens=24, num_beams=2,
                    early_stopping=True, return_dict_in_generate=True, output_scores=True)
            text = self.trocr_processor.batch_decode(
                output.sequences, skip_special_tokens=True)[0].strip()
            transitions = self.trocr_model.compute_transition_scores(
                output.sequences, output.scores,
                beam_indices=getattr(output, "beam_indices", None),
                normalize_logits=True)
            tokens = output.sequences[:, -transitions.shape[1]:]
            special = self.trocr_processor.tokenizer.all_special_ids
            valid = torch.ones_like(tokens, dtype=torch.bool)
            for token_id in special:
                valid &= tokens != token_id
            scores = transitions[valid]
            confidence = float(scores.mean().exp().item() * 100.0) if scores.numel() else 0.0
            return text, confidence
        except Exception as exc:
            if not self.trocr_error:
                self.trocr_error = str(exc)
                self._log("TrOCR unavailable: %s", exc)
                try:
                    self.queue.put(("engine_warning", f"TrOCR unavailable; EasyOCR will continue. Details: {exc}"))
                except Exception:
                    pass
            return "", 0.0

    def _open_training_progress(self):
        if self.training_popup is not None:
            try:
                if self.training_popup.winfo_exists():
                    self.training_popup.lift()
                    return
            except Exception:
                pass
        popup = tk.Toplevel(self.root)
        popup.title("Update Learning")
        popup.geometry("560x220")
        popup.resizable(False, False)
        popup.transient(self.root)
        popup.protocol("WM_DELETE_WINDOW", self._training_popup_close_requested)
        self.training_popup = popup
        frame = ttk.Frame(popup, padding=18)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="UPDATING LEARNING", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        self.training_stage_label = ttk.Label(frame, text="STARTING...", wraplength=510)
        self.training_stage_label.pack(anchor="w", pady=(12, 8))
        self.training_progressbar = ttk.Progressbar(
            frame, mode="determinate", maximum=100, value=0)
        self.training_progressbar.pack(fill="x", pady=(0, 5))
        self.training_percent_label = ttk.Label(
            frame, text="0%", font=("Segoe UI", 10, "bold"))
        self.training_percent_label.pack(anchor="e")
        bottom = ttk.Frame(frame)
        bottom.pack(fill="x", pady=(8, 0))
        self.training_pause_button = ttk.Button(
            bottom, text="PAUSE", command=self._toggle_learning_pause)
        self.training_pause_button.pack(side="left")
        self.training_stop_button = ttk.Button(
            bottom, text="STOP", command=self._stop_learning_update)
        self.training_stop_button.pack(side="left", padx=(8, 0))
        self.training_close_button = ttk.Button(
            bottom, text="CLOSE", state="disabled", command=self._close_training_popup)
        self.training_close_button.pack(side="right")
        popup.update_idletasks()

    def _toggle_learning_pause(self):
        if not self.trocr_training:
            return
        if self.learning_pause_event.is_set():
            self.learning_pause_event.clear()
            if self.training_pause_button:
                self.training_pause_button.config(text="RESUME")
            if self.training_stage_label:
                self.training_stage_label.config(text="LEARNING PAUSED.")
        else:
            self.learning_pause_event.set()
            if self.training_pause_button:
                self.training_pause_button.config(text="PAUSE")
            if self.training_stage_label:
                self.training_stage_label.config(text="RESUMING LEARNING...")

    def _stop_learning_update(self):
        if not self.trocr_training:
            return
        self.learning_stop_event.set()
        self.learning_pause_event.set()
        if self.training_stop_button:
            self.training_stop_button.config(text="STOPPING...", state="disabled")
        if self.training_pause_button:
            self.training_pause_button.config(state="disabled")
        if self.training_stage_label:
            self.training_stage_label.config(text="STOPPING AFTER THE CURRENT STEP. SAVED CORRECTIONS WILL BE KEPT.")

    def _wait_for_learning_control(self):
        while not self.learning_pause_event.wait(timeout=0.2):
            if self.learning_stop_event.is_set():
                raise InterruptedError("Learning update stopped by user.")
        if self.learning_stop_event.is_set():
            raise InterruptedError("Learning update stopped by user.")

    def _training_popup_close_requested(self):
        if not self.trocr_training:
            self._close_training_popup()

    def _close_training_popup(self):
        popup = self.training_popup
        self.training_popup = None
        self.training_progressbar = None
        self.training_percent_label = None
        self.training_stage_label = None
        self.training_close_button = None
        self.training_pause_button = None
        self.training_stop_button = None
        if popup is not None:
            try:
                popup.destroy()
            except Exception:
                pass

    def _update_training_progress(self, percent, stage, finished=False):
        if finished:
            self._diag_event("learning_update_finished",
                             percent=percent,stage=stage)
            threading.Thread(target=self._diag_sync_shared,
                             daemon=True).start()
        percent = max(0, min(100, int(round(percent))))
        popup = self.training_popup
        if popup is None:
            return
        try:
            if not popup.winfo_exists():
                return
            self.training_progressbar["value"] = percent
            self.training_percent_label.config(text=f"{percent}%")
            self.training_stage_label.config(text=stage)
            if finished:
                if self.training_pause_button:
                    self.training_pause_button.config(state="disabled")
                if self.training_stop_button:
                    self.training_stop_button.config(state="disabled")
                self.training_close_button.config(state="normal")
                popup.protocol("WM_DELETE_WINDOW", self._close_training_popup)
        except Exception:
            pass

    def _maybe_train_trocr(self, automatic=False):
        if automatic:return False  # Explicit UPDATE LEARNING only (v5.6.18).
        if automatic and getattr(self,"_stop_all_requested",False):return False
        if not automatic:self._stop_all_requested=False
        if self.trocr_training or self.busy or self.export_busy or not self.learning_dir:
            return False
        usable = [
            dict(ex) for ex in self.learning_data.get("examples", [])
            if ex.get("image") and CASE_RE.fullmatch(
                str(ex.get("confirmed_case_number") or ""))
        ]
        attempted_ids = set(self.learning_data.get("trocr_v3_attempted_ids", []))
        pending = []
        previously_used = []
        for ex in usable:
            ex["example_id"] = self._learning_example_id(ex)
            if not ex["example_id"]:
                continue
            (pending if ex["example_id"] not in attempted_ids else previously_used).append(ex)

        first_update = not attempted_ids
        minimum_new = 10 if first_update else 5
        if len(pending) < minimum_new:
            return False

        # Process ALL pending corrections in one update. The training worker
        # loads individual crops only when needed, avoiding image-memory spikes.
        selected_new = pending
        replay = self._even_sample(previously_used, 16)
        examples = selected_new + replay
        if len(examples) < 10:
            return False
        new_ids = [ex["example_id"] for ex in selected_new]

        self.trocr_training = True
        self._learning_auto_run = bool(automatic)
        self._training_worker_done.clear()
        self.learning_pause_event.set()
        self.learning_stop_event.clear()
        if hasattr(self, "status"):
            self.status.config(
                text=f"Updating ALL {len(selected_new)} new examples and {len(replay)} reference examples.")
        if not automatic:
            self.scan_btn.config(state="disabled")
            self._open_training_progress()
        self._set_background_learning_indicator(True)
        self.finish_batch_btn.config(state="disabled")
        self._update_training_progress(0, "Preparing the new examples...")
        training_dir = Path(self.learning_dir)
        threading.Thread(
            target=self._train_trocr_worker,
            args=(training_dir, examples, new_ids),
            daemon=True).start()
        return True

    def _finish_import_batch(self):
        if self.busy or self.trocr_training:
            messagebox.showinfo(
                "Processing in progress",
                "Finish or stop the scan before updating learning.")
            return
        if not self.learning_dir:
            messagebox.showinfo("No folder selected", "Choose the PDF folder first.")
            return
        pending = self._pending_learning_examples()
        attempted = set(self.learning_data.get("trocr_v3_attempted_ids", []))
        required = 5 if attempted else 10
        if len(pending) < required:
            messagebox.showinfo(
                "More confirmed examples needed",
                f"{len(pending)} new confirmed example(s) are ready. "
                f"At least {required} are needed for the next learning update. "
                "Your corrections are already saved.")
            return
        if not self._maybe_train_trocr():
            messagebox.showinfo(
                "Learning update not started",
                "There are not enough usable new examples yet. Your corrections are saved.")
            return

    def _normalize_case_text(self, value):
        value = str(value or "").replace("—", "-").replace("–", "-")
        value = re.sub(r"\s+", "", value)
        return value.upper()

    def _train_trocr_worker(self, learning_dir, examples, new_ids):
        candidate_dir = None

        def report(percent, stage):
            # Worker thread communicates with Tk only through the thread-safe queue.
            try:
                self.queue.put(("training_progress", percent, stage))
            except Exception:
                pass

        try:
            import torch
            from PIL import Image
            from transformers import TrOCRProcessor, VisionEncoderDecoderModel
            if self._learning_auto_run:
                # Auto-training runs in the worker thread with restrained CPU
                # parallelism; avoid saturating a patrol workstation at startup.
                try:
                    torch.set_num_threads(min(2, max(1, (os.cpu_count() or 2) // 4)))
                except RuntimeError:
                    pass
                if os.name == "nt":
                    try:
                        import ctypes
                        kernel = ctypes.windll.kernel32
                        kernel.SetThreadPriority(kernel.GetCurrentThread(), -1)
                    except Exception:
                        pass

            report(2, "Preparing and checking the saved training crops...")
            hw5618.backup_learning_once(learning_dir)
            complete_history=list(self.learning_data.get('examples',[]))
            all_safe,quarantined=hw5618.audit_examples(complete_history,learning_dir)
            safe_ids={ex.get('example_id') for ex in all_safe}
            clean=[ex for ex in examples if ex.get('example_id') in safe_ids]
            hw5618.write_quarantine_report(learning_dir,quarantined)
            report(3,f"Audited handwriting training data; {len(quarantined)} samples quarantined")
            usable = []
            total_examples = max(1, len(clean))
            for index, ex in enumerate(clean, 1):
                self._wait_for_learning_control()
                image_path = learning_dir / ex["image"]
                if image_path.is_file():
                    try:
                        # Verify now, but don't retain the decompressed pixels.
                        # Later, each training step opens only its own crop.
                        with Image.open(image_path) as im:
                            im.verify()
                        usable.append((ex,image_path, ex["confirmed_case_number"]))
                    except Exception:
                        self._log("Could not load training crop %s", image_path)
                if index == total_examples or index % 5 == 0:
                    report(
                        2 + 5 * index / total_examples,
                        f"Checking training crops: {index}/{total_examples}")
            if len(usable) < 10:
                raise RuntimeError(
                    f"Only {len(usable)} usable case-number crops; at least 10 are required.")

            # Group by image/source identity: never put two crops from the same
            # original page into both training and independent validation.
            grouped={}
            for ex,image_path,target in usable:
                key=str(ex.get('source_pdf') or image_path.stem)
                grouped.setdefault(key,[]).append((image_path,target))
            keys=sorted(grouped)
            validation_keys=set(k for i,k in enumerate(keys) if i%5==0)
            train_items=[pair for k in keys if k not in validation_keys for pair in grouped[k]]
            validation_items=[pair for k in keys if k in validation_keys for pair in grouped[k]]
            if len(validation_items) < 2 or len(train_items) < 8:
                raise RuntimeError("Not enough examples for a meaningful train/validation split.")

            device = "cuda" if torch.cuda.is_available() else "cpu"
            base_name = "microsoft/trocr-base-handwritten"
            active_dir = learning_dir / "trocr_v2_finetuned"
            recovery_dir = learning_dir / "trocr_v2_previous"
            baseline_dir = active_dir if (active_dir / "config.json").exists() else (
                recovery_dir if (recovery_dir / "config.json").exists() else None)

            report(
                10,
                "Loading TrOCR model and tokenizer. The first load may take longer while model files are downloaded...")
            if baseline_dir is not None:
                processor = TrOCRProcessor.from_pretrained(str(baseline_dir), local_files_only=True)
                base_model = VisionEncoderDecoderModel.from_pretrained(
                    str(baseline_dir), local_files_only=True).to(device)
                baseline_source = (
                    "current fine-tuned model" if baseline_dir == active_dir
                    else "previous recovery model")
            else:
                local_model=self._local_model_source()
                source=str(local_model) if local_model else base_name
                processor = TrOCRProcessor.from_pretrained(
                    source,local_files_only=True)
                base_model = VisionEncoderDecoderModel.from_pretrained(
                    source,local_files_only=True).to(device)
                baseline_source = "Microsoft base model"

            pad_token_id, decoder_start_token_id = self._configure_trocr_token_ids(
                base_model, processor)
            base_model.eval()
            report(22, f"Model loaded on {device.upper()}. Testing the current model...")

            def evaluate(model, start_percent, end_percent, stage_name, model_processor=None):
                processor_for_eval=model_processor or processor
                model.eval()
                correct = 0
                total_validation = len(validation_items)
                for index, (image_path, target) in enumerate(validation_items, 1):
                    self._wait_for_learning_control()
                    with Image.open(image_path) as image:
                        pixels = processor_for_eval(images=image.convert("RGB"), return_tensors="pt").pixel_values.to(device)
                    with torch.inference_mode():
                        output = model.generate(pixels, max_new_tokens=24, num_beams=2)
                    predicted = processor_for_eval.batch_decode(output, skip_special_tokens=True)[0]
                    if self._normalize_case_text(predicted) == self._normalize_case_text(target):
                        correct += 1
                    progress = start_percent + (
                        (end_percent - start_percent) * index / total_validation)
                    report(
                        progress,
                        f"{stage_name}: example {index}/{total_validation} "
                        f"({correct} exact matches so far)")
                return correct

            baseline_correct = evaluate(
                base_model, 24, 34, "Testing existing model")
            # Independently compare the ORIGINAL Microsoft model on the SAME
            # held-out cases. Never download anything during training.
            local_base=(Path(os.environ.get('LOCALAPPDATA') or
                   str(Path.home()/'.local'/'share'))/'MFCaseOCR'/'models'/'offline-base')
            original_source=str(local_base) if (local_base/'config.json').is_file() else 'microsoft/trocr-base-handwritten'
            original_processor=TrOCRProcessor.from_pretrained(original_source,local_files_only=True)
            original_model=VisionEncoderDecoderModel.from_pretrained(original_source,local_files_only=True).to(device)
            self._configure_trocr_token_ids(original_model,original_processor)
            original_correct=evaluate(original_model,24,34,'Testing original Microsoft model',original_processor)
            del original_model
            # Keep the best of the current trained model and original base.
            required_correct=max(baseline_correct,original_correct)

            model = base_model
            optimizer = torch.optim.AdamW(model.parameters(), lr=1e-5)
            best_correct = baseline_correct
            best_state = None
            best_epoch = 0
            epochs_completed = 0
            report(
                36,
                f"Fine-tuning TrOCR with all {len(train_items)} training examples per pass; "
                f"validation is checked after each of up to 2 epochs on {device.upper()}...")

            # Keep a CPU copy of the best checkpoint seen so far. This lets us
            # discard a later epoch if it harms validation performance.
            for epoch in range(2):
                self._wait_for_learning_control()
                model.train()
                train_progress_start, train_progress_end = ((36, 52) if epoch == 0 else (65, 81))
                for example_index, (image_path, label) in enumerate(train_items, 1):
                    self._wait_for_learning_control()
                    with Image.open(image_path) as image:
                        pixels = processor(images=image.convert("RGB"), return_tensors="pt").pixel_values.to(device)
                    labels = processor.tokenizer(
                        label, return_tensors="pt", padding="max_length",
                        max_length=20, truncation=True).input_ids.to(device)
                    labels[labels == pad_token_id] = -100
                    loss = model(pixel_values=pixels, labels=labels).loss
                    loss.backward()
                    torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                    optimizer.step()
                    optimizer.zero_grad(set_to_none=True)
                    last_loss = float(loss.detach().cpu().item())
                    progress = train_progress_start + (
                        (train_progress_end - train_progress_start) *
                        example_index / len(train_items))
                    report(
                        progress,
                        f"Fine-tuning epoch {epoch + 1}/2 • "
                        f"example {example_index}/{len(train_items)} • loss {last_loss:.4f}")

                epochs_completed = epoch + 1
                eval_start, eval_end = ((55, 65) if epoch == 0 else (78, 90))
                epoch_correct = evaluate(
                    model, eval_start, eval_end,
                    f"Validation after epoch {epoch + 1}/2")

                if epoch_correct > best_correct:
                    best_correct = epoch_correct
                    best_epoch = epoch + 1
                    best_state = {
                        name: tensor.detach().cpu().clone()
                        for name, tensor in model.state_dict().items()
                    }
                    report(
                        eval_end,
                        f"Epoch {epoch + 1} improved validation ({best_correct}/"
                        f"{len(validation_items)}). Keeping this checkpoint as best so far...")
                else:
                    if best_state is not None:
                        model.load_state_dict(best_state)
                    report(
                        eval_end,
                        f"Epoch {epoch + 1} did not improve validation. "
                        f"Stopping and retaining the best checkpoint...")
                    break

            if best_state is not None:
                model.load_state_dict(best_state)
            tuned_correct = best_correct

            baseline_accuracy = 100.0 * baseline_correct / len(validation_items)
            tuned_accuracy = 100.0 * tuned_correct / len(validation_items)
            improved = tuned_correct > required_correct
            validation_report = {
                "baseline_accuracy_pct": baseline_accuracy,
                "original_accuracy_pct":100.0*original_correct/len(validation_items),
                "original_correct":original_correct,
                "accuracy_pct": tuned_accuracy,
                "correct": tuned_correct,
                "total": len(validation_items),
                "training_examples": len(train_items),
                "epochs_completed": epochs_completed,
                "best_epoch": best_epoch,
                "early_stopped": epochs_completed < 2,
                "evaluated_at": datetime.now().isoformat(timespec="seconds"),
                "saved_as_active": improved,
                "baseline_source": baseline_source
            }

            self._wait_for_learning_control()
            if improved:
                report(94, "Validation improved. Saving and activating the updated model...")
                candidate_dir = learning_dir / f"trocr_candidate_{int(time.time())}"
                candidate_dir.mkdir(parents=True, exist_ok=False)
                model.eval()
                model.save_pretrained(str(candidate_dir))
                processor.save_pretrained(str(candidate_dir))
                previous_dir = learning_dir / "trocr_v2_previous"
                if active_dir.exists():
                    if previous_dir.exists():
                        shutil.rmtree(previous_dir)
                    os.replace(active_dir, previous_dir)
                os.replace(candidate_dir, active_dir)
                candidate_dir = None
                active_text = "New TrOCR model passed the held-out exact-match comparison and was activated."
            else:
                report(94, "Validation did not improve. Keeping the existing model...")
                active_text = (
                    "TrOCR training did not improve held-out exact matches; the existing model was kept.")

            db = learning_dir / "learning_examples_v3.json"
            with self._shared_learning_write_lock():
                latest = json.loads(db.read_text(encoding="utf-8")) if db.exists() else {}
                latest["trocr_v2_validation"] = validation_report
                latest["last_training_summary"] = {
                    "examples_total": len(usable),
                    "new_examples": len(new_ids),
                    "reference_examples": max(0, len(examples) - len(new_ids)),
                    "examples_handwritten": sum(1 for ex in examples if self._normalized_training_type(ex.get("training_type")) == "handwritten"),
                    "examples_printed": sum(1 for ex in examples if self._normalized_training_type(ex.get("training_type")) == "printed"),
                    "examples_unsure": sum(1 for ex in examples if self._normalized_training_type(ex.get("training_type")) == "unsure"),
                    "updated_at": validation_report.get("evaluated_at"),
                    "model_activated": bool(improved)
                }
                attempted_ids = set(latest.get("trocr_v3_attempted_ids", []))
                attempted_ids.update(ex.get('example_id') for ex,_,_ in usable if ex.get('example_id'))
                latest["trocr_v3_attempted_ids"] = sorted(attempted_ids)
                latest["trocr_v2_attempted_count"] = len(attempted_ids)
                if improved:
                    trained_ids = set(latest.get("trocr_v3_trained_ids", []))
                    trained_ids.update(ex.get('example_id') for ex,_,_ in usable if ex.get('example_id'))
                    latest["trocr_v3_trained_ids"] = sorted(trained_ids)
                    latest["trocr_v2_trained_count"] = len(trained_ids)
                if db.is_file():
                    shutil.copy2(db, db.with_name("learning_examples_v3.last_good.json"))
                temp = db.with_name(db.name + f".{os.getpid()}.tmp")
                temp.write_text(json.dumps(latest, indent=2), encoding="utf-8")
                os.replace(temp, db)

            final_message = (
                f"{active_text} Held-out exact match: {tuned_correct}/{len(validation_items)} "
                f"({tuned_accuracy:.1f}%), baseline {baseline_correct}/{len(validation_items)}. "
                f"Training passes completed: {epochs_completed}/2; best pass: {best_epoch or 'none'}.")
            report(100, final_message)
            self.queue.put(("learning_status", final_message))
        except InterruptedError as exc:
            self._log("TrOCR learning update stopped: %s", exc)
            self.queue.put(("learning_stopped", "Learning update stopped. All saved corrections were kept; no incomplete model was activated."))
        except Exception as exc:
            self._log("TrOCR training/validation failed: %s", exc)
            error_message = f"TrOCR update failed; existing model retained: {exc}"
            report(100, error_message)
            self.queue.put(("learning_status", error_message))
        finally:
            if candidate_dir and candidate_dir.exists():
                shutil.rmtree(candidate_dir, ignore_errors=True)
            self._training_worker_done.set()

    def scan(self, only_iids=None):
        """Start from current selection and report every blocked/failed request."""
        self._stop_all_requested=False
        was_busy=self.busy
        try:
            return self._start_scan(only_iids)
        except Exception as exc:
            logging.exception("SCAN could not start")
            if not was_busy:
                self.busy=False
                self.paused=False
                self.stop_event.clear()
                self.pause_event.set()
                self.pause_btn.config(state="disabled",text="PAUSE")
                self._refresh_scan_button()
            self._diag_event("scan_start_error",error=repr(exc))
            self._scan_blocked(f"SCAN could not start: {exc}. Your PDFs and learning data are unchanged by this failed start.")

    def _scan_blocked(self, message):
        self.status.config(text=message)
        messagebox.showinfo("SCAN",message,parent=self.root)

    def _resume_scan_after_training(self, only_iids=None):
        self._scan_waiting_for_training=False
        if getattr(self,"_stop_all_requested",False):return
        return self._start_scan(only_iids)

    def _start_scan(self, only_iids=None):
        if getattr(self,"_scan_case_editor",None) is not None:
            self._scan_blocked("Save the edited case with Enter or cancel with Esc first.")
            self._scan_case_editor.focus_set()
            return
        if not self._startup_ready or self.reader is None or self.trocr_model is None:
            self._scan_blocked("OCR models are not ready. Wait for startup to finish or check the startup error before scanning.")
            return
        if self.busy or self._training_blocks_work() or self.export_busy:
            self._scan_blocked("An operation is already running. Finish scanning or sending before starting another scan.")
            return
        if self._background_learning_active():
            self._yield_background_learning("scanning")
            if not self._training_worker_done.is_set():
                # Do not freeze the Tk UI or overlap TrOCR/GPU with training.
                # Re-enter only when the worker has released its model.
                if not self._scan_waiting_for_training:
                    self._scan_waiting_for_training=True
                    self.status.config(text="PREPARING SCAN: STOPPING BACKGROUND LEARNING…")
                    self.root.after(200,lambda selection=only_iids:
                        self._resume_scan_after_training(selection))
                return
        self._scan_waiting_for_training=False
        if not self.folder or not self.records:
            self._scan_blocked("Click SELECT FILES and choose a folder containing PDFs first.")
            return
        self.scan_year_prefix = self._year_prefix()
        if pymupdf is None or easyocr is None or np is None:
            messagebox.showerror(
                "Setup required",
                "Run SETUP.bat first. Python 3.11, PyMuPDF, EasyOCR and NumPy must be installed.")
            return
        try:
            settings_path = self.folder / ".case_ocr_settings.json"
            settings_path.write_text(
                json.dumps({"year": REQUIRED_CASE_YEAR}, indent=2),
                encoding="utf-8")
        except Exception:
            self._log("Could not save case-year setting")
        self.scan_items = self._pending_scan_items(only_iids)
        self.scan_items.sort(key=lambda pair: self._natural_pdf_sort_key(pair[1]["path"]))
        if not self.scan_items:
            if getattr(self,"_sequence_review_pending",False):
                self.busy=True
                self.queue.put(("done",))
                return
            if self._rescan_force_iids:
                self._rescan_force_iids.clear()
            # RESCAN ALL is an explicit separate action; ordinary SCAN never resets approvals.
            self._scan_blocked("No unscanned PDFs selected. Select rows or press Ctrl+A. Approved files are protected; use UNVERIFY before rescanning them.")
            self.restart_btn.config(text="STOP", state="normal")
            self._refresh_scan_button()
            return
        if self._post_scan_learning_after is not None:
            try:self.root.after_cancel(self._post_scan_learning_after)
            except tk.TclError:pass
            self._post_scan_learning_after=None
        self._scan_clock_start=time.monotonic()
        self._scan_clock_final_elapsed=0.
        self._scan_clock_pause_seconds=0.
        self._scan_clock_pause_start=None
        self._scan_clock_done=0
        self._run_outcomes = {"total_selected": len(self.scan_items)}
        self.busy = True
        self._sequence_review_pending = True
        self._save_state()
        self.paused = False
        self.stop_event.clear()
        self.pause_event.set()
        self.pause_btn.config(text="PAUSE", state="normal")
        self.restart_btn.config(text="STOP", state="normal")
        self.scan_btn.config(state="disabled")
        self._refresh_rescan_button()
        self._refresh_review_controls()
        self._update_completed_count()
        self._scan_progress_update(0,"SCANNING")
        self.status.config(text=f"Scanning {len(self.scan_items)} file(s)...")
        self._diag_start_run()
        self._diag_event("sequence_order",mode="natural_filename_scan_queue",
             ordered_files=[Path(rec["path"]).name for iid,rec in self.scan_items],
             minimum_independent_matches=3,
             filenames_need_consecutive_numbers=False)
        self._log("Scan started in %s with year filter %s", self.folder, self.scan_year_prefix or "ALL")
        self._diag_event("performance_config", primary="upper_right_quarter_first",
             learned_fallback_max_zones_per_pdf=0,confidence_threshold=95.0,
             early_exit_clear_printed_threshold=99.5,early_exit_unique_pdf_threshold=95.0,
             background_learning_waits_before_ocr=True)

        if self.reader is None:
            self._loader_start("ocr", "Loading OCR engines…")
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        try:
            try:
                import torch
                gpu_available = bool(torch.cuda.is_available())
            except Exception:
                gpu_available = False
            reader_gpu = gpu_available
            if self.reader is None:
                try:
                    self.reader = easyocr.Reader(
                ["en"],gpu=reader_gpu,verbose=False,download_enabled=False)
                except Exception:
                    self._log("EasyOCR GPU initialization failed; retrying on CPU")
                    reader_gpu = False
                    self.reader = easyocr.Reader(["en"], gpu=False, verbose=False,
                                                  download_enabled=False)
            self.queue.put(("engine_status", f"EasyOCR initialized • {'GPU' if reader_gpu else 'CPU'}"))
            records_snapshot = list(self.scan_items)
            total = len(records_snapshot)
            processed = 0
            stopped = False
            for idx, (iid, rec) in enumerate(records_snapshot, 1):
                if self.stop_event.is_set():
                    stopped = True
                    break
                while not self.pause_event.wait(timeout=0.2):
                    if self.stop_event.is_set():
                        break
                if self.stop_event.is_set():
                    stopped = True
                    break
                source_path = Path(rec["path"])
                self._worker_current_iid=iid
                self.queue.put(("file_started",iid,source_path.name,idx,total))
                processed = idx
                try:
                    if not source_path.is_file():
                        raise FileNotFoundError(f"Source PDF no longer exists: {source_path.name}")
                    if self._pdf_is_blank(source_path):
                        self._diag_event("blank_kept_in_root",
                            file=source_path.name,
                            reason="explicit_user_send_required")
                        self.queue.put((
                            "row", iid, "", None,
                            "Blank PDF — retained in root for manual review",
                            False, 0.0, [], False, idx))
                        continue
                    self._diag_current_file=source_path.name
                    result = self._ocr_pdf(source_path)
                    if self.stop_event.is_set():
                        stopped=True;processed=idx-1;break
                    candidate, snippet, note, handwritten, score, engines, agreement = result
                    self.queue.put((
                        "row", iid, candidate, snippet, note, handwritten,
                        score, engines, agreement, idx))
                except InterruptedError:
                    stopped=True
                    processed=idx-1
                    break
                except Exception as exc:
                    self._diag_event("scan_error",file=source_path.name,error=repr(exc))
                    self._log("Scan failed for %s: %s", source_path, exc)
                    self.queue.put((
                        "row", iid, "", None, f"Scan error: {exc}",
                        False, 0.0, [], False, idx))
            if self.stop_event.is_set():
                stopped = True
            self._worker_current_iid=None
            self.queue.put(("stopped", processed) if stopped else ("done",))
        except Exception as exc:
            self._worker_current_iid=None
            self._log("Fatal scan initialization error: %s", exc)
            self.queue.put(("fatal", str(exc)))

    def toggle_pause(self):
        if not self.busy:
            return
        if self.paused:
            started=getattr(self,'_scan_clock_pause_start',None)
            if started is not None:
                self._scan_clock_pause_seconds+=max(0.,time.monotonic()-started)
                self._scan_clock_pause_start=None
            self.paused = False
            self.pause_event.set()
            self.pause_btn.config(text="PAUSE")
            self.status.config(text="SCANNING...")
        else:
            self.paused = True
            self._scan_clock_pause_start=time.monotonic()
            self.pause_event.clear()
            self.pause_btn.config(text="RESUME")
            self.status.config(text="SCAN PAUSED.")

    def _restore_last_folder(self):
        folder=getattr(self,"_last_folder","")
        if folder and not self.folder:
            self.choose_folder(folder)

    def stop_or_restart_scan(self):
        """STOP is cancellation only; it never launches a new scan."""
        self._stop_all_requested=True
        self._scan_clock_final_elapsed=self._scan_clock_actual()
        self._scan_clock_start=None
        self._scan_clock_pause_start=None
        self.stop_event.set();self.pause_event.set();self.paused=False
        self.learning_stop_event.set();self.learning_pause_event.set()
        self._export_cancel_event.set()
        self._scan_waiting_for_training=False
        job=getattr(self,"_post_scan_learning_after",None)
        if job is not None:
            self.root.after_cancel(job);self._post_scan_learning_after=None
        self.pause_btn.config(state="disabled",text="PAUSE")
        self.restart_btn.config(text="STOP",state="normal")
        self._save_state()
        self.status.config(text="STOP requested. No new background work will start. Finishing any active read/write safely; SEND FILES remains available afterward.")
        self._refresh_scan_button()

    def _request_close(self):
        self.stop_or_restart_scan()
        self._close_when_workers_idle()

    def _close_when_workers_idle(self):
        if self.busy or self.export_busy or self.trocr_training:
            self.root.after(150,self._close_when_workers_idle);return
        if self.folder and not self._save_state():
            self.status.config(text="COULD NOT SAVE PROGRESS. CHECK FOLDER ACCESS BEFORE CLOSING.");return
        self._save_user_settings()
        self.root.destroy()

    def restart_scan(self):
        if self.busy or self._training_blocks_work():
            messagebox.showinfo("Processing in progress", "Finish the current scan or model update before restarting.")
            return
        if not self.folder:
            return
        if not messagebox.askyesno(
            "Restart scan",
            "Clear detected candidates and user confirmations for PDFs still in the root folder, then scan them again?"):
            return

        # Re-enumerate the root folder so PDFs already moved to BLANKS are not resurrected.
        previous_records = {Path(r["path"]).name:r for r in self.records.values() if r.get("path")}
        for tree in (self.tree, self.completed_tree):
            for item in tree.get_children():
                tree.delete(item)
        for iid in list(self.completed_edit_widgets):
            self._close_completed_edit(iid)
        self.completed_checked_iids.clear()
        self.completed_edit_drafts.clear()
        self.completed_photo_refs.clear()
        self.scan_checked_iids.clear()
        self.records = {}
        pdfs = sorted(
            (p for p in self.folder.iterdir()
             if p.is_file() and p.suffix.lower() == ".pdf"),
            key=self._natural_pdf_sort_key)
        for pdf in pdfs:
            iid = f"pdf::{pdf.name}"
            previous = previous_records.get(pdf.name, {})
            if previous.get("exported"):
                previous["path"] = pdf
                previous["snippet"] = None
                previous["completed_iid"] = None
                self.records[iid] = previous
                self._insert_scan_row(iid,previous)
                continue
            self.records[iid] = {
                "path": pdf, "candidate": "", "confirmed": "", "status": "Not scanned",
                "snippet": None, "handwritten": False, "score": 0.0,
                "engines": [], "engine_agreement": False, "auto_labeled": False,
                "manual_approved": False, "completed_iid": None, "exported": False,
                "export_name": ""
            }
            self.records[iid]["scan_selected"] = bool(previous.get("scan_selected", True))
            if self.records[iid]["scan_selected"]:
                self.scan_checked_iids.add(iid)
            else:
                self.scan_checked_iids.discard(iid)
            self._insert_scan_row(iid, self.records[iid])
        self._save_state()
        self._update_completed_count()
        self._refresh_scan_button()
        self._update_scan_file_count()
        self._refresh_review_controls()
        self.finish_batch_btn.config(state="disabled")
        self.scan()

    def _pdf_is_blank(self, path):
        """
        Check the single report page before OCR. If a PDF unexpectedly has
        multiple pages, do not classify/move it automatically as blank.
        """
        doc = pymupdf.open(path)
        try:
            if len(doc) != 1:
                return False
            page = doc[0]
            if page.get_text("text").strip():
                return False
            pix = page.get_pixmap(
                matrix=pymupdf.Matrix(1.0, 1.0),
                colorspace=pymupdf.csGRAY, alpha=False)
            gray = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                pix.height, pix.width)
            h, w = gray.shape
            y0, y1 = int(h * 0.04), max(int(h * 0.96), 1)
            x0, x1 = int(w * 0.04), max(int(w * 0.96), 1)
            content = gray[y0:y1, x0:x1]
            dark_fraction = float(np.count_nonzero(content < 245)) / float(content.size)
            return dark_fraction <= 0.00002
        finally:
            self._page_ocr_budget=None
            doc.close()

    def _get_learned_field_regions(self, width, height, limit=3):
        """Return a few distinct reviewer-confirmed field locations."""
        recent = self.learning_data.get("field_regions", [])[-200:]
        selected = []
        centers = []
        for item in reversed(recent):
            rect = item.get("rect_norm") or []
            if len(rect) != 4:
                continue
            try:
                x0, y0, x1, y1 = [float(v) for v in rect]
            except (TypeError, ValueError):
                continue
            if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
                continue
            cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
            # Keep the most recent examples that represent distinct field zones.
            if any(((cx - px) ** 2 + (cy - py) ** 2) ** 0.5 < 0.09
                   for px, py in centers):
                continue
            selected.append((int(x0 * width), int(y0 * height),
                             min(width, int(x1 * width)),
                             min(height, int(y1 * height))))
            centers.append((cx, cy))
            if len(selected) >= limit:
                break
        return selected

    @staticmethod
    def _merge_case_hits(primary, additional, learned=False):
        """Merge OCR passes without losing each recognized value's best box."""
        merged = {item["number"]: dict(item) for item in primary}
        for hit in additional:
            number = hit.get("number")
            if not number:
                continue
            candidate = dict(hit)
            if learned:
                candidate["learned_location"] = True
            if number not in merged:
                merged[number] = candidate
                continue
            current = merged[number]
            current_engines = list(current.get("engines") or [])
            had_easyocr_box = any(e.startswith("EasyOCR") for e in current_engines)
            candidate_has_easyocr = any(
                e.startswith("EasyOCR") for e in (candidate.get("engines") or []))
            for engine in candidate.get("engines") or []:
                if engine not in current_engines:
                    current_engines.append(engine)
            current["engines"] = current_engines
            scores=dict(current.get("engine_scores") or {})
            for engine,score in (candidate.get("engine_scores") or {}).items():
                scores[engine]=max(float(scores.get(engine,0)),float(score))
            current["engine_scores"]=scores
            current["score"] = max(float(current.get("score") or 0),
                                   float(candidate.get("score") or 0))
            if candidate.get("label_type"):
                priority = {"incident": 4, "case": 4, "report": 2, "sys": 1}
                old = current.get("label_type")
                if old is None or priority[candidate["label_type"]] > priority[old]:
                    current["label_type"] = candidate["label_type"]
                    current["label_distance"] = candidate.get("label_distance", 0.0)
            if candidate.get("learned_location"):
                current["learned_location"] = True
            if candidate.get("box_verified") and candidate.get("box_rect"):
                new_rect=candidate["box_rect"]
                prior_rect=current.get("box_rect")
                new_area=(new_rect[2]-new_rect[0])*(new_rect[3]-new_rect[1])
                prior_area=((prior_rect[2]-prior_rect[0])*(prior_rect[3]-prior_rect[1])
                            if prior_rect else float("inf"))
                if new_area<prior_area:
                    current["box_rect"]=new_rect
                current["box_verified"]=True
            # Prefer a specific OCR detection box over a wide TrOCR region box.
            if candidate.get("rect") and (
                not current.get("rect") or
                (candidate_has_easyocr and not had_easyocr_box)):
                current["rect"] = candidate["rect"]
        return list(merged.values())

    def _find_case_field_label_regions(self, image):
        """Detect INCIDENT NO. without case sensitivity, including split labels.

        Coordinates end at the printed label, NOT an attached '08' in a single
        OCR box: the full case number is searched immediately to its RIGHT.
        """
        if self.reader is None:
            return []
        try:
            detections = self._timed_easyocr_readtext(image, detail=1, paragraph=False)
        except Exception as exc:
            self._log("Case label detection failed: %s", exc)
            return []
        self._diag_event("ocr_raw_detections",scope="4x4_label",
            detections=[{"text":str(d[1])[:120],
                         "confidence":round(float(d[2] or 0)*100,3),
                         "bbox":[[round(float(p[0]),2),round(float(p[1]),2)]
                                 for p in d[0]]} for d in detections[:150]])
        parsed = []
        for bbox, text, confidence in detections:
            xs = [float(p[0]) for p in bbox]
            ys = [float(p[1]) for p in bbox]
            parsed.append((min(xs), min(ys), max(xs), max(ys), str(text), float(confidence or 0)))
        found = []
        for x0, y0, x1, y1, text, score in parsed:
            match = CASE_FIELD_LABEL_RE.search(text)
            if match:
                end = x0 + (x1-x0)*(match.end()/max(1,len(text)))
                label_kind = "incident" if "incident" in match.group(0).lower() else "case"
                found.append((x0,y0,min(x1,end),y1,label_kind,score))
        # Join INCIDENT + NO. fragments on the same row. If a digit is attached
        # to NO. it must not become part of the label rectangle.
        for left in parsed:
            if not re.search(r"\b(?:INCIDENT|CASE)\b", left[4], re.I):
                continue
            for right in parsed:
                if right is left or right[0] < left[2] - 5:
                    continue
                center_delta = abs((left[1]+left[3])/2 - (right[1]+right[3])/2)
                max_gap = max(70, (left[3]-left[1]) * 4.5)
                if right[0]-left[2] > max_gap or center_delta > max(22, (left[3]-left[1])*0.8):
                    continue
                joined = left[4].strip() + " " + right[4].strip()
                match = CASE_FIELD_LABEL_RE.search(joined)
                if match:
                    end = right[0] + (right[2]-right[0]) * (
                        max(0, match.end()-len(left[4])-1) / max(1, len(right[4])))
                    found.append((left[0], min(left[1],right[1]),
                                  min(right[2], max(right[0],end)),
                                  max(left[3],right[3]),
                                   "incident" if "incident" in match.group(0).lower() else "case",
                                  min(left[5],right[5])))
        # Prefer incident labels, compact boxes, and deduplicate within one line.
        result = []
        for item in sorted(found, key=lambda a: (
                0 if a[4]=="incident" else 1,
                (a[2]-a[0])*(a[3]-a[1]), -a[5])):
            if any(item[4]==r[4] and abs(item[0]-r[0])<30
                   and abs((item[1]+item[3])/2 - (r[1]+r[3])/2)<20
                   for r in result):
                continue
            result.append(item[:5])
        return result[:12]

    @staticmethod
    def _incident_hits_from_detections(detections, label_rect, x_offset, y_offset,
                                       engine="EasyOCR-incident"):
        """Read a complete number to the right, joining separate OCR boxes.

        The algorithm never stops at the year '08', never invents a hyphen, and
        never pulls an OFFENSE CODE number from a different field. A candidate's
        bounding rectangle includes its final digit.
        """
        lx0, ly0, lx1, ly1 = label_rect
        cy = (ly0+ly1)/2
        lh = max(10.0,ly1-ly0)
        raw = []
        for bbox, text, confidence in detections:
            xs = [float(p[0]) / x_offset[0] + x_offset[1] for p in bbox]
            ys = [float(p[1]) / y_offset[0] + y_offset[1] for p in bbox]
            x0,y0,x1,y1 = min(xs),min(ys),max(xs),max(ys)
            if abs((y0+y1)/2-cy) > max(15,lh*0.65):
                continue
            txt = str(text or "").strip().replace("—","-").replace("–","-")
            match = CASE_FIELD_LABEL_RE.search(txt)
            if match:
                suffix = txt[match.end():].strip()
                if not suffix:
                    continue
                # OCR may include label and number inside one wide box.
                x0 = x0 + (x1-x0)*match.end()/max(1,len(txt))
                txt = suffix
            if x1 < lx1-9 or x0 < lx1-max(15,lh*0.6):
                continue
            if x0-lx1 > max(340,lh*12):
                continue
            raw.append((x0,y0,x1,y1,txt,float(confidence or 0)))
        deduplicated=[]
        for item in sorted(raw, key=lambda r:r[5], reverse=True):
            duplicate=False
            for prior in deduplicated:
                intersection=max(0,min(item[2],prior[2])-max(item[0],prior[0])) * max(0,min(item[3],prior[3])-max(item[1],prior[1]))
                smaller=min((item[2]-item[0])*(item[3]-item[1]),(prior[2]-prior[0])*(prior[3]-prior[1]))
                if item[4]==prior[4] and smaller>0 and intersection/smaller>=0.8:
                    duplicate=True
                    break
            if not duplicate:
                deduplicated.append(item)
        raw=sorted(deduplicated,key=lambda r:(r[0],r[1]))
        numeric=[]
        for item in raw:
            # Any other label between INCIDENT NO. and the digits is a stop.
            if re.fullmatch(r"[0-9Il|\s.,:-]+",item[4]) and re.search(r"[Il|]",item[4]):
                return []  # A damaged continuation must not leave a partial prefix.
            if re.search(r"[A-Za-z]",item[4]):
                if not numeric:
                    return []
                break
            if not re.fullmatch(r"[\d\s\-.,:]+",item[4]):
                if not numeric:
                    return []
                break
            if numeric and item[0]-numeric[-1][2] > max(35,lh*1.7):
                break
            if not numeric and item[0]-lx1 > max(210,lh*8):
                return []
            numeric.append(item)
        if not numeric:
            return []
        joined = " ".join(r[4].strip() for r in numeric)
        joined = re.sub(r"\s+(?!08\s*-)","",joined)
        matches=list(INCIDENT_NUMBER_RE.finditer(joined))
        if not matches:
            return []
        # A second full number to the right is an ambiguity, not an excuse to
        # choose whichever has a higher detector confidence.
        values = list(dict.fromkeys(normalize_case_number(m.group(1)) for m in matches))
        left = int(min(r[0] for r in numeric))
        top = int(min(r[1] for r in numeric))
        right = int(max(r[2] for r in numeric))
        bottom = int(max(r[3] for r in numeric))
        score = max(0.0,min(100.0,min(r[5] for r in numeric)*100))
        if len(values) != 1 or not re.fullmatch(r"08-\d{1,7}",joined.strip()):
            return []
        return [{"number":values[0],"score":score,"rect":(left,top,right,bottom),
                 "box_rect":(left,top,right,bottom),"box_verified":True,
                 "engines":[engine],"engine_scores":{"EasyOCR":score},"label_type":"incident",
                 "label_context":True,"incident_label_context":True,
                 "case_field_label_context":True,"label_distance":0.0}]

    def _recognize_case_strip(self, image):
        """Read a known number line without asking the detector to find it again.

        A narrow 1 may disappear from a detector's word boxes. Both passes
        recognize the full crop, retaining actual scores and literal digits.
        No character substitution, padding of case digits, or score boost.
        """
        if image is None or not getattr(image,"size",0):
            return []
        from PIL import Image, ImageOps
        pil=Image.fromarray(image).convert("L")
        if pil.width<8 or pil.height<5:
            return []
        variants=[pil,ImageOps.autocontrast(pil).resize(
            (pil.width*2,pil.height*2),Image.Resampling.LANCZOS),
            ImageOps.expand(pil,border=10,fill=255)]
        hits=[]
        for variant in variants:
            try:
                readings=self._timed_easyocr_recognize(np.asarray(variant),
                    detail=1,paragraph=False,allowlist="0123456789-",
                    decoder="beamsearch",beamWidth=5)
                for _bbox,text,score in readings:
                    number=re.sub(r"\s+","",normalize_case_number(text))
                    # The ENTIRE line must be one 08- case, never a partial match.
                    if not CASE_RE.fullmatch(number):
                        continue
                    hits=self._merge_case_hits(hits,[{
                        "number":number,"score":float(score)*100,
                        "engines":["EasyOCR-strip"],
                        "engine_scores":{"EasyOCR":float(score)*100},
                        "rect":(0,0,pil.width,pil.height)}])
            except Exception as exc:
                self._log("Whole-number strip recognition failed: %s",exc)
        return hits

    def _read_incident_to_right(self, page_image, label_rect):
        lx0,ly0,lx1,ly1 = label_rect
        h,w = page_image.shape[:2]
        span=int(round(4*72*2.2))
        label_h=max(10.0,ly1-ly0)
        pad_y=max(14,int(label_h*0.65))
        x0=max(0,w-span,int(lx0-12))
        x1=min(w,int(lx1+max(470,label_h*16)))
        y0=max(0,int(ly0-pad_y))
        y1=min(h,span,int(ly1+pad_y))
        if y1<=y0 or x1<=x0:
            return []
        cropped=page_image[y0:y1,x0:x1].copy()
        passes=[(cropped,1.0,"EasyOCR-incident")]
        try:
            from PIL import Image,ImageEnhance,ImageOps,ImageFilter
            pil=Image.fromarray(cropped).convert("RGB")
            pil=ImageOps.autocontrast(pil,cutoff=1)
            pil=ImageEnhance.Contrast(pil).enhance(1.35)
            pil=ImageEnhance.Sharpness(pil).enhance(1.2)
            doubled=pil.resize((pil.width*2,pil.height*2),Image.Resampling.LANCZOS)
            passes.append((np.asarray(doubled),2.0,"EasyOCR-incident-enhanced"))
        except Exception as exc:
            self._log("Could not enhance INCIDENT NO. field: %s",exc)
        hits=[]
        for image,scale,engine in passes:
            try:
                detected=self._timed_easyocr_readtext(image,detail=1,paragraph=False)
                found=self._incident_hits_from_detections(
                    detected,label_rect,(scale,x0),(scale,y0),engine)
                hits=self._merge_case_hits(hits,found)
                # A verified 99.5% printed case from the original field box
                # needs no additional costly OCR of the same pixels.
                if scale == 1.0 and self._clear_localized_case(hits,99.5) is not None:
                    self._diag_event("scan_fast_field",stage="original_detector",case=hits[0]["number"])
                    return hits
            except Exception as exc:
                self._log("INCIDENT NO. right-field pass failed: %s",exc)
        try:
            nx=max(0,int(lx1+1))
            strip=page_image[y0:y1,nx:x1]
            if strip.size:
                detected=self._timed_easyocr_readtext(strip,detail=1,paragraph=False,
                                              allowlist="0123456789-")
                hits=self._merge_case_hits(hits,self._incident_hits_from_detections(
                    detected,label_rect,(1.0,nx),(1.0,y0),"EasyOCR-digits"))
        except Exception as exc:
            self._log("Number-only incident recovery failed: %s",exc)
        sx=max(0,w-span,int(lx1-label_h*.35))
        sy=max(0,int(ly0-label_h*.55))
        ex=min(w,int(lx1+max(300,label_h*12)))
        ey=min(h,span,int(ly1+max(3,label_h*.15)))
        tight_strip=page_image[sy:ey,sx:ex]
        if tight_strip.size:
            recovered=self._recognize_case_strip(tight_strip)
            for hit in recovered:
                hit.update(rect=(sx,sy,ex,ey),label_type="incident",
                           label_context=True,incident_label_context=True,label_distance=0.0)
            hits=self._merge_case_hits(hits,recovered)
            try:
                text,score=self._trocr_read_scored(tight_strip)
                number=re.sub(r"\s+","",normalize_case_number(text))
                if CASE_RE.fullmatch(number):
                    hits=self._merge_case_hits(hits,[{
                        "number":number,"score":score,"engines":["TrOCR-strip"],
                        "engine_scores":{"TrOCR":score},"rect":(sx,sy,ex,ey),
                        "label_type":"incident","label_context":True,
                        "incident_label_context":True,"label_distance":0.0}])
            except Exception as exc:
                self._log("Tight case-line TrOCR failed: %s",exc)
        # TrOCR is a supporting read ONLY of the right-hand field, never a
        # search across the printed OFFENSE CODE or other page regions.
        try:
            num_x0=max(0,int(lx1+1))
            number_strip=page_image[y0:y1,
                                    num_x0:min(w,int(lx1+max(300,label_h*10)))]
            if number_strip.size:
                trocr,trocr_score=self._trocr_read_scored(number_strip)
                number=normalize_case_number(trocr)
                if CASE_RE.fullmatch(number):
                    hits=self._merge_case_hits(hits,[{
                        "number":number,"score":trocr_score,
                        "rect":(num_x0,y0,min(w,int(lx1+max(300,label_h*10))),y1),"engines":["TrOCR-incident"],
                        "engine_scores":{"TrOCR":trocr_score},
                        "label_type":"incident","label_context":True,
                        "incident_label_context":True,"label_distance":0.0}])
        except Exception as exc:
            self._log("TrOCR INCIDENT NO. field failed: %s",exc)
        return hits


    @staticmethod
    def _localized_case_hits(hits, bounds):
        """Only a detector-localized complete 08- read can become a scan result."""
        left,top,right,bottom=bounds
        valid=[]
        for hit in hits:
            if not CASE_RE.fullmatch(normalize_case_number(hit.get("number"))):
                continue
            rect=hit.get("box_rect")
            if not hit.get("box_verified") or not isinstance(rect,(tuple,list)) or len(rect)!=4:
                continue
            try:
                x0,y0,x1,y1=map(float,rect)
            except (TypeError,ValueError):
                continue
            if not (all(math.isfinite(v) for v in (x0,y0,x1,y1)) and
                    left<=x0<x1<=right and top<=y0<y1<=bottom):
                continue
            valid.append(dict(hit,rect=tuple(rect)))
        return valid

    @staticmethod
    @staticmethod
    def _clear_localized_case(hits, threshold=95.0):
        """One complete, verified >=95% read from EITHER engine is sufficient.

        Weaker model variants cannot veto a clear high-confidence reading.
        Independent 95%+ conflicts still go to review.
        """
        high=[]
        for h in hits:
            case=normalize_case_number(h.get("number"))
            box=h.get("box_rect")
            try: score=float(h.get("score") or 0)
            except (TypeError,ValueError): continue
            if (CASE_RE.fullmatch(case) and h.get("box_verified")
                and isinstance(box,(tuple,list)) and len(box)==4
                and math.isfinite(score) and score>=threshold):
                high.append(h)
        if len({_case_identity(h["number"]) for h in high})!=1:
            return None
        return max(high,key=lambda h:float(h.get("score") or 0))

    @contextmanager
    def _perf_measure(self, stage):
        begin=perf_counter()
        try:
            yield
        finally:
            elapsed=(perf_counter()-begin)*1000
            stats=getattr(self,"_perf_stages",None)
            if stats is not None:
                duration,calls=stats.get(stage,(0.,0))
                stats[stage]=(duration+elapsed,calls+1)

    def _timed_easyocr_readtext(self,*args,**kwargs):
        with self._perf_measure("easyocr"):
            return self._budgeted_ocr("printed",args[0],
                lambda:self.reader.readtext(*args,**kwargs),[])

    def _timed_easyocr_recognize(self,*args,**kwargs):
        with self._perf_measure("easyocr"):
            return self.reader.recognize(*args,**kwargs)

    def _perf_file_report(self, path, total_start):
        stats=getattr(self,"_perf_stages",{})
        self._diag_event("performance_file", file=Path(path).name,
                         total_ms=round((perf_counter()-total_start)*1000,2),
                         stages={key:{"ms":round(value[0],2),"calls":value[1]}
                                 for key,value in stats.items()},
                         note="Local-only timings; no case PDF or text uploaded")

    @staticmethod
    def _automatic_case_crop_rects_4x4(region, max_regions=5):
        """Locate plausible ink/word rectangles within the original 4 x 4 ROI.

        This is geometry, NOT OCR or approval. It does not require a printed
        INCIDENT NO. label or a first-stage EasyOCR text detection. A crop's
        content must independently read as an entire 08- case before use.
        """
        if region is None or not getattr(region, "size", 0):
            return []
        from PIL import Image
        gray = np.asarray(Image.fromarray(region).convert("L"))
        h, w = gray.shape
        if w < 45 or h < 30:
            return []
        try:
            import cv2
        except ImportError:
            # EasyOCR normally installs OpenCV; without it, retain the normal
            # EasyOCR and label passes rather than invent candidate locations.
            return []
        blurred = cv2.GaussianBlur(gray, (3, 3), 0)
        ink = cv2.adaptiveThreshold(
            blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV, 41, 14)
        ink = cv2.morphologyEx(ink, cv2.MORPH_OPEN,
                                np.ones((2, 2), dtype=np.uint8))
        # Join digits on the same handwritten line before proposing a crop.
        # The old 14-pixel join isolated 08- from the trailing case digits.
        joined = cv2.dilate(ink, np.ones((4, max(20, min(42, int(w*.045)))),
                                           dtype=np.uint8))
        contours, _ = cv2.findContours(joined, cv2.RETR_EXTERNAL,
                                        cv2.CHAIN_APPROX_SIMPLE)
        proposals=[]
        for contour in contours:
            x, y, cw, ch = cv2.boundingRect(contour)
            # Ignore isolated specks and huge printed table rows. Keep all
            # four inches available, but prefer free handwriting to the right.
            if (cw < 38 or ch < 11 or cw > w*.88 or ch > 135 or
                    cw/ch < 0.85):
                continue
            # Widen to a complete field crop, like the green reviewer box.
            # Padding is asymmetric because digits after the hyphen are on
            # the RIGHT; a tight 08- detector rectangle is not a full field.
            pad_y=max(9, min(27, int(ch*.40)))
            pad_left=max(6, min(30, int(ch*.5)))
            pad_right=max(8, min(36, int(ch*.65)))
            x0=max(0, x-pad_left); y0=max(0, y-pad_y)
            x1=min(w, x+cw+pad_right); y1=min(h, y+ch+pad_y)
            if x1-x0<42 or y1-y0<16:
                continue
            # Avoid proposing overlapping duplicates of the same ink line.
            priority=(y0/(h+1), -(x+cw)/(w+1), -cw)
            proposals.append((priority,(x0,y0,x1,y1)))
        proposals.sort(key=lambda item:item[0])
        kept=[]
        for _, rect in proposals:
            x0,y0,x1,y1=rect
            duplicate=False
            for a0,b0,a1,b1 in kept:
                intersect=max(0,min(x1,a1)-max(x0,a0))*max(0,min(y1,b1)-max(y0,b0))
                smaller=min((x1-x0)*(y1-y0),(a1-a0)*(b1-b0))
                if App._same_number_box(rect,(a0,b0,a1,b1)):
                    duplicate=True
                    break
            if not duplicate:
                kept.append(rect)
                if len(kept)>=max_regions:
                    break
        return kept

    @staticmethod
    def _same_number_box(a,b):
        """Similar extents on one line, not merely containment by a large crop."""
        aw,ah=a[2]-a[0],a[3]-a[1];bw,bh=b[2]-b[0],b[3]-b[1]
        if min(aw,ah,bw,bh)<=0:return False
        intersection=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
        union=aw*ah+bw*bh-intersection
        return (intersection/union>=.55 and min(aw,bw)/max(aw,bw)>=.5
                and abs((a[1]+a[3]-b[1]-b[3])/2)<=.4*min(ah,bh))

    def _probe_trocr_default_bias(self):
        """One-time local negative controls for a degenerate trained decoder.

        If a handwriting model invents the SAME complete 08- case from both
        a blank image and unrelated printed words, its confidence is not
        dependable for that value. No scan PDF is used or transmitted.
        """
        if getattr(self,"_trocr_bias_checked",False):
            return
        self._trocr_bias_checked=True
        self._trocr_bias_values=set()
        page_budget=getattr(self,"_page_ocr_budget",None)
        self._page_ocr_budget=None
        try:
            from PIL import Image,ImageDraw,ImageFont
            controls=[]
            white=Image.new("RGB",(320,85),"white")
            controls.append(np.asarray(white))
            words=Image.new("RGB",(480,105),"white")
            pen=ImageDraw.Draw(words)
            pen.text((38,37),"OFFENSE DESCRIPTION",fill="black")
            controls.append(np.asarray(words))
            predictions=[]
            for negative in controls:
                raw,score=self._trocr_read_scored(negative)
                normalized=normalize_case_number(raw)
                predictions.append((normalized,float(score or 0.)))
            if (len(predictions)==2 and
                    predictions[0][0]==predictions[1][0] and
                    CASE_RE.fullmatch(predictions[0][0]) and
                    min(v[1] for v in predictions)>=75.):
                value=_case_identity(predictions[0][0])
                self._trocr_bias_values.add(value)
                self._diag_event("trocr_default_bias_detected",
                    repeated_case=predictions[0][0],
                    confidence=[round(v[1],2) for v in predictions],
                    warning="Handwriting model repeated a case on blank and non-case controls")
                self._log("TrOCR default-bias safeguard: model-only %s reads need manual review",value)
        except Exception as exc:
            self._diag_event("trocr_bias_probe_error",error=str(exc))

        finally:
            self._page_ocr_budget=page_budget

    @staticmethod
    def _plausible_case_ink_crop(region, rect, detected_words=()):
        """Reject model-only hallucinations from non-number ink before TrOCR.

        A neural decoder's token likelihood is NOT visual proof that the pixels
        contain `08-...`. A complete number needs plausible *physical* ink
        extent; neither EasyOCR recognition nor an INCIDENT label is required.
        The printed detector's verified literal case readings stay eligible
        separately even when a handwritten geometry heuristic is inconclusive.
        """
        try:
            height,width=region.shape[:2]
            x0,y0,x1,y1=map(int,rect)
        except (TypeError,ValueError,AttributeError):
            return False,"invalid_box"
        if not (0<=x0<x1<=width and 0<=y0<y1<=height):
            return False,"out_of_roi"
        cw,ch=x1-x0,y1-y0
        # The old OCR path accepted 28x24 boxes and near-full-width rules as
        # complete seven-character case numbers. They are not whole fields.
        ratio=cw/max(1,ch)
        if cw<29 or ch<9 or ratio<1.25 or ratio>13.5:
            return False,"implausible_number_line_dimensions"
        gray=np.asarray(region[y0:y1,x0:x1])
        if gray.ndim==3:
            gray=np.asarray(np.dot(gray[:,:,:3],[.299,.587,.114]),dtype=np.uint8)
        # A single noisy pixel or a dark photographic block is not a number.
        bg=float(np.percentile(gray,80))
        ink=gray < min(205.,bg-26.)
        density=float(ink.mean())
        if density<.006 or density>.59:
            return False,"insufficient_or_saturated_ink"
        if int((ink.sum(axis=0)>1).sum())<max(12,min(30,int(cw*.13))):
            return False,"insufficient_horizontal_glyph_ink"
        if int((ink.sum(axis=1)>1).sum())<6:
            return False,"insufficient_vertical_glyph_ink"
        # Scattered specks can satisfy total density without containing any
        # digit-height strokes. Require substantial connected glyph ink.
        try:
            import cv2
            _,_,stats,_=cv2.connectedComponentsWithStats(ink.astype(np.uint8),8)
            substantial=[r for r in stats[1:] if r[cv2.CC_STAT_HEIGHT]>=max(5,int(ch*.20))
                         and r[cv2.CC_STAT_AREA]>=max(6,int(ch*.30))]
            if (len(substantial)<1 and not any(r[cv2.CC_STAT_WIDTH]>=cw*.25
                    and r[cv2.CC_STAT_HEIGHT]>=max(6,int(ch*.25)) for r in substantial)):
                return False,"specks_without_number_strokes"
        except ImportError:
            pass  # Preserve the existing density gates if OpenCV is unavailable.
        # A full-width horizontal rule masquerading as a number line should
        # not receive an invented OCR case number.
        wide_rows=(ink.sum(axis=1)>=cw*.77)
        if wide_rows.sum()>=max(2,int(ch*.09)):
            # Reject the rule only when it overwhelms all other glyph ink.
            remaining=ink[~wide_rows,:]
            if remaining.size==0 or remaining.mean()<.006:
                return False,"ruled_form_line"
        for word_rect,token,confidence in detected_words:
            value=str(token or '').strip()
            if CASE_RE.fullmatch(normalize_case_number(value)) or CASE_FIELD_LABEL_RE.search(value):
                continue
            # High-confidence ordinary words already recognized by EasyOCR
            # must never be relabelled as handwritten case numbers by TrOCR.
            # Uncertain O/0 substitutions and mixed digits are not blocked.
            if (not re.search(r'[A-Za-z]{4,}',value) or
                re.search(r'[0-9]',value) or float(confidence or 0)<60.):
                continue
            ax0,ay0,ax1,ay1=map(int,word_rect)
            overlap=max(0,min(x1,ax1)-max(x0,ax0))*max(0,min(y1,ay1)-max(y0,ay0))
            word_area=max(1,(ax1-ax0)*(ay1-ay0))
            if overlap/word_area>=.62:
                return False,"non_case_word_detected"
        return True,"visual_number_line_evidence"

    def _prepare_printed_region(self, image, zones, quarter):
        region,qx,qy=quarter
        h,w=image.shape[:2]
        allowed=[(qx,qy,qx+region.shape[1],qy+region.shape[0])]+[tuple(z['rect']) for z in zones]
        x0=min(r[0] for r in allowed);y0=min(r[1] for r in allowed)
        x1=max(r[2] for r in allowed);y1=max(r[3] for r in allowed)
        canvas=np.full_like(image[y0:y1,x0:x1],255)
        for a,b,c,d in allowed:
            canvas[b-y0:d-y0,a-x0:c-x0]=image[b:d,a:c]
        self._page_ocr_budget['printed_region']=(canvas,x0,y0)

    def _automatic_detections(self, region, x_offset, y_offset):
        """One shared detector pass for the quarter and learned locations."""
        budget=getattr(self,"_page_ocr_budget",None)
        shared=(budget.get("printed_region") or budget.get("quarter")) if budget and budget["thread"]==threading.get_ident() else None
        if shared is None:
            return self._timed_easyocr_readtext(region,detail=1,paragraph=False)
        image,qx,qy=shared
        if "shared_detections" not in budget:
            budget["shared_detections"]=self._timed_easyocr_readtext(image,detail=1,paragraph=False)
        h,w=region.shape[:2]
        result=[]
        for box,text,score in budget["shared_detections"]:
            shifted=[[float(x)+qx-x_offset,float(y)+qy-y_offset] for x,y in box]
            if all(0<=x<=w and 0<=y<=h for x,y in shifted):
                result.append((shifted,text,score))
        return result

    def _automatic_handwritten_4x4(self, region, x_offset=0, y_offset=0, run_trocr=True):
        """Reuse one detector pass; spend at most three reads on ranked fields."""
        height,width=region.shape[:2]
        detections=(self._automatic_detections(region,x_offset,y_offset)
                    if self.reader is not None else [])
        pieces=[]
        for box,text,confidence in detections:
            xs=[float(p[0]) for p in box];ys=[float(p[1]) for p in box]
            if not all(math.isfinite(v) for v in xs+ys):continue
            rect=(max(0,int(min(xs))),max(0,int(min(ys))),
                  min(width,int(math.ceil(max(xs)))),min(height,int(math.ceil(max(ys)))))
            if rect[2]>rect[0] and rect[3]>rect[1]:
                pieces.append((rect,normalize_case_number(text),float(confidence or 0)*100))
        self._diag_event("region_raw_reads",readings=[dict(rect=r,text=t,score=c) for r,t,c in pieces])
        def adjacent(a,b):
            h=min(a[3]-a[1],b[3]-b[1])
            return (0<=b[0]-a[2]<=max(5,h*.6) and
                    min(a[3],b[3])-max(a[1],b[1])>=.6*h)
        def union(a,b):return (min(a[0],b[0]),min(a[1],b[1]),max(a[2],b[2]),max(a[3],b[3]))
        # Join split printed labels using only nearby nonnumeric fragments.
        expanded=list(pieces)
        for rect,text,score in pieces:
            if text.upper() not in ("INCIDENT","CASE"):continue
            for other,token,conf in pieces:
                if adjacent(rect,other) and re.fullmatch(r"(?:N[O0]\.?|NUMBER)",token,re.I):
                    expanded.append((union(rect,other),text+" "+token,min(score,conf)))
        labels=[];printed=[];numeric=[]
        for rect,text,score in expanded:
            match=CASE_FIELD_LABEL_RE.search(text)
            if match:
                kind="incident" if INCIDENT_LABEL_RE.search(text) else "case"
                labels.append((rect,kind))
                tail=normalize_case_number(text[match.end():].lstrip(" .:#"))
                if CASE_RE.fullmatch(tail):printed.append((rect,tail,score))
                continue
            if CASE_RE.fullmatch(text):
                # A complete detector reading must NEVER acquire extra digits.
                printed.append((rect,text,score));continue
            if text in ("08-","08"):
                joined=text;bounds=rect;confidence=score
                for other,token,conf in sorted(pieces,key=lambda p:p[0][0]):
                    if other==rect or not adjacent(bounds,other):continue
                    if not re.fullmatch(r"-?[0-9]*",token) or not token:continue
                    # A literal dash must exist in a detected fragment.
                    if joined=="08" and not token.startswith("-"):continue
                    if joined!="08" and token.startswith("-"):continue
                    joined+=token;bounds=union(bounds,other);confidence=min(confidence,conf)
                if CASE_RE.fullmatch(joined):printed.append((bounds,joined,confidence))
            if (re.fullmatch(r"[0-9Il| -]+",text) and
                    (text.startswith("08") or text.startswith("-"))):
                numeric.append(rect)
        def relation(rect):
            choices=[]
            for label,kind in labels:
                if rect==label:return kind,0.0
                lh=label[3]-label[1]
                dy=abs((rect[1]+rect[3]-label[1]-label[3])/2)
                gap=rect[0]-label[2]
                box_h=max(1,rect[3]-rect[1])
                if (-0.65*lh<=gap<=max(125,9*lh) and
                        dy<=max(lh,box_h)*1.05 and box_h<=3.0*lh):
                    choices.append((0 if kind=="incident" else 1,dy+max(0,gap)*.1,kind))
            if not choices:return None,float("inf")
            _,distance,kind=min(choices);return kind,distance
        def blocked(rect):
            # Explicit nonnumeric form words are not number-crop evidence.
            return any(self._same_number_box(rect,r) and re.fullmatch(r"[A-Za-z .]+",t)
                       and re.search(r"[A-Za-z]{2,}",t) for r,t,c in pieces)
        boxes=[p[0] for p in printed]
        proposals=self._automatic_case_crop_rects_4x4(region,max_regions=12)
        for box in numeric+proposals:
            if blocked(box) or any(self._same_number_box(box,b) for b in boxes):continue
            boxes.append(box)
        for label,kind in labels[:4]:
            # No extra EasyOCR detector pass: reuse the 4x4 region and draw
            # the same tight number crop the operator would place to the
            # RIGHT of the existing label. Do not OCR the label itself.
            x0,y0,x1,y1=label;h=max(8,y1-y0)
            field=(min(width,int(x1+2)),max(0,int(y0-h*.48)),
                   min(width,int(x1+max(145,10*h))),
                   min(height,int(y1+h*.48)))
            if (field[2]>field[0]+35 and field[3]>field[1]+11 and
                    not any(self._same_number_box(field,r) and
                            relation(r)[0]==kind and c>=95.
                            for r,n,c in printed)):
                boxes.append(field)
        def priority(box):
            kind,distance=relation(box)
            full=any(self._same_number_box(box,r) for r,n,c in printed)
            numeric_hint=any(self._same_number_box(box,r) for r in numeric)
            # A physically located INCIDENT number is searched before
            # non-case text above/below it. Unlabeled 08- crops still follow.
            return (0 if kind=="incident" else 1 if kind=="case" else 2,
                    not full, not numeric_hint, distance,box[1],-box[2])
        boxes.sort(key=priority)
        candidates=[];calls=0;seen=set()
        for rect in boxes:
            if rect in seen:continue
            seen.add(rect)
            if getattr(self,"stop_event",None) is not None and self.stop_event.is_set():
                raise InterruptedError("Stopped during quarter-page OCR")
            choices=[(n,min(c,94.0) if hw5618.following_ink(region,r) else c,"EasyOCR")
                     for r,n,c in printed if self._same_number_box(rect,r)]
            x0,y0,x1,y1=rect;pad=max(3,int((y1-y0)*.2))
            # One complete printed read >=95% already qualifies; do not
            # spend another model pass manufacturing a contradictory value.
            printed_complete=any(score>=95. for _n,score,_engine in choices)
            if (run_trocr and not printed_complete and calls<(
                    5 if getattr(self,"_page_ocr_budget",None) and
                    self._page_ocr_budget.get("handwriting_uncertain") else 3) and
                    (self.trocr_model is not None or self.trocr_error=="")):
                viable,reason=self._plausible_case_ink_crop(region,rect,pieces)
                if not viable:
                    self._diag_event("model_crop_rejected",rect=list(rect),
                                     reason=reason,printed_available=bool(choices))
                else:
                    crop=region[max(0,y0-pad):min(height,y1+pad),max(0,x0-pad):min(width,x1+pad)]
                    if crop.size and int(crop.max())-int(crop.min())>=8:
                        self._probe_trocr_default_bias()
                        raw,score=self._trocr_read_scored(crop);calls+=1
                        baseline_raw=normalize_case_number(raw)
                        alt_values=[baseline_raw]
                        if score<95 or hw5618.following_ink(region,rect):
                            for variant in hw5618.image_variants(crop):
                                if calls>=5:break
                                alt,alt_score=self._trocr_read_scored(variant);calls+=1
                                if CASE_RE.fullmatch(normalize_case_number(alt)):
                                    alt_values.append(normalize_case_number(alt))
                                    if alt_score>score:raw,score=alt,alt_score
                        if (hw5618.missing_end_conflict(alt_values) or
                                hw5618.following_ink(region,rect)):
                            score=min(score,94.0)
                            self._diag_event('trailing_digit_review',rect=list(rect),readings=alt_values)
                        self._diag_event("crop_raw_read",rect=list(rect),engine="TrOCR",
                                         text=str(raw or ""),score=score)
                        number=normalize_case_number(raw)
                        label=CASE_FIELD_LABEL_RE.search(number)
                        if label:number=normalize_case_number(number[label.end():].lstrip(" .:#"))
                        if CASE_RE.fullmatch(number):
                            if _case_identity(number) in getattr(self,"_trocr_bias_values",set()):
                                self._diag_event("trocr_default_value_rejected",
                                    rect=list(rect),reading=number,
                                    reason="Identical high-confidence predictions from blank and unrelated text")
                            else:
                                choices.append((number,float(score or 0),"TrOCR"))
            if not choices:continue
            number,score,engine=max(choices,key=lambda c:c[1])
            scores={e:c for n,c,e in choices if _case_identity(n)==_case_identity(number)}
            self._diag_event("crop_read_selection",rect=list(rect),readings=[list(c) for c in choices],selected=number,score=score)
            kind,distance=relation(rect)
            absolute=(x0+x_offset,y0+y_offset,x1+x_offset,y1+y_offset)
            candidates.append(dict(number=number,score=score,rect=absolute,box_rect=absolute,
                box_verified=True,prefix_source=engine,engines=list(scores),engine_scores=scores,
                label_type=kind,label_context=bool(kind),incident_label_context=kind=="incident",label_distance=distance))
        self._diag_event("bounded_ocr_pass",printed_calls=int(self.reader is not None),
                         handwriting_calls=calls,candidates=len(candidates))
        return candidates


    def _stage_auto_location(self, record):
        record.pop("pending_auto_location",None)
        if (not record.get("auto_labeled") or record.get("sequence_inferred")
                or record.get("scan_ambiguous") or record.get("sequence_conflict")):
            return
        value=normalize_case_number(record.get("confirmed"))
        score=float(record.get("score") or 0)
        snippet=record.get("snippet")
        if (not CASE_RE.fullmatch(value) or not math.isfinite(score) or score<95
                or not snippet or len(snippet)<5 or snippet[1] is None):
            return
        hit=next((h for h in snippet[4] if h.get("box_verified")
                  and _case_identity(h.get("number"))==_case_identity(value)
                  and float(h.get("score") or 0)>=95),None)
        if not hit or not hit.get("rect"):
            return
        h,w=snippet[1].shape[:2]
        a,b,c,d=map(float,hit["rect"])
        if not (0<=a<c<=w and 0<=b<d<=h):
            return
        original=str(record.get("original_path") or record.get("path") or "")
        identity=str(Path(original).absolute())+"#"+str(snippet[0])
        record["pending_auto_location"]={
            "rect_norm":[a/w,b/h,c/w,d/h],"source_id":identity,
            "source_pdf":identity,"confirmed_case_number":value,
            "source":"uncorrected-auto95-sent","score":score}

    def _commit_auto_location(self, record):
        entry=record.get("pending_auto_location")
        if (not entry or not record.get("exported") or not record.get("auto_labeled")
                or record.get("sequence_inferred") or record.get("scan_ambiguous")
                or record.get("sequence_conflict")
                or _case_identity(record.get("confirmed"))!=_case_identity(entry.get("confirmed_case_number"))):
            return
        try:
            with self.data_lock:
                entries=self.learning_data.setdefault("auto_field_regions_full_page",[])
                if not any(x.get("source_id")==entry["source_id"] for x in entries):
                    entries.append(dict(entry))
                self._persist_learning_data()
            record.pop("pending_auto_location",None)
        except Exception as exc:
            self._log("Sent file retained; automatic location save pending: %s",exc)

    def _ranked_learned_scan_zones(self, width, height):
        data=getattr(self,"learning_data",{}) or {}
        lock=getattr(self,"data_lock",None)
        with lock if lock is not None else nullcontext():
            snapshot={key:list(data.get(key,[]) or [])
                      for key in ("field_regions_full_page","field_regions","auto_field_regions_full_page")}
        full=snapshot.get("field_regions_full_page",[])
        old=snapshot.get("field_regions",[])
        automatic=snapshot.get("auto_field_regions_full_page",[])
        # The same set of human selections is reused across thousands of
        # PDFs; recompute the clustering only when the saved crop list changes.
        key=(width,height,id(data),len(full),len(old),len(automatic),
             str(automatic[-1]) if automatic else "",
             str(full[-1]) if full else "",str(old[-1]) if old else "")
        if getattr(self,"_learned_zone_cache_key",None)==key:
            return list(self._learned_zone_cache)
        zones=_learned_crop_rectangles(snapshot,width,height)
        self._learned_zone_cache_key=key
        self._learned_zone_cache=tuple(zones)
        return zones

    def _scan_one_learned_zone(self, page_image, zone):
        """Read one reviewer-confirmed *physical* field, handwriting first."""
        x0,y0,x1,y1=zone['rect']
        cropped=page_image[y0:y1,x0:x1].copy()
        if cropped.size==0 or int(cropped.max())-int(cropped.min())<8:
            return []
        hits=[]
        def add_hit(number,score,engine,rect):
            number=normalize_case_number(number)
            try:score=float(score or 0.)
            except (TypeError,ValueError):return
            if (not CASE_RE.fullmatch(number) or not math.isfinite(score)
                    or score<0 or score>100):return
            hits.append(dict(number=number,score=score,
                rect=rect,box_rect=rect,box_verified=True,
                prefix_source=engine,engines=[engine],
                engine_scores={engine.split('-',1)[0]:score},
                label_type=None,label_context=False,
                learned_location=True,learned_sample_count=zone['count']))
        # Keep learned LOCATION unchanged; extract only NUMBER pixels for TrOCR.
        words=self._automatic_detections(cropped,x0,y0) if getattr(self,'reader',None) is not None else []
        label_rects=[]
        for box,token,_confidence in words:
            if CASE_FIELD_LABEL_RE.search(str(token)):
                xs=[p[0] for p in box];ys=[p[1] for p in box]
                label_rects.append((min(xs),min(ys),max(xs),max(ys)))
        number_crop,number_rect,number_reason=hw5618.number_only(
            cropped,label_rects=label_rects,
            require_separation=bool(label_rects or
                    cropped.shape[1]>cropped.shape[0]*6.0))
        viable,reason=(self._plausible_case_ink_crop(
            number_crop,(0,0,number_crop.shape[1],number_crop.shape[0]))
            if number_crop is not None else (False,number_reason))
        if not viable:
            self._diag_event('model_crop_rejected',rect=[x0,y0,x1,y1],reason=reason,source='learned')
        if viable and getattr(self,'trocr_model',None) is not None:
            self._probe_trocr_default_bias()
            with self._perf_measure('learned_handwriting'):
                raw,score=self._trocr_read_scored(number_crop)
                readings=[normalize_case_number(raw)]
                if score<95 or number_reason=='edge_clipped':
                    for variant in hw5618.image_variants(number_crop):
                        alt,alt_score=self._trocr_read_scored(variant)
                        if CASE_RE.fullmatch(normalize_case_number(alt)):
                            readings.append(normalize_case_number(alt))
                            if alt_score>score:raw,score=alt,alt_score
                if hw5618.missing_end_conflict(readings) or number_reason=='edge_clipped':
                    score=min(score,94.0)
                    self._diag_event('trailing_digit_review',readings=readings,source='learned')
            number=normalize_case_number(raw)
            bias=getattr(self,'_trocr_bias_values',set())
            if _case_identity(number) not in bias:
                add_hit(number,score,'TrOCR-learned',(x0,y0,x1,y1))
            elif CASE_RE.fullmatch(number):
                self._diag_event('trocr_default_value_rejected',
                    rect=[x0,y0,x1,y1],reading=number,reason='model negative controls')
        # Always consider printed evidence; the shared pass is cached.
        with self._perf_measure('learned_printed'):
            printed=self._automatic_handwritten_4x4(cropped,x0,y0,run_trocr=False)
        for hit in printed:
            hits.append(dict(hit,learned_location=True,learned_sample_count=zone['count']))
        if hits:
            best=_select_primary_case_hit(hits,'learned',page_image.shape)
            hits=self._resolve_ocr_alternates(hits,'learned',best)
        return self._diag_field_filter(hits,'learned')

    def _ocr_pdf(self,path):
        """Search only each page's upper-right quarter for automatic OCR.

        Printed and handwritten fields are checked with EasyOCR and TrOCR.
        No page-wide or learned outside-region OCR is permitted; missing or
        ambiguous case numbers remain for full-page manual review.
        """
        total_start=perf_counter()
        self._perf_stages={}
        with self._perf_measure("network_io_pdf_open"):
            doc=pymupdf.open(path)
        try:
            if not len(doc):
                return ("",None,"Empty PDF — manual review required",False,0.0,[],False)
            results=[]
            pages=[]
            any_label=False
            if getattr(self,"trocr_model",None) is not None:
                self._probe_trocr_default_bias()
            for page_index,page in enumerate(doc):
                stop=getattr(self,"stop_event",None)
                pause=getattr(self,"pause_event",None)
                if stop is not None and stop.is_set():
                    self._diag_event("scan_stop_within_pdf",file=Path(path).name,
                                     page=page_index+1,total_pages=len(doc))
                    # Leave this file unprocessed so it can be scanned later.
                    raise InterruptedError("Stopped during a multi-page PDF")
                if pause is not None:
                    while not pause.wait(timeout=0.2):
                        if stop is not None and stop.is_set():
                            raise InterruptedError("Stopped during a paused PDF")
                if stop is not None and stop.is_set():
                    raise InterruptedError("Stopped during a multi-page PDF")
                with self._perf_measure("pdf_render"):
                    pix=page.get_pixmap(matrix=pymupdf.Matrix(2.2,2.2),alpha=False)
                    image=np.frombuffer(pix.samples,dtype=np.uint8).reshape(
                        pix.height,pix.width,pix.n).copy()
                pages.append(image)
                self._page_ocr_budget={"thread":threading.get_ident(),
                    "printed":0,"handwriting":0,"cache":{}}
                height,width=image.shape[:2]
                side=height//2
                crop_width=width-width//2
                offset_x=width-crop_width
                region=image[:side,offset_x:].copy()
                self._page_ocr_budget["quarter"]=(region,offset_x,0)
                # Every saved manual crop contributes to the ranked spatial
                # clusters.  More examples sharpen the standard size/position;
                # separate form styles retain separate candidate locations.
                zones=self._ranked_learned_scan_zones(width,height)
                self._diag_event('learned_scan_map',page=page_index+1,
                    examples=sum(z['count'] for z in zones),
                    zones=[dict(rect=list(z['rect']),count=z['count']) for z in zones])
                self._prepare_printed_region(image,zones,(region,offset_x,0))
                printed_hits=self._automatic_handwritten_4x4(region,offset_x,0,run_trocr=False)
                printed_complete=self._clear_localized_case(printed_hits,95.0) is not None
                learned_complete=False
                for zone in ([] if printed_complete else zones):
                    if self._page_ocr_budget["handwriting"] >= (
                            4 if self._page_ocr_budget.get("handwriting_uncertain") else 2):
                        break
                    if getattr(self,'stop_event',None) is not None and self.stop_event.is_set():
                        raise InterruptedError('Stopped during learned-zone OCR')
                    with self._perf_measure('learned_zone'):
                        candidates=self._scan_one_learned_zone(image,zone)
                    if candidates:
                        # All of these candidates have a physical location
                        # backed by a human-verified historical crop.
                        results.append((page_index,image,candidates,'learned'))
                    if self._clear_localized_case(candidates,95.0) is not None:
                        learned_complete=True
                        break
                if not learned_complete:
                    # The unchanged upper-right-quarter search is the fallback
                    # when none of the learned locations produces a clear read.
                    self._diag_event("ocr_label_pass",page=page_index+1,
                                     region=[offset_x,0,width,side],labels=[])
                    with self._perf_measure("bounded_upper_right_quarter"):
                        hits=(printed_hits if printed_complete else
                              self._automatic_handwritten_4x4(region,offset_x,0))
                    if hits:
                        incident=[h for h in hits if h.get("label_type")=="incident"]
                        case=[h for h in hits if h.get("label_type")=="case"]
                        results.append((page_index,image,hits,
                                        "incident" if incident else "case" if case else "fallback"))
                # Automatic OCR is restricted to the EXACT upper-right quarter
                # rendered ROI on this page. No reviewer-learned or
                # whole-page fallback may inspect pixels outside that square.
                # Any crop saved outside the square is for MANUAL review only.
                roi=(offset_x, 0, width, side)
                bounded=[]
                for source_page, source_image, source_hits, source_kind in results:
                    if source_page != page_index:
                        bounded.append((source_page,source_image,source_hits,source_kind))
                        continue
                    eligible=(self._localized_case_hits(source_hits,(0,0,width,height))
                              if source_kind=='learned' else
                              self._localized_case_hits(source_hits,roi))
                    if eligible:
                        bounded.append((source_page,source_image,eligible,source_kind))
                    elif source_hits:
                        self._diag_event("outside_quarter_rejected",
                            page=page_index+1,source=source_kind,
                            count=len(source_hits))
                results=bounded
                # Fast path: only a single, detector-located 08- identity at
                # >=95%, with no conflicting candidates on the inspected page.
                # Never use an inferred/TrOCR-only position to skip later pages.
                matches=[r for r in results if r[0]==page_index and r[3] in
                         ("incident","case","header","fallback","learned")]
                candidate_cases={_case_identity(h.get("number")) for _idx,_im,hh,_kind in matches
                                 for h in hh if CASE_RE.fullmatch(normalize_case_number(h.get("number")))}
                if (len(candidate_cases)==1 and matches and
                    any(self._clear_localized_case(hh,95.0) is not None
                        for _idx,_im,hh,_kind in matches)):
                    if len(doc)>page_index+1:
                        self._diag_event("scan_fast_pdf",file=Path(path).name,
                            page=page_index+1,total_pages=len(doc),
                            reason="unique verified 95pct case from upper-right")
                    break
            # Labeled results are SECONDARY clues, not a gate that suppresses
            # independent handwritten evidence inside the 4x4 region.
            if not results:
                index=max(0,len(pages)-1)
                why=("Case label but no complete 08- case in "
                     f"upper-right quarter — MANUAL REVIEW"
                     if any_label else
                     "No valid 08- case in the upper-right quarter search — MANUAL REVIEW")
                return ("",(index,pages[-1],"Upper-right quarter case field not read",None,[]),
                        why,False,0.0,[],False)
            results=[(idx,img,self._diag_field_filter(hits,kind),kind)
                     for idx,img,hits,kind in results]
            results=[item for item in results if item[2]]
            if not results:
                return ("",(0,pages[0],"No valid 08- crop",None,[]),
                        "No valid 08- case in the upper-right quarter — MANUAL REVIEW",False,0.0,[],False)
            def rank(entry):
                index,image,hits,kind=entry
                verified=max((float(h.get("score") or 0) for h in hits
                    if h.get("box_verified") and h.get("box_rect")),default=0.0)
                return (verified>=95.0,verified,
                        {"learned":6,"header":5,"incident":4,"case":3,"fallback":2}[kind])
            index,image,hits,kind=max(results,key=rank)
            self._diag_event("ocr_candidates",page=index+1,source=kind,
                 all_candidates=self._diag_hits(hits),
                 per_page=[{"page":x[0]+1,"source":x[3],
                            "hits":self._diag_hits(x[2])} for x in results])
            hits=self._diag_field_filter(hits,kind)
            hits=self._prefer_case_margin(hits,kind,image)
            if not hits:
                self._diag_event("ocr_decision",page=index+1,source=kind,
                     outcome="manual_review",reason="no_year_matching_case_candidate")
                return ("",(index,image,
                         f"upper-right quarter case read rejected",None,[]),
                         "No valid 08- case in the upper-right quarter search — MANUAL REVIEW",
                         False,0.0,[],False)
            best=_select_primary_case_hit(hits,kind,image.shape)
            hits=self._resolve_ocr_alternates(hits,kind,best)
            number=best["number"]
            engines=list(best.get("engines") or [])
            engine_names={e.split("-",1)[0] for e in engines}
            agree="EasyOCR" in engine_names and "TrOCR" in engine_names
            distinct={h["number"] for h in hits}
            across=set()
            for _page,_image,_hits,_source in results:
                if _source!=kind:
                    continue
                _filtered=self._diag_field_filter(_hits,kind)
                _filtered=self._prefer_case_margin(_filtered,kind,_image)
                if not _filtered:
                    continue
                _best=_select_primary_case_hit(_filtered,kind,_image.shape)
                across.update(h["number"] for h in
                    self._resolve_ocr_alternates(_filtered,kind,_best))
            numeric_keys=lambda values: {_case_identity(value) for value in values}
            high_conf_cases={_case_identity(h.get("number"))
                for _pg,_img,hh,_source in results
                if _source in ("header","fallback","incident","case","learned")
                for h in hh if CASE_RE.fullmatch(normalize_case_number(h.get("number")))
                and h.get("box_verified") and h.get("box_rect")
                and float(h.get("score") or 0)>=95.0}
            all_region_cases={_case_identity(h.get("number"))
                for _idx,_im,other_hits,source in results
                if source in ("header","fallback","incident","case","learned")
                for h in other_hits
                if CASE_RE.fullmatch(normalize_case_number(h.get("number")))}
            # Only independently verified, >=95% competing readings of the
            # selected physical field can block automatic approval. A weak
            # variant or unrelated lower-page number cannot cause REVIEW.
            ambiguous=len({_case_identity(h.get("number")) for h in hits
                           if h.get("box_verified") and h.get("box_rect") and
                           float(h.get("score") or 0.)>=95.0})>1
            visuals=[]
            for h in hits:
                rect=h.get("rect")
                visuals.append({
                    "number":h["number"],
                    "rect":[int(v) for v in h["box_rect"]]
                           if h.get("box_verified") and h.get("box_rect") else None,
                    "box_verified":bool(h.get("box_verified") and h.get("box_rect")),
                    "score":float(h.get("score") or 0),
                    "engines":list(h.get("engines") or []),
                    "engine_scores":dict(h.get("engine_scores") or {}),
                    "label_type":h.get("label_type"),
                    "label_distance":0.0,
                    "learned_location":bool(h.get("learned_location"))})
            self._diag_event("ocr_decision",page=index+1,source=kind,
                 primary=number,score=float(best.get("score") or 0),
                 ambiguous=ambiguous,conflicting=sorted(distinct),
                 engines=engines,selected_bbox=best.get("rect"),
                 outcome="manual_review" if ambiguous else "candidate_ready")
            if ambiguous:
                all_conflicts=sorted({h.get("number") for h in hits
                        if CASE_RE.fullmatch(normalize_case_number(h.get("number")))
                        and float(h.get("score") or 0.)>=95.0})
                note=(f"upper-right quarter case field has conflicting OCR reads: "
                      + ", ".join(all_conflicts)+" — MANUAL REVIEW")
            elif kind=="learned":
                note="Verified manual-crop location — handwriting-first OCR; 95% required for auto-completion"
            elif kind=="incident":
                note="Case field right-of-label full number — Upper-right quarter labeled printed OCR"
            elif kind=="case":
                note="CASE NUMBER field full number — Upper-right quarter labeled printed OCR"
            elif kind=="header":
                note="Upper-right header quarter-page candidate — either-engine approval and before/after check"
            else:
                note=("Upper-right fallback in the upper-right quarter — right-margin case box "
                      "takes priority over fax pagination; 95% required for "
                      "direct approval, otherwise before/after or REVIEW")
            if len(doc)>1:
                note+=f" • page {index+1} of {len(doc)}"
            return (number,(index,image,f"{number} — {kind}",
                    best.get("rect"),visuals),note,
                    "TrOCR" in engine_names,float(best.get("score") or 0),
                    engines,agree)
        finally:
            self._page_ocr_budget=None
            doc.close()
            self._perf_file_report(path,total_start)

    def _find_case_candidates(self, image, x_offset=0, y_offset=0, run_trocr=True, case_pattern_override=None):
        """Find only case-number-shaped text, prioritizing nearby field labels."""
        year_prefix = REQUIRED_CASE_YEAR  # HARD GATE: no non-08 case enters OCR candidates.
        case_pattern = case_pattern_override or re.compile(
            r"(?<![A-Za-z0-9|])(08\s*-\s*[0-9]{1,7})(?![A-Za-z0-9|]|\s+(?!08\s*-)[0-9]|\s+[Il|](?![A-Za-z]))")
        detections = self._timed_easyocr_readtext(image, detail=1, paragraph=False)
        candidates = {}
        parsed = []
        field_label_boxes = []

        def bbox_metrics(bbox):
            xs = [float(point[0]) for point in bbox]
            ys = [float(point[1]) for point in bbox]
            return min(xs), min(ys), max(xs), max(ys)

        def field_type(text):
            clean = re.sub(r"\s+", " ", str(text or "")).strip()
            if re.search(r"INCIDENT NO\.", clean, re.I):
                return "incident"
            if re.search(r"Case Number", clean, re.I):
                return "case"
            return None

        # Parse the first EasyOCR pass and identify labels that are visible as
        # their own text boxes or combined with the handwritten number.
        for bbox, raw_text, confidence in detections:
            text = str(raw_text).replace("—", "-").replace("–", "-")
            x0, y0, x1, y1 = bbox_metrics(bbox)
            parsed.append((bbox, text, confidence, (x0, y0, x1, y1)))
            label_kind = field_type(text)
            if label_kind:
                field_label_boxes.append((x0, y0, x1, y1, text, label_kind))

        # EasyOCR sometimes splits short labels such as "Sys." and "#" into
        # separate detections. Join adjacent nonnumeric fragments on one line
        # so that the label can still anchor the case number to its right.
        fragments = [item for item in parsed
                     if not any(ch.isdigit() for ch in item[1])]
        fragments.sort(key=lambda item: ((item[3][1] + item[3][3]) / 2.0, item[3][0]))
        for i, left in enumerate(fragments):
            lx0, ly0, lx1, ly1 = left[3]
            joined = left[1]
            jx1 = lx1
            jy0, jy1 = ly0, ly1
            for right in fragments[i + 1:i + 4]:
                rx0, ry0, rx1, ry1 = right[3]
                vertical_delta = abs((ry0 + ry1) / 2.0 - (jy0 + jy1) / 2.0)
                horizontal_gap = rx0 - jx1
                if vertical_delta > 24.0 or horizontal_gap < -4.0 or horizontal_gap > 55.0:
                    continue
                joined = joined + " " + right[1]
                jx1 = rx1
                jy0, jy1 = min(jy0, ry0), max(jy1, ry1)
                label_kind = field_type(joined)
                if label_kind:
                    field_label_boxes.append((lx0, jy0, jx1, jy1, joined, label_kind))

        def label_relation(rect, text):
            # A detection containing both the field label and number is itself
            # an anchored candidate. Otherwise, the number must be to its right
            # and on the same horizontal line as the label.
            own_label = field_type(text)
            if own_label:
                return own_label, 0.0
            if not field_label_boxes:
                return None, float("inf")
            x0, y0, x1, y1 = rect
            cy = (y0 + y1) / 2.0
            best_kind, best_distance = None, float("inf")
            priority = {"incident": 4, "case": 4, "report": 2, "sys": 1}
            for lx0, ly0, lx1, ly1, _label_text, kind in field_label_boxes:
                label_height = max(1.0, ly1 - ly0)
                vertical_delta = abs(cy - (ly0 + ly1) / 2.0)
                horizontal_gap = x0 - lx1
                max_gap = max(260.0, (lx1 - lx0) * 2.5)
                if (-14.0 <= horizontal_gap <= max_gap and
                        vertical_delta <= max(48.0, (y1 - y0 + label_height) * 0.85)):
                    distance = vertical_delta + max(0.0, horizontal_gap) * 0.12
                    if (best_kind is None or priority[kind] > priority[best_kind] or
                            (priority[kind] == priority[best_kind] and distance < best_distance)):
                        best_kind, best_distance = kind, distance
            return best_kind, best_distance

        def add_candidate(number, engine, score, rect, label_kind=None,
                          label_distance=float("inf"), box_verified=False):
            number=re.sub(r"\s+","",str(number or ""))
            if not CASE_RE.fullmatch(number) or not number.startswith("08-"):
                return
            entry = candidates.setdefault(number, {
                "number": number, "score": 0.0, "rect": rect,
                "engines": [], "engine_scores": {}, "label_context": False,
                "incident_label_context": False, "label_type": None,
                "label_distance": float("inf"),"box_verified":False,
                "box_rect":None})
            if engine not in entry["engines"]:
                entry["engines"].append(engine)
            family=engine.split("-",1)[0]
            entry["engine_scores"][family]=max(entry["engine_scores"].get(family,0),float(score or 0))
            previous_score = entry["score"]
            entry["score"] = max(previous_score, float(score or 0.0))
            if rect and (not entry.get("rect") or float(score or 0.0) > previous_score):
                entry["rect"] = rect
            if box_verified and rect:
                current_box=entry.get("box_rect")
                new_area=(rect[2]-rect[0])*(rect[3]-rect[1])
                old_area=((current_box[2]-current_box[0])*(current_box[3]-current_box[1])
                          if current_box else float("inf"))
                if new_area < old_area:
                    entry["box_rect"]=rect
                entry["box_verified"]=True
            if label_kind:
                priority = {"incident": 4, "case": 4, "report": 2, "sys": 1}
                old_kind = entry.get("label_type")
                if old_kind is None or priority[label_kind] > priority[old_kind]:
                    entry["label_type"] = label_kind
                entry["label_context"] = True
                entry["label_distance"] = min(entry["label_distance"], label_distance)
                if label_kind == "incident":
                    entry["incident_label_context"] = True

        # EasyOCR sometimes detects "08-" and "287" as two boxes. A partial
        # prefix should not win simply because its score is high. Join only
        # adjacent numeric fragments that share the SAME physical line.
        fragments=sorted(parsed,key=lambda item:(item[3][1]+item[3][3],item[3][0]))
        consumed_prefixes=set()
        for index,(_box,text,confidence,rect) in enumerate(fragments):
            stem=re.sub(r"\s+", "", normalize_case_number(text))
            if not re.fullmatch(r"08-\d{0,6}",stem):
                continue
            x0,y0,x1,y1=rect
            tail=[];last_x=x1
            for _,next_text,next_score,next_rect in sorted(fragments,key=lambda e:e[3][0]):
                xx0,yy0,xx1,yy1=next_rect
                if xx0 < last_x-2 or (xx0==x0 and yy0==y0):continue
                height=max(10,y1-y0)
                if abs((yy0+yy1)/2-(y0+y1)/2)>max(16,height*.75):continue
                if xx0-last_x>max(24,min(55,height*1.15)):continue
                piece=re.sub(r"\s+", "",str(next_text))
                if not re.fullmatch(r"\d{1,6}",piece):continue
                if len(stem.split('-',1)[1])+sum(len(v[0]) for v in tail)+len(piece)>7:
                    continue
                tail.append((piece,float(next_score or 0),next_rect))
                last_x=xx1
                if len(tail)>=3:break
            if not tail:continue
            joined_number=stem+''.join(v[0] for v in tail)
            if not CASE_RE.fullmatch(joined_number):continue
            far=max(v[2][2] for v in tail)
            ymin=min([y0]+[v[2][1] for v in tail]);ymax=max([y1]+[v[2][3] for v in tail])
            joined_rect=(int(x0+x_offset),int(ymin+y_offset),
                         int(far+x_offset),int(ymax+y_offset))
            kind,distance=label_relation((x0,ymin,far,ymax),joined_number)
            add_candidate(joined_number,"EasyOCR-joined",
                          min([float(confidence or 0)]+[v[1] for v in tail])*100,
                          joined_rect,kind,distance,box_verified=True)
            consumed_prefixes.add((x0,y0,x1,y1))

        combined_text = []
        for bbox, text, confidence, local_rect in parsed:
            combined_text.append(text)
            normalized = re.sub(r"\s*-\s*", "-", text)
            x0, y0, x1, y1 = local_rect
            rect = (
                max(0, int(x0) + x_offset), max(0, int(y0) + y_offset),
                int(x1) + x_offset, int(y1) + y_offset)
            label_kind, label_distance = label_relation(local_rect, text)

            # Standard case-number form remains the primary rule.
            strict_matches = list(case_pattern.finditer(normalized))
            for match in ([] if local_rect in consumed_prefixes else strict_matches):
                add_candidate(
                    re.sub(r"\s+", "", match.group(1)), "EasyOCR",
                    float(confidence) * 100.0, rect, label_kind,
                    label_distance,box_verified=True)

            digit_count = sum(ch.isdigit() for ch in text)
            likely_case_line = digit_count >= 2 or "-" in text
            # Fax pagination, e.g. "122 P.005/015", is not a case number.
            # Never let TrOCR hallucinations turn a slash into an 08- prefix.
            fax_pagination=bool(re.search(
                r"(?:\bP\s*[.:-]?\s*\d{1,4}\s*/\s*\d{1,4}\b|"
                r"\b\d{1,4}\s*/\s*\d{1,4}\b)",
                text,re.I) and not strict_matches)
            if run_trocr and likely_case_line and not fax_pagination:
                px=max(4,int((y1-y0)*.3));py=max(3,int((y1-y0)*.15))
                bx0=max(0,int(x0)-px);by0=max(0,int(y0)-py)
                bx1=min(image.shape[1],int(x1)+px);by1=min(image.shape[0],int(y1)+py)
                for hit in self._recognize_case_strip(image[by0:by1,bx0:bx1]):
                    add_candidate(hit["number"],"EasyOCR-strip",hit["score"],
                                  (bx0+x_offset,by0+y_offset,bx1+x_offset,by1+y_offset),
                                  label_kind,label_distance)
                try:
                    crop_x0 = max(0, int(x0) - 12)
                    crop_y0 = max(0, int(y0) - 12)
                    crop_x1 = min(image.shape[1], int(x1) + 12)
                    crop_y1 = min(image.shape[0], int(y1) + 12)
                    line_crop = image[crop_y0:crop_y1, crop_x0:crop_x1]
                    if line_crop.size:
                        trocr_line, trocr_score = self._trocr_read_scored(line_crop)
                        trocr_norm = re.sub(
                            r"\s*-\s*", "-",
                            trocr_line.replace("—", "-").replace("–", "-"))
                        for match in case_pattern.finditer(trocr_norm):
                            add_candidate(
                                re.sub(r"\s+", "", match.group(1)), "TrOCR",
                                trocr_score, rect, label_kind, label_distance)
                except Exception as exc:
                    self._log("TrOCR line recognition failed: %s", exc)

        # Re-read each OCR box that looks number-like as a separate enlarged
        # image. This is a genuine second OCR pass on the crop, not a numerical
        # score boost. The detector's returned confidence is retained as-is.
        try:
            from PIL import Image, ImageEnhance, ImageOps, ImageFilter
            enhanced_seen = set()
            for _bbox, raw_text, _raw_score, (bx0, by0, bx1, by1) in parsed:
                # No duplicate enhanced recognition for a strongly recognized
                # exact 08- candidate. Keep enhancement for uncertain reads.
                if (float(_raw_score)>=0.95 and
                    case_pattern.search(re.sub(r"\s*-\s*","-",str(raw_text)))):
                    continue
                if ("/" in str(raw_text) and
                    not case_pattern.search(str(raw_text))):
                    # Skip fax counters in the enhanced OCR pass too.
                    continue
                if sum(ch.isdigit() for ch in raw_text) < 2 and "-" not in raw_text:
                    continue
                cx0 = max(0, int(bx0) - 14); cy0 = max(0, int(by0) - 10)
                cx1 = min(image.shape[1], int(bx1) + 14); cy1 = min(image.shape[0], int(by1) + 10)
                if cx1 <= cx0 or cy1 <= cy0:
                    continue
                crop_key = (cx0, cy0, cx1, cy1)
                if crop_key in enhanced_seen:
                    continue
                enhanced_seen.add(crop_key)
                if len(enhanced_seen) > 12:
                    break
                crop_arr = image[cy0:cy1, cx0:cx1]
                if not crop_arr.size:
                    continue
                gray = Image.fromarray(crop_arr).convert("L")
                gray = ImageOps.autocontrast(gray, cutoff=1)
                gray = ImageEnhance.Contrast(gray).enhance(1.25)
                gray = ImageEnhance.Sharpness(gray).enhance(1.4)
                enlarged = gray.resize((max(1, gray.width * 3), max(1, gray.height * 3)),
                                       Image.Resampling.LANCZOS).convert("RGB")
                enhanced_reads = self._timed_easyocr_readtext(np.asarray(enlarged), detail=1, paragraph=False)
                if re.search(r"08\s*-",str(raw_text)):
                    enhanced_reads += self._timed_easyocr_readtext(np.asarray(enlarged),
                        detail=1,paragraph=False,allowlist="0123456789-")
                for ebbox, enhanced_text, enhanced_score in enhanced_reads:
                    normalized = re.sub(r"\s*-\s*", "-",
                                         str(enhanced_text).replace("—", "-").replace("–", "-"))
                    exs = [float(point[0]) for point in ebbox]
                    eys = [float(point[1]) for point in ebbox]
                    er = (cx0 + min(exs) / 3.0, cy0 + min(eys) / 3.0,
                          cx0 + max(exs) / 3.0, cy0 + max(eys) / 3.0)
                    absolute_rect = (int(er[0] + x_offset), int(er[1] + y_offset),
                                     int(er[2] + x_offset), int(er[3] + y_offset))
                    kind, distance = label_relation(er, str(enhanced_text))
                    for match in case_pattern.finditer(normalized):
                        add_candidate(re.sub(r"\s+", "", match.group(1)),
                                      "EasyOCR-enhanced", float(enhanced_score) * 100.0,
                                      absolute_rect, kind, distance,
                                      box_verified=True)
        except Exception as exc:
            self._log("Enlarged candidate-crop OCR pass failed: %s", exc)

        # A second OCR pass is tightly anchored to any recognized case-number
        # field label (INCIDENT NO., CASE NO., REPORT NO., or the rarer SYS. #).
        # It enlarges only the label and the area to its right, not the whole page.
        if run_trocr and field_label_boxes:
            seen_regions = set()
            for lx0, ly0, lx1, ly1, _label_text, label_kind in field_label_boxes:
                region_x0 = max(0, int(lx0) - 8)
                region_x1 = min(image.shape[1], int(lx1 + max(300.0, (lx1 - lx0) * 2.5)))
                label_height = max(1.0, ly1 - ly0)
                vertical_pad = max(24, int(round(label_height * 1.4)))
                region_y0 = max(0, int(ly0) - vertical_pad)
                region_y1 = min(image.shape[0], int(ly1) + vertical_pad)
                region_key = (region_x0, region_y0, region_x1, region_y1)
                if region_key in seen_regions or region_x1 <= region_x0 or region_y1 <= region_y0:
                    continue
                seen_regions.add(region_key)
                anchored_crop = image[region_y0:region_y1, region_x0:region_x1]
                if not anchored_crop.size:
                    continue
                # Upscale the relatively small field strip before a second
                # EasyOCR pass; this can recover a faint final handwritten digit.
                try:
                    from PIL import Image, ImageEnhance
                    pil_crop = Image.fromarray(anchored_crop).convert("RGB")
                    enlarged = pil_crop.resize(
                        (max(1, pil_crop.width * 2), max(1, pil_crop.height * 2)),
                        Image.Resampling.BICUBIC)
                    enhanced = ImageEnhance.Contrast(enlarged).enhance(1.25)
                    enhanced = ImageEnhance.Brightness(enhanced).enhance(1.03)
                    anchor_detections = self._timed_easyocr_readtext(
                        np.asarray(enhanced), detail=1, paragraph=False)
                    for bbox, raw_text, confidence in anchor_detections:
                        text = str(raw_text).replace("—", "-").replace("–", "-")
                        local = bbox_metrics(bbox)
                        ax0, ay0, ax1, ay1 = [v / 2.0 for v in local]
                        local_rect = (
                            region_x0 + ax0, region_y0 + ay0,
                            region_x0 + ax1, region_y0 + ay1)
                        absolute_rect = (
                            int(local_rect[0] + x_offset), int(local_rect[1] + y_offset),
                            int(local_rect[2] + x_offset), int(local_rect[3] + y_offset))
                        # The second pass is scoped to the recognized field strip;
                        # a matching value in this crop is label-associated.
                        for match in case_pattern.finditer(re.sub(r"\s*-\s*", "-", text)):
                            add_candidate(
                                re.sub(r"\s+", "", match.group(1)), "EasyOCR-anchored",
                                float(confidence) * 100.0, absolute_rect,
                                label_kind, 0.0, box_verified=True)
                except Exception as exc:
                    self._log("Label-anchored EasyOCR recognition failed: %s", exc)

                try:
                    label_value_text, trocr_score = self._trocr_read_scored(anchored_crop)
                    label_value_norm = re.sub(
                        r"\s*-\s*", "-",
                        label_value_text.replace("—", "-").replace("–", "-"))
                    region_rect = (
                        region_x0 + x_offset, region_y0 + y_offset,
                        region_x1 + x_offset, region_y1 + y_offset)
                    for match in case_pattern.finditer(label_value_norm):
                        add_candidate(
                            re.sub(r"\s+", "", match.group(1)),
                            "TrOCR-anchored", trocr_score, region_rect, label_kind, 0.0)
                except Exception as exc:
                    self._log("Label-anchored TrOCR recognition failed: %s", exc)

        # Preserve joined detections, but only when the joined text still
        # contains a complete case-number-shaped value.
        combined = " ".join(combined_text)
        for match in case_pattern.finditer(re.sub(r"\s*-\s*", "-", combined)):
            number = re.sub(r"\s+", "", match.group(1))
            if number not in candidates:
                add_candidate(number, "EasyOCR", 0.0, None)

        # Return every strict case-number-shaped candidate. Field labels affect
        # ranking, but do not hide other possible case numbers from the reviewer.
        return list(candidates.values())


    def _poll(self):
        try:
            while True:
                msg = self.queue.get_nowait()
                kind = msg[0]
                if kind == "review_crop_result":
                    _,window,iid,rect,prior,result,callback=msg
                    rec=self.records.get(iid)
                    if (rec and rec.get("manual_crop_origin")=="drawn" and
                        tuple(rec.get("manual_crop_rect") or ())==tuple(rect)):
                        try:callback(prior,result)
                        except tk.TclError:pass
                elif kind == "folder_load_result":
                    _, generation, year, saved, pdfs, state_error = msg
                    if generation == self._folder_load_generation:
                        if self._folder_load_timeout_id is not None:
                            self.root.after_cancel(self._folder_load_timeout_id)
                            self._folder_load_timeout_id=None
                        try:
                            self._apply_folder_load(year, saved, pdfs, state_error)
                        finally:
                            self._loader_finish("rolls")
                            self.refresh_root_btn.config(state="normal")
                            if self._startup_ready and not (self._loading_jobs & {"rolls", "learning"}):
                                self.choose_folder_btn.config(state="normal")
                elif kind == "folder_load_error":
                    _, generation, detail = msg
                    if generation == self._folder_load_generation:
                        if self._folder_load_timeout_id is not None:
                            self.root.after_cancel(self._folder_load_timeout_id)
                            self._folder_load_timeout_id=None
                        self._loader_finish("rolls")
                        self.refresh_root_btn.config(state="normal")
                        self.choose_folder_btn.config(state="normal")
                        self.status.config(text="COULD NOT REFRESH ROOT PDFS; CURRENT LIST REMAINS UNCHANGED.")
                        messagebox.showerror("Folder load failed", detail)
                elif kind == "offline_models_ready":
                    self._offline_prepare_busy=False
                    if getattr(self,"prepare_offline_btn",None) is not None:
                        self.prepare_offline_btn.config(state="normal")
                    self.reader=None
                    self.trocr_model=None
                    self.trocr_processor=None
                    self.trocr_error=""
                    self.status.config(text="MODELS INSTALLED LOCALLY. "
                                            "Initializing offline OCR…")
                    self._begin_model_preload()
                elif kind == "offline_models_error":
                    self._offline_prepare_busy=False
                    if getattr(self,"prepare_offline_btn",None) is not None:
                        self.prepare_offline_btn.config(state="normal")
                    self.status.config(text="MODEL PREPARATION FAILED: "+str(msg[1]))
                    messagebox.showerror("Offline models",
                        "Could not prepare all models; nothing was uploaded. "
                        "Check the connection or available disk space.\n\n"+str(msg[1]))
                elif kind == "startup_models_ready":
                    self._complete_startup()
                elif kind == "startup_models_error":
                    self._fail_startup(msg[1])
                elif kind == "model_load_start":
                    self._loader_start("model", msg[1])
                elif kind == "model_load_done":
                    self._loader_finish("model")
                elif kind == "learning_connect_result":
                    _, connected, detail = msg
                    startup_origin = self._learning_connection_source == "startup"
                    self._learning_connection_source = None
                    self._loader_finish("learning")
                    if (not (self._loading_jobs & {"rolls", "learning"})
                            and not startup_origin):
                        self.choose_folder_btn.config(state="normal")
                    self._refresh_learning_stats()
                    self._refresh_learning_button_state()
                    if connected:
                        self.status.config(text=f"{detail} PDF queue is ready.")
                        if startup_origin:
                            pass  # Shared learning may load, but model training never starts at launch.
                        elif self._startup_learning_pending:
                            pass  # Shared learning may load, but model training never starts at launch.
                    else:
                        if startup_origin:
                            self._startup_learning_pending = False
                            self._fail_startup(detail)
                        self.status.config(text="SHARED LEARNING UNAVAILABLE; SCANNING DISABLED.")
                        if not startup_origin:
                            messagebox.showwarning(
                            "Shared learning unavailable",
                            "Scanning is blocked until shared learning is available. "
                            "Reconnect to the department network and retry startup.\n\n"
                            f"Expected location:\n\\\\172.16.1.231\\MicroficheNew\\2008\\.case_ocr_learning\n\n"
                            f"Details: {detail}")
                elif kind == "blank":
                    _, iid, destination, idx = msg
                    if iid==self._active_scan_iid:
                        self._active_scan_iid=None
                        if self.tree.exists(iid):self.tree.item(iid,tags=())
                    if iid in self.records:
                        self.records[iid]["status"] = "Moved to BLANKS"
                        if self.tree.exists(iid):
                            self.tree.delete(iid)
                    self._scan_progress_update(idx,"SCANNING")
                    self.status.config(
                        text=f"Scanned {idx} of {len(self.scan_items)} PDFs. Blank PDF moved to BLANKS: {destination.name}")
                    self._refresh_review_controls()
                    self._save_state()
                elif kind == "blank_conflict":
                    _, iid, destination, idx = msg
                    if iid==self._active_scan_iid:
                        self._active_scan_iid=None
                        if self.tree.exists(iid):self.tree.item(iid,tags=())
                    if iid in self.records:
                        self.records[iid]["status"] = "Blank candidate — filename conflict; left in root"
                        if self.tree.exists(iid):
                            self._insert_scan_row(iid, self.records[iid])
                    self._scan_progress_update(idx,"SCANNING")
                    self.status.config(
                        text=f"Blank PDF not moved because BLANKS already contains {destination.name}.")
                    self._refresh_review_controls()
                    self._save_state()
                elif kind == "file_started":
                    _,iid,filename,index,total=msg
                    self._active_scan_iid=iid
                    self._scan_progress_update(index-1,"SCANNING",filename=filename,index=index)
                    if self.tree.exists(iid):
                        self.tree.item(iid,tags=("scanning_now",))
                    self.status.config(text=f"SCANNING {index} of {total}: {filename}")
                    self._schedule_scan_preview_refresh()
                    self._audit_event("FILE_SCANNING",original_name=filename,index=index,total=total)
                elif kind == "row":
                    _, iid, cand, snip, note, hand, score, engines, agreement, idx = msg
                    r = self.records[iid]
                    if iid==self._active_scan_iid:
                        self._active_scan_iid=None
                        if self.tree.exists(iid):
                            self.tree.item(iid,tags=())
                    normalized_candidate = normalize_case_number(cand) if cand else ""
                    if not CASE_RE.fullmatch(normalized_candidate):
                        normalized_candidate = ""  # Hard 08- gate before saving any OCR read.
                    if r.get("manual_approved") and not r.get("auto_labeled"):
                        self._diag_event("manual_override_preserved",
                            file=Path(r["path"]).name,ocr_candidate=normalized_candidate,
                            confirmed=r.get("confirmed"),score=float(score or 0))
                        if snip is not None and r.get("snippet") is None:
                            r["snippet"]=snip
                        self._insert_scan_row(iid,r)
                        self._scan_progress_update(idx,"SCANNING")
                        self._save_state()
                        continue
                    r.pop("pending_auto_location",None)
                    r.update(confirmed="",manual_approved=False,auto_labeled=False)
                    if self.completed_tree.exists(iid):
                        self.completed_tree.delete(iid)
                    r.update(
                        candidate=normalized_candidate, snippet=snip, status=note,
                        handwritten=hand, score=score, engines=engines,
                        engine_agreement=agreement,
                        engine_scores=next((dict(h.get("engine_scores") or {}) for h in
                            (snip[4] if snip and len(snip)>4 else [])
                            if h.get("number")==normalized_candidate),{}),
                        scan_source=("incident" if note.startswith("Case field right-of-label")
                                     or note.startswith("INCIDENT NO. has conflicting")
                                     else "case" if note.startswith("CASE NUMBER field")
                                     else "fallback" if note.startswith("Upper-right")
                                     else "incident-unreadable" if note.startswith("Case field located")
                                     else "unknown"),
                        scan_ambiguous="conflicting OCR reads" in note,
                        sequence_excluded=False,
                        sequence_conflict=False, sequence_inferred=False,
                        sequence_previous_prefill=False,
                        sequence_anchors=[], sequence_suggestion="")
                    if note.startswith("Upper-right header"):
                         r["scan_source"]="header"
                     # Diagnostic counters belong to THIS run, not accumulated saved state.
                    if note.startswith("Scan error"):
                        reason = "scan_error"
                    elif "conflicting OCR reads" in note:
                        reason = "conflicting_readings"
                    elif note.startswith("Upper-right"):
                        reason = "unverified_fallback_location"
                    elif not normalized_candidate:
                        reason = "no_case_number"
                    elif r["scan_source"] == "incident" and score < self._get_auto_threshold():
                        reason = "incident_low_or_uncalibrated_confidence"
                    else:
                        reason = "incident_candidate"
                    self._diag_event("row_decision",file=Path(r["path"]).name,
                         candidate=normalized_candidate,score=float(score or 0),
                         source=r["scan_source"],ambiguous=bool(r["scan_ambiguous"]),
                         status=note,engines=list(engines or []),
                         outcome_bucket=reason)
                    self._run_outcomes[reason] = self._run_outcomes.get(reason, 0) + 1
                    auto_done = False
                    labeled = r["scan_source"] in ("incident","case")
                    positioned_header = r["scan_source"] == "header"
                    strong = float(score or 0) >= self._get_auto_threshold()
                    # A fallback's qualifying score is usable only with a real OCR
                    # bounding box from _ocr_pdf's 4x4 region, not an unlocated
                    # TrOCR guess or a preview-only crop.
                    fallback_95 = (
                        r["scan_source"] == "fallback"
                        and float(score or 0) >= self._get_auto_threshold()
                        and isinstance(snip,(tuple,list)) and len(snip)>=4
                        and snip[3] is not None
                        and self._is_in_4x4_rect(snip[3],snip[1])
                    )
                    localized_complete_08=bool(snip is not None and len(snip)>1
                        and snip[1] is not None and any(
                            h.get("box_verified") and
                            _case_identity(h.get("number"))==_case_identity(normalized_candidate)
                            and self._is_in_4x4_rect(
                                h.get("box_rect") or h.get("rect"),snip[1])
                            for h in self._valid_ocr_box_candidates(r)))
                    # One unambiguous located 08- reading at >=95% completes.
                    # Source label and other model's confidence are irrelevant.
                    if (normalized_candidate and localized_complete_08 and strong
                            and not r["scan_ambiguous"]):
                        block_reason=self._auto_label_block_reason(
                            score,agreement,normalized_candidate,r["engine_scores"])
                        if block_reason is None:
                            r["confirmed"]=normalized_candidate
                            r["approval_rule_version"]="5.3.2"
                            r["auto_labeled"]=True
                            r["manual_approved"]=True
                            rule=("95pct_labeled_case_field" if labeled
                                  else "95pct_unique_upper_right_header" if positioned_header
                                  else "95pct_localized_4x4_fallback")
                            area=("labeled field" if labeled else "upper-right header"
                                  if positioned_header else "Upper-right quarter localized fallback")
                            r["status"]=(
                                f"{score:.1f}% {area} — automatically approved; "
                                "awaiting SEND FILES")
                            self._diag_event("approval_decision",
                                file=Path(r["path"]).name,outcome="auto_approved",
                                rule=rule,case=normalized_candidate,
                                score=float(score or 0),source=r["scan_source"])
                            self._stage_auto_location(r)
                            self._move_to_completed(iid)
                            auto_done=True
                            self._run_outcomes["auto_approved"]=(
                                self._run_outcomes.get("auto_approved",0)+1)
                        else:
                            r["status"]=note+" • Auto-confirm blocked: "+block_reason
                    elif not normalized_candidate:
                        r["status"]=(
                            "Blank PDF — retained in root for manual review"
                            if note.startswith("Blank PDF") else
                            "No valid 08- case number in the upper-right quarter — checking before/after fallback")
                    elif r["scan_ambiguous"]:
                        r["status"]=note+" — conflicting case-field reads; manual review"
                    elif (labeled or positioned_header) and not localized_complete_08:
                        r["status"]=("No independently located full 08- OCR box — MANUAL REVIEW; "
                                      "model-only or partial text is not safe to approve")
                    elif labeled or positioned_header:
                        r["status"]=(f"{score:.1f}% case-field OCR — "
                            + (f"below fixed {self._get_auto_threshold():g}% threshold"
                               if not strong else "full localized 08- field not independently verified")
                            + "; manual review")
                    elif r["scan_source"]=="fallback":
                        r["status"]=(f"{float(score or 0):.1f}% Upper-right quarter fallback — "
                            "no verified full-number box or conflicting candidate; "
                            "review the field location before approval")
                    # A qualifying fallback with one localized case can complete;
                    # unlocated guesses and conflicting year-matched cases cannot.
                    self._diag_event("row_final",file=Path(r["path"]).name,
                         status=r.get("status"),candidate=normalized_candidate,
                         score=float(score or 0),auto_approved=auto_done,
                         source=r.get("scan_source"),ambiguous=r.get("scan_ambiguous"))
                    self._insert_scan_row(iid, r)
                    self._scan_progress_update(idx,"SCANNING")
                    self.status.config(text=f"Scanned {idx} of {len(self.scan_items)} PDF(s): {Path(r['path']).name}")
                    self._incremental_sequence_check(iid)
                    self._refresh_review_controls()
                    self._update_completed_count()
                    self._release_page_pixels(r)
                    self._save_state()
                elif kind == "export_result":
                    _, iid, source_name, destination, error = msg
                    self._apply_export_result(iid, source_name, destination, error)
                elif kind == "export_done":
                    self.export_busy = False
                    self._export_in_flight_iids.clear()
                    if not self.busy:
                        self._scan_progress_update(len(self.scan_items),"SENDING FINISHED")
                    self._rebuild_scan_list()
                    # A send changes the set of independent anchors. Reapply
                    # the rule immediately to remaining unreviewed root PDFs.
                    self._last_sequence_signature=None
                    try:
                        if not getattr(self,"_stop_all_requested",False):
                            self._apply_sequence_consensus()
                    except Exception as exc:
                        self._log("Could not recheck remaining review files after SEND: %s",exc)
                        self.status.config(text="SEND FINISHED, BUT BEFORE/AFTER RECHECK FAILED; REVIEW REMAINING PDFS.")
                    self._update_completed_count()
                    self._refresh_scan_button()
                    self._export_reschedule = False
                    self._schedule_auto_export()
                elif kind == "engine_status":
                    _, message = msg
                    self._loader_finish("ocr")
                    self.model_status_label.config(text=message)
                elif kind == "engine_warning":
                    _, message = msg
                    self.model_status_label.config(text=message)
                    self._log(message)
                elif kind == "model_status":
                    _, message = msg
                    self.model_status_label.config(text=message)
                elif kind == "training_progress":
                    _, percent, stage = msg
                    self._update_training_progress(percent, stage)
                    if self._learning_auto_run:
                        self._startup_notice(f"Learning update {int(percent)}% — {stage}")
                elif kind == "learning_stopped":
                    _, message = msg
                    self.trocr_training = False
                    self.learning_pause_event.set()
                    self.learning_stop_event.clear()
                    # Keep the live OCR model stable during and after training.
                    # A validated new checkpoint activates on the next start.
                    self._training_model_ready_on_restart=True
                    self._set_background_learning_indicator(False)
                    self._load_learning_data()
                    self.status.config(text=message)
                    self._log(message)
                    self._refresh_scan_button()
                    self._refresh_learning_button_state()
                    self._update_training_progress(100, message, finished=True)
                    self._learning_auto_run = False
                    # Flush any approved transfers deferred during training.
                    self._schedule_auto_export()
                    if not self._startup_ready:
                        self._begin_model_preload()
                elif kind == "learning_status":
                    _, message = msg
                    self.trocr_training = False
                    self.learning_pause_event.set()
                    self.learning_stop_event.clear()
                    # The active model can be reloaded from its validated disk checkpoint.
                    # Keep the live OCR model stable during and after training.
                    # A validated new checkpoint activates on the next start.
                    self._training_model_ready_on_restart=True
                    self._set_background_learning_indicator(False)
                    self._load_learning_data()
                    self.status.config(text=message)
                    self._log(message)
                    self._refresh_scan_button()
                    self._refresh_learning_button_state()
                    self._update_training_progress(100, message, finished=True)
                    self._learning_auto_run = False
                    # Flush any approved transfers deferred during training.
                    self._schedule_auto_export()
                    if not self._startup_ready:
                        self._begin_model_preload()
                elif kind == "fatal":
                    self._scan_clock_final_elapsed=self._scan_clock_actual()
                    self._scan_clock_start=None
                    self._scan_clock_pause_start=None
                    self._update_run_timers()
                    self._loader_finish("ocr")
                    self._loader_finish("model")
                    self.busy = False
                    self._refresh_scan_button()
                    self.pause_btn.config(state="disabled", text="PAUSE")
                    self.restart_btn.config(state="normal", text="STOP")
                    self._refresh_review_controls()
                    self._refresh_learning_button_state()
                    self._update_completed_count()
                    self._active_scan_iid=None
                    self._rescan_force_iids.clear()
                    self._audit_event("SCAN_FATAL",error=str(msg[1]))
                    self._log("Fatal scan error: %s", msg[1])
                    messagebox.showerror("OCR initialization failed", msg[1])
                elif kind == "update_check_result":
                    self.update_busy = False
                    self.update_btn.config(state="normal", text="UPDATE")
                    latest_version, release, error = msg[1], msg[2], msg[3]
                    if self._startup_auto_update:
                        if error:
                            self._startup_software_complete(error)
                        elif not release:
                            self._startup_software_complete()
                        else:
                            self.update_release = release
                            self._startup_notice(
                                f"Version {latest_version} found. Downloading and validating; the app will restart automatically…")
                            self._download_update(release)
                    elif error:
                        messagebox.showwarning("Update check", error)
                    elif not release:
                        messagebox.showinfo("Update check", f"M.F. Case Number OCR is up to date (V{self.app_version}).")
                    else:
                        self.update_release = release
                        if messagebox.askyesno(
                            "Update available",
                            f"Version {latest_version} is available. You are running V{self.app_version}.\n\n"
                            "Download and install the update now? The program will restart when installation finishes."):
                            self._download_update(release)
                elif kind == "update_download_ready":
                    self.update_busy = False
                    self.update_btn.config(state="normal", text="UPDATE")
                    archive, version = Path(msg[1]), msg[2]
                    if self._startup_auto_update:
                        self._startup_notice(
                            f"V{str(version).lstrip('v')} verified. The program will close and reopen automatically now.")
                        self.root.after(1200, lambda path=archive: self._launch_update_installer(path))
                    elif messagebox.askyesno(
                            "Install update",
                            f"Version {version} has downloaded and passed ZIP integrity checks.\n\n"
                            "The app will close, install the update, and reopen. Continue?"):
                        self._launch_update_installer(archive)
                    else:
                        try:
                            archive.unlink(missing_ok=True)
                        except Exception:
                            pass
                elif kind == "update_error":
                    self.update_busy = False
                    self.update_btn.config(state="normal", text="UPDATE")
                    if self._startup_auto_update:
                        self._startup_software_complete(msg[1])
                    else:
                        messagebox.showerror("Update failed", msg[1])
                elif kind == "stopped":
                    self._scan_clock_final_elapsed=self._scan_clock_actual()
                    self._scan_clock_start=None
                    self._scan_clock_pause_start=None
                    self._update_run_timers()
                    self._loader_finish("ocr")
                    self._loader_finish("model")
                    self.busy = False
                    self.paused = False
                    self.stop_event.clear()
                    self.pause_event.set()
                    self._refresh_scan_button()
                    self.pause_btn.config(state="disabled", text="PAUSE")
                    self.restart_btn.config(state="normal", text="STOP")
                    self._sequence_review_pending=False
                    try:
                        self._apply_sequence_consensus()
                    except Exception as exc:
                        self._sequence_review_pending=True
                        self._log("Stop sequence save failed: %s",exc)
                    self._refresh_review_controls()
                    self._refresh_learning_button_state()
                    self._update_completed_count()
                    self.status.config(text="SCAN STOPPED. COMPLETED FILES MAY NOW BE SENT. REMAINING FILES STAY IN ROOT.")
                    self._active_scan_iid=None
                    self._rescan_force_iids.clear()
                    self._save_state()
                    self._audit_event("SCAN_STOPPED",processed=msg[1])
                elif kind == "diagnostic_token_needed":
                    self._configure_github_logs(replacement=True,retry_log=Path(msg[1]))
                elif kind == "diagnostic_upload_status":
                    self._log("%s",msg[1])
                    if not self.busy:self.status.config(text=msg[1])
                elif kind == "done":
                    self._scan_clock_final_elapsed=self._scan_clock_actual()
                    self._scan_clock_start=None
                    self._scan_clock_pause_start=None
                    self._scan_clock_done=len(getattr(self,'scan_items',[]) or [])
                    self._update_run_timers()
                    self._loader_finish("ocr")
                    self._loader_finish("model")
                    self.busy = False
                    self._finish_scan_sequence_review()
                    self.paused = False
                    self.pause_event.set()
                    self._refresh_scan_button()
                    self.pause_btn.config(state="disabled", text="PAUSE")
                    self.restart_btn.config(state="normal")
                    self._refresh_review_controls()
                    self._refresh_learning_button_state()
                    self._update_completed_count()
                    self.status.config(
                        text=("Scan complete. AUTO SEND will transfer eligible completed PDFs "
                              "after before/after checks." if self.auto_send_var.get()
                              else "Scan complete. Approved PDFs remain available for review and SEND FILES."))
                    self._refresh_learning_button_state()
                    self._save_state()
                    self._diag_event("run_end",summary=dict(self._run_outcomes))
                    self._active_scan_iid=None
                    self._rescan_force_iids.clear()
                    self._audit_event("SCAN_FINISHED",processed=len(self.scan_items))
                    if bool(self.auto_send_var.get()):
                        self.root.after_idle(lambda:self.export(automatic=True))
                    self._schedule_post_scan_learning()
                    threading.Thread(target=self._diag_sync_shared,args=(self._diag_file,),daemon=True).start()
                    self._log("Scan-run summary: %s", json.dumps(self._run_outcomes, sort_keys=True))
                    self._schedule_auto_export()
        except queue.Empty:
            pass
        except Exception:
            logging.exception("UI queue event failed; continuing to process worker completion")
        finally:
            self.root.after(150, self._poll)
        self._update_scan_file_count()

    def _review_ready_ids(self):
        ready = []
        for iid, record in self.records.items():
            if record.get("status") in ("Not scanned", "Scanning"):
                continue
            if record.get("status") == "Moved to BLANKS":
                continue
            record_path = record.get("path")
            if not record_path or not Path(record_path).is_file():
                continue
            ready.append(iid)
        return ready

    def _refresh_review_controls(self):
        if not hasattr(self,"review_btn") or not hasattr(self,"tree"):
            return
        ready=set(self._review_ready_ids())
        enabled=not self._training_blocks_work() and any(
            iid in ready and self.tree.exists(iid) and
            (not self.folder or Path(self.records[iid]["path"]).parent==Path(self.folder))
            for iid in self.tree.selection())
        state="normal" if enabled else "disabled"
        self.review_btn.config(state=state)

    def on_double_click(self, event):
        if self._training_blocks_work():
            return
        iid = self.tree.identify_row(event.y)
        if iid:
            if self.tree.identify_column(event.x) == "#1":
                return "break"
            if iid not in self._review_ready_ids():
                return
            selected = [item for item in self.tree.selection()
                        if item in self._review_ready_ids()]
            if iid not in selected:
                selected = [iid]
            self._open_review_queue(selected, selected.index(iid))

    def _on_completed_click(self, event):
        """Checkbox clicks affect export selection, not the review highlight."""
        if (self.completed_tree.identify_region(event.x, event.y) == "cell"
                and self.completed_tree.identify_column(event.x) == "#7"):
            iid = self.completed_tree.identify_row(event.y)
            if iid in self.records and self.completed_tree.exists(iid):
                if iid in self.completed_checked_iids:
                    self.completed_checked_iids.remove(iid)
                else:
                    self.completed_checked_iids.add(iid)
                self._update_completed_checkbox(iid)
                self._update_completed_count()
            return "break"

    def _update_completed_checkbox(self, iid):
        if not self.completed_tree.exists(iid):
            return
        self.completed_tree.set(iid, "select", "☑" if iid in self.completed_checked_iids else "☐")

    def _refresh_completed_checkbox_heading(self):
        all_rows = set(self.completed_tree.get_children(""))
        self.completed_checked_iids.intersection_update(all_rows)
        all_checked = bool(all_rows) and all_rows <= self.completed_checked_iids
        self.completed_tree.heading("select", text="☑ SELECT ALL" if all_checked else "☐ SELECT ALL")

    def _toggle_completed_select_all(self):
        rows = set(self.completed_tree.get_children(""))
        if rows and rows <= self.completed_checked_iids:
            self.completed_checked_iids.difference_update(rows)
        else:
            self.completed_checked_iids.update(rows)
        for iid in rows:
            self._update_completed_checkbox(iid)
        self._update_completed_count()

    def on_completed_double_click(self, event):
        if self._training_blocks_work():
            return
        iid = self.completed_tree.identify_row(event.y)
        if not iid or iid not in self.records:
            return
        column = self.completed_tree.identify_column(event.x)
        if column == "#0":
            self._show_completed_evidence(iid)
        elif column == "#2":
            self._start_completed_edit(iid)
        elif column == "#7":
            return "break"

    def _close_completed_edit(self, iid):
        widgets = self.completed_edit_widgets.pop(iid, None)
        if widgets:
            for widget in widgets:
                try:
                    widget.destroy()
                except tk.TclError:
                    pass

    def _remember_completed_draft(self, iid, entry):
        self.completed_edit_drafts[iid] = entry.get()

    def _start_completed_edit(self, iid):
        """Focus the permanently visible, initially blank CHANGE box."""
        if iid not in self.completed_edit_widgets:
            self._position_completed_unverify_buttons()
        widgets = self.completed_edit_widgets.get(iid)
        if widgets:
            widgets[0].focus_set()

    def _cancel_completed_case_edit(self, iid):
        """Discard the draft, leaving the existing confirmed case unchanged."""
        self.completed_edit_drafts.pop(iid, None)
        widgets = self.completed_edit_widgets.get(iid)
        if widgets:
            widgets[0].delete(0, "end")

    def _save_learning_label_for_file(self, source_name, confirmed_number):
        """Keep the ground-truth label consistent after a filename correction."""
        if not CASE_RE.fullmatch(normalize_case_number(confirmed_number)):
            raise ValueError("Case number must begin with 08- before saving learning.")
        if not self.learning_dir:
            return
        changed = False
        for example in self.learning_data.get("examples", []):
            if example.get("source_pdf") == source_name and (
                    _case_identity(example.get("confirmed_case_number")) !=
                    _case_identity(confirmed_number)):
                example["training_quarantined"] = True
                example["training_quarantine_reason"] = "filename_changed_after_crop_created"
                changed = True
        for region in self.learning_data.get("field_regions", []):
            if region.get("source_pdf") == source_name:
                region["confirmed_case_number"] = confirmed_number
                changed = True
        if changed:
            self._persist_learning_data(strict_revision=True)
            self._refresh_learning_stats()

    def _save_completed_case_edit(self, iid, entry):
        record = self.records.get(iid)
        if not record or record.get("exported"):
            self._close_completed_edit(iid)
            return
        if iid in self._export_in_flight_iids:
            self.status.config(text="THAT FILE IS BEING RENAMED; EDITS ARE TEMPORARILY UNAVAILABLE.")
            return
        value = entry.get().strip()
        value = normalize_case_number(value)
        if not CASE_RE.fullmatch(value):
            messagebox.showerror(
                "Invalid case number",
                "Only 08- followed by 1–7 case digits is valid. No year will be added or corrected automatically.",
                parent=self.root)
            return

        record.pop("case_draft",None)
        old_case = str(record.get("confirmed", ""))
        if (value == old_case and record.get("manual_approved") and not record.get("auto_labeled")
                and not record.get("learning_pending")):
            record.pop("case_draft", None)
            record["sequence_excluded"] = False
            record["sequence_conflict"] = False
            record["scan_ambiguous"] = False
            record["sequence_inferred"] = False
            if not self._save_state():
                self.status.config(text="CORRECTION IS IN MEMORY BUT COULD NOT BE SAVED; CHECK FOLDER ACCESS AND RETRY ENTER.")
                return False
            self._cancel_completed_case_edit(iid)
            self._recheck_after_decision()
            return True
        old_cache = self._completed_thumbnail_path(record)
        if old_cache and old_cache.exists():
            try:
                old_cache.unlink()
            except OSError:
                logging.exception("Could not remove old thumbnail cache after case-number edit")
        self._audit_event("CASE_CORRECTED",original_name=record.get("original_name") or record["path"].name,
                          previous=old_case,confirmed=value)
        record["confirmed"] = value
        record["scan_ambiguous"] = False
        # Editing is an explicit human decision, not another OCR inference.
        record["manual_approved"] = True
        record["auto_labeled"] = False
        record["sequence_inferred"] = False
        record["sequence_previous_prefill"] = False
        record["sequence_conflict"] = False
        record["sequence_excluded"] = False
        record["sequence_anchors"] = []
        try:
            if record.get("snippet") is None:
                self._ensure_preview(record)
            crop_saved=self._save_learning_example(record,value)  # Append only.
            record["learning_pending"]=crop_saved is False
        except Exception as exc:
            record["learning_pending"]=True
            self._log("Corrected case retained; learning update pending: %s",exc)
        record["status"]=f"Manual case {value} saved — awaiting SEND FILES"
        self._insert_scan_row(iid,record)
        self.completed_edit_drafts.pop(iid, None)
        self._close_completed_edit(iid)
        self._insert_completed_row(iid, record)
        record["completed_iid"]=iid
        saved=self._save_state()
        self._update_completed_count()
        if not saved:
            self.status.config(text="CORRECTION IS IN MEMORY BUT COULD NOT BE SAVED; CHECK FOLDER ACCESS AND RETRY ENTER.")
            return False
        message = (f"Case number saved as {value} for {record['path'].name}. "
                   "It remains in root until SEND FILES.")
        if record.get("learning_pending"):
            message += " Learning crop not saved; draw a usable case box and approve again."
        elif not getattr(self,"_stop_all_requested",False):
            self._schedule_post_scan_learning()
        self.status.config(text=message)
        self.completed_send_status.config(text=message)
        self._recheck_after_decision()
        return True
        # A human correction must remain in COMPLETED until the user presses
        # SEND FILES; it must not race an automatic export callback.

    def _completed_xscroll_command(self, *args):
        self.completed_tree.xview(*args)
        self.root.after_idle(self._position_completed_unverify_buttons)

    def _completed_xscrollbar_update(self, first, last):
        self.completed_xscrollbar.set(first, last)
        self.root.after_idle(self._position_completed_unverify_buttons)

    def _completed_scrollbar_command(self, *args):
        self.completed_tree.yview(*args)
        self.root.after_idle(self._position_completed_unverify_buttons)

    def _completed_scrollbar_update(self, first, last):
        self.completed_scrollbar.set(first, last)
        self.root.after_idle(self._position_completed_unverify_buttons)

    def _scroll_completed(self, amount):
        self.completed_tree.yview_scroll(amount, "units")
        self.root.after_idle(self._position_completed_unverify_buttons)

    def _position_completed_unverify_buttons(self):
        """Virtualize the CHANGE widgets and UNVERIFY button for visible rows."""
        if not hasattr(self, "completed_tree"):
            return
        visible = set()
        for iid in self.completed_tree.get_children(""):
            record = self.records.get(iid)
            if not record or not record.get("confirmed"):
                continue
            edit_cell = self.completed_tree.bbox(iid, "#2")
            unverify_cell = self.completed_tree.bbox(iid, "#6")
            if not edit_cell and not unverify_cell:
                continue
            visible.add(iid)
            if edit_cell:
                x, y, w, h = edit_cell
                widgets = self.completed_edit_widgets.get(iid)
                if widgets is None or not widgets[0].winfo_exists():
                    entry = ttk.Entry(self.completed_tree, font=("Segoe UI", 10))
                    # Never prefill with the current number. Drafts survive scrolling.
                    draft = self.completed_edit_drafts.get(iid, "")
                    if draft:
                        entry.insert(0, draft)
                    save = ttk.Button(self.completed_tree, text="SAVE",
                                      command=lambda key=iid, e=entry: self._save_completed_case_edit(key, e))
                    cancel = ttk.Button(self.completed_tree, text="CANCEL",
                                        command=lambda key=iid: self._cancel_completed_case_edit(key))
                    entry.bind("<KeyRelease>", lambda _e, key=iid, e=entry:
                               self._remember_completed_draft(key, e))
                    entry.bind("<Return>", lambda _e, key=iid, e=entry:
                               (self._save_completed_case_edit(key, e), "break")[1])
                    entry.bind("<Escape>", lambda _e, key=iid:
                               (self._cancel_completed_case_edit(key), "break")[1])
                    widgets = (entry, save, cancel)
                    self.completed_edit_widgets[iid] = widgets
                entry, save, cancel = widgets
                disabled = "disabled" if record.get("exported") else "normal"
                entry.configure(state=disabled)
                save.configure(state=disabled)
                cancel.configure(state=disabled)
                entry_h = max(23, min(31, h // 3))
                buttons_h = max(23, min(30, h // 3))
                top = y + max(2, (h - entry_h - buttons_h - 5) // 2)
                entry.place(x=x + 5, y=top, width=max(90, w - 10), height=entry_h)
                button_w = (w - 15) // 2
                save.place(x=x + 5, y=top + entry_h + 5,
                           width=button_w, height=buttons_h)
                cancel.place(x=x + 10 + button_w, y=top + entry_h + 5,
                             width=max(46, w - 15 - button_w), height=buttons_h)
            if unverify_cell:
                x, y, w, h = unverify_cell
                btn = self.completed_unverify_buttons.get(iid)
                if btn is None or not btn.winfo_exists():
                    btn = tk.Button(self.completed_tree, text="UNVERIFY",
                                    fg="#000000", bg="#ee5757", activeforeground="#000000",
                                    activebackground="#ff7878", disabledforeground="#555555",
                                    relief="raised", bd=1, cursor="hand2",
                                    font=("Segoe UI", 9, "bold"),
                                    command=lambda key=iid: self._show_unverify_dialog(key))
                    self.completed_unverify_buttons[iid] = btn
                btn.configure(state="disabled" if record.get("exported") else "normal")
                button_w = max(70, min(88, w - 14))
                button_h = max(26, min(34, h - 12))
                btn.place(x=x + (w - button_w) // 2,
                          y=y + (h - button_h) // 2,
                          width=button_w, height=button_h)
        for iid in list(self.completed_edit_widgets):
            if iid not in visible:
                widgets = self.completed_edit_widgets[iid]
                self.completed_edit_drafts[iid] = widgets[0].get()
                self._close_completed_edit(iid)
        for iid, btn in list(self.completed_unverify_buttons.items()):
            if iid not in visible:
                try:
                    btn.place_forget()
                except tk.TclError:
                    pass
            if iid not in self.records or not self.completed_tree.exists(iid):
                btn.destroy()
                self.completed_unverify_buttons.pop(iid, None)

    def _completed_mousewheel(self, event):
        direction = -1 if event.delta > 0 else 1
        self.completed_tree.yview_scroll(direction * max(1, abs(int(event.delta / 120))), "units")
        self.root.after_idle(self._position_completed_unverify_buttons)
        return "break"

    def _completed_thumbnail_path(self, record):
        if not self.folder:
            return None
        case = str(record.get("confirmed") or "unknown")
        name = record.get("path").name if record.get("path") else "unknown.pdf"
        token = hashlib.sha1(f"{name}|{case}".encode("utf-8")).hexdigest()[:18]
        cache_dir = self.folder / ".case_ocr_thumbnails"
        cache_dir.mkdir(exist_ok=True)
        return cache_dir / f"{token}.png"

    def _make_completed_thumbnail(self, record):
        """Build a small, cached image of the exact OCR field used for the filename."""
        from PIL import Image, ImageDraw, ImageFont, ImageTk
        cache_path = self._completed_thumbnail_path(record)
        if cache_path and cache_path.exists():
            try:
                cached = Image.open(cache_path).convert("RGB")
                cached.thumbnail((190, 58), Image.Resampling.LANCZOS)
                return ImageTk.PhotoImage(cached)
            except Exception:
                logging.exception("Could not load cached case-number thumbnail")

        self._ensure_preview(record)
        snippet = record.get("snippet")
        crop = None
        if snippet and snippet[1] is not None:
            page = Image.fromarray(snippet[1]).convert("RGB")
            candidates = self._valid_ocr_box_candidates(record)
            confirmed = str(record.get("confirmed") or "").strip()
            chosen = next((c for c in candidates
                           if str(c.get("number") or "").strip() == confirmed and c.get("rect")), None)
            rect = chosen.get("rect") if chosen else None
            # Prefer the reviewer's green box when the final confirmed value was
            # manually corrected and has no matching red OCR candidate.
            if not rect:
                rect = record.get("manual_crop_rect") or snippet[3]
            # Last resort: any recognized number box, then the corner OCR region.
            if not rect:
                fallback = next((c for c in candidates if c.get("rect")), None)
                rect = fallback.get("rect") if fallback else None
            if rect:
                x0, y0, x1, y1 = [int(round(float(v))) for v in rect]
                pad_x = max(18, int((x1 - x0) * 0.28))
                pad_y = max(12, int((y1 - y0) * 0.45))
                x0 = max(0, x0 - pad_x); y0 = max(0, y0 - pad_y)
                x1 = min(page.width, x1 + pad_x); y1 = min(page.height, y1 + pad_y)
                if x1 > x0 and y1 > y0:
                    crop = page.crop((x0, y0, x1, y1))
            if crop is None:
                # Fallback: display the complete upper-right OCR search zone rather than
                # showing an unrelated full-page region or a blank thumbnail.
                w = min(page.width, int(5 * 72 * 2.2))
                h = min(page.height, int(5 * 72 * 2.2))
                crop = page.crop((page.width - w, 0, page.width, h))
        if crop is None:
            crop = Image.new("RGB", (190, 58), "white")
            draw = ImageDraw.Draw(crop)
            draw.text((6, 20), str(record.get("confirmed") or "Preview unavailable"), fill="black")
        crop.thumbnail((190, 58), Image.Resampling.LANCZOS)
        tile = Image.new("RGB", (198, 62), "white")
        x = (tile.width - crop.width) // 2
        y = (tile.height - crop.height) // 2
        tile.paste(crop, (x, y))
        ImageDraw.Draw(tile).rectangle((0, 0, tile.width - 1, tile.height - 1), outline="#999999")
        if cache_path:
            try:
                tile.save(cache_path)
            except Exception:
                logging.exception("Could not save case-number thumbnail cache")
        return ImageTk.PhotoImage(tile)

    def _completed_queue_status(self, record):
        score = float(record.get("score", 0.0) or 0.0)
        match = f"{score:.1f}%" if score > 0 else "—"
        approval = "AUTOMATCH" if record.get("auto_labeled") else "APPROVED"
        return f"{match} | {approval}"

    def _display_original_parent(self, record):
        """Show the rightmost folder segments; never the full network share."""
        path = str(record.get("original_path") or record.get("path") or "")
        pieces = [piece for piece in re.split(r"[\\/]", path) if piece]
        # The file name is already displayed under ORIGINAL NAME.
        parts = pieces[:-1]
        if not parts:
            return "—"
        tail = "\\".join(parts[-2:])
        label = ("…\\" if len(parts) > 2 else "") + tail
        try:
            from tkinter import font as tkfont
            ft = tkfont.nametofont("TkDefaultFont")
            max_px = max(64, int(self.completed_tree.column("original_path", "width")) - 18)
            while label and ft.measure(label) > max_px:
                # Elide the left, retaining the closest folder name and its end.
                label = "…" + label[2:] if label.startswith("…") else "…" + label[1:]
        except (tk.TclError, AttributeError):
            pass
        return label or "…"

    def _schedule_completed_layout(self):
        if getattr(self, "_completed_layout_after", None) is None:
            self._completed_layout_after = self.root.after(90, self._autofit_completed_columns)

    def _autofit_completed_columns(self):
        """Size the columns to their content, share any spare width, center all cells."""
        self._completed_layout_after = None
        if not hasattr(self, "completed_tree") or not self.completed_tree.winfo_exists():
            return
        from tkinter import font as tkfont
        face = tkfont.nametofont("TkDefaultFont")
        columns = ("#0", "case", "edit_case", "file", "original_path", "status", "unverify", "select")
        mins = {"#0": 255, "case": 105, "edit_case": 195, "file": 155,
                "original_path": 135, "status": 150, "unverify": 88, "select": 103}
        maxs = {"#0": 275, "case": 155, "edit_case": 245, "file": 390,
                "original_path": 330, "status": 225, "unverify": 100, "select": 125}
        samples = {key: [] for key in columns}
        for iid in self.completed_tree.get_children(""):
            record = self.records.get(iid)
            if not record:
                continue
            samples["case"].append(str(record.get("confirmed", "")))
            samples["file"].append(Path(record["path"]).name)
            samples["status"].append(self._completed_queue_status(record))
        base = {}
        for col in columns:
            desired = max((face.measure(text) + 28 for text in samples[col]), default=0)
            desired = max(desired, mins[col])
            base[col] = min(maxs[col], desired)
        base["#0"] = 258  # Preserve the enlarged IMAGE thumbnail.
        viewport = max(750, self.completed_tree.winfo_width() - 20)
        spare = max(0, viewport - sum(base.values()))
        for col in ("file", "original_path", "status", "edit_case", "case", "select"):
            add = min(spare, maxs[col] - base[col])
            base[col] += add
            spare -= add
        changed = False
        for col in columns:
            if self.completed_tree.column(col, "width") != base[col]:
                self.completed_tree.column(col, width=base[col], anchor="center")
                changed = True
        if changed:
            for iid in self.completed_tree.get_children(""):
                rec = self.records.get(iid)
                if rec:
                    self.completed_tree.set(iid, "original_path", self._display_original_parent(rec))
        self.root.after_idle(self._position_completed_unverify_buttons)

    def _completed_row_values(self, iid, record):
        return (record.get("confirmed", ""), "", record["path"].name,
                self._display_original_parent(record),
                self._completed_queue_status(record), "",
                "☑" if iid in self.completed_checked_iids else "☐")

    def _insert_completed_row(self, iid, record):
        # This legacy table is hidden in the unified UI. Export uses records,
        # so do not render thousands of invisible completed-page thumbnails.
        if hasattr(self,"show_all_files_var"):
            return
        if self.completed_tree.exists(iid):
            stale = self._completed_thumbnail_path(record)
            if stale and stale.exists():
                try:
                    stale.unlink()
                except Exception:
                    logging.exception("Could not refresh case-number thumbnail cache")
        photo = self._make_completed_thumbnail(record)
        self.completed_photo_refs[iid] = photo
        vals = self._completed_row_values(iid, record)
        if self.completed_tree.exists(iid):
            self.completed_tree.item(iid, image=photo, text="", values=vals)
        else:
            self.completed_tree.insert("", "end", iid=iid, image=photo, text="", values=vals)
        self.root.after_idle(self._position_completed_unverify_buttons)
        self._schedule_completed_layout()
        if self._table_sort_state.get("completed"):
            self._apply_table_sort("completed")
        self._refresh_completed_checkbox_heading()

    def _show_completed_evidence(self, iid):
        """Open the original PDF page with the selected field in green and other OCR detections in red."""
        record = self.records.get(iid)
        if not record:
            return
        self._ensure_preview(record)
        snippet = record.get("snippet")
        if not snippet or snippet[1] is None:
            messagebox.showwarning(
                "Preview unavailable",
                "The original PDF preview could not be rebuilt. Check that the source PDF is still in the selected root folder.",
                parent=self.root)
            return
        try:
            from PIL import Image, ImageTk, ImageDraw
            original = Image.fromarray(snippet[1]).convert("RGB")
            # If saved OCR preview pixels are blank, render the PDF page directly.
            try:
                import numpy as _np
                if float(_np.asarray(original).std()) < 1.0:
                    with pymupdf.open(record["path"]) as _doc:
                        _page_num = max(0, min(int(snippet[0] or 0), len(_doc) - 1))
                        _pix = _doc[_page_num].get_pixmap(
                            matrix=pymupdf.Matrix(2.2, 2.2), alpha=False)
                        original = Image.frombytes("RGB", [_pix.width, _pix.height], _pix.samples)
                        record["snippet"] = (
                            _page_num, _np.asarray(original).copy(),
                            snippet[2], snippet[3], snippet[4] if len(snippet) > 4 else [])
                        snippet = record["snippet"]
            except Exception:
                logging.exception("Could not rebuild blank evidence preview from source PDF")
            candidates = self._valid_ocr_box_candidates(record)
            confirmed = str(record.get("confirmed") or "").strip()
            selected = next((c for c in candidates
                             if str(c.get("number") or "").strip() == confirmed and c.get("rect")), None)
            green_rect = (record.get("manual_crop_rect") or (selected.get("rect") if selected else None)) \
                if CASE_RE.fullmatch(normalize_case_number(confirmed)) else None

            win = tk.Toplevel(self.root)
            win.title(f"M.F. Case Evidence — {record['path'].name}")
            win.transient(self.root)
            try:
                win.state("zoomed")
            except tk.TclError:
                win.geometry("1400x900")
            head = ttk.Frame(win, padding=(10, 8))
            head.pack(fill="x")
            case_description = (
                f"Case number: {confirmed or 'Not set'} • SEQUENCE-INFERRED (not OCR)"
                if record.get("sequence_inferred") else
                f"Case number: {confirmed or 'Not set'}")
            ttk.Label(head, text=case_description,
                      font=("Segoe UI", 12, "bold")).pack(side="left")
            score_description = (
                "Sequential filename inference; no OCR confidence assigned"
                if record.get("sequence_inferred") else
                f"OCR detector score: {float(record.get('score') or 0):.1f}%"
                if record.get("score") else "OCR detector score: unavailable")
            ttk.Label(head, text=score_description).pack(side="right")
            ttk.Label(win, text=str(record["path"]), padding=(10, 0)).pack(anchor="w")
            ttk.Label(win, text="GREEN = FIELD USED FOR THE FILENAME / LEARNING CROP     RED = OTHER DETECTED CASE-NUMBER CANDIDATES",
                      foreground="#1b6e36", padding=(10, 5)).pack(anchor="w")
            panel = ttk.Frame(win, padding=(8, 0, 8, 8))
            panel.pack(fill="both", expand=True)
            panel.rowconfigure(0, weight=1); panel.columnconfigure(0, weight=1)
            canvas = tk.Canvas(panel, bg="#333333", highlightthickness=0)
            sx = ttk.Scrollbar(panel, orient="horizontal", command=canvas.xview)
            sy = ttk.Scrollbar(panel, orient="vertical", command=canvas.yview)
            canvas.configure(xscrollcommand=sx.set, yscrollcommand=sy.set)
            canvas.grid(row=0, column=0, sticky="nsew")
            sy.grid(row=0, column=1, sticky="ns")
            sx.grid(row=1, column=0, sticky="ew")
            photo_ref = {"photo": None}
            state = {"scale": 1.0}

            def render():
                avail_w = max(300, canvas.winfo_width() - 20)
                avail_h = max(300, canvas.winfo_height() - 20)
                # Keep full page readable and scrollable; fit to width, capped at 1:1.
                scale = min(avail_w / original.width, 1.0)
                if avail_h / original.height < scale:
                    scale = min(scale, avail_h / original.height)
                state["scale"] = max(0.15, scale)
                w = max(1, int(original.width * state["scale"]))
                h = max(1, int(original.height * state["scale"]))
                shown = original.resize((w, h), Image.Resampling.LANCZOS)
                draw = ImageDraw.Draw(shown)
                if green_rect:
                    x0, y0, x1, y1 = [int(round(float(v) * state["scale"])) for v in green_rect]
                    draw.rectangle((x0, y0, x1, y1), outline="#00e85a", width=max(3, int(3*state["scale"])))
                    draw.text((x0, max(0, y0-18)), confirmed or "SELECTED", fill="#00e85a", stroke_width=2, stroke_fill="#111111")
                chosen_used = False
                for candidate in candidates:
                    rect = candidate.get("rect")
                    if not rect:
                        continue
                    if (not chosen_used and selected is candidate):
                        chosen_used = True
                        continue
                    # Do not draw a red alternative over the selected green location.
                    if green_rect and tuple(map(float, rect)) == tuple(map(float, green_rect)):
                        continue
                    x0, y0, x1, y1 = [int(round(float(v) * state["scale"])) for v in rect]
                    draw.rectangle((x0, y0, x1, y1), outline="#ff3030", width=max(2, int(2*state["scale"])))
                    label = str(candidate.get("number") or "candidate")
                    draw.text((x0, max(0, y0-17)), label, fill="#ff3030", stroke_width=2, stroke_fill="#111111")
                photo = ImageTk.PhotoImage(shown)
                photo_ref["photo"] = photo
                canvas.delete("all")
                canvas.create_image(0, 0, image=photo, anchor="nw")
                canvas.configure(scrollregion=(0, 0, w, h))
                canvas.xview_moveto(0); canvas.yview_moveto(0)

            def render_when_ready(_event=None):
                if not win.winfo_exists():
                    return
                if canvas.winfo_width() <= 2 or canvas.winfo_height() <= 2:
                    win.after(100, render_when_ready)
                    return
                render()

            canvas.bind("<Configure>", render_when_ready)
            win.after(250, render_when_ready)
            win.bind("<Escape>", lambda _e: win.destroy())
        except Exception as exc:
            logging.exception("Could not open completed case evidence viewer")
            messagebox.showerror("Evidence preview failed", str(exc), parent=self.root)

    def _remove_learning_data_for_file(self, source_name):
        """Remove this file's saved learning examples and spatial hints."""
        if not self.learning_dir:
            return
        kept = []
        removed_images = []
        for example in self.learning_data.get("examples", []):
            if example.get("source_pdf") == source_name:
                if example.get("image"):
                    removed_images.append(example.get("image"))
            else:
                kept.append(example)
        self.learning_data["examples"] = kept
        self.learning_data["field_regions"] = [
            region for region in self.learning_data.get("field_regions", [])
            if region.get("source_pdf") != source_name]
        for rel in removed_images:
            try:
                target = (self.learning_dir / rel).resolve()
                examples_root = (self.learning_dir / "examples_v3").resolve()
                if target.is_relative_to(examples_root) and target.is_file():
                    target.unlink()
            except Exception:
                logging.exception("Could not remove unverified learning crop %s", rel)
        try:
            self._persist_learning_data(strict_revision=True)
        except Exception:
            logging.exception("Could not persist removal of unverified learning examples")
        self._refresh_learning_stats()

    def _selected_unverify_ids(self):
        rows = self.completed_tree.get_children("")
        selected = self.completed_checked_iids or set(self.completed_tree.selection())
        return [
            iid for iid in rows
            if iid in selected and iid not in self._export_in_flight_iids
            and self.records.get(iid, {}).get("confirmed")
            and not self.records[iid].get("exported")
        ]

    def _unverify_completed_selected(self):
        selected = self._selected_unverify_ids()
        if not selected or self.export_busy or self._training_blocks_work():
            return
        if not messagebox.askyesno(
            "Unverify selected files",
            f"Return {len(selected)} selected file(s) to the Scan list? "
            "Their confirmed case numbers, saved learning examples and location hints "
            "will be cleared.",
            parent=self.root,
        ):
            return
        for iid in selected:
            self._unverify_record(iid)
        self.completed_send_status.config(
            text=f"Unverified {len(selected)} file(s). Review them again from the Scan list.")

    def _show_unverify_dialog(self, iid):
        record = self.records.get(iid)
        if not record or record.get("exported"):
            return
        win = tk.Toplevel(self.root)
        win.title("UNVERIFY FILE")
        win.transient(self.root)
        win.resizable(False, False)
        win.grab_set()
        body = ttk.Frame(win, padding=18)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="UNVERIFY THIS FILE?", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Label(body, text=record["path"].name, wraplength=420).pack(anchor="w", pady=(8, 4))
        ttk.Label(body, text=(
            "This returns the PDF to the review list and clears its confirmed case number. "
            "Its saved learning example and location hint will also be removed. "
            "Previously trained model weights are not automatically rolled back."),
            wraplength=440).pack(anchor="w", pady=(2, 14))
        buttons = ttk.Frame(body)
        buttons.pack(fill="x")
        def no():
            win.destroy()
        def yes():
            self._unverify_record(iid)
            win.destroy()
        ttk.Button(buttons, text="UNVERIFY — YES", command=yes).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="NO", command=no).pack(side="right")
        win.bind("<Escape>", lambda _e: no())
        win.bind("<Return>", lambda _e: yes())
        win.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - win.winfo_width()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - win.winfo_height()) // 2
        win.geometry(f"+{max(0,x)}+{max(0,y)}")

    def _unverify_record(self, iid):
        record = self.records.get(iid)
        if not record or record.get("exported"):
            return
        if iid in self._export_in_flight_iids:
            self.status.config(text="THAT FILE IS BEING RENAMED; UNVERIFY IS TEMPORARILY UNAVAILABLE.")
            return
        old_thumbnail = self._completed_thumbnail_path(record)
        if old_thumbnail and old_thumbnail.exists():
            try:
                old_thumbnail.unlink()
            except Exception:
                logging.exception("Could not remove stale thumbnail for unverified file")
        if self.completed_tree.exists(iid):
            self.completed_tree.delete(iid)
        self.completed_photo_refs.pop(iid, None)
        self.completed_checked_iids.discard(iid)
        self.completed_edit_drafts.pop(iid, None)
        self._close_completed_edit(iid)
        old_button = self.completed_unverify_buttons.pop(iid, None)
        if old_button:
            old_button.destroy()
        record["confirmed"] = ""
        record["send_after_manual_approval"] = False
        record["manual_approved"] = False
        record["auto_labeled"] = False
        record["sequence_inferred"] = False
        record["sequence_previous_prefill"] = False
        record["sequence_conflict"] = False
        record["sequence_anchors"] = []
        record["sequence_suggestion"] = ""
        # Explicit UNVERIFY is a veto, not permission to silently infer again.
        record["sequence_excluded"] = True
        record["manual_crop_rect"] = None
        record["status"] = "Unverified — returned to review"
        record["completed_iid"] = None
        # Unverify changes approval only; preserve all historical learning data.
        self._insert_scan_row(iid, record)
        self._refresh_review_controls()
        self._update_completed_count()
        self._update_scan_file_count()
        self._save_state()
        self._recheck_after_decision()
        self.status.config(text=f"Unverified {record['path'].name}; returned to the review list.")

    def review_selected(self):
        if self._training_blocks_work():
            return
        ready=set(self._review_ready_ids())
        selected = [
            iid for iid in self.tree.selection()
            if iid in ready and not self.records[iid].get("exported")
            and (not self.folder or Path(self.records[iid]["path"]).parent==Path(self.folder))]
        if not selected:
            messagebox.showinfo(
                "Nothing ready to review",
                "Select a PDF whose scan has finished. You can review completed scans while the remaining PDFs continue processing.")
            return
        self._open_review_queue(selected, 0)

    def review_next(self):
        if self._training_blocks_work():
            return
        pending = [
            iid for iid in self._review_ready_ids()
            if not self.records[iid].get("confirmed")]
        if not pending:
            messagebox.showinfo(
                "No scanned files ready",
                "No completed OCR results are waiting for review yet. The scanner may still be processing the remaining PDFs.")
            return
        self._open_review_queue(pending, 0)

    def review_completed_selected(self):
        if self._training_blocks_work():
            return
        selected = list(self.completed_tree.selection())
        if not selected:
            messagebox.showinfo(
                "Select completed files",
                "Select one or more files in Review to inspect and approve them.")
            return
        self._open_review_queue(selected, 0)

    def _open_review_queue(self, iids, index=0):
        self._yield_background_learning("review")
        ready = set(self._review_ready_ids())
        self.review_queue = [
            iid for iid in iids
            if iid in self.records and
            (iid in ready or self.records[iid].get("confirmed"))
        ]
        if not self.review_queue:
            return
        self._show_review_item(index)

    def _move_to_completed(self, iid, batch=False):
        r = self.records[iid]
        if r.get("sequence_inferred"):
            queue_status = "Confirmed by matching neighbors, OCR score retained — ready to send"
        elif r.get("auto_labeled") and r.get("manual_approved"):
            source=r.get("scan_source")
            if source=="fallback":
                queue_status="Localized OCR approved — ready to send"
            else:
                queue_status=f"{float(r.get('score') or 0):.1f}% OCR approved — ready to send"
        elif r.get("manual_approved"):
            queue_status = "Approved — ready to send"
        elif r.get("auto_labeled"):
            queue_status = "Auto-detected — review required"
        else:
            queue_status = "Confirmed — ready to send"
        r["status"] = queue_status
        r["export_error"] = ""
        self._insert_completed_row(iid, r)
        r["completed_iid"] = iid
        # Do not hide approved PDFs: the unified list shows COMPLETED.
        self._insert_scan_row(iid,r)
        if not batch:
            self._update_completed_count()
            self._update_scan_file_count()
            self._save_state()
            # _schedule_auto_export only picks machine-approved scans. A human
            # approval stays visible in Completed for a final edit and SEND.
            self._schedule_auto_export()

    def _update_completed_count(self):
        approved = sum(
            1 for r in self.records.values()
            if r.get("confirmed") and r.get("manual_approved") and not r.get("exported"))
        needs_review = sum(
            1 for r in self.records.values()
            if r.get("confirmed") and r.get("auto_labeled")
            and not r.get("manual_approved") and not r.get("exported"))
        self._refresh_completed_checkbox_heading()
        selected_count = len(self.completed_checked_iids)
        self.completed_count.config(
            text=f"{approved} approved to send • {needs_review} auto-detected awaiting review"
                 f" • {selected_count} selected")
        can_export=(not self.busy and not self.export_busy and not self._training_blocks_work()
                    and not getattr(self,"_sequence_review_pending",False) and
                    any(self._eligible_for_kurt(iid,r) for iid,r in self.records.items()))
        self.export_btn.config(state="normal" if can_export else "disabled")
        if hasattr(self,"send_selected_btn"):
            self.send_selected_btn.config(state="normal" if can_export else "disabled")
        can_unverify = not self.export_busy and not self._training_blocks_work() and self._selected_unverify_ids()
        self.unverify_selected_btn.config(state="normal" if can_unverify else "disabled")
        pending_review = any(
            r.get("confirmed") and not r.get("manual_approved")
            and not r.get("exported") for r in self.records.values())
        self.review_completed_btn.config(
            state="normal" if pending_review and not self._training_blocks_work() else "disabled")

    def _ensure_preview(self, record):
        existing = record.get("snippet")
        if existing is not None and len(existing) > 1 and existing[1] is not None:
            try:
                sample = np.asarray(existing[1]) if np is not None else None
                # Older queue state can contain an empty/black placeholder image.
                # Re-render those from the original PDF instead of opening a blank viewer.
                if sample is not None and sample.size and float(sample.std()) >= 1.0:
                    return
            except Exception:
                pass
        record["snippet"] = None
        path = record.get("path")
        if not path or not path.is_file() or pymupdf is None or np is None:
            return
        try:
            meta = record.get("preview_meta", {}) or {}
            with pymupdf.open(path) as doc:
                if len(doc) == 0:
                    return
                page_num = max(0, min(int(meta.get("page_num", 0)), len(doc) - 1))
                pix = doc[page_num].get_pixmap(
                    matrix=pymupdf.Matrix(2.2, 2.2), alpha=False)
                image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                    pix.height, pix.width, pix.n).copy()
                rect = tuple(meta["rect"]) if meta.get("rect") else None
                label = meta.get("label", "Full page — preview rebuilt from source PDF")
                candidates = meta.get("candidates", [])
                record["snippet"] = (page_num, image, label, rect, candidates)
        except Exception:
            logging.exception("Could not rebuild review preview for %s", path.name)

    def _ocr_review_crop(self, crop):
        """Manual full-page crops use the same literal-prefix and call limits."""
        hits=self._automatic_handwritten_4x4(np.asarray(crop.convert("RGB")))
        identities={_case_identity(h["number"]) for h in hits}
        if len(identities)!=1:
            return ("",0.0,"","No unambiguous complete 08- reading; enter the case manually.")
        best=max(hits,key=lambda h:h["score"])
        return (best["number"],best["score"],"+".join(best["engines"]),"")

    def _show_review_item(self, index):
        if not self.review_queue:
            return
        index = max(0, min(index, len(self.review_queue)-1))
        self.review_index = index
        iid = self.review_queue[index]
        r = self.records[iid]
        self._ensure_preview(r)
        visual_candidates = self._valid_ocr_box_candidates(r)
        candidate_values = []
        for item in visual_candidates:
            number = item.get("number")
            if number and number not in candidate_values:
                candidate_values.append(number)
        if (CASE_RE.fullmatch(normalize_case_number(r.get("candidate")))
                and r.get("candidate") not in candidate_values):
            candidate_values.insert(0, r.get("candidate"))
        sequence_values, sequence_message = self._sequence_hint_for_iid(iid)
        # A two-sided exact agreement is a stronger clue than a truncated OCR
        # candidate. Put that value first in the selector, but keep all OCR
        # readings available as alternatives for reviewer inspection.
        sequence_consensus = (
            len(sequence_values) == 1
            and "both bounding anchors read" in sequence_message.lower()
        )
        if sequence_consensus:
            consensus_value = sequence_values[0]
            candidate_values = [consensus_value] + [
                value for value in candidate_values if value != consensus_value]
        else:
            for value in sequence_values:
                if value not in candidate_values:
                    candidate_values.append(value)

        initial_reading = self._initial_review_reading(
            r, candidate_values, sequence_values, sequence_message)
        if not CASE_RE.fullmatch(normalize_case_number(initial_reading)):
            initial_reading = ""

        win = tk.Toplevel(self.root)
        win.title(f"Verify Case Number ({index+1} of {len(self.review_queue)})")
        win.transient(self.root)
        win.grab_set()
        try:
            win.state("zoomed")
        except tk.TclError:
            win.geometry("1400x900")

        # Two-pane review: portrait document on the left, all controls on the right.
        body = ttk.Frame(win, padding=10)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=1, minsize=300)
        body.rowconfigure(0, weight=1)

        preview_panel = ttk.Frame(body)
        preview_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        preview_panel.rowconfigure(0, weight=1)
        preview_panel.columnconfigure(0, weight=1)

        canvas = tk.Canvas(preview_panel, bg="#333", highlightthickness=0)
        sx = ttk.Scrollbar(preview_panel, orient="horizontal", command=canvas.xview)
        sy = ttk.Scrollbar(preview_panel, orient="vertical", command=canvas.yview)
        canvas.configure(xscrollcommand=sx.set, yscrollcommand=sy.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        sy.grid(row=0, column=1, sticky="ns")
        sx.grid(row=1, column=0, sticky="ew")

        controls = ttk.Frame(body, padding=12)
        controls.grid(row=0, column=1, sticky="nsew")
        controls.columnconfigure(0, weight=1)

        ttk.Label(controls, text=f"File {index+1} of {len(self.review_queue)}",
                  font=("Segoe UI", 13, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 8))
        ttk.Label(controls, text=r["path"].name, wraplength=280).grid(
            row=1, column=0, sticky="w", pady=(0, 12))

        size_button = ttk.Button(controls, text="RESTORE WINDOW")
        size_button.grid(row=2, column=0, sticky="ew", pady=(0, 14))

        def toggle_size():
            try:
                if win.state() == "zoomed":
                    win.state("normal")
                    win.geometry("1400x900")
                    size_button.config(text="FULL SCREEN")
                else:
                    win.state("zoomed")
                    size_button.config(text="RESTORE WINDOW")
            except tk.TclError:
                pass
        size_button.config(command=toggle_size)

        ttk.Label(controls, text=f"OCR READ: {float(r.get('score') or 0):.1f}%",
                  font=("Segoe UI", 10, "bold")).grid(row=3, column=0, sticky="w")
        candidate_choice_var = tk.StringVar(value=initial_reading)
        candidate_selector = ttk.Combobox(
            controls, textvariable=candidate_choice_var,
            values=candidate_values, state="readonly" if candidate_values else "disabled")
        # Candidate dropdown hidden; red-box selection remains available on the PDF.
        if sequence_message:
            if sequence_consensus and not r.get("manual_approved"):
                sequence_display = (
                    f"{sequence_message} The strongest suggestion ({sequence_values[0]}) "
                    "has been placed in the confirmation field. Verify it against the document before confirming."
                )
            else:
                sequence_display = sequence_message
            ttk.Label(controls, text=sequence_display, wraplength=280,
                      foreground="#8a5a00").grid(row=5, column=0, sticky="w", pady=(0, 8))
            label_row = 6
        else:
            label_row = 5
        ttk.Label(controls, text="CLICK TO EDIT — CONFIRMED CASE NUMBER (08- PLUS 1–7 DIGITS):",
                  wraplength=280).grid(row=label_row, column=0, sticky="w")
        entry = ttk.Entry(controls, font=("Segoe UI", 14))
        entry.grid(row=label_row + 1, column=0, sticky="ew", pady=(5, 4))
        entry.insert(0, initial_reading)
        entry.configure(state="normal")
        entry.bind("<Control-a>", lambda event: (entry.selection_range(0,"end"),"break")[1])
        entry.bind("<Control-A>", lambda event: (entry.selection_range(0,"end"),"break")[1])
        entry.bind("<Button-1>", lambda event: entry.focus_set(), add="+")
        def use_selected_candidate(_event=None):
            # Hidden OCR dropdown cannot overwrite a manual correction.
            selected_value=candidate_choice_var.get().strip()
            if selected_value and not entry.get().strip():
                entry.insert(0,selected_value)
        candidate_selector.bind("<<ComboboxSelected>>", use_selected_candidate)
        err = ttk.Label(controls, text="", foreground="red", wraplength=280)
        err.grid(row=label_row + 2, column=0, sticky="w", pady=(0, 12))

        ttk.Separator(controls).grid(row=label_row + 3, column=0, sticky="ew", pady=8)

        nav_buttons = ttk.Frame(controls)
        # Previous/Next controls removed from simplified one-file review.
        nav_buttons.columnconfigure(0, weight=1)
        nav_buttons.columnconfigure(1, weight=1)

        def move(delta):
            win.destroy()
            nxt = index + delta
            if 0 <= nxt < len(self.review_queue):
                self._show_review_item(nxt)

        ttk.Button(nav_buttons, text="◀ PREVIOUS",
                   command=lambda: move(-1),
                   state="normal" if index > 0 else "disabled").grid(
                       row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(nav_buttons, text="NEXT ▶",
                   command=lambda: move(1),
                   state="normal" if index < len(self.review_queue)-1 else "disabled").grid(
                       row=0, column=1, sticky="ew", padx=(4, 0))

        send_after_approval=False
        selection={"enabled":False,"drag":None,"preview_rect":None}
        def confirm():
            value=normalize_case_number(entry.get().strip())
            if not CASE_RE.fullmatch(value):
                err.config(text="CASE NUMBER MUST START WITH 08- FOLLOWED BY 1–7 DIGITS.")
                return
            pending_rect=selection.get("preview_rect")
            if pending_rect:
                r["manual_crop_rect"]=tuple(pending_rect)
                r["manual_crop_origin"]="drawn"
            original_ocr=str(r.get("candidate") or "")
            prior_confirmed=str(r.get("confirmed") or "")
            r["training_type"]="unsure"
            r.pop("case_draft",None)
            r["confirmed"]=value
            r["manual_approved"]=True
            r["auto_labeled"]=False
            r["sequence_inferred"]=False
            r["sequence_conflict"]=False
            r["scan_ambiguous"]=False
            r["sequence_anchors"]=[]
            r["sequence_excluded"]=False
            r["sequence_suggestion"]=""
            r["export_error"]=""
            r["send_after_manual_approval"]=False
            r["status"]=f"Manual case confirmed as {value} — awaiting SEND FILES"
            self._move_to_completed(iid)
            r["status"]=f"Manual case confirmed as {value} — awaiting SEND FILES"
            self._insert_scan_row(iid,r)
            if not self._save_state():
                err.config(text="COULD NOT SAVE YOUR CORRECTION TO THE ROOT-FOLDER "
                          "state file. The case remains entered here; check network "
                          "access, then click SAVE REVIEW again.")
                return
            learning_error=None
            try:
                self._save_learning_example(r,value)
            except Exception as exc:
                learning_error=str(exc)
                self._log("Case saved locally, but learning sync failed: %s",exc)
            r["learning_pending"]=bool(learning_error)
            if learning_error:
                r["status"]=(f"Manual case {value} SAVED — learning pending; "
                             "awaiting SEND FILES")
            else:
                r["status"]=(f"Manual case {value} SAVED; example saved (UPDATE LEARNING to train) — "
                             "awaiting SEND FILES")
            self._diag_event("manual_case_confirmation",
                file=Path(r["path"]).name,ocr_candidate=original_ocr,
                previous_confirmed=prior_confirmed,confirmed=value,
                learning_saved=not bool(learning_error),
                transfer="deferred_until_send_all_selected")
            self._insert_scan_row(iid,r)
            self._save_state()
            self.status.config(text=r["status"])
            self._recheck_after_decision()
            win.destroy()
            if learning_error:
                messagebox.showwarning("Case number saved",
                    f"{value} is saved and remains in SCAN. Shared learning "
                    f"did not save: {learning_error}\\n\\n"
                    "Reopen REVIEW SELECTED and save again when the learning "
                    "share is available. No PDF was sent.")

        action_row = label_row + 5
        ttk.Button(controls,text="SAVE REVIEW",
                   command=confirm).grid(row=action_row+1,column=0,sticky="ew",pady=(0,8))
        ttk.Button(controls,text="SKIP",
                   command=win.destroy).grid(row=action_row+2,column=0,sticky="ew")

        ttk.Label(controls,
                  text="SAVE REVIEW STORES THE CORRECTED 08- CASE AND LEARNING "
                       "without moving any PDF. You can reopen and revise it "
                       "until you press SEND FILES.",
                  wraplength=280).grid(row=action_row + 2, column=0, sticky="w", pady=(18, 0))

        # Full-page preview with optional manual case-number crop selection.
        try:
            from PIL import Image, ImageTk, ImageDraw
            if r.get("snippet") is None:
                raise FileNotFoundError("The original PDF is missing or its preview could not be rebuilt.")
            page_num, full_arr, label, auto_rect = r.get("snippet")[:4]
            original = Image.fromarray(full_arr).convert("RGB")

            zoom = {"value": 1.0, "fit": True}
            photo_ref = {"photo": None, "crop": None}
            selection = {
                "enabled": False,
                "drag": None,
                "preview_rect": None
            }

            preview_note = (f"Page {page_num+1} • {len(visual_candidates)} verified 08- field box(es) in red • {label}"
                            if label else f"Page {page_num+1} • No located OCR candidate")
            ttk.Label(controls, text=preview_note, wraplength=280).grid(
                row=26, column=0, sticky="w", pady=(12, 0))

            crop_heading = ttk.Label(
                controls, text="SELECT CASE FIELD / READ OCR",
                font=("Segoe UI", 10, "bold"))
            crop_heading.grid(row=27, column=0, sticky="w", pady=(14, 4))

            selection_status = ttk.Label(
                controls,
                text="Draw a box around the complete case number. OCR will suggest a read in the field; verify it before saving. Only complete 08- numbers are accepted.",
                wraplength=280)
            selection_status.grid(row=29, column=0, sticky="w", pady=(5, 5))

            def set_selection_mode():
                # A single drag is a complete action. Never leave the user in a
                # persistent mode requiring "Finish Box Selection".
                selection["enabled"] = True
                selection["drag"] = None
                selection_status.config(
                    text="DRAG AROUND THE FULL CASE NUMBER (08-...) AND RELEASE TO OCR IT. YOU MAY REDRAW AS NEEDED.")
            selection_button = ttk.Button(
                controls, text="DRAW / REDRAW CROP BOX",
                command=set_selection_mode)
            selection_button.grid(row=30, column=0, sticky="ew", pady=(0, 5))

            def clear_selection():
                selection["preview_rect"] = None
                r["manual_crop_rect"] = None
                r["manual_crop_origin"]=""
                selection["drag"] = None
                redraw()
                update_crop_preview()
                selection_status.config(
                    text="MANUAL BOX CLEARED. THE OCR-DETECTED BOX WILL BE USED WHEN AVAILABLE.")
                self._save_state()

            ttk.Button(
                controls, text="CLEAR MANUAL BOX",
                command=clear_selection).grid(row=31, column=0, sticky="ew")

            learned_overlay_var=tk.BooleanVar(value=False)
            learned_overlay_zones=self._ranked_learned_scan_zones(original.width,original.height)
            overlay_label=(f"Show learned scan areas ({sum(z['count'] for z in learned_overlay_zones)} crops, "
                           f"{len(learned_overlay_zones)} locations)")
            ttk.Checkbutton(controls,text=overlay_label,variable=learned_overlay_var,
                            command=lambda: redraw()).grid(row=33,column=0,sticky="w",pady=(5,0))

            crop_preview_row = ttk.Frame(controls)
            crop_preview_row.grid(row=32, column=0, sticky="ew", pady=(8, 0))
            crop_preview_label = ttk.Label(
                crop_preview_row, text="CROP PREVIEW WILL APPEAR HERE.", anchor="center")
            crop_preview_label.pack(side="left", fill="x", expand=True)
            score_value = float(r.get("score", 0.0) or 0.0)
            review_score_label = ttk.Label(
                crop_preview_row,
                text=(f"OCR score\n{score_value:.1f}%" if score_value else "OCR score\n—"),
                font=("Segoe UI", 10, "bold"), anchor="center", justify="center")
            review_score_label.pack(side="right", padx=(8, 4))

            def redraw():
                avail_w = max(200, canvas.winfo_width()-24)
                avail_h = max(200, canvas.winfo_height()-24)
                if zoom["fit"]:
                    zoom["value"] = min(
                        avail_w/original.width, avail_h/original.height, 1.0)
                scale = zoom["value"]
                w = max(1, int(original.width*scale))
                h = max(1, int(original.height*scale))
                resized = original.resize((w, h), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(resized)
                photo_ref["photo"] = photo
                canvas.delete("all")
                canvas.create_image(0, 0, image=photo, anchor="nw")
                canvas.configure(scrollregion=(0, 0, w, h))

                # Optional cyan overlays show the exact prioritized crop
                # locations learned from all verified manual selections.
                if learned_overlay_var.get():
                    for rank, zone in enumerate(learned_overlay_zones,1):
                        zx0,zy0,zx1,zy1=zone['rect']
                        canvas.create_rectangle(zx0*scale,zy0*scale,
                            zx1*scale,zy1*scale,outline="#00a5e5",width=2,
                            dash=(6,3))
                        canvas.create_text(zx0*scale,max(10,zy0*scale-8),
                            anchor="sw",text=f"{rank}: {zone['count']} crops",
                            fill="#0077aa",font=("Segoe UI",9,"bold"))
                # Red = one real, independently localized full 08- OCR box
                # per physical field. Green = reviewer-selected training crop.
                if visual_candidates:
                    for candidate_box in visual_candidates:
                        rect = candidate_box.get("rect")
                        if not rect:
                            continue
                        ax0, ay0, ax1, ay1 = rect
                        canvas.create_rectangle(
                            ax0*scale, ay0*scale, ax1*scale, ay1*scale,
                            outline="#ff3333", width=2, dash=(5, 3))
                        label_text = candidate_box.get("number", "")
                        if label_text:
                            canvas.create_text(
                                ax0*scale, max(2, ay0*scale-8), anchor="sw",
                                text=label_text, fill="#ff3333",
                                font=("Segoe UI", max(8, int(9*scale)), "bold"))
                # An unlabeled snippet rectangle is not proof of an 08- read.
                manual_rect = selection["preview_rect"] or r.get("manual_crop_rect")
                if manual_rect:
                    mx0, my0, mx1, my1 = manual_rect
                    canvas.create_rectangle(
                        mx0*scale, my0*scale, mx1*scale, my1*scale,
                        outline="#00ff66", width=3)
                    handle = max(4, 6*scale)
                    for hx, hy in ((mx0,my0),(mx1,my0),(mx0,my1),(mx1,my1)):
                        canvas.create_rectangle(
                            hx*scale-handle, hy*scale-handle,
                            hx*scale+handle, hy*scale+handle,
                            fill="#00ff66", outline="#003311")
                if zoom["fit"]:
                    canvas.xview_moveto(0)
                    canvas.yview_moveto(0)

            def update_crop_preview():
                manual_rect = selection["preview_rect"] or r.get("manual_crop_rect")
                matching=next((h for h in visual_candidates
                               if _case_identity(h.get("number")) == _case_identity(entry.get())),None)
                rect_to_show = manual_rect or (matching.get("rect") if matching else None)
                if not rect_to_show:
                    crop_preview_label.config(
                        image="", text="NO CROP BOX YET. DRAW ONE AROUND THE CASE NUMBER.")
                    photo_ref["crop"] = None
                    return
                x0, y0, x1, y1 = [int(v) for v in rect_to_show]
                x0=max(0,min(original.width,x0)); x1=max(0,min(original.width,x1))
                y0=max(0,min(original.height,y0)); y1=max(0,min(original.height,y1))
                if x1 <= x0 or y1 <= y0:
                    crop_preview_label.config(image="", text="CROP BOX IS EMPTY.")
                    photo_ref["crop"] = None
                    return
                crop = original.crop((x0,y0,x1,y1))
                crop.thumbnail((260,110),Image.Resampling.LANCZOS)
                crop_photo = ImageTk.PhotoImage(crop)
                photo_ref["crop"] = crop_photo
                crop_preview_label.config(
                    image=crop_photo,
                    text=f"{'Manual' if manual_rect else 'OCR'} crop • {x1-x0} × {y1-y0} px",
                    compound="top")
                r["training_type"] = "unsure"

            def canvas_to_image(event):
                scale = max(0.01, zoom["value"])
                x = canvas.canvasx(event.x) / scale
                y = canvas.canvasy(event.y) / scale
                return (
                    max(0.0, min(float(original.width), x)),
                    max(0.0, min(float(original.height), y)))

            def apply_candidate_selection(number):
                if not CASE_RE.fullmatch(normalize_case_number(number)):
                    return
                chosen = next((item for item in visual_candidates
                               if item.get("number") == number), None)
                if not number:
                    return
                current_case=normalize_case_number(entry.get().strip())
                already_verified=bool(r.get("manual_approved") and
                                      not r.get("auto_labeled"))
                manually_changed=bool(current_case and
                                      current_case!=initial_reading)
                # OCR box clicks select the training crop, not the verified
                # case. Only a literal empty entry may receive an OCR hint.
                if not entry.get().strip() and not manually_changed and not already_verified:
                    entry.insert(0,number)
                candidate_choice_var.set(number)

                if chosen:
                    # This is a real OCR candidate with a corresponding red box.
                    r["candidate"] = number
                    r["score"] = float(chosen.get("score") or 0.0)
                    review_score_label.config(
                        text=f"OCR score\n{r['score']:.1f}%" if r["score"] else "OCR score\n—")
                    r["engines"] = list(chosen.get("engines") or [])
                    families = {str(e).split("-", 1)[0] for e in r["engines"]}
                    r["engine_agreement"] = (
                        "EasyOCR" in families and "TrOCR" in families)
                    rect = chosen.get("rect")
                    if rect:
                        x0, y0, x1, y1 = [int(v) for v in rect]
                        pad_x = max(24, int((x1-x0)*0.55))
                        pad_y = max(12, int((y1-y0)*0.45))
                        r["manual_crop_rect"] = (
                            max(0, x0-pad_x), max(0, y0-pad_y),
                            min(original.width, x1+int(pad_x*0.25)),
                            min(original.height, y1+pad_y))
                        r["manual_crop_origin"]="candidate"
                        selection["preview_rect"] = None
                    selection_status.config(
                        text=(f"Selected OCR box {number} for crop learning. "
                              f"Confirmed case remains {entry.get().strip()}. "
                              "Typing a correction has priority over OCR box selection. "
                              "You can redraw the training crop if needed."))
                    redraw()
                    update_crop_preview()
                elif number in sequence_values:
                    # A sequence suggestion is not an OCR read. Do not copy its
                    # value into r['candidate'], alter OCR confidence, or claim
                    # engine agreement. It only pre-fills the human confirmation.
                    selection_status.config(
                        text=f"Selected sequence suggestion {number}. This is based on neighboring files, not an OCR detection. Verify the actual digits and the green training crop before confirming.")
                self._save_state()

            def on_candidate_dropdown(event=None):
                apply_candidate_selection(candidate_choice_var.get())

            candidate_selector.bind("<<ComboboxSelected>>", on_candidate_dropdown)

            def begin_box(event):
                if not selection["enabled"]:
                    x, y = canvas_to_image(event)
                    # Select the smallest red candidate box under the click.
                    matches = []
                    for candidate_box in visual_candidates:
                        rect = candidate_box.get("rect")
                        if not rect:
                            continue
                        x0, y0, x1, y1 = [float(v) for v in rect]
                        if x0 <= x <= x1 and y0 <= y <= y1:
                            area = max(1.0, (x1-x0)*(y1-y0))
                            matches.append((area, candidate_box))
                    if matches:
                        _area, chosen = min(matches, key=lambda item: item[0])
                        apply_candidate_selection(chosen.get("number", ""))
                    return "break"
                x, y = canvas_to_image(event)
                existing = r.get("manual_crop_rect")
                tolerance = max(8.0, 10.0 / max(zoom["value"], 0.1))
                mode = "draw"
                edges = set()
                start_rect = None
                if existing:
                    x0,y0,x1,y1 = existing
                    within = x0 <= x <= x1 and y0 <= y <= y1
                    if within:
                        near_left = abs(x-x0) <= tolerance
                        near_right = abs(x-x1) <= tolerance
                        near_top = abs(y-y0) <= tolerance
                        near_bottom = abs(y-y1) <= tolerance
                        if near_left: edges.add("left")
                        if near_right: edges.add("right")
                        if near_top: edges.add("top")
                        if near_bottom: edges.add("bottom")
                        if edges:
                            mode = "resize"
                            start_rect = tuple(existing)
                        else:
                            mode = "move"
                            start_rect = tuple(existing)
                selection["drag"] = {
                    "mode": mode, "start": (x,y),
                    "start_rect": start_rect, "edges": edges}
                if mode == "draw":
                    selection["preview_rect"] = (x,y,x,y)
                canvas.focus_set()
                redraw()
                return "break"

            def drag_box(event):
                drag = selection.get("drag")
                if not selection["enabled"] or not drag:
                    return
                x,y = canvas_to_image(event)
                sx,sy = drag["start"]
                if drag["mode"] == "draw":
                    rect_now = (min(sx,x), min(sy,y), max(sx,x), max(sy,y))
                elif drag["mode"] == "move":
                    x0,y0,x1,y1 = drag["start_rect"]
                    dx,dy=x-sx,y-sy
                    width,height=x1-x0,y1-y0
                    nx0=max(0.0,min(original.width-width,x0+dx))
                    ny0=max(0.0,min(original.height-height,y0+dy))
                    rect_now=(nx0,ny0,nx0+width,ny0+height)
                else:
                    x0,y0,x1,y1=drag["start_rect"]
                    dx,dy=x-sx,y-sy
                    edges=drag["edges"]
                    if "left" in edges: x0=max(0.0,min(x1-1,x0+dx))
                    if "right" in edges: x1=min(float(original.width),max(x0+1,x1+dx))
                    if "top" in edges: y0=max(0.0,min(y1-1,y0+dy))
                    if "bottom" in edges: y1=min(float(original.height),max(y0+1,y1+dy))
                    rect_now=(x0,y0,x1,y1)
                selection["preview_rect"]=tuple(int(round(v)) for v in rect_now)
                redraw()
                update_crop_preview()
                return "break"

            def apply_crop_ocr_result(original_entry, suggestion):
                if not win.winfo_exists():
                    return
                number,score,engine,error=suggestion
                if error:
                    selection_status.config(text="CROP OCR UNAVAILABLE: "+error[:180])
                    return
                if not CASE_RE.fullmatch(normalize_case_number(number)):
                    selection_status.config(text="NO COMPLETE 08- READ IN SELECTED BOX. ENTER THE NUMBER MANUALLY OR REDRAW. CROP COORDINATES REMAIN SAVED.")
                    return
                # Never replace an edit made while OCR was running.
                if entry.get()==original_entry:
                    entry.delete(0,"end")
                    entry.insert(0,number)
                r["crop_ocr_suggestion"]=number
                r["crop_ocr_engine"]=engine
                r["crop_ocr_score"]=float(score or 0)
                selection_status.config(text=f"Crop OCR suggests {number} ({engine}, {score:.1f}%). Verify or correct before SAVE REVIEW.")
                redraw();update_crop_preview()

            def finish_box(event):
                if not selection["enabled"] or not selection.get("drag"):
                    return
                drag=selection["drag"]
                selection["drag"]=None
                selection["enabled"]=False  # Release completes the box selection.
                selected=selection.get("preview_rect")
                if selected:
                    x0,y0,x1,y1=selected
                    if x1-x0 >= 35 and y1-y0 >= 10:
                        r["manual_crop_rect"]=selected
                        r["manual_crop_origin"]="drawn"
                        r["training_type"]="unsure"
                        selection_status.config(
                            text=f"Manual crop selected: {x1-x0} × {y1-y0} pixels. Include the field label and complete case number in this box.")
                        self._save_state()
                        crop=original.crop(tuple(map(int, selected)))
                        original_entry=entry.get()
                        crop_rect=tuple(map(int,selected))
                        selection_status.config(text="READING YOUR SELECTED CROP LOCALLY…")
                        def crop_worker():
                            try:
                                suggestion=self._ocr_review_crop(crop)
                            except Exception as exc:
                                suggestion=("",0.0,"",str(exc))
                            self.queue.put(("review_crop_result",win,iid,crop_rect,
                                            original_entry,suggestion,apply_crop_ocr_result))
                        threading.Thread(target=crop_worker,daemon=True).start()
                    else:
                        selection["preview_rect"]=None
                        selection_status.config(
                            text="THAT BOX IS TOO SMALL. INCLUDE INCIDENT NO. AND THE FULL CASE NUMBER IN A LARGER BOX.")
                redraw()
                update_crop_preview()
                return "break"

            def on_canvas_resize(event):
                if zoom["fit"]:
                    redraw()

            def on_wheel(event):
                if event.state & 0x0004:
                    zoom["fit"] = False
                    zoom["value"] = max(0.15, min(
                        4.0, zoom["value"]*(1.15 if event.delta > 0 else 1/1.15)))
                    redraw()
                else:
                    canvas.yview_scroll(int(-event.delta/120), "units")
                return "break"

            def on_linux_up(event):
                if event.state & 0x0004:
                    zoom["fit"] = False
                    zoom["value"] = min(4.0, zoom["value"]*1.15)
                    redraw()
                else:
                    canvas.yview_scroll(-3, "units")
                return "break"

            def on_linux_down(event):
                if event.state & 0x0004:
                    zoom["fit"] = False
                    zoom["value"] = max(0.15, zoom["value"]/1.15)
                    redraw()
                else:
                    canvas.yview_scroll(3, "units")
                return "break"

            canvas.bind("<Configure>", on_canvas_resize)
            canvas.bind("<MouseWheel>", on_wheel)
            canvas.bind("<Button-4>", on_linux_up)
            canvas.bind("<Button-5>", on_linux_down)
            canvas.bind("<ButtonPress-1>", begin_box)
            canvas.bind("<B1-Motion>", drag_box)
            canvas.bind("<ButtonRelease-1>", finish_box)
            win.update_idletasks()
            redraw()
            update_crop_preview()
        except Exception as exc:
            ttk.Label(controls, text=f"Preview unavailable: {exc}",
                      wraplength=280).grid(row=26, column=0, sticky="w")

        # Pressing Enter in the case-number field confirms and advances the queue.
        entry.bind("<Return>", lambda event: (confirm(), "break")[1])
        entry.focus_set()

    def refresh_completed_list(self):
        """Reconcile the UI with actual unsent PDFs; preserve files and history."""
        if self.busy or self._training_blocks_work():
            messagebox.showinfo("Refresh unavailable", "The list can be refreshed when processing finishes.")
            return
        visible_rows = set()
        for iid, record in self.records.items():
            source = record.get("path")
            if (record.get("confirmed") and not record.get("exported")
                    and source is not None and source.is_file()):
                visible_rows.add(iid)
        for iid in list(self.completed_tree.get_children("")):
            if iid not in visible_rows:
                self.completed_tree.delete(iid)
                self.completed_photo_refs.pop(iid, None)
                self.completed_checked_iids.discard(iid)
                self.completed_edit_drafts.pop(iid, None)
                self._close_completed_edit(iid)
                btn = self.completed_unverify_buttons.pop(iid, None)
                if btn is not None:
                    btn.destroy()
                if iid in self.records:
                    self.records[iid]["completed_iid"] = None
        for iid in visible_rows:
            record = self.records[iid]
            if not self.completed_tree.exists(iid):
                self._insert_completed_row(iid, record)
            else:
                self.completed_tree.item(iid, values=self._completed_row_values(iid, record))
            record["completed_iid"] = iid
        self._update_completed_count()
        self.root.after_idle(self._position_completed_unverify_buttons)
        self.status.config(text=f"Completed list refreshed: {len(visible_rows)} unsent PDF(s).")

    def _eligible_for_kurt(self, iid, rec, automatic=False):
        """Transfer only verified PDFs still in root; keep explicit OCR conflicts
        in review unless a person confirmed the corrected case number.
        """
        human_approved = bool(rec.get("manual_approved")) and not bool(rec.get("auto_labeled"))
        if (not self.folder or rec.get("case_draft")
                or not rec.get("confirmed")
                or not rec.get("manual_approved") or rec.get("exported")
                or not CASE_RE.fullmatch(str(rec.get("confirmed") or ""))):
            return False
        if getattr(self,"_sequence_review_pending",False):
            return False
        # Every verified COMPLETED record may be transferred after scanning.
        # Records without confirmed+manual_approved are rejected above.
        if (rec.get("sequence_conflict") or rec.get("scan_ambiguous")) and not human_approved:
            return False
        path = rec.get("path")
        if path is None or not Path(path).is_file():
            return False
        # Do not send a record from a different folder into the current Kurt
        # destination if the user changed folders while an after() was pending.
        if Path(path).parent.resolve() != Path(self.folder).resolve():
            return False
        # Do not send while a scan is still building sequence evidence.
        if self.busy:
            return False
        if automatic and rec.get("export_error"):
            return False
        return True

    def _schedule_auto_export(self):
        """Do not send early: waiting for the final before/after pass is safest."""
        return None

    def _run_scheduled_auto_export(self):
        """Early case-transition transfers are intentionally disabled."""
        self._auto_export_after=None
        return None

    def export_selected(self):
        """Kept as a compatibility alias; Completed now has one SEND button."""
        return self.export()

    def _unsaved_completed_changes(self, selected_set):
        """Prevent exporting an old case number while CHANGE contains a draft."""
        pending = []
        for iid in self.completed_tree.get_children(""):
            if selected_set is not None and iid not in selected_set:
                continue
            record = self.records.get(iid)
            if not record or record.get("exported"):
                continue
            widgets = self.completed_edit_widgets.get(iid)
            if widgets and widgets[0].winfo_exists():
                current = widgets[0].get().strip()
            else:
                current = self.completed_edit_drafts.get(iid, "").strip()
            if current:
                pending.append(iid)
        return pending

    def export(self, selected_iids=None, automatic=False):
        """Move all verified root PDFs to COMPLETED on explicit SEND FILES."""
        if getattr(self,"_scan_case_editor",None) is not None:
            commit=getattr(self,"_scan_case_commit",None)
            if commit is not None:
                commit()
            if getattr(self,"_scan_case_editor",None) is not None:
                self.status.config(text="CORRECT OR CANCEL THE UNFINISHED CASE EDIT BEFORE EXPORTING.")
                self._scan_case_editor.focus_set()
                return
        if automatic and getattr(self,"_stop_all_requested",False):
            return
        if automatic and not bool(self.auto_send_var.get()):
            return
        if automatic and not self._startup_ready:
            return
        if self.export_busy:
            self._export_reschedule = True
            self.status.config(text="EXPORT IS ALREADY IN PROGRESS. WAIT FOR IT TO FINISH.")
            return
        if not self.folder or self.busy or self._training_blocks_work() or getattr(self,"_sequence_review_pending",False):
            if not automatic:
                message = "Sending waits until the scan and sequence review finish."
                self.status.config(text=message)
                self.completed_send_status.config(text=message)
            return
        self._yield_background_learning("sending verified files")
        # Export is intentionally independent of scan/review selection.
        selected_set = None
        if not automatic:
            drafts = self._unsaved_completed_changes(selected_set)
            if drafts:
                message = (f"Save or Cancel the CHANGE entry for {len(drafts)} selected "
                           "file(s) before SEND FILES. Nothing was exported.")
                self.status.config(text=message)
                self.completed_send_status.config(text=message)
                self._start_completed_edit(drafts[0])
                return
        # Always refresh sequence evidence immediately before selecting rename jobs.
        try:
            self._apply_sequence_consensus()
        except Exception as exc:
            self._sequence_review_pending=True
            self.status.config(text=f"Before/after check failed; nothing renamed: {exc}")
            return
        approved = [
            (iid, Path(r["path"]), str(r["confirmed"]))
            for iid, r in self.records.items()
            if (selected_set is None or iid in selected_set)
            and self._eligible_for_kurt(iid, r, automatic=automatic)
        ]
        # Preserve the original scan order when numbering multiple pages
        # belonging to the same case (Page 2 must precede Page 10).
        approved.sort(key=lambda item: self._natural_pdf_sort_key(item[1]))
        if not approved:
            if not automatic:
                message = "No approved files waiting to be sent."
                self.status.config(text=message)
                self.completed_send_status.config(text=message)
            return
        if not automatic:
            # A manual SEND retries prior transfers that failed, without popups.
            for iid, _, _ in approved:
                self.records[iid]["export_error"] = ""
        if automatic:
            self._audit_event("AUTO_SEND_STARTED",approved_count=len(approved))
        self._export_cancel_event.clear()
        self.export_busy = True
        self._export_in_flight_iids = {iid for iid, _, _ in approved}
        self._update_completed_count()
        message = f"Sending {len(approved)} approved PDF(s) to COMPLETED…"
        self.status.config(text=message)
        self.completed_send_status.config(text=message)
        target = Path(self.folder) / "COMPLETED"
        threading.Thread(
            target=self._export_worker, args=(target, approved),
            name="MFCase-PDFTransfer", daemon=True).start()

    def _export_worker(self, destination_folder, approved):
        """One writer serializes original PDF moves, collision suffixes, and errors."""
        try:
            destination_folder.mkdir(parents=True, exist_ok=True)
            reserved = {p.name.casefold() for p in destination_folder.iterdir() if p.is_file()}
            for iid, source, case in approved:
                if self._export_cancel_event.is_set():break
                case = _mfcase_export_case_name(case)
                original = getattr(self,"records",{}).get(iid,{}).get("original_name") or source.name
                base = f"{Path(original).stem}_{case}"
                filename = f"{base}.pdf"
                counter = 1
                destination = None
                reserved_by_us = False
                try:
                    if not source.is_file():
                        raise FileNotFoundError(f"Original PDF is missing: {source.name}")
                    if destination_folder.resolve() != (source.parent / "COMPLETED").resolve():
                        raise ValueError("Send destination must be the source folder's COMPLETED folder.")
                    if source.resolve() == (destination_folder / filename).resolve():
                        self.queue.put(("export_result",iid,source.name,str(source),""))
                        continue
                    # O_EXCL reserves a unique filename atomically, including
                    # when coworkers export the same case to the same share.
                    while True:
                        if filename.casefold() in reserved:
                            filename = f"{base} ({counter}).pdf"
                            counter += 1
                            continue
                        destination = destination_folder / filename
                        try:
                            handle = os.open(str(destination), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                            os.close(handle)
                            reserved_by_us = True
                            break
                        except FileExistsError:
                            reserved.add(filename.casefold())
                            filename = f"{base} ({counter}).pdf"
                            counter += 1
                    # The source and COMPLETED are on the same volume, so an
                    # atomic rename replaces only our own zero-byte reservation.
                    os.replace(str(source), str(destination))
                    self._diag_event("transfer_result",file=source.name,
                         source_file=source.name,destination_file=destination.name,
                         case=case,outcome="moved")
                    reserved_by_us = False
                    try:
                        # Refresh Windows Explorer "Date modified" when MFCase
                        # finishes processing; PDF contents remain unchanged.
                        os.utime(destination, None)
                    except OSError as metadata_error:
                        self._log(
                            "Transfer succeeded, but Date modified could not be "
                            "updated for %s: %s", destination, metadata_error)
                except Exception as exc:
                    if reserved_by_us and destination is not None:
                        try:
                            if destination.stat().st_size == 0:
                                destination.unlink()
                        except OSError:
                            self._log("Could not clean reserved filename %s", destination)
                    self._diag_event("transfer_result",file=source.name,
                         destination_file=str(destination) if destination else None,
                         outcome="failed",error=repr(exc))
                    self.queue.put(("export_result", iid, source.name, "", str(exc)))
                else:
                    reserved.add(filename.casefold())
                    self.queue.put(("export_result", iid, source.name, str(destination), ""))
        except Exception as exc:
            # Keep every untouched source in the queue for a later retry.
            for iid, source, _case in approved:
                self.queue.put(("export_result", iid, source.name, "", str(exc)))
        finally:
            self.queue.put(("export_done",))

    def _apply_export_result(self, iid, source_name, destination, error):
        """Update record and Tk widgets only after the worker confirms a move."""
        rec = self.records.get(iid)
        if not rec:
            return
        self._export_in_flight_iids.discard(iid)
        if error:
            rec["export_error"] = error
            self._audit_event("SEND_ERROR",original_name=source_name,error=error)
            rec["status"] = f"Transfer failed: {error}"
            self._log("PDF transfer failed for %s: %s", source_name, error)
            message = (f"Transfer failed for {source_name}: {error}. "
                       "Source retained; click SEND FILES to retry.")
            self.status.config(text=message)
            self.completed_send_status.config(text=message)
            if self.completed_tree.exists(iid):
                self.completed_tree.item(iid, values=self._completed_row_values(iid, rec))
            self._insert_scan_row(iid,rec)
        else:
            target = Path(destination)
            rec["original_name"] = rec.get("original_name") or source_name
            rec["original_path"] = rec.get("original_path") or str(rec["path"])
            rec["path"] = target
            rec["exported"] = True
            self._commit_auto_location(rec)
            rec["send_after_manual_approval"] = False
            rec["export_error"] = ""
            rec["export_name"] = target.name
            # Track EVERY successful transfer. Only independent confirmed
            # cases are anchors; sequence-inferred transfers are audit-only.
            original=Path(rec.get("original_name") or source_name).name
            if CASE_RE.fullmatch(normalize_case_number(rec.get("confirmed"))):
                self.sent_history[original]={
                    "original_name":original,"destination_name":target.name,
                    "confirmed":normalize_case_number(rec["confirmed"]),
                    "ocr_candidate":normalize_case_number(rec.get("sequence_original_candidate") or rec.get("candidate")),
                    "manual_approved":bool(rec.get("manual_approved")),
                    "auto_labeled":bool(rec.get("auto_labeled")),
                    "score":float(rec.get("score") or 0),
                    "sequence_inferred":bool(rec.get("sequence_inferred")),
                    "source":"mfcase_send", "sent_at":datetime.now().isoformat(timespec="seconds")}
                self._save_sent_history()
            self._audit_event("FILE_SENT",
                              original_name=rec.get("original_name") or source_name,
                              original_path=rec.get("original_path") or "",
                              confirmed_case=rec.get("confirmed") or "",
                              sent_path=str(target),sent_name=target.name,
                              inferred=bool(rec.get("sequence_inferred")))
            rec["status"] = f"Sent to COMPLETED: {target.name}"
            if self.completed_tree.exists(iid):
                self.completed_tree.delete(iid)
            # Keep the renamed PDF visible in its original folder.
            self.completed_photo_refs.pop(iid, None)
            self.completed_checked_iids.discard(iid)
            self.scan_checked_iids.discard(iid)
            self.completed_edit_drafts.pop(iid, None)
            self._close_completed_edit(iid)
            button = self.completed_unverify_buttons.pop(iid, None)
            if button is not None:
                button.destroy()
            rec["completed_iid"] = None
            rec["scan_selected"] = False
            if self.tree.exists(iid):self.tree.delete(iid)
            self._schedule_scan_preview_refresh()
            message = f"Sent to COMPLETED: {target.name} (from {source_name})"
            self.status.config(text=message)
            self.completed_send_status.config(text=message)
        self._save_state()
        self._update_completed_count()
        self._update_scan_file_count()


def _install_startup_error_reporter():
    """Make startup failures visible when pythonw.exe has no terminal."""
    import traceback
    original=sys.excepthook
    def report(tp,value,tb):
        details="".join(traceback.format_exception(tp,value,tb))
        try:
            logroot=Path(os.getenv("LOCALAPPDATA") or tempfile.gettempdir())/"MFCaseOCR"/"logs"
            logroot.mkdir(parents=True,exist_ok=True)
            dest=logroot/"startup_error.txt"
            dest.write_text(details,encoding="utf-8")
        except Exception:
            dest=None
        if os.name=="nt":
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(None,
                    "MFCase could not start.\n\n"+str(value)+
                    "\n\nError log: "+str(dest or "unavailable"),
                    "MFCase startup error",0x10)
            except Exception:
                pass
        original(tp,value,tb)
    sys.excepthook=report


def main():
    _install_startup_error_reporter()
    root=tk.Tk(); App(root); root.mainloop()
if __name__=="__main__": main()
