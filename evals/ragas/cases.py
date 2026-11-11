"""Reviewed evaluation questions and source checks."""
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from app.rag.ingest import INDEX_PATH

CASE_PATH = Path(__file__).with_name('cases.json')


def load_cases() -> list[dict]:
    cases = json.loads(CASE_PATH.read_text())
    identifiers = [case['id'] for case in cases]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError('Evaluation case IDs must be unique')

    with closing(sqlite3.connect(f'file:{INDEX_PATH}?mode=ro', uri=True)) as database:
        for case in cases:
            if case['expected_status'] != 'answered':
                continue
            row = database.execute(
                'SELECT document_id, start_page, end_page, text FROM chunks WHERE chunk_id = ?',
                (case['expected_chunk_id'],),
            ).fetchone()
            if row is None:
                raise ValueError(f"Missing reference chunk for {case['id']}")
            document_id, start_page, end_page, text = row
            locator = str(start_page) if start_page == end_page else f'{start_page}-{end_page}'
            if (document_id != case['expected_document_id'] or locator != case['expected_locator']
                    or case['source_phrase'] not in text or not case['reference_answer'].strip()):
                raise ValueError(f"Reference source does not match the index for {case['id']}")
    return cases
