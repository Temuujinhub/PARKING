"""Optional local OCR trial. Its output is never an entitlement or a gate decision."""
import base64
import csv
import io
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from PIL import Image, ImageOps

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


def ocr_inputs(data, source):
    """Use the camera's event-specific plate crop, not guessed frame coordinates.

    Different firmware bounding-box units are not interchangeable. Unsupported
    boxes are deliberately ignored. No synthetic image generation is used.
    """
    with Image.open(io.BytesIO(data)) as original:
        if original.width * original.height > 20_000_000:
            raise ValueError('image exceeds pixel budget')
        image = ImageOps.exif_transpose(original).convert('RGB')
        image.thumbnail((1920, 1080))
        if source == 'event_plate_crop':
            scale = min(3, 1600 / image.width, max(1, 96 / image.height))
            if scale > 1:
                image = image.resize((round(image.width * scale), round(image.height * scale)), Image.Resampling.LANCZOS)
            image = ImageOps.expand(image, border=10, fill='white')
        out = io.BytesIO(); image.save(out, format='PNG')
        inputs = [(out.getvalue(), 'original_crop' if source == 'event_plate_crop' else 'original_frame',
                   '7' if source == 'event_plate_crop' else '11')]
        if source == 'event_plate_crop':
            out = io.BytesIO()
            ImageOps.autocontrast(ImageOps.grayscale(image)).save(out, format='PNG')
            inputs.append((out.getvalue(), 'contrast_crop', '13'))
        return inputs


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
        deadline = time.monotonic() + 12
        suggestions = {}
        with tempfile.TemporaryDirectory(prefix='parking-ocr-') as folder:
            for image, variant, psm in ocr_inputs(data, source):
                path = Path(folder) / 'event.png'
                path.write_bytes(image)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError('OCR time budget exhausted')
                result = subprocess.run([executable, str(path), 'stdout', '-l', 'mon+eng',
                    '--psm', psm, '-c', 'load_system_dawg=0', '-c', 'load_freq_dawg=0', 'tsv'],
                    capture_output=True, timeout=min(8, remaining), check=True)
                lines = {}
                for row in csv.DictReader(io.StringIO(result.stdout.decode('utf-8', errors='replace')), delimiter='\t'):
                    text = (row.get('text') or '').strip()
                    if row.get('level') != '5' or not text:
                        continue
                    key = tuple(row.get(k) for k in ('page_num', 'block_num', 'par_num', 'line_num'))
                    item = lines.setdefault(key, {'text': '', 'confidence': 100.0})
                    item['text'] = (item['text'] + ' ' + text).strip()[:64]
                    item['confidence'] = min(item['confidence'], max(0, min(100, float(row['conf']))))
                from ..session_logic import normalize_plate, is_valid_plate
                for item in lines.values():
                    plate = normalize_plate(item['text'])
                    item.update(normalized_plate=plate, valid_format=is_valid_plate(plate), variants=[variant])
                    if plate in suggestions:
                        suggestions[plate]['variants'].append(variant)
                        suggestions[plate]['confidence'] = min(suggestions[plate]['confidence'], item['confidence'])
                    else:
                        suggestions[plate] = item
        from .camera_evidence import camera_evidence
        return {'engine': 'tesseract', 'source': source, 'suggestions': list(suggestions.values())[:20],
                'camera_evidence': camera_evidence(raw), 'independent_frames': 1,
                'automatic_decision': False}
    except (subprocess.SubprocessError, OSError, ValueError, KeyError, Image.DecompressionBombError) as exc:
        raise HTTPException(502, 'Хоёр дахь танилт амжилтгүй; оператор эх зургийг тулгаж шалгана уу') from exc
    finally:
        _worker.release()
