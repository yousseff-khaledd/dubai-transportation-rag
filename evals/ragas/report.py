"""Aggregate per-case evaluation results and render a readable report."""
import statistics

from evals.ragas.scoring import ALL_METRICS


def summarize(results: dict) -> dict:
    answered_cases = [item for item in results['cases'] if item['expected_status'] == 'answered']
    first_attempts = [item['attempts'][0] for item in results['cases']]
    scores = {name: [attempt.get('ragas_scores', {}).get(name) for attempt in first_attempts]
              for name in results['metrics']}
    modes = results['mode_comparison']
    return {
        'status_matches': sum(attempt['status_matches'] for attempt in first_attempts),
        'case_count': len(first_attempts),
        'first_attempt_errors': sum(attempt['http_status'] >= 500 for attempt in first_attempts),
        'answered_first_attempt': sum(attempt['response'].get('status') == 'answered' for attempt in first_attempts),
        'document_hit_at_5': sum(item['attempts'][0].get('document_rank') is not None for item in answered_cases),
        'citation_valid': sum(item['attempts'][0].get('citation_valid', False) for item in answered_cases),
        'answered_case_count': len(answered_cases),
        'chat_latency_seconds': {
            'mean': round(statistics.mean(attempt['elapsed_seconds'] for attempt in first_attempts), 3),
            'median': round(statistics.median(attempt['elapsed_seconds'] for attempt in first_attempts), 3),
        },
        'mode_document_hit_at_5': {
            mode: sum(row[mode]['document_rank'] is not None for row in modes.values())
            for mode in ('lexical', 'semantic', 'hybrid')
        } if modes else {},
        'mode_errors': {
            mode: sum(row[mode]['http_status'] >= 500 for row in modes.values())
            for mode in ('lexical', 'semantic', 'hybrid')
        },
        'ragas': {
            name: {
                'count': len(valid := [value for value in values if value is not None]),
                'mean': round(statistics.mean(valid), 4) if valid else None,
                'median': round(statistics.median(valid), 4) if valid else None,
            }
            for name, values in scores.items()
        },
    }


def report_text(results: dict) -> str:
    summary = results['summary']
    lines = [
        '# Ragas evaluation', '',
        f"Run: `{results['run_id']}`. Corpus: {results['document_count']} documents, {results['chunk_count']} chunks. "
        f"Chat model: `{results['chat_model']}`; embedding model: `{results['embedding_model']}`; "
        f"Ragas: `{results['ragas_version']}`; final top_k: {results['top_k']}.", '',
        '## First-attempt results', '',
        f"- Expected status: {summary['status_matches']}/{summary['case_count']} cases.",
        f"- API/server errors: {summary['first_attempt_errors']} cases.",
        f"- Expected document in selected chat top 5: {summary['document_hit_at_5']}/{summary['answered_case_count']} answer cases.",
        f"- Valid citations to selected chunks: {summary['citation_valid']}/{summary['answered_case_count']} answer cases.",
        f"- Chat request elapsed time: mean {summary['chat_latency_seconds']['mean']}s, "
        f"median {summary['chat_latency_seconds']['median']}s (local API and model calls, excluding Ragas judging).",
        f"- Follow-up continuity and thread isolation: "
        f"{'skipped' if results['followup']['continuity_passed'] is None else ('pass' if results['followup']['continuity_passed'] else 'fail')}.",
        '', '## Ragas scores', '',
        '| Metric | Scored cases | Mean | Median |', '| --- | ---: | ---: | ---: |',
    ]
    for name, values in summary['ragas'].items():
        lines.append(f"| {name} | {values['count']} | {values['mean'] if values['mean'] is not None else '-'} | "
                     f"{values['median'] if values['median'] is not None else '-'} |")
    lines += [
        '', 'Scores are judge estimates, not search probabilities. Unanswered and failed requests have no Ragas score; '
        'the denominator for each mean is shown above. Review individual cases before acting on a small difference.',
        '', '## Search mode comparison', '',
        '| Mode | Expected document in final top 5 | Errors |', '| --- | ---: | ---: |',
    ]
    for mode, count in summary['mode_document_hit_at_5'].items():
        lines.append(f"| {mode} | {count}/{len(results['mode_comparison'])} | {summary['mode_errors'][mode]} |")
    if not summary['mode_document_hit_at_5']:
        lines.append('| Not run | - | - |')
    lines += [
        '', 'These mode calls use the same original question. Chat may rewrite the question; its Ragas contexts come '
        'from the actual checkpointed chat evidence, not from these separate search calls.',
        '', '## Cases', '',
        '| ID | Expected / actual first status | Document rank | Faithfulness | Answer correctness | Context precision | Context recall | Answer relevancy |',
        '| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |',
    ]
    for item in results['cases']:
        attempt = item['attempts'][0]
        scores = attempt.get('ragas_scores', {})
        formatted = [f'{scores[name]:.3f}' if scores.get(name) is not None else '-' for name in ALL_METRICS]
        lines.append('| ' + ' | '.join([
            item['id'], f"{item['expected_status']} / {attempt['response'].get('status')}",
            str(attempt.get('document_rank') or '-'), *formatted,
        ]) + ' |')
    failures = [item for item in results['cases'] if not item['attempts'][0]['status_matches']
                or item['attempts'][0].get('metric_errors')]
    lines += ['', '## Failures and judge errors', '']
    if not failures:
        lines.append('None in this run.')
    for item in failures:
        attempt = item['attempts'][0]
        retry = item['attempts'][1] if len(item['attempts']) > 1 else None
        lines.append(f"- **{item['id']}**: first HTTP {attempt['http_status']}, "
                     f"status `{attempt['response'].get('status')}`; metric errors "
                     f"`{attempt.get('metric_errors', {})}`. "
                     + (f"Retry HTTP {retry['http_status']}, status `{retry['response'].get('status')}`."
                        if retry else ''))
    scored_cases = sorted(
        (item for item in results['cases']
         if item['attempts'][0].get('ragas_scores', {}).get('answer_correctness') is not None),
        key=lambda item: item['attempts'][0]['ragas_scores']['answer_correctness'],
    )[:3]
    lines += ['', '## Lowest answer-correctness scores for review', '']
    for item in scored_cases:
        attempt = item['attempts'][0]
        lines += [
            f"### {item['id']} - {attempt['ragas_scores']['answer_correctness']:.3f}", '',
            f"- Question: {item['question']}",
            f"- Reference: {item['reference_answer']}",
            f"- Answer: {attempt['response']['answer']}",
            f"- Selected chunks: {', '.join(hit['chunk_id'] for hit in attempt['evidence'])}", '',
        ]
    lines += [
        '', '## Interpretation', '',
        'The ten source-grounded questions are a small, corpus-derived set. Human review of low-scoring examples is '
        'required: an LLM judge can misread legal exceptions or table labels, especially when it uses the same model '
        'family as the answerer. Do not treat these averages as accuracy on unseen questions. `results.json` retains '
        'each answer, selected chunk text, citation, model error, and score for audit.', '',
    ]
    return '\n'.join(lines)


