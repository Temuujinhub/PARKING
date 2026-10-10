import base64
from types import SimpleNamespace
import subprocess

import pytest
from fastapi import HTTPException
from app.services import exit_review_ocr as O


def test_disabled_trial_never_starts_a_process(monkeypatch):
    monkeypatch.setattr(O.settings,'exit_review_ocr_enabled',False)
    monkeypatch.setattr(O.subprocess,'run',lambda *a,**k:pytest.fail('must not start'))
    with pytest.raises(HTTPException) as e: O.recognize({})
    assert e.value.status_code==503


def test_original_crop_is_used_with_bounded_local_process(monkeypatch):
    import io
    out=io.BytesIO();O.Image.new('RGB',(150,40),'white').save(out,format='JPEG');data=out.getvalue()
    monkeypatch.setattr(O.settings,'exit_review_ocr_enabled',True)
    monkeypatch.setattr(O.shutil,'which',lambda name:'/test/tesseract')
    monkeypatch.setattr(O,'valid_jpeg',lambda image:image==data)
    def run(args,**kw):
        assert args[0]=='/test/tesseract' and args[2:6] == ['stdout','-l','mon+eng','--psm']
        assert args[6] in ('7','13') and 'load_system_dawg=0' in args and args[-1]=='tsv'
        with O.Image.open(args[1]) as image: assert image.height>40
        assert kw['timeout']==8 and not kw.get('shell')
        return SimpleNamespace(stdout=b'level\tpage_num\tblock_num\tpar_num\tline_num\tconf\ttext\n5\t1\t1\t1\t1\t91\t1234\n5\t1\t1\t1\t1\t88\tABC\n')
    monkeypatch.setattr(O.subprocess,'run',run)
    r=O.recognize({'Picture':{'CutoutPic':{'Content':base64.b64encode(data).decode()}}})
    assert r['suggestions'][0]['text']=='1234 ABC' and r['suggestions'][0]['confidence']==88
    assert r['suggestions'][0]['variants']==['original_crop','contrast_crop']
    assert not r['suggestions'][0]['valid_format'] and r['independent_frames']==1
    assert r['source']=='event_plate_crop' and not r['automatic_decision']


def test_missing_original_never_fetches_current_camera_frame(monkeypatch):
    monkeypatch.setattr(O.settings,'exit_review_ocr_enabled',True)
    monkeypatch.setattr(O.shutil,'which',lambda name:'/test/tesseract')
    with pytest.raises(HTTPException) as e: O.recognize({})
    assert e.value.status_code==404


def test_timeout_releases_worker(monkeypatch):
    monkeypatch.setattr(O.settings,'exit_review_ocr_enabled',True)
    monkeypatch.setattr(O.shutil,'which',lambda name:'/test/tesseract')
    monkeypatch.setattr(O,'original_image',lambda raw:(b'test','event_frame'))
    monkeypatch.setattr(O,'ocr_inputs',lambda *args:[(b'test','original_frame','11')])
    def timeout(*args,**kw):raise subprocess.TimeoutExpired('test',8)
    monkeypatch.setattr(O.subprocess,'run',timeout)
    for _ in range(2):
        with pytest.raises(HTTPException) as e: O.recognize({})
        assert e.value.status_code==502
