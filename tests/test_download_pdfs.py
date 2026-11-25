"""The PDF downloader verifies files before keeping them locally."""

import csv
import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.download_pdfs import download_pdfs


class DownloadPdfTests(unittest.TestCase):
    def test_download_verifies_hash_and_skips_matching_file(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            content = b'%PDF-1.4\nexample'
            with (data_dir / 'manifest.csv').open('w', newline='') as manifest:
                writer = csv.DictWriter(manifest, fieldnames=('document_id', 'local_file', 'content_url', 'sha256'))
                writer.writeheader()
                writer.writerow({'document_id': '1', 'local_file': 'pdfs/1.pdf',
                                 'content_url': 'https://dlp.dubai.gov.ae/Legislation Reference/1.pdf',
                                 'sha256': hashlib.sha256(content).hexdigest()})
            with patch('scripts.download_pdfs.urlopen', return_value=io.BytesIO(content)) as fetch:
                download_pdfs(data_dir)
                self.assertEqual((data_dir / 'pdfs/1.pdf').read_bytes(), content)
                download_pdfs(data_dir)
                self.assertEqual(fetch.call_count, 1)

    def test_bad_download_is_not_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            (data_dir / 'manifest.csv').write_text(
                'document_id,local_file,content_url,sha256\n'
                '1,pdfs/1.pdf,https://dlp.dubai.gov.ae/1.pdf,' + hashlib.sha256(b'expected').hexdigest() + '\n')
            with patch('scripts.download_pdfs.urlopen', return_value=io.BytesIO(b'wrong')):
                with self.assertRaisesRegex(ValueError, 'PDF hash mismatch'):
                    download_pdfs(data_dir)
            self.assertFalse((data_dir / 'pdfs/1.pdf').exists())


if __name__ == '__main__':
    unittest.main()
