"""BM25 lexical search and exact cosine semantic search."""
import logging
import os
import re
import sqlite3
from contextlib import closing
from typing import Literal

import numpy as np
from dotenv import load_dotenv
from openai import OpenAI, OpenAIError
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.rag.ingest import INDEX_PATH, PROJECT_DIR

load_dotenv(PROJECT_DIR / '.env')
LOGGER = logging.getLogger('uvicorn.error')
STOP_WORDS = set('a an the is are was were be to of in on for and or with what which how does do can i it'.split())


class SearchUnavailable(Exception):
    pass


class EmbeddingFailure(Exception):
    pass


class SearchFilters(BaseModel):
    model_config = ConfigDict(extra='forbid')
    year: int | None = Field(default=None, ge=1900, le=2100, strict=True)
    type: Literal['Law', 'Resolution', 'Regulation'] | None = None


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    query: str = Field(min_length=1, max_length=2000)
    mode: Literal['lexical', 'semantic', 'hybrid'] = 'lexical'
    filters: SearchFilters = Field(default_factory=SearchFilters)
    top_k: int = Field(default=10, ge=1, le=20, strict=True)

    @field_validator('query')
    @classmethod
    def strip_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError('query must contain text')
        return value.strip()


class SearchHit(BaseModel):
    chunk_id: str
    document_id: str
    title: str
    text: str
    source_url: str
    content_url: str
    primary_category: str
    year: int | None
    type: str | None
    status: str | None
    article_number: str | None
    locator_type: Literal['pdf_page'] = 'pdf_page'
    locator: str
    score: float


class SearchResponse(BaseModel):
    mode: Literal['lexical', 'semantic']
    ranking_stage: Literal['retrieval'] = 'retrieval'
    score_type: Literal['bm25', 'cosine_similarity']
    score_direction: Literal['lower_is_better', 'higher_is_better']
    results: list[SearchHit]


def embedding_settings() -> tuple[str, str, str]:
    base_url = os.getenv('OPENAI_BASE_URL', '').rstrip('/')
    model = os.getenv('EMBEDDING_MODEL', '')
    key = os.getenv('OPENAI_API_KEY', '')
    if not all((base_url, model, key)):
        raise SearchUnavailable('Embedding settings are missing from .env')
    return base_url, model, key


def embed_texts(texts: list[str]) -> np.ndarray:
    # EMBEDDING_MODEL in .env names the deployment (currently text-embedding-3-small).
    base_url, model, key = embedding_settings()
    try:
        with OpenAI(base_url=base_url, api_key=key, timeout=30, max_retries=1) as client:
            response = client.embeddings.create(model=model, input=texts)
        vectors = np.asarray([item.embedding for item in sorted(response.data, key=lambda item: item.index)], dtype=np.float32)
    except OpenAIError as error:
        LOGGER.error('Embedding request failed: %s', type(error).__name__)
        raise EmbeddingFailure('Embedding service request failed; check configuration or retry') from error
    if vectors.ndim != 2 or len(vectors) != len(texts) or not np.isfinite(vectors).all():
        raise EmbeddingFailure('Embedding service returned invalid vectors')
    # Normalize each vector to length 1 so a dot product gives cosine similarity.
    lengths = np.linalg.norm(vectors, axis=1, keepdims=True)
    if np.any(lengths == 0):
        raise EmbeddingFailure('Embedding service returned an empty vector')
    return vectors / lengths


def synchronize_search_indexes() -> dict[str, int]:
    """Rebuild FTS5 and embed only missing chunks; commit each batch for retries."""
    if not INDEX_PATH.is_file():
        raise SearchUnavailable('Run POST /ingest first')
    with closing(sqlite3.connect(INDEX_PATH)) as connection:
        connection.execute('PRAGMA foreign_keys = ON')
        with connection:
            connection.execute("CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(chunk_id UNINDEXED, text, tokenize='porter unicode61')")
            connection.execute('DELETE FROM chunks_fts')
            connection.execute('INSERT INTO chunks_fts SELECT chunk_id, text FROM chunks')
            connection.execute('''CREATE TABLE IF NOT EXISTS embeddings (
                chunk_id TEXT NOT NULL REFERENCES chunks(chunk_id) ON DELETE CASCADE,
                model_key TEXT NOT NULL, vector BLOB NOT NULL, PRIMARY KEY (chunk_id, model_key))''')
        base_url, model, _ = embedding_settings()
        model_key = f'{base_url}/{model}'
        missing = connection.execute('''SELECT c.chunk_id, c.text FROM chunks c
            JOIN documents d ON d.document_id = c.document_id
            LEFT JOIN embeddings e ON e.chunk_id = c.chunk_id AND e.model_key = ?
            WHERE d.primary_category = 'Roads and Transport' AND d.language = 'en'
            AND e.chunk_id IS NULL ORDER BY c.chunk_id''', (model_key,)).fetchall()
        written = 0
        for offset in range(0, len(missing), 64):
            batch = missing[offset:offset + 64]
            vectors = embed_texts([row[1] for row in batch])
            with connection:
                connection.executemany('INSERT OR REPLACE INTO embeddings VALUES (?, ?, ?)',
                    [(row[0], model_key, vector.tobytes()) for row, vector in zip(batch, vectors)])
            written += len(batch)
            LOGGER.info('Embedding progress: %s/%s chunks', written, len(missing))
        LOGGER.info('Search indexes ready: %s new embeddings', written)
        return {'embeddings_written': written}


