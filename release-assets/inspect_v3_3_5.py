import subprocess,zipfile
from pathlib import Path
root=Path("release-assets/inspect"); root.mkdir(parents=True,exist_ok=True)
zp=root/"CaseNumberOCR_Update.zip"
if not zp.exists():
 subprocess.run(["gh","release","download","v3.3.5","--repo","chrisbrook0127/CaseNumberOCR-Updates","--pattern","CaseNumberOCR_Update.zip","--dir",str(root)],check=True)
with zipfile.ZipFile(zp) as z:
 s=z.read("case_number_ocr.py").decode("utf-8",errors="replace").splitlines()
ranges=[(33,250),(661,1165),(1275,1678),(1679,1837),(2498,2670),(3095,3177),(3200,3388)]
out=[]
for a,b in ranges:
 out += [f"===== ORIGINAL SOURCE LINES {a}-{b} ====="]
 out += [f"{i+1}: {s[i]}" for i in range(a-1,min(b,len(s)))]
(root/"source_excerpts.txt").write_text("\n".join(out),encoding="utf-8")
print(f"Extracted {len(out)} lines from {len(s)}-line app.")
