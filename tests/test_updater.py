import sys,tempfile,unittest,zipfile,shutil
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
import updater_apply as updater

class Updater(unittest.TestCase):
    def archive(self,root):
        archive=root/'update.zip'
        with zipfile.ZipFile(archive,'w') as z:
            for name in ('case_number_ocr.py','updater_apply.py','update_config.json'):
                z.write(ROOT/'app'/name,name)
        return archive
    def test_update_preserves_documents_and_learning(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);app=root/'app';app.mkdir()
            (app/'case_number_ocr.py').write_text('old source')
            (app/'private.pdf').write_bytes(b'original document')
            (app/'.case_ocr_learning').mkdir()
            (app/'.case_ocr_learning'/'example.json').write_text('private training')
            updater.apply_update(app,self.archive(root))
            self.assertEqual((app/'case_number_ocr.py').read_bytes(),(ROOT/'app/case_number_ocr.py').read_bytes())
            self.assertEqual((app/'private.pdf').read_bytes(),b'original document')
            self.assertEqual((app/'.case_ocr_learning/example.json').read_text(),'private training')
    def test_unsafe_archive_rejected_before_changes(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);app=root/'app';app.mkdir();(app/'case_number_ocr.py').write_text('old')
            archive=self.archive(root)
            with zipfile.ZipFile(archive,'a') as z:z.writestr('../outside.txt','unsafe')
            with self.assertRaises(RuntimeError):updater.apply_update(app,archive)
            self.assertEqual((app/'case_number_ocr.py').read_text(),'old')
            self.assertFalse((root/'outside.txt').exists())
    def test_failed_replacement_rolls_back(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);app=root/'app';app.mkdir()
            for name in ('case_number_ocr.py','updater_apply.py'):(app/name).write_text('original '+name)
            original=updater.os.replace;failed=[False]
            def replace(source,target):
                if Path(target).name=='updater_apply.py' and not failed[0]:
                    failed[0]=True;raise OSError('simulated failure')
                return original(source,target)
            with patch.object(updater.os,'replace',side_effect=replace):
                with self.assertRaises(OSError):updater.apply_update(app,self.archive(root))
            self.assertEqual((app/'case_number_ocr.py').read_text(),'original case_number_ocr.py')
            self.assertEqual((app/'updater_apply.py').read_text(),'original updater_apply.py')

if __name__=='__main__':unittest.main()
