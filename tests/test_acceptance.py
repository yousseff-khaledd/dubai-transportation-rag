"""Corpus-boundary and repeat-ingestion acceptance checks."""
import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from app.rag import ingest, store


class IngestionAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.manifest_path = Path(self.directory.name) / 'manifest.csv'
        self.qdrant_path = Path(self.directory.name) / 'qdrant'
        self.path_patch = patch.object(store, 'QDRANT_PATH', self.qdrant_path)
        self.path_patch.start()
        with ingest.MANIFEST_PATH.open(newline='') as file:
            reader = csv.DictReader(file)
            self.fields = reader.fieldnames
            self.record = next(row for row in reader if row['document_id'] == '13')

    def tearDown(self):
        if self.qdrant_path.exists():
            store.client().close()
            store._client.cache_clear()
        self.path_patch.stop()
        self.directory.cleanup()

    def write_manifest(self, record):
        with self.manifest_path.open('w', newline='') as file:
            writer = csv.DictWriter(file, fieldnames=self.fields)
            writer.writeheader()
            writer.writerow(record)

    def run_ingestion(self):
        with patch.object(ingest, 'MANIFEST_PATH', self.manifest_path), \
             patch('app.rag.search.embedding_settings', return_value=('https://example.test', 'embedding', 'unused')), \
             patch('app.rag.search.embed_texts', side_effect=lambda texts: np.tile([1.0, 0.0], (len(texts), 1))):
            return ingest.ingest_corpus()

    def chunk_ids(self):
        if not store.client().collection_exists(store.COLLECTION):
            return set()
        points, _ = store.client().scroll(store.COLLECTION, scroll_filter=store.document_filter('13'),
                                          limit=1000, with_payload=True)
        return {point.payload['chunk_id'] for point in points}

    def test_out_of_category_record_is_not_indexed(self):
        self.write_manifest({**self.record, 'primary_category': 'Other'})
        result = self.run_ingestion()
        self.assertEqual((result.indexed_documents, result.failed_documents), (0, 1))
        self.assertEqual(self.chunk_ids(), set())

    def test_repeat_ingestion_does_not_duplicate_chunks(self):
        self.write_manifest(self.record)
        first = self.run_ingestion()
        first_ids = self.chunk_ids()
        second = self.run_ingestion()
        self.assertEqual((first.indexed_documents, first.failed_documents), (1, 0))
        self.assertTrue(first_ids)
        self.assertEqual(first.embeddings_written, len(first_ids))
        self.assertEqual((second.skipped_documents, second.chunks_written, second.embeddings_written), (1, 0, 0))
        self.assertEqual(self.chunk_ids(), first_ids)


if __name__ == '__main__':
    unittest.main()
