import re, subprocess, zipfile
from pathlib import Path
root=Path("release-assets/inspect")
root.mkdir(parents=True,exist_ok=True)
subprocess.run(["gh","release","download","v3.3.5","--repo","chrisbrook0127/CaseNumberOCR-Updates","--pattern","CaseNumberOCR_Update.zip","--dir",str(root)],check=True)
zp=root/"CaseNumberOCR_Update.zip"
report=[]
with zipfile.ZipFile(zp) as z:
 report.append("ZIP CONTENTS")
 for i in z.infolist(): report.append(f"{i.filename} | {i.file_size}")
 py_names=[n for n in z.namelist() if n.endswith(".py")]
 for name in py_names:
  data=z.read(name).decode("utf-8",errors="replace")
  report += ["",f"===== SOURCE: {name} ({len(data.splitlines())} lines) ====="]
  lines=data.splitlines()
  patterns=re.compile(r"select files|completed|auto.?confirm|threshold|95|update learning|finish batch|train|learning|review files|scan|pause|resume|restart|stop|folder|200|queue|batch|case.number|candidate",re.I)
  selected=set()
  for i,line in enumerate(lines):
   if patterns.search(line):
    for j in range(max(0,i-2),min(len(lines),i+3)): selected.add(j)
  # Keep source map compact enough to inspect, prioritizing relevant functions and UI definitions.
  ordered=sorted(selected)
  last=-2
  for i in ordered:
   if i>last+1: report.append(f"... lines {i+1}-{i+1} ...")
   report.append(f"{i+1}: {lines[i][:300]}")
   last=i
  # Also report class/function signatures throughout source.
  report += ["", "FUNCTION/CLASS MAP"]
  for i,line in enumerate(lines):
   if re.match(r"\s*(class |def |async def )",line):
    report.append(f"{i+1}: {line.strip()}")
(root/"source_report.txt").write_text("\n".join(report),encoding="utf-8")
print(f"Wrote report from {len(py_names)} Python files; ZIP bytes={zp.stat().st_size}; report lines={len(report)}")
