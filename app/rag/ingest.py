"""Index the approved local PDF corpus by legal article."""

import csv
import hashlib
import logging
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field
from pypdf import PdfReader
import pdfplumber

from app.rag.table_chunks import extract_table_chunks

PROJECT_DIR = Path(__file__).resolve().parents[2]
MANIFEST_PATH = PROJECT_DIR / "data" / "manifest.csv"
INDEX_PATH = PROJECT_DIR / "data" / "index.sqlite"
MAX_CHARS = 800
OVERLAP_CHARS = 80
ARTICLE_PATTERN = re.compile(r"^Article\s*\((\d+)\)\s*$", re.IGNORECASE)
ANNEX_PATTERN = re.compile(r"^(Schedule|Annex|Appendix)\b", re.IGNORECASE)
PAGE_PATTERN = re.compile(r"^Page\s+\d+\s+of\s+\d+$", re.IGNORECASE)
LOGGER = logging.getLogger("uvicorn.error")


@dataclass(frozen=True)
class SourceLine:
    text: str
    page: int


class IngestResult(BaseModel):
    indexed_documents: int = 0
    skipped_documents: int = 0
    failed_documents: int = 0
    chunks_written: int = 0
    embeddings_written: int = 0
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def extract_lines(pdf_path: Path, table_boxes: dict[int, list[tuple]] | None = None) -> tuple[list[SourceLine], list[str]]:
    lines = []
    warnings = []
    table_boxes = table_boxes or {}

    with pdfplumber.open(pdf_path) as table_pdf:
        for page_number, page in enumerate(PdfReader(pdf_path).pages, start=1):
            page_text = page.extract_text() or ""
            original_lines = [" ".join(line.split()) for line in page_text.splitlines() if line.strip()]
            original_marker = next((index for index, line in enumerate(original_lines[:6])
                                    if PAGE_PATTERN.fullmatch(line)), None)
            header_lines = set(original_lines[:original_marker]) if original_marker is not None else set()
            if page_number in table_boxes:
                boxes = table_boxes[page_number]
                outside_tables = table_pdf.pages[page_number - 1].filter(
                    lambda character: character.get("object_type") != "char" or not any(
                        left <= (character["x0"] + character["x1"]) / 2 <= right
                        and top <= (character["top"] + character["bottom"]) / 2 <= bottom
                        for left, top, right, bottom in boxes
                    )
                )
                page_text = outside_tables.extract_text() or ""
            page_lines = [" ".join(line.split()) for line in page_text.splitlines()]
            page_lines = [line for line in page_lines if line and line not in header_lines
                          and not PAGE_PATTERN.fullmatch(line)]

            page_marker = next((index for index, line in enumerate(page_lines[:6]) if PAGE_PATTERN.fullmatch(line)), None)
            if page_marker is None:
                page_marker = next((index + 1 for index, line in enumerate(page_lines[:5])
                                    if index + 1 < len(page_lines) and line.endswith("Page")
                                    and re.fullmatch(r"\d+ of \d+", page_lines[index + 1])), None)
            if page_marker is not None:
                page_lines = page_lines[page_marker + 1 :]

            footer = next((index for index, line in enumerate(page_lines) if line.startswith("©")), None)
            if footer is not None:
                page_lines = page_lines[:footer]

            page_lines = [line for line in page_lines if any(character.isalnum() for character in line)]
            page_text = " ".join(page_lines)
            if not page_text:
                if page_number not in table_boxes:
                    warnings.append(f"{pdf_path.name} page {page_number}: no extractable text")
                continue
            if len(page_text) < 400 and re.search(r"\bMap\s*\(\d+\)", page_text):
                warnings.append(f"{pdf_path.name} page {page_number}: map content not indexed as text")
                continue

            lines.extend(SourceLine(line, page_number) for line in page_lines)

    return lines, warnings


def looks_like_heading(line: str) -> bool:
    return len(line) <= 100 and line[0].isupper() and line[-1] not in ".;:," and not line[0].isdigit()


def section_starts(lines: list[SourceLine]) -> list[tuple[int, str, str, str]]:
    starts = []
    for index, line in enumerate(lines):
        article = ARTICLE_PATTERN.fullmatch(line.text)
        if article:
            heading_index = index - 1 if index > 0 and looks_like_heading(lines[index - 1].text) else index
            heading = lines[heading_index].text if heading_index < index else ""
            starts.append((heading_index, "article", article.group(1), heading))
        elif ANNEX_PATTERN.match(line.text) and (index == 0 or lines[index - 1].page != line.page):
            starts.append((index, "annex", "", line.text))
    return sorted(set(starts))


def split_body(text: str, capacity: int) -> list[tuple[int, int]]:
    spans = []
    start = 0
    while start < len(text):
        end = min(start + capacity, len(text))
        if end < len(text):
            minimum = start + capacity // 2
            for separator in (". ", "; ", ": ", " "):
                break_at = text.rfind(separator, minimum, end)
                if break_at >= 0:
                    end = break_at + len(separator)
                    break
        spans.append((start, end))
        if end == len(text):
            break
        start = max(0, end - OVERLAP_CHARS)
        next_space = text.find(" ", start, end)
        if next_space >= 0:
            start = next_space + 1
    return spans


