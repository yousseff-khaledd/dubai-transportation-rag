"""Run a small, repeatable Ragas evaluation without changing the API."""
import argparse
import asyncio
import json
import os
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from langgraph.checkpoint.sqlite import SqliteSaver

from app.agents.chat import CHECKPOINT_PATH, build_chat_graph
from app.api.app import app
from app.rag.ingest import INDEX_PATH
from evals.ragas.cases import load_cases
from evals.ragas.report import report_text, summarize
from evals.ragas.scoring import ALL_METRICS, CORE_METRICS, build_metrics, score_answer

RESULTS_DIR = Path(__file__).with_name('results')
TOP_K = 5


def saved_state(thread_id: str) -> dict:
    with closing(sqlite3.connect(CHECKPOINT_PATH)) as database:
        graph = build_chat_graph(SqliteSaver(database))
        return graph.get_state({'configurable': {'thread_id': thread_id}}).values


def chat_attempt(client: TestClient, case: dict, thread_id: str) -> dict:
    request = {
        'thread_id': thread_id, 'message': case['question'], 'mode': case.get('mode', 'hybrid'),
        'top_k': TOP_K, 'filters': case.get('filters', {}),
    }
    started = time.perf_counter()
    response = client.post('/chat', json=request)
    state = saved_state(thread_id)
    return {
        'http_status': response.status_code,
        'response': response.json(),
        'elapsed_seconds': round(time.perf_counter() - started, 3),
        'planned_query': state.get('plan', {}).get('search_query'),
        'evidence': [
            {key: hit[key] for key in ('chunk_id', 'document_id', 'locator', 'text')}
            for hit in state.get('evidence', [])
        ],
    }


def source_rank(case: dict, evidence: list[dict], field: str) -> int | None:
    expected = case.get('expected_' + field)
    return next((rank for rank, hit in enumerate(evidence, 1) if hit[field] == expected), None)


def citation_valid(attempt: dict) -> bool:
    citations = attempt['response'].get('citations', [])
    selected = {(hit['chunk_id'], hit['document_id'], hit['locator']) for hit in attempt['evidence']}
    return bool(citations) and all(
        (citation['chunk_id'], citation['document_id'], citation['locator']) in selected
        for citation in citations
    )


async def score_attempt(case: dict, attempt: dict, metrics: dict) -> None:
    response = attempt['response']
    attempt['status_matches'] = response.get('status') == case['expected_status']
    if case['expected_status'] != 'answered':
        return
    attempt['document_rank'] = source_rank(case, attempt['evidence'], 'document_id')
    attempt['chunk_rank'] = source_rank(case, attempt['evidence'], 'chunk_id')
    attempt['citation_valid'] = citation_valid(attempt) if response.get('status') == 'answered' else False
    if response.get('status') != 'answered' or not response.get('answer'):
        return
    contexts = [hit['text'] for hit in attempt['evidence']]
    attempt['ragas_scores'], attempt['metric_errors'] = await score_answer(
        metrics, attempt['planned_query'] or case['question'], response['answer'],
        case['reference_answer'], contexts,
    )


def compare_modes(client: TestClient, case: dict) -> dict:
    comparison = {}
    for mode in ('lexical', 'semantic', 'hybrid'):
        started = time.perf_counter()
        response = client.post('/search', json={'query': case['question'], 'mode': mode, 'top_k': TOP_K})
        body = response.json()
        hits = body.get('results', [])
        comparison[mode] = {
            'http_status': response.status_code,
            'elapsed_seconds': round(time.perf_counter() - started, 3),
            'document_rank': source_rank(case, hits, 'document_id'),
            'chunk_rank': source_rank(case, hits, 'chunk_id'),
            'chunk_ids': [hit['chunk_id'] for hit in hits],
            'document_ids': [hit['document_id'] for hit in hits],
            'trace': body.get('trace', []),
            'error': body.get('detail') if response.status_code != 200 else None,
        }
    return comparison


