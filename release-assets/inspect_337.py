import subprocess,zipfile,re
from pathlib import Path
root=Path("release-assets/inspect337");root.mkdir(parents=True,exist_ok=True)
subprocess.run(["gh","release","download","v3.3.6","--repo","chrisbrook0127/CaseNumberOCR-Updates","--pattern","CaseNumberOCR_Update.zip","--dir",str(root)],check=True)
z=zipfile.ZipFile(root/"CaseNumberOCR_Update.zip")
src=z.read("case_number_ocr.py").decode("utf-8").splitlines()
names=["_normalized_training_type","_sync_learning_type_databases","_save_learning_example","_load_learning_data"]
out=[]
for name in names:
 matches=[i for i,l in enumerate(src) if re.match(rf"\s*(?:@staticmethod\s*)?$",l) is None and re.search(rf"\bdef {name}\s*\(",l)]
 out.append(f"\n==== {name} matches: {matches} ====")
 for i in matches:
  for j in range(max(0,i-3),min(len(src),i+12)):
   out.append(f"{j+1}: {src[j]}")
out.append("\n==== CALLS ====")
for i,l in enumerate(src):
 if "_normalized_training_type" in l:
  out.append(f"{i+1}: {l}")
(root/"normalized_type_report.txt").write_text("\n".join(out),encoding="utf-8")
print("\n".join(out))
