#!/usr/bin/env python3
"""
Author - Stephen Abkin, Class of 2027, Cohort J
Date - 05/04/2025

zlp_scheduler.py — list every 100-minute study window (starts 08:00-16:10)
for the Zachry Leadership Program.

When a course offers multiple sections, the script chooses
**one** section — the one that preserves the greatest number of class windows
after it is added to the schedule.  Courses with only one section are locked
first.  The algorithm is a fast greedy heuristic:

    1. Add all single-option courses to the busy grid.
    2. While multi-option courses remain:
         - Score every candidate section (# free windows left if selected)
         - Pick the section with the highest score (tie -> earliest start)
         - Add it permanently to the grid, repeat.

If no 100-minute block exists on any weekday, the script prints the least-bad
start time(s) per day **and** which classes cause those conflicts.
"""

from __future__ import annotations
import re
from pathlib import Path
from typing import Dict, List, Tuple
from copy import deepcopy

# ═════════════════════ constants ════════════════════════════════════════
DAY_LETTERS = "MTWRF"
DAY_NAMES   = {"M":"Monday","T":"Tuesday","W":"Wednesday",
               "R":"Thursday","F":"Friday"}

GRID_START, GRID_END = 8*60, 16*60 + 10        # 08:00–16:10 grid for study slots
BLOCK_LEN   = 100                              # 100-minute window
STEP_MIN    = 5                                # 5-minute grid

CRS_RE  = re.compile(r"^[A-Z]{4}$")
NUM_RE  = re.compile(r"^\d{3}[Ll]?$")
TIME_RE = re.compile(r"^(2[0-3]|1\d|0\d):([0-5]\d)$")
DAYS_RE = re.compile(r"^[MTWRF]+$", re.I)

DEFAULT_SHEET = "sections.xlsx"

# Toggle: print all tied-best candidates before locking one in.
SHOW_TIED_CHOICES = False

# ═════════════════════ helpers ══════════════════════════════════════════
def to_minutes(hhmm: str) -> int:
    return int(hhmm[:2])*60 + int(hhmm[3:])

def to_hhmm(m: int) -> str:
    return f"{m//60:02d}:{m%60:02d}"

def overlaps(a:Tuple[int,int], b:Tuple[int,int]) -> bool:
    return max(a[0],b[0]) < min(a[1],b[1])     # half-open