def filter_clause(filters: SearchFilters) -> tuple[str, list]:
    clauses = ['d.primary_category = ?', 'd.language = ?']
    parameters = ['Roads and Transport', 'en']
    if filters.year is not None:
        clauses.append('d.year = ?')
        parameters.append(filters.year)
    if filters.type is not None:
        clauses.append('d.type = ?')
        parameters.append(filters.type)
    return ' AND '.join(clauses), parameters


def lexical_search(connection: sqlite3.Connection, request: SearchRequest) -> list[tuple[sqlite3.Row, float]]:
    terms = list(dict.fromkeys(re.findall(r'\w+', request.query.lower())))
    terms = [term for term in terms if term not in STOP_WORDS]
    if not terms:
        return []
    # Extracted words are quoted so user text cannot become FTS operators.
    match_query = ' OR '.join(f'"{term}"' for term in terms)
    where, parameters = filter_clause(request.filters)
    # SQLite FTS5 BM25 scores use ascending order: lower, often negative, is better.
    # BM25 measures word matches; it is not a probability or confidence value.
    rows = connection.execute(f'''SELECT c.*, d.title, d.source_url, d.content_url,
        d.primary_category, d.year, d.type, d.status, bm25(chunks_fts) AS score
        FROM chunks_fts JOIN chunks c ON c.chunk_id = chunks_fts.chunk_id
        JOIN documents d ON d.document_id = c.document_id
        WHERE chunks_fts MATCH ? AND {where}
        ORDER BY score ASC, c.chunk_id ASC LIMIT ?''', [match_query, *parameters, request.top_k]).fetchall()
    return [(row, row['score']) for row in rows]


def semantic_search(connection: sqlite3.Connection, request: SearchRequest) -> list[tuple[sqlite3.Row, float]]:
    base_url, model, _ = embedding_settings()
    where, parameters = filter_clause(request.filters)
    rows = connection.execute(f'''SELECT c.*, d.title, d.source_url, d.content_url,
        d.primary_category, d.year, d.type, d.status, e.vector
        FROM chunks c JOIN documents d ON d.document_id = c.document_id
        LEFT JOIN embeddings e ON e.chunk_id = c.chunk_id AND e.model_key = ?
        WHERE {where} ORDER BY c.chunk_id''', [f'{base_url}/{model}', *parameters]).fetchall()
    if not rows:
        return []
    if any(row['vector'] is None for row in rows):
        raise SearchUnavailable('Some chunks need embeddings; run POST /ingest')
    matrix = np.vstack([np.frombuffer(row['vector'], dtype=np.float32) for row in rows])
    query_vector = embed_texts([request.query])[0]
    if matrix.shape[1] != len(query_vector):
        raise SearchUnavailable('Embedding dimensions changed; rebuild the embeddings index')
    # Both stored chunk vectors and the query vector are normalized.
    # Their dot product is cosine similarity: higher means more similar vectors,
    # not a percentage probability that the chunk answers the question.
    scores = matrix @ query_vector
    best_indices = np.argsort(-scores, kind='stable')[:request.top_k]
    return [(rows[index], float(scores[index])) for index in best_indices]


def search(request: SearchRequest) -> SearchResponse:
    if not INDEX_PATH.is_file():
        raise SearchUnavailable('Run POST /ingest first')
    try:
        with closing(sqlite3.connect(f'file:{INDEX_PATH}?mode=ro', uri=True)) as connection:
            connection.row_factory = sqlite3.Row
            ranked = lexical_search(connection, request) if request.mode == 'lexical' else semantic_search(connection, request)
    except sqlite3.OperationalError as error:
        raise SearchUnavailable('Search index unavailable; run POST /ingest') from error
    hits = []
    for row, score in ranked:
        locator = str(row['start_page']) if row['start_page'] == row['end_page'] else f"{row['start_page']}-{row['end_page']}"
        hits.append(SearchHit(**{field: row[field] for field in (
            'chunk_id', 'document_id', 'title', 'text', 'source_url', 'content_url',
            'primary_category', 'year', 'type', 'status', 'article_number')}, locator=locator, score=score))
    LOGGER.info('Search complete: mode=%s results=%s', request.mode, len(hits))
    return SearchResponse(mode=request.mode, score_type='bm25' if request.mode == 'lexical' else 'cosine_similarity',
        score_direction='lower_is_better' if request.mode == 'lexical' else 'higher_is_better', results=hits)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    print(synchronize_search_indexes())
