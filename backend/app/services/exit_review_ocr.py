"""Optional local OCR trial. Its output is never an entitlement or a gate decision."""
import base64
import csv
import io
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from fastapi import HTTPException

from ..config import settings
from .snapshot import _payload_picture, valid_jpeg

_worker = threading.BoundedSemaphore(1)


def original_image(raw):
    raw = raw if isinstance(raw, dict) else {}
    pic = raw.get('Picture') if isinstance(raw.get('Picture'), dict) else {}
    for node in (pic.get('CutoutPic'), raw.get('CutoutPic')):
        if not isinstance(node, dict) or not isinstance(node.get('Content'), str):
            continue
        try:
            data = base64.b64decode(node['Content'], validate=True)
        except (TypeError, ValueError):
            continue
        if valid_jpeg(data):
            return data, 'event_plate_crop'
    return _payload_picture(raw), 'event_frame'


def recognize(raw):
    if not settings.exit_review_ocr_enabled:
        raise HTTPException(503, 'Хоёр дахь танилтын туршилтыг сервер дээр идэвхжүүлээгүй байна')
    executable = shutil.which('tesseract')
    if not executable:
        raise HTTPException(503, 'Серверт Tesseract болон mon/eng хэлний загвар суулгах шаардлагатай')
    data, source = original_image(raw)
    if not data:
        raise HTTPException(404, 'Энэ event-ийн эх кадр/тайрдас хадгалагдаагүй. Одоогийн кадраар орлуулахгүй')
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(413, 'Туршилтын зураг хэт том байна')
    if not _worker.acquire(blocking=False):
        raise HTTPException(429, 'Өөр танилтын туршилт ажиллаж байна; түр хүлээнэ үү')
    try:
        with tempfile.TemporaryDirectory(prefix='parking-ocr-') as folder:
            path = Path(folder) / 'event.jpg'
            path.write_bytes(data)
            result = subprocess.run([executable, str(path), 'stdout', '-l', 'mon+eng',
                                     '--psm', '7' if source == 'event_plate_crop' else '11', 'tsv'],
                                    capture_output=True, timeout=8, check=True)
        lines = {}
        for row in csv.DictReader(io.StringIO(result.stdout.decode('utf-8', errors='replace')), delimiter='\t'):
            text = (row.get('text') or '').strip()
            if row.get('level') != '5' or not text:
                continue
            key = tuple(row.get(k) for k in ('page_num', 'block_num', 'par_num', 'line_num'))
            item = lines.setdefault(key, {'text': '', 'confidence': 100.0})
            item['text'] = (item['text'] + ' ' + text).strip()[:64]
            item['confidence'] = min(item['confidence'], max(0, min(100, float(row['conf']))))
        return {'engine': 'tesseract', 'source': source, 'suggestions': list(lines.values())[:20],
                'automatic_decision': False}
    except (subprocess.SubprocessError, OSError, ValueError, KeyError) as exc:
        raise HTTPException(502, 'Хоёр дахь танилт амжилтгүй; оператор эх зургийг тулгаж шалгана уу') from exc
    finally:
        _worker.release()