async def evaluate_followup(client: TestClient, run_id: str, metrics: dict, reference: dict) -> dict:
    thread_a = f'eval_{run_id}_followup_a'
    thread_b = f'eval_{run_id}_followup_b'
    initial = chat_attempt(client, {'question': 'What does Law No. (9) of 2023 regulate?'}, thread_a)
    followup_case = {
        'question': 'What insurance is required for those vehicles?',
        'expected_status': 'answered', 'reference_answer': reference['reference_answer'],
        'expected_document_id': reference['expected_document_id'],
        'expected_chunk_id': reference['expected_chunk_id'],
    }
    followup = chat_attempt(client, followup_case, thread_a)
    await score_attempt(followup_case, followup, metrics)
    isolated = chat_attempt(client, {'question': followup_case['question']}, thread_b)
    return {
        'initial': initial, 'thread_a_followup': followup, 'thread_b_same_question': isolated,
        'continuity_passed': (initial['response'].get('status') == 'answered'
                              and followup['response'].get('status') == 'answered'
                              and isolated['response'].get('status') == 'needs_clarification'),
    }


def save(output_dir: Path, results: dict) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / 'results.json').write_text(json.dumps(results, indent=2, ensure_ascii=False) + '\n')
    if 'summary' in results:
        (output_dir / 'report.md').write_text(report_text(results))


async def run_evaluation(args) -> None:
    cases = load_cases()
    if args.limit is not None:
        if args.limit < 1:
            raise ValueError('--limit must be positive')
        cases = cases[:args.limit]
    metric_names = CORE_METRICS if args.metrics == 'core' else ALL_METRICS
    client_model, metrics = build_metrics(metric_names)
    import ragas

    run_id = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    output_dir = RESULTS_DIR / run_id
    with closing(sqlite3.connect(f'file:{INDEX_PATH}?mode=ro', uri=True)) as database:
        document_count = database.execute('SELECT count(*) FROM documents').fetchone()[0]
        chunk_count = database.execute('SELECT count(*) FROM chunks').fetchone()[0]
    results = {
        'run_id': run_id, 'ragas_version': ragas.__version__, 'chat_model': os.getenv('CHAT_MODEL'),
        'embedding_model': os.getenv('EMBEDDING_MODEL'), 'document_count': document_count,
        'chunk_count': chunk_count, 'top_k': TOP_K, 'metrics': list(metric_names),
        'cases': [], 'mode_comparison': {},
    }
    try:
        with TestClient(app) as api:
            for case in cases:
                print(f"Evaluating {case['id']}...", flush=True)
                attempt = chat_attempt(api, case, f'eval_{run_id}_{case["id"]}_1')
                await score_attempt(case, attempt, metrics)
                attempts = [attempt]
                if attempt['http_status'] in (502, 504):
                    retry = chat_attempt(api, case, f'eval_{run_id}_{case["id"]}_2')
                    await score_attempt(case, retry, metrics)
                    attempts.append(retry)
                results['cases'].append({**case, 'attempts': attempts})
                save(output_dir, results)

            positive_cases = [case for case in cases if case['expected_status'] == 'answered']
            if not args.skip_modes:
                for case in positive_cases:
                    print(f"Comparing search modes for {case['id']}...", flush=True)
                    results['mode_comparison'][case['id']] = compare_modes(api, case)
                    save(output_dir, results)

            if len(cases) == len(load_cases()):
                reference = next(case for case in cases if case['id'] == 'Q02')
                print('Evaluating follow-up and thread isolation...', flush=True)
                results['followup'] = await evaluate_followup(api, run_id, metrics, reference)
            else:
                results['followup'] = {'continuity_passed': None, 'skipped': 'limited smoke run'}
    finally:
        await client_model.close()

    results['summary'] = summarize(results)
    save(output_dir, results)
    print(f'Wrote {output_dir / "report.md"}')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit', type=int, help='Run only the first N cases for a smoke test')
    parser.add_argument('--metrics', choices=('core', 'all'), default='all')
    parser.add_argument('--skip-modes', action='store_true', help='Skip the three-mode comparison')
    asyncio.run(run_evaluation(parser.parse_args()))


if __name__ == '__main__':
    main()
