"""Capture the section 9 acceptance demonstration against a local API process."""
import csv
import hashlib
import json
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from langgraph.checkpoint.sqlite import SqliteSaver

from app.agents.chat import CHECKPOINT_PATH, build_chat_graph
from app.rag.ingest import INDEX_PATH, MANIFEST_PATH, PROJECT_DIR

EVIDENCE_DIR = PROJECT_DIR / 'evidence' / 'acceptance'
BASE_URL = 'http://127.0.0.1:8011'


def request_json(path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = Request(BASE_URL + path, data=data, headers={'Content-Type': 'application/json'})
    try:
        with urlopen(request, timeout=120) as response:
            return {'http_status': response.status, 'body': json.load(response)}
    except HTTPError as error:
        return {'http_status': error.code, 'body': json.load(error)}


def start_server(log_file):
    process = subprocess.Popen(
        [sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8011'],
        cwd=PROJECT_DIR, stdout=log_file, stderr=subprocess.STDOUT,
    )
    for _ in range(40):
        if process.poll() is not None:
            raise RuntimeError(f'API process exited with code {process.returncode}')
        try:
            if request_json('/health')['http_status'] == 200:
                return process
        except URLError:
            time.sleep(0.25)
    process.terminate()
    raise TimeoutError('Local acceptance API did not start')


def stop_server(process):
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def summarize_search(response):
    body = response['body']
    if response['http_status'] != 200:
        return response
    return {
        'http_status': 200, 'mode': body['mode'], 'score_type': body['score_type'],
        'score_direction': body['score_direction'], 'trace': body['trace'],
        'before_rerank': body['before_rerank'], 'after_rerank': body['after_rerank'],
        'results': [
            {key: hit[key] for key in ('chunk_id', 'document_id', 'title', 'locator', 'year', 'type', 'score')}
            for hit in body['results']
        ],
    }


def indexed_excerpt(chunk_id):
    with closing(sqlite3.connect(f'file:{INDEX_PATH}?mode=ro', uri=True)) as database:
        row = database.execute(
            'SELECT document_id, start_page, end_page, text FROM chunks WHERE chunk_id = ?', (chunk_id,)
        ).fetchone()
    if row is None:
        return None
    document_id, start_page, end_page, text = row
    locator = str(start_page) if start_page == end_page else f'{start_page}-{end_page}'
    return {'chunk_id': chunk_id, 'document_id': document_id, 'locator': locator, 'text': text}


def checkpoint_summary(thread_id):
    with closing(sqlite3.connect(CHECKPOINT_PATH)) as database:
        graph = build_chat_graph(SqliteSaver(database))
        snapshot = graph.get_state({'configurable': {'thread_id': thread_id}})
    return {'state_keys': sorted(snapshot.values), 'pending_interrupts': [item.id for item in snapshot.interrupts]}


def main():
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    questions = json.loads((EVIDENCE_DIR / 'questions.json').read_text())
    run_id = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    results = {'run_id': run_id, 'started_utc': datetime.now(timezone.utc).isoformat()}

    test_run = subprocess.run(
        [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-q'],
        cwd=PROJECT_DIR, capture_output=True, text=True, timeout=120,
    )
    (EVIDENCE_DIR / 'unit_tests.txt').write_text(test_run.stdout + test_run.stderr)
    results['unit_tests_passed'] = test_run.returncode == 0

    with MANIFEST_PATH.open(newline='') as file:
        manifest = list(csv.DictReader(file))
    html = (PROJECT_DIR / 'data/evidence/roads_and_transport_filtered_results.html').read_text()
    results['corpus'] = {
        'manifest_count': len(manifest),
        'unique_document_ids': len({row['document_id'] for row in manifest}),
        'all_category_and_language_match': all(
            row['primary_category'] == 'Roads and Transport' and row['language'] == 'en' for row in manifest
        ),
        'pdf_hashes_match': all(
            hashlib.sha256((PROJECT_DIR / 'data' / row['local_file']).read_bytes()).hexdigest() == row['sha256']
            for row in manifest
        ),
        'portal_primary_filter_saved': 'selected="selected" value="25">Roads and Transport' in html,
    }
    results['parsing_samples'] = [indexed_excerpt(question['expected_chunk_id']) for question in questions[:3]]
    results['question_sources_valid'] = [
        {
            'id': question['id'],
            'matches_index': bool(
                (excerpt := indexed_excerpt(question['expected_chunk_id']))
                and excerpt['document_id'] == question['expected_document_id']
                and excerpt['locator'] == question['expected_locator']
                and question['source_phrase'] in excerpt['text']
            ),
        }
        for question in questions
    ]

    with (EVIDENCE_DIR / 'server.log').open('w') as log_file:
        server = start_server(log_file)
        try:
            results['first_server_pid'] = server.pid
            results['health'] = request_json('/health')
            results['openapi'] = request_json('/openapi.json')['body']
            results['ingestion'] = [request_json('/ingest', {}), request_json('/ingest', {})]

            comparison_query = questions[0]['question']
            results['search_modes'] = {
                mode: summarize_search(request_json('/search', {
                    'query': comparison_query, 'mode': mode, 'top_k': 10, 'filters': {},
                }))
                for mode in ('lexical', 'semantic', 'hybrid')
            }

            filter_cases = {
                'year': {'year': 2023}, 'type': {'type': 'Law'},
                'combined': {'year': 2023, 'type': 'Law'},
                'no_match': {'year': 1900, 'type': 'Law'},
            }
            results['filters'] = {}
            for mode in ('lexical', 'semantic', 'hybrid'):
                for label, filters in filter_cases.items():
                    response = summarize_search(request_json('/search', {
                        'query': 'vehicle permit', 'mode': mode, 'top_k': 5, 'filters': filters,
                    }))
                    if response['http_status'] == 502:
                        results.setdefault('filter_retries', {})[f'{mode}_{label}'] = response
                        response = summarize_search(request_json('/search', {
                            'query': 'vehicle permit', 'mode': mode, 'top_k': 5, 'filters': filters,
                        }))
                    if response['http_status'] == 200:
                        response['all_match'] = all(
                            (filters.get('year') is None or hit['year'] == filters['year'])
                            and (filters.get('type') is None or hit['type'] == filters['type'])
                            for hit in response['results']
                        )
                    results['filters'][f'{mode}_{label}'] = response

            supported = request_json('/chat', {
                'thread_id': f'at_supported_{run_id}', 'message': comparison_query,
                'mode': 'hybrid', 'top_k': 10, 'filters': {},
            })
            results['supported_answer'] = supported
            results['cited_excerpts'] = [
                indexed_excerpt(citation['chunk_id']) for citation in supported['body'].get('citations', [])
            ]

            thread_a = f'at_memory_a_{run_id}'
            thread_b = f'at_memory_b_{run_id}'
            results['memory'] = {
                'thread_a_initial': request_json('/chat', {
                    'thread_id': thread_a, 'message': 'What does Law No. (9) of 2023 regulate?',
                }),
                'thread_a_followup': request_json('/chat', {
                    'thread_id': thread_a, 'message': 'What insurance is required for those vehicles?',
                }),
                'thread_b_same_followup': request_json('/chat', {
                    'thread_id': thread_b, 'message': 'What insurance is required for those vehicles?',
                }),
            }

            restart_thread = f'at_restart_{run_id}'
            paused = request_json('/chat', {
                'thread_id': restart_thread, 'message': 'What insurance is required for those vehicles?',
            })
            results['restart'] = {'paused': paused, 'saved_state': checkpoint_summary(restart_thread)}
            stop_server(server)
            log_file.write(f'\n--- RESTART after PID {server.pid} ---\n')
            log_file.flush()
            server = start_server(log_file)
            results['restart']['new_server_pid'] = server.pid
            interrupt_id = paused['body'].get('interrupt_id')
            if interrupt_id:
                results['restart']['wrong_thread'] = request_json('/chat/resume', {
                    'thread_id': f'at_wrong_{run_id}', 'interrupt_id': interrupt_id,
                    'message': 'autonomous vehicles',
                })
                results['restart']['resumed'] = request_json('/chat/resume', {
                    'thread_id': restart_thread, 'interrupt_id': interrupt_id,
                    'message': 'autonomous vehicles under Law No. (9) of 2023',
                })
                results['restart']['repeated_resume'] = request_json('/chat/resume', {
                    'thread_id': restart_thread, 'interrupt_id': interrupt_id,
                    'message': 'autonomous vehicles',
                })

            results['abstention'] = request_json('/chat', {
                'thread_id': f'at_unsupported_{run_id}',
                'message': 'What were the Dubai vehicle permit fees in 1900?',
                'mode': 'lexical',
                'filters': {'year': 1900},
            })
            results['out_of_scope'] = request_json('/chat', {
                'thread_id': f'at_unrelated_{run_id}', 'message': 'How do I cook pasta?',
            })
            results['api_errors'] = {
                'empty_message': request_json('/chat', {'thread_id': f'at_bad_{run_id}', 'message': ' '}),
                'invalid_top_k': request_json('/chat', {'thread_id': f'at_bad_{run_id}', 'message': 'parking', 'top_k': 21}),
                'unsupported_mode': request_json('/search', {'query': 'parking', 'mode': 'unknown'}),
                'invalid_filter': request_json('/chat', {'thread_id': f'at_bad_{run_id}', 'message': 'parking', 'filters': {'year': '2023'}}),
                'invalid_resume': request_json('/chat/resume', {
                    'thread_id': f'at_bad_{run_id}', 'interrupt_id': 'missing', 'message': 'parking',
                }),
            }

            results['evaluation'] = []
            for question in questions:
                payload = {
                    'query': question['question'], 'mode': 'hybrid', 'top_k': 5, 'filters': {},
                }
                response = summarize_search(request_json('/search', payload))
                returned_ids = [hit['document_id'] for hit in response.get('results', [])]
                record = {
                    'id': question['id'], 'question': question['question'],
                    'expected_document_id': question['expected_document_id'],
                    'expected_locator': question['expected_locator'],
                    'expected_chunk_id': question['expected_chunk_id'],
                    'hit_in_final_top_5': question['expected_document_id'] in returned_ids,
                    'response': response,
                }
                if response['http_status'] == 502:
                    retry = summarize_search(request_json('/search', payload))
                    record['retry_after_model_failure'] = retry
                results['evaluation'].append(record)
        finally:
            if server.poll() is None:
                stop_server(server)

    log_lines = (EVIDENCE_DIR / 'server.log').read_text().splitlines()
    results['tool_and_middleware_trace'] = [
        line for line in log_lines if 'Excerpt tool called:' in line or 'Answer agent middleware:' in line
        or 'model-issued excerpt calls' in line
    ]
    results['hybrid_node_trace'] = [
        line for line in log_lines if any(label in line for label in (
            'Lexical node started', 'Lexical node completed', 'Semantic node started',
            'Semantic node completed', 'Join node started', 'Rerank node started',
        ))
    ]
    results['finished_utc'] = datetime.now(timezone.utc).isoformat()
    (EVIDENCE_DIR / 'results.json').write_text(json.dumps(results, indent=2, ensure_ascii=False) + '\n')
    print('Evidence written to', EVIDENCE_DIR / 'results.json')
    print('Evaluation hits:', sum(row['hit_in_final_top_5'] for row in results['evaluation']), '/ 10')


if __name__ == '__main__':
    main()
