"""Evaluation failures remain visible instead of becoming zero scores or hidden retries."""
import asyncio
import unittest
from unittest.mock import Mock

from evals.ragas.report import summarize, used_fallback
from evals.ragas.run import compare_modes
from evals.ragas.scoring import score_answer


class FailingMetric:
    async def ascore(self, **kwargs):
        raise TypeError('judge returned invalid output')


class EvaluationTests(unittest.TestCase):
    def test_fallback_counts_exclude_empty_and_unknown_results(self):
        self.assertTrue(used_fallback({'ranking_stage': 'retrieval', 'before_rerank': ['one']}))
        self.assertFalse(used_fallback({'ranking_stage': 'retrieval', 'before_rerank': []}))
        self.assertFalse(used_fallback({'ranking_stage': 'reranked', 'before_rerank': ['one']}))
        self.assertFalse(used_fallback({}))

    def test_mode_comparison_preserves_filters_and_ranking_details(self):
        client = Mock()
        client.post.return_value.status_code = 200
        client.post.return_value.json.return_value = {
            'results': [], 'ranking_stage': 'retrieval',
            'before_rerank': [], 'after_rerank': [],
        }
        comparison = compare_modes(client, {'question': 'permits', 'filters': {'year': 2023}})
        self.assertEqual(set(comparison), {'lexical', 'semantic', 'hybrid'})
        for call in client.post.call_args_list:
            self.assertEqual(call.kwargs['json']['filters'], {'year': 2023})
        self.assertEqual(comparison['hybrid']['ranking_stage'], 'retrieval')

    def test_judge_failure_has_no_numeric_score(self):
        scores, errors = asyncio.run(score_answer(
            {'faithfulness': FailingMetric()}, 'question', 'answer', 'reference', ['context'],
        ))
        self.assertIsNone(scores['faithfulness'])
        self.assertEqual(errors['faithfulness'], 'TypeError')

    def test_retry_does_not_replace_first_attempt_failure(self):
        first = {'http_status': 502, 'response': {'status': 'error'},
                 'status_matches': False, 'elapsed_seconds': 1.0,
                 'retrieval': {'ranking_stage': 'retrieval', 'before_rerank': ['one']}}
        retry = {'http_status': 200, 'response': {'status': 'answered'}, 'status_matches': True,
                 'document_rank': 1, 'citation_valid': True}
        results = {'cases': [{'expected_status': 'answered', 'attempts': [first, retry]}],
                   'metrics': ['faithfulness'], 'mode_comparison': {}}
        summary = summarize(results)
        self.assertEqual(summary['first_attempt_errors'], 1)
        self.assertEqual(summary['chat_rerank_fallbacks'], 1)
        self.assertEqual(summary['document_hit_at_5'], 0)
        self.assertEqual(summary['ragas']['faithfulness']['count'], 0)


if __name__ == '__main__':
    unittest.main()
