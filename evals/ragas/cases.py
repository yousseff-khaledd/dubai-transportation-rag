"""Reviewed evaluation questions and source checks."""
import json
from pathlib import Path

from app.rag.store import chunk_payload

CASE_PATH = Path(__file__).with_name('cases.json')


def load_cases() -> list[dict]:
    cases = json.loads(CASE_PATH.read_text())
    identifiers = [case['id'] for case in cases]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError('Evaluation case IDs must be unique')

    for case in cases:
        if case['expected_status'] != 'answered':
            continue
        payload = chunk_payload(case['expected_chunk_id'])
        if payload is None:
            raise ValueError(f"Missing reference chunk for {case['id']}")
        locator = str(payload['start_page']) if payload['start_page'] == payload['end_page'] else f"{payload['start_page']}-{payload['end_page']}"
        if (payload['document_id'] != case['expected_document_id'] or locator != case['expected_locator']
                or case['source_phrase'] not in payload['text'] or not case['reference_answer'].strip()):
            raise ValueError(f"Reference source does not match the index for {case['id']}")
    return cases
