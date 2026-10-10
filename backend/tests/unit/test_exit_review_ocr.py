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
    data=b'controlled-test-image'
    monkeypatch.setattr(O.settings,'exit_review_ocr_enabled',True)
    monkeypatch.setattr(O.shutil,'which',lambda name:'/test/tesseract')
    monkeypatch.setattr(O,'valid_jpeg',lambda image:image==data)
    def run(args,**kw):
        assert args[0]=='/test/tesseract' and args[2:] == ['stdout','-l','mon+eng','--psm','7','tsv']
        assert O.Path(args[1]).read_bytes()==data
        assert kw['timeout']==8 and not kw.get('shell')
        return SimpleNamespace(stdout=b'level\tpage_num\tblock_num\tpar_num\tline_num\tconf\ttext\n5\t1\t1\t1\t1\t91\t1234\n5\t1\t1\t1\t1\t88\tABC\n')
    monkeypatch.setattr(O.subprocess,'run',run)
    r=O.recognize({'Picture':{'CutoutPic':{'Content':base64.b64encode(data).decode()}}})
    assert r['suggestions']==[{'text':'1234 ABC','confidence':88}]
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
    def timeout(*args,**kw):raise subprocess.TimeoutExpired('test',8)
    monkeypatch.setattr(O.subprocess,'run',timeout)
    for _ in range(2):
        with pytest.raises(HTTPException) as e: O.recognize({})
        assert e.value.status_code==502