def merge(intvs: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    """Return a list of non-overlapping (start, end) tuples."""
    merged: List[Tuple[int, int]] = []
    for s, e in sorted(intvs):
        if not merged or s > merged[-1][1]:
            merged.append((s, e))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
    return merged

def free_and_min_conflict(day:List[Tuple[int,int]]):
    """Return (free blocks, starts with least overlaps, min overlap count) for merged grid."""
    free,best,mincnt=[],[],float('inf')
    for st in range(GRID_START, GRID_END+1, STEP_MIN):
        blk=(st, st+BLOCK_LEN)
        cnt=sum(1 for iv in day if overlaps(blk,iv))
        if cnt==0: free.append(blk)
        if cnt<mincnt:
            mincnt,best=cnt,[st]
        elif cnt==mincnt:
            best.append(st)
    return free,best,mincnt

def offenders_for_start(st:int, day_labeled:List[Tuple[int,int,str]])->List[Tuple[str,int,int]]:
    """Return all (code, start_min, end_min) classes overlapping a block."""
    blk=(st, st+BLOCK_LEN)
    hits=[]
    for s,e,code in day_labeled:
        if overlaps((s,e), blk):
            hits.append((code, s, e))
    hits.sort(key=lambda x: x[1])
    return hits

def min_conflict_by_labels(day_labeled:List[Tuple[int,int,str]])->Tuple[List[int],int]:
    """
    Using the *labeled* grid (no merging), find starts with the true minimum
    number of conflicting classes. Returns (best_starts, min_conflict_count).
    """
    best, mincnt = [], float('inf')
    for st in range(GRID_START, GRID_END+1, STEP_MIN):
        cnt = sum(1 for s,e,_ in day_labeled if overlaps((s,e), (st, st+BLOCK_LEN)))
        if cnt < mincnt:
            mincnt, best = cnt, [st]
        elif cnt == mincnt:
            best.append(st)
    return best, mincnt

# ═════════════ Excel / CSV loader ═══════════════════════════════════════
def rows_from_file(path:str)->list[tuple[str,str,str,str,str]]:
    import pandas as pd
    p=Path(path)
    if p.suffix.lower() in {".xlsx",".xls"}:
        df=pd.read_excel(p,engine="openpyxl")
    elif p.suffix.lower()==".csv":
        df=pd.read_csv(p)
    else:
        raise ValueError("file must be .xlsx, .xls, or .csv")

    need=["Subject","Number","Days","Start","Duration"]
    miss=[c for c in need if c not in df.columns]
    if miss:
        raise ValueError(f"missing columns: {', '.join(miss)}")

    rows=[]
    for _,r in df.iterrows():
        rows.append((
            str(r["Subject"]).strip().upper(),
            str(r["Number"]).strip(),
            str(r["Days"]).strip().upper(),
            str(r["Start"]).strip(),
            str(int(r["Duration"])).strip()
        ))
    return rows

# ═════════════ validator / inserter ═════════════════════════════════════
def add_section(parts:tuple[str,str,str,str,str],
                sections:Dict[str,List[Tuple[str,int,int,str]]],
                echo:bool)->bool:
    subj,num,days,start,dur=parts
    code=f"{subj} {num}"
    days=days.upper()
    try:
        if not CRS_RE.fullmatch(subj) or not NUM_RE.fullmatch(num):
            raise ValueError("course code malformed (e.g. MEEN 221)")
        if not DAYS_RE.fullmatch(days):
            raise ValueError("days must be combo of MTWRF")
        if not TIME_RE.fullmatch(start):
            raise ValueError("start must be HH:MM 24-hour")
        if not dur.isdigit() or int(dur)<=0:
            raise ValueError("duration must be positive int")
        st=to_minutes(start)
    except ValueError as err:
        print(f"Error: {err}")
        return False
    sections.setdefault(code,[]).append((days,st,int(dur),code))
    if echo: print("Success!")
    return True

# ═════════════════════ main routine ═════════════════════════════════════
def main()->None:
    sections:Dict[str,List[Tuple[str,int,int,str]]]={}

    def_sheet=Path(__file__).with_name(DEFAULT_SHEET)
    if def_sheet.exists():
        try:
            for row in rows_from_file(def_sheet):
                add_section(row,sections,echo=False)
            print("[spreadsheet] sections loaded successfully")
        except Exception as e:
            print(f"[file-load error] {e}"); return
    else:
        path=input("Spreadsheet path (Enter to skip): ").strip()
        if path:
            try:
                for r in rows_from_file(path):
                    add_section(r,sections,echo=False)
                print("[spreadsheet] sections loaded successfully")
            except Exception as e:
                print(f"[file-load error] {e}"); return

    if not sections:
        print("\nEnter each section:"
              "\n  <SUBJ> <NUM|NUML> <DAYS> <HH:MM> <DURATION>"
              "\nType 'done' when finished.\n")
        while True:
            line=input("> ").strip()
            if line.lower()=="done": break
            parts=line.split()
            if len(parts)!=5:
                print("Error: expected exactly 5 fields."); continue
            add_section(tuple(parts),sections,echo=True)
    if not sections:
        print("\nNo data entered; nothing to compute."); return

    mandatory={c:lst[0] for c,lst in sections.items() if len(lst)==1}
    options  ={c:lst[:] for c,lst in sections.items() if len(lst)>1}

    busy={d:[] for d in DAY_LETTERS}
    busy_raw={d:[] for d in DAY_LETTERS}
    for days,st,dur,code in mandatory.values():
        iv=(st,st+dur)
        for d in days:
            busy[d].append(iv)
            busy_raw[d].append((iv[0],iv[1],code))
    busy={d:merge(v) for d,v in busy.items()}

    def windows_after_add(cand,grid):
        tmp=deepcopy(grid)
        days,st,dur,_=cand
        iv=(st,st+dur)
        for d in days:
            tmp[d].append(iv)
            tmp[d]=merge(tmp[d])
        return sum(len(free_and_min_conflict(tmp[d])[0]) for d in DAY_LETTERS)

    chosen={}
    while options:
        best_course=None
        best_sec=None
        best_score=-1
        tied=[]
        for course,lst in options.items():
            for cand in lst:
                score=windows_after_add(cand,busy)
                if score>best_score or best_sec is None:
                    best_course,best_sec,best_score=course,cand,score
                    tied=[(course,cand,score)]
                elif score==best_score:
                    tied.append((course,cand,score))
                    if cand[1]<best_sec[1]:
                        best_course,best_sec=course,cand
        chosen[best_course]=best_sec
        days,st,dur,code=best_sec
        iv=(st,st+dur)
        for d in days:
            busy[d].append(iv)
            busy_raw[d].append((iv[0],iv[1],code))
        busy={d:merge(v) for d,v in busy.items()}
        del options[best_course]

    free_any=False
    free_by_day,best_by_day={},{}
    for d in DAY_LETTERS:
        free,best,cnt=free_and_min_conflict(busy[d])  # ok for "free" detection
        free_by_day[d]=free
        best_by_day[d]=(best,cnt)  # kept for reference; not used for min-by-class output
        if free: free_any=True

    print("\n100-minute meeting blocks (start 08:00-16:10):")
    if free_any:
        for d in DAY_LETTERS:
            if free_by_day[d]:
                print(f"{DAY_NAMES[d]}:")
                for s,e in free_by_day[d]:
                    print(f"  {to_hhmm(s)} – {to_hhmm(e)}")
    else:
        # Use labeled grid to compute the TRUE minimum # of conflicting classes.
        for d in DAY_LETTERS:
            best_starts, mincnt = min_conflict_by_labels(busy_raw[d])
            if not best_starts:
                continue

            # Group only those starts that have exactly that minimum conflict count
            groups: Dict[Tuple[str, ...], Dict[str, object]] = {}
            for s in best_starts:
                hits = offenders_for_start(s, busy_raw[d])  # [(code, s, e), ...]
                if len(hits) != mincnt:
                    continue  # safety: should already match
                codes = tuple(sorted(code for code,_,_ in hits))
                if codes not in groups:
                    info = {code:(ss,ee,ee-ss) for code,ss,ee in hits}
                    groups[codes] = {"starts": [], "info": info}
                groups[codes]["starts"].append(s)

            print(f"{DAY_NAMES[d]}: minimum conflicts = {mincnt}")
            for codes in sorted(groups.keys()):
                data = groups[codes]
                starts_str = ", ".join(to_hhmm(s) for s in sorted(set(data["starts"])))
                if len(codes)==1:
                    code=codes[0]
                    ss,ee,dur=data["info"][code]
                    print(f"  Least conflict class: {code} (start {to_hhmm(ss)}, {dur} min)")
                else:
                    print("  Least conflict classes:")
                    for code in codes:
                        ss,ee,dur=data["info"][code]
                        print(f"    • {code} (start {to_hhmm(ss)}, {dur} min)")
                print(f"  Minimum conflict start times: {starts_str}")

# ═════════════════════════════════════════════════════════════════════════
if __name__=="__main__":
    main()
