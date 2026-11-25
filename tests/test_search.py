"""Filtered Qdrant retrieval and hybrid graph checks."""
import tempfile
import unittest
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

import numpy as np
from fastapi.testclient import TestClient
from qdrant_client import models

from app.rag import search as retrieval, store
from app.agents.retrieval_graph import run_retrieval
from app.main import app


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path_patch = patch.object(store, 'QDRANT_PATH', Path(self.directory.name) / 'qdrant')
        self.path_patch.start()
        self.settings_patch = patch.object(retrieval, 'embedding_settings',
                                           return_value=('https://example.test', 'embedding', 'unused'))
        self.settings_patch.start()
        store.ensure_collection(2, store.model_key('https://example.test', 'embedding'))
        self.database = store.client()
        points = []
        for document_id, year, kind, category, vector in [
            ('a', 2023, 'Law', 'Roads and Transport', [0, 1]),
            ('b', 2025, 'Resolution', 'Roads and Transport', [1, 0]),
            ('c', 2023, 'Law', 'Other', [1, 0]),
        ]:
            points.append(models.PointStruct(
                id=store.point_id(document_id), vector=vector,
                payload={'chunk_id': document_id, 'document_id': document_id,
                         'title': document_id, 'text': 'Vehicle permit requirements',
                         'source_url': 'https://example.test/source', 'content_url': 'https://example.test/pdf',
                         'primary_category': category, 'language': 'en', 'year': year, 'type': kind,
                         'status': None, 'article_number': '1', 'start_page': 2, 'end_page': 3},
            ))
        self.database.upsert(store.COLLECTION, points, wait=True)
        self.rerank_patch = patch('app.agents.retrieval_nodes.rank_candidates', side_effect=lambda query, candidates: candidates)
        self.rerank_patch.start()
        self.client = TestClient(app)

    def tearDown(self):
        self.rerank_patch.stop()
        self.settings_patch.stop()
        self.path_patch.stop()
        self.database.close()
        store._client.cache_clear()
        self.directory.cleanup()

    def test_filters_and_scope_before_top_k(self):
        for mode in ('lexical', 'semantic', 'hybrid'):
            for filters, expected in [({}, {'a', 'b'}), ({'year': 2023}, {'a'}),
                    ({'type': 'Resolution'}, {'b'}), ({'year': 2023, 'type': 'Law'}, {'a'}),
                    ({'year': 2025, 'type': 'Law'}, set())]:
                with self.subTest(mode=mode, filters=filters), patch.object(
                    retrieval, 'embed_texts', return_value=np.array([[1, 0]], dtype=np.float32)
                ):
                    response = self.client.post('/search', json={
                        'query': 'vehicle permit', 'mode': mode, 'filters': filters, 'top_k': 1,
                    })
                    self.assertEqual(response.status_code, 200, response.text)
                    results = response.json()['results']
                    self.assertEqual(bool(results), bool(expected))
                    self.assertTrue({hit['document_id'] for hit in results}.issubset(expected))
                    if results:
                        self.assertEqual(results[0]['locator'], '2-3')

    def test_rank_order_and_labels(self):
        with patch.object(retrieval, 'embed_texts', return_value=np.array([[1, 0]], dtype=np.float32)):
            response = self.client.post('/search', json={'query': 'vehicle', 'mode': 'semantic'}).json()
        self.assertEqual([hit['document_id'] for hit in response['results']], ['b', 'a'])
        self.assertEqual(response['score_type'], 'cosine_similarity')
        self.assertEqual(response['score_direction'], 'higher_is_better')
        self.assertEqual(response['ranking_stage'], 'reranked')

    def test_hybrid_trace_and_deduplication(self):
        with patch.object(retrieval, 'embed_texts', return_value=np.array([[1, 0]], dtype=np.float32)):
            response = self.client.post('/search', json={'query': 'vehicle', 'mode': 'hybrid'}).json()
        self.assertEqual(set(response['trace'][:2]), {'lexical_search', 'semantic_search'})
        self.assertEqual(response['trace'][2:], ['merge_candidates', 'rerank_candidates'])
        self.assertEqual(len(response['before_rerank']), 2)
        self.assertEqual(len(set(response['before_rerank'])), 2)
        self.assertEqual(response['score_type'], 'rrf')

    def test_hybrid_branches_overlap_before_join(self):
        both_started = Barrier(2)

        def simultaneous_search(request):
            both_started.wait(timeout=3)
            return retrieval.SearchResponse(
                mode=request.mode, score_type='bm25' if request.mode == 'lexical' else 'cosine_similarity',
                score_direction='higher_is_better', results=[],
            )

        with patch('app.agents.retrieval_nodes.search', side_effect=simultaneous_search):
            result = run_retrieval(retrieval.SearchRequest(query='vehicle permit', mode='hybrid'))
        self.assertEqual(set(result['trace'][:2]), {'lexical_search', 'semantic_search'})
        self.assertEqual(result['trace'][2:], ['merge_candidates', 'rerank_candidates'])

    def test_invalid_requests(self):
        for values in [{'query': '   '}, {'query': 'x', 'top_k': 21}, {'query': 'x', 'mode': 'unknown'},
                       {'query': 'x', 'filters': {'year': '2023'}}, {'query': 'x', 'filters': {'type': 'Unknown'}}]:
            self.assertEqual(self.client.post('/search', json=values).status_code, 422)

    def test_missing_collection_is_controlled(self):
        self.database.delete_collection(store.COLLECTION)
        self.assertEqual(self.client.post('/search', json={'query': 'vehicle', 'mode': 'semantic'}).status_code, 503)

    def test_model_failure_is_controlled(self):
        with patch.object(retrieval, 'embed_texts', side_effect=retrieval.EmbeddingFailure('Embedding service request failed')):
            self.assertEqual(self.client.post('/search', json={'query': 'vehicle', 'mode': 'semantic'}).status_code, 502)

    def test_semantic_no_match_skips_embedding(self):
        with patch.object(retrieval, 'embed_texts') as embed:
            response = self.client.post('/search', json={
                'query': 'vehicle', 'mode': 'semantic', 'filters': {'year': 1900},
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['results'], [])
        embed.assert_not_called()

    def test_punctuation_does_not_become_query_syntax(self):
        self.assertEqual(self.client.post('/search', json={'query': 'vehicle " OR * (permit)'}).status_code, 200)


if __name__ == '__main__':
    unittest.main()