def build_chunks(document_id: str, lines: list[SourceLine]) -> list[tuple]:
    starts = section_starts(lines)
    if not starts:
        starts = [(0, "preamble", "", "")]
    elif starts[0][0] > 0:
        starts.insert(0, (0, "preamble", "", ""))

    chunks = []
    for section_number, (section_start, section_type, article_number, heading) in enumerate(starts):
        section_end = starts[section_number + 1][0] if section_number + 1 < len(starts) else len(lines)
        section_lines = lines[section_start:section_end]
        if section_type == "article":
            marker_index = next(index for index, line in enumerate(section_lines) if ARTICLE_PATTERN.fullmatch(line.text))
            prefix = f"{heading + chr(10) if heading else ''}Article ({article_number})\n"
            body_lines = section_lines[marker_index + 1 :]
        elif section_type == "annex":
            prefix = f"{heading}\n"
            body_lines = section_lines[1:]
        else:
            prefix = "Preamble\n"
            body_lines = section_lines

        if not body_lines:
            continue
        body = ""
        line_spans = []
        for line in body_lines:
            if body:
                body += " "
            start = len(body)
            body += line.text
            line_spans.append((start, len(body), line.page))

        for part_number, (start, end) in enumerate(split_body(body, MAX_CHARS - len(prefix)), start=1):
            chunk_text = prefix + body[start:end].strip()
            pages = [page for line_start, line_end, page in line_spans if line_start < end and line_end > start]
            chunk_id = hashlib.sha256(
                f"{document_id}:{section_number}:{part_number}:{chunk_text}".encode()
            ).hexdigest()[:20]
            chunks.append((chunk_id, document_id, section_type, article_number or None,
                           min(pages), max(pages), chunk_text))
    return chunks


def create_tables(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("""CREATE TABLE IF NOT EXISTS documents (
        document_id TEXT PRIMARY KEY, title TEXT NOT NULL, source_url TEXT NOT NULL,
        content_url TEXT NOT NULL, primary_category TEXT NOT NULL, language TEXT NOT NULL,
        year INTEGER, type TEXT, authority TEXT, status TEXT, sha256 TEXT NOT NULL
    )""")
    connection.execute("""CREATE TABLE IF NOT EXISTS chunks (
        chunk_id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(document_id),
        section_type TEXT NOT NULL, article_number TEXT, start_page INTEGER NOT NULL,
        end_page INTEGER NOT NULL, text TEXT NOT NULL
    )""")
    connection.execute("CREATE INDEX IF NOT EXISTS chunks_document_id ON chunks(document_id)")


def ingest_corpus() -> IngestResult:
    result = IngestResult()
    LOGGER.info("Starting ingestion from %s", MANIFEST_PATH)
    with MANIFEST_PATH.open(newline="") as manifest, closing(sqlite3.connect(INDEX_PATH)) as connection:
        create_tables(connection)
        for record in csv.DictReader(manifest):
            document_id = record["document_id"]
            try:
                if record["primary_category"] != "Roads and Transport" or record["language"] != "en":
                    raise ValueError("document is outside the approved English Roads and Transport corpus")
                pdf_path = (PROJECT_DIR / "data" / record["local_file"]).resolve()
                pdf_path.relative_to((PROJECT_DIR / "data" / "pdfs").resolve())
                content_hash = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
                if content_hash != record["sha256"]:
                    raise ValueError("PDF hash does not match the manifest")
                existing = connection.execute(
                    "SELECT sha256 FROM documents WHERE document_id = ?", (document_id,)
                ).fetchone()
                if existing and existing[0] == content_hash:
                    result.skipped_documents += 1
                    LOGGER.info("Skipped unchanged document %s", document_id)
                    continue

                table_chunks, table_boxes, table_warnings = extract_table_chunks(pdf_path, document_id)
                lines, warnings = extract_lines(pdf_path, table_boxes)
                warnings.extend(table_warnings)
                prose_chunks = [chunk for chunk in build_chunks(document_id, lines)
                                if not (chunk[2] == "annex" and all(
                                    page in table_boxes for page in range(chunk[4], chunk[5] + 1)))]
                chunks = prose_chunks + table_chunks
                if not chunks:
                    raise ValueError("no article or preamble text could be indexed")
                with connection:
                    connection.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
                    connection.execute("""INSERT OR REPLACE INTO documents
                        (document_id, title, source_url, content_url, primary_category, language,
                         year, type, authority, status, sha256)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
                        document_id, record["title"], record["source_url"], record["content_url"],
                        record["primary_category"], record["language"],
                        int(record["year"]) if record["year"] else None,
                        record["type"] or None, record["authority"] or None,
                        record["status"] or None, content_hash,
                    ))
                    connection.executemany("""INSERT INTO chunks
                        (chunk_id, document_id, section_type, article_number, start_page, end_page, text)
                        VALUES (?, ?, ?, ?, ?, ?, ?)""", chunks)
                result.indexed_documents += 1
                result.chunks_written += len(chunks)
                result.warnings.extend(warnings)
                for warning in warnings:
                    LOGGER.warning(warning)
                LOGGER.info("Indexed document %s: %s chunks", document_id, len(chunks))
            except Exception as error:
                result.failed_documents += 1
                result.errors.append(f"{document_id}: {error}")
                LOGGER.error("Failed to index document %s: %s", document_id, error)
    LOGGER.info(
        "Ingestion complete: %s indexed, %s skipped, %s failed, %s chunks written, %s warnings",
        result.indexed_documents, result.skipped_documents, result.failed_documents,
        result.chunks_written, len(result.warnings),
    )
    return result
