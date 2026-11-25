"""Reranker failures preserve usable retrieval evidence instead of failing chat."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from openai import APIConnectionError

from app.agents import retrieval_nodes
from app.main import app
from app.rag.search import SearchHit, SearchRequest, SearchResponse, SearchUnavailable


def candidate(chunk_id: str, score: float) -> SearchHit:
    return SearchHit(chunk_id=chunk_id, document_id='1525', title='Autonomous vehicles',
                     text='Operating a vehicle requires a permit.', source_url='', content_url='',
                     primary_category='Roads and Transport', year=2023, type='Law', status=None,
                     article_number='3', locator='2', score=score)


class RerankingTests(unittest.TestCase):
    def setUp(self):
        self.candidates = [candidate('first', 0.8), candidate('second', 0.7), candidate('third', 0.6)]
        self.state = {'request': SearchRequest(query='autonomous vehicle permits', top_k=2),
                      'candidates': self.candidates}
        self.environment = patch.dict('os.environ', {
            'OPENAI_BASE_URL': 'https://example.test/v1', 'OPENAI_API_KEY': 'test', 'CHAT_MODEL': 'test',
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.client_patch = patch.object(retrieval_nodes, 'OpenAI')
        self.client = self.client_patch.start()
        self.addCleanup(self.client_patch.stop)
        self.create = self.client.return_value.__enter__.return_value.chat.completions.create

    def response(self, content):
        self.create.return_value = SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=content))])

    def assert_retrieval_fallback(self, result):
        self.assertEqual(result['ranking_stage'], 'retrieval')
        self.assertEqual(result['results'], self.candidates[:2])
        self.assertEqual(result['after_rerank'], ['first', 'second', 'third'])

    def test_valid_ranking_reorders_and_keeps_scores(self):
        self.response(json.dumps({'ranking': [2, 0, 1]}))
        result = retrieval_nodes.rerank_candidates_node(self.state)
        self.assertEqual(result['ranking_stage'], 'reranked')
        self.assertEqual(result['results'], [self.candidates[2], self.candidates[0]])

    def test_invalid_rankings_keep_original_order_without_losing_candidates(self):
        for content in ['not JSON', None, '{}', 'null', '[]', '{"ranking": [0, 1]}',
                        '{"ranking": [0, 0, 2]}', '{"ranking": [0, 1, 3]}',
                        '{"ranking": [false, 1, 2]}', '{"ranking": ["0", 1, 2]}']:
            with self.subTest(content=content), self.assertLogs('uvicorn.error', level='WARNING'):
                self.response(content)
                self.assert_retrieval_fallback(retrieval_nodes.rerank_candidates_node(self.state))

    def test_provider_failure_falls_back_without_logging_provider_details(self):
        self.create.side_effect = APIConnectionError(message='private provider details',
                                                     request=httpx.Request('POST', 'https://example.test'))
        with self.assertLogs('uvicorn.error', level='WARNING') as logs:
            self.assert_retrieval_fallback(retrieval_nodes.rerank_candidates_node(self.state))
        self.assertIn('APIConnectionError', '\n'.join(logs.output))
        self.assertNotIn('private provider details', '\n'.join(logs.output))

    def test_search_api_reports_fallback_and_returns_evidence(self):
        self.response('{"ranking": [0, 0, 2]}')
        search_response = SearchResponse(mode='lexical', score_type='bm25',
                                        score_direction='higher_is_better', results=self.candidates)
        with patch.object(retrieval_nodes, 'search', return_value=search_response):
            response = TestClient(app).post('/search', json={
                'query': 'autonomous vehicle permits', 'mode': 'lexical', 'top_k': 2,
            })
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body['ranking_stage'], 'retrieval')
        self.assertEqual([hit['chunk_id'] for hit in body['results']], ['first', 'second'])
        self.assertEqual(body['before_rerank'], body['after_rerank'])

    def test_empty_results_skip_model(self):
        result = retrieval_nodes.rerank_candidates_node({**self.state, 'candidates': []})
        self.assertEqual(result['ranking_stage'], 'retrieval')
        self.assertEqual(result['results'], [])
        self.client.assert_not_called()

    def test_chat_can_answer_after_invalid_hybrid_ranking(self):
        self.response('{"ranking": [0, 0, 2]}')

        def retrieved(request):
            return SearchResponse(mode=request.mode,
                                  score_type='bm25' if request.mode == 'lexical' else 'cosine_similarity',
                                  score_direction='higher_is_better', results=self.candidates)

        def draft(state):
            self.assertEqual([hit['chunk_id'] for hit in state['evidence']], ['first', 'second', 'third'])
            self.assertEqual(state['retrieval']['ranking_stage'], 'retrieval')
            self.assertEqual(state['retrieval']['before_rerank'], state['retrieval']['after_rerank'])
            return {'draft': {'status': 'answered', 'answer': 'Operating a vehicle requires a permit.',
                              'citations': [{'chunk_id': 'first', 'document_id': '1525', 'locator': '2'}]}}

        plan = {'plan': {'route': 'retrieve', 'search_query': self.state['request'].query, 'clarification': None}}
        with tempfile.TemporaryDirectory() as folder, \
                patch('app.agents.chat.CHECKPOINT_PATH', Path(folder) / 'checkpoints.sqlite'), \
                patch('app.agents.chat.question_guard', return_value=plan), \
                patch.object(retrieval_nodes, 'search', side_effect=retrieved), \
                patch('app.agents.chat.draft_answer', side_effect=draft):
            response = TestClient(app).post('/chat', json={
                'thread_id': 'rerank_fallback',
                'message': 'What permits are required to operate autonomous vehicles in Dubai?',
                'mode': 'hybrid', 'top_k': 20,
            })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['status'], 'answered')
        self.assertEqual(response.json()['citations'][0]['chunk_id'], 'first')

    def test_missing_model_configuration_remains_an_error(self):
        with patch.dict('os.environ', {'CHAT_MODEL': ''}), self.assertRaises(SearchUnavailable):
            retrieval_nodes.rerank_candidates_node(self.state)
        self.create.assert_not_called()


if __name__ == '__main__':
    unittest.main()
