from pathlib import Path
import base64,hashlib,zipfile
ROOT=Path(__file__).resolve().parents[1]
app=ROOT/'app';out=ROOT/'dist';out.mkdir(exist_ok=True)
version='5.6.4'
expected={n:hashlib.sha256((app/n).read_bytes()).hexdigest() for n in ('case_number_ocr.py','updater_apply.py')}
(app/'verify_package.py').write_text('from pathlib import Path\nimport hashlib\nROOT=Path(__file__).resolve().parent\nEXPECTED='+repr(expected)+'\nfor name,expected in EXPECTED.items():\n    assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==expected, name\nprint("PASS: V5.6.4 application and updater integrity")\n')
# Stable archive metadata makes local and published hashes reproducible.
def archive(path,items):
    with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for file,name in sorted(items,key=lambda x:x[1]):
            info=zipfile.ZipInfo(name,(2026,11,17,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o644<<16
            z.writestr(info,file.read_bytes())
update_names=('case_number_ocr.py','updater_apply.py','update_config.json','RUN_CASE_OCR.bat','Launch_Case_OCR.vbs','MF.ico','CHANGELOG_V5_6_4.txt')
update=out/'CaseNumberOCR_Update.zip'
archive(update,[(app/n,n) for n in update_names])
folder='MFCase_Number_OCR_V5.6.4_Full_Online_Install'
archive(out/(folder+'.zip'),[(p,folder+'/'+p.name) for p in app.iterdir() if p.is_file()])
release=ROOT/'release-assets';release.mkdir(exist_ok=True)
(release/'update.base64').write_text(base64.b64encode(update.read_bytes()).decode())
(release/'SHA256.txt').write_text(hashlib.sha256(update.read_bytes()).hexdigest()+'\n')
(release/'RELEASE_TAG.txt').write_text('v'+version+'\n')
print('Built V'+version+' update and full installer; SHA256 '+hashlib.sha256(update.read_bytes()).hexdigest())
