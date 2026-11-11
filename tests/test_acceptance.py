"""Corpus-boundary and repeat-ingestion acceptance checks."""
import csv
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from app.rag import ingest


class IngestionAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.manifest_path = Path(self.directory.name) / 'manifest.csv'
        self.index_path = Path(self.directory.name) / 'index.sqlite'
        with ingest.MANIFEST_PATH.open(newline='') as file:
            reader = csv.DictReader(file)
            self.fields = reader.fieldnames
            self.record = next(row for row in reader if row['document_id'] == '13')

    def write_manifest(self, record):
        with self.manifest_path.open('w', newline='') as file:
            writer = csv.DictWriter(file, fieldnames=self.fields)
            writer.writeheader()
            writer.writerow(record)

    def run_ingestion(self):
        with patch.object(ingest, 'MANIFEST_PATH', self.manifest_path), patch.object(ingest, 'INDEX_PATH', self.index_path):
            return ingest.ingest_corpus()

    def test_out_of_category_record_is_not_indexed(self):
        self.write_manifest({**self.record, 'primary_category': 'Other'})
        result = self.run_ingestion()
        self.assertEqual((result.indexed_documents, result.failed_documents), (0, 1))
        with closing(sqlite3.connect(self.index_path)) as database:
            self.assertEqual(database.execute('SELECT count(*) FROM documents').fetchone()[0], 0)

    def test_repeat_ingestion_does_not_duplicate_chunks(self):
        self.write_manifest(self.record)
        first = self.run_ingestion()
        with closing(sqlite3.connect(self.index_path)) as database:
            first_ids = {row[0] for row in database.execute('SELECT chunk_id FROM chunks')}
        second = self.run_ingestion()
        with closing(sqlite3.connect(self.index_path)) as database:
            second_ids = {row[0] for row in database.execute('SELECT chunk_id FROM chunks')}
            document_count = database.execute('SELECT count(*) FROM documents').fetchone()[0]
        self.assertEqual((first.indexed_documents, first.failed_documents), (1, 0))
        self.assertTrue(first_ids)
        self.assertEqual((second.skipped_documents, second.chunks_written), (1, 0))
        self.assertEqual(document_count, 1)
        self.assertEqual(second_ids, first_ids)


if __name__ == '__main__':
    unittest.main()
