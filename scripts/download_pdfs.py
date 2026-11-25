"""Download the manifest's source PDFs from the Dubai Legislation Portal."""

import csv
import hashlib
from pathlib import Path
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen


DATA_DIR = Path(__file__).resolve().parents[1] / 'data'


def download_pdfs(data_dir: Path = DATA_DIR) -> None:
    with (data_dir / 'manifest.csv').open(newline='') as manifest:
        records = list(csv.DictReader(manifest))

    for record in records:
        relative_path = Path(record['local_file'])
        if relative_path.parts != ('pdfs', f"{record['document_id']}.pdf"):
            raise ValueError(f"Unexpected PDF path for document {record['document_id']}")
        pdf_path = data_dir / relative_path
        expected_hash = record['sha256']
        if pdf_path.exists() and hashlib.sha256(pdf_path.read_bytes()).hexdigest() == expected_hash:
            print(f"Verified {pdf_path.name}")
            continue

        source_url = record['content_url']
        if urlparse(source_url).hostname != 'dlp.dubai.gov.ae':
            raise ValueError(f"Unexpected source host for document {record['document_id']}")
        request = Request(quote(source_url, safe=':/?&=%'), headers={'User-Agent': 'dubai-transportation-rag/1.0'})
        with urlopen(request, timeout=60) as response:
            content = response.read()
        if hashlib.sha256(content).hexdigest() != expected_hash:
            raise ValueError(f"PDF hash mismatch for document {record['document_id']}")
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        pdf_path.write_bytes(content)
        print(f"Downloaded {pdf_path.name}")


if __name__ == '__main__':
    download_pdfs()
