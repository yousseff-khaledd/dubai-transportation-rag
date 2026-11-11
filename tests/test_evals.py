"""Evaluation failures remain visible instead of becoming zero scores or hidden retries."""
import asyncio
import unittest

from evals.ragas.report import summarize
from evals.ragas.scoring import score_answer


class FailingMetric:
    async def ascore(self, **kwargs):
        raise TypeError('judge returned invalid output')


class EvaluationTests(unittest.TestCase):
    def test_judge_failure_has_no_numeric_score(self):
        scores, errors = asyncio.run(score_answer(
            {'faithfulness': FailingMetric()}, 'question', 'answer', 'reference', ['context'],
        ))
        self.assertIsNone(scores['faithfulness'])
        self.assertEqual(errors['faithfulness'], 'TypeError')

    def test_retry_does_not_replace_first_attempt_failure(self):
        first = {'http_status': 502, 'response': {'status': 'error'},
                 'status_matches': False, 'elapsed_seconds': 1.0}
        retry = {'http_status': 200, 'response': {'status': 'answered'}, 'status_matches': True,
                 'document_rank': 1, 'citation_valid': True}
        results = {'cases': [{'expected_status': 'answered', 'attempts': [first, retry]}],
                   'metrics': ['faithfulness'], 'mode_comparison': {}}
        summary = summarize(results)
        self.assertEqual(summary['first_attempt_errors'], 1)
        self.assertEqual(summary['document_hit_at_5'], 0)
        self.assertEqual(summary['ragas']['faithfulness']['count'], 0)


if __name__ == '__main__':
    unittest.main()
