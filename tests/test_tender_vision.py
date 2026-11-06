import json
from pathlib import Path

import fitz
import pytest

from scripts.tender_acquisition import vision


def two_page_pdf():
    document = fitz.open()
    for name in ('SOCIETE ATLAS SARL', 'SOCIETE RIF SA'):
        page = document.new_page()
        page.insert_text((72, 72), name)
    data = document.tobytes()
    document.close()
    return data


def test_structured_names_reject_uncertain_entries():
    raw = json.dumps({'legible': True, 'companies': [
        {'name': 'SOCIETE ATLAS SARL', 'certain': True},
        {'name': 'SOCIETE ATLAS SARL', 'certain': True},
        {'name': 'Maybe SARL', 'certain': False},
    ]})
    assert vision.validate_output(raw) == ['SOCIETE ATLAS SARL']
    with pytest.raises(ValueError):
        vision.validate_output('{"companies": [], "legible": true, "extra": 1}')


def test_all_pdf_pages_are_rendered_and_deleted(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    calls = []

    def fake_batch(images, directory):
        calls.append(len(images))
        assert all(Path(image).exists() for image in images)
        return ['SOCIETE ATLAS SARL', 'SOCIETE RIF SA']

    monkeypatch.setattr(vision, 'codex_batch', fake_batch)
    assert vision.extract_names(two_page_pdf()) == ['SOCIETE ATLAS SARL', 'SOCIETE RIF SA']
    assert calls == [2]
    assert list((tmp_path / '.scratch').iterdir()) == []


def test_agent_failure_removes_images(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def fail(*args):
        raise RuntimeError('agent unavailable')

    monkeypatch.setattr(vision, 'codex_batch', fail)
    with pytest.raises(RuntimeError):
        vision.extract_names(two_page_pdf())
    assert list((tmp_path / '.scratch').iterdir()) == []
