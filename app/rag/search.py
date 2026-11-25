"""Filtered BM25 and semantic retrieval from the local Qdrant collection."""
import logging
import os
import re
from typing import Literal

import numpy as np
from dotenv import load_dotenv
from openai import OpenAI, OpenAIError
from pydantic import BaseModel, ConfigDict, Field, field_validator
from qdrant_client import models

from app.rag.store import COLLECTION, PROJECT_DIR, client, model_key

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


def search_filter(filters: SearchFilters) -> models.Filter:
    conditions = [
        models.FieldCondition(key='primary_category', match=models.MatchValue(value='Roads and Transport')),
        models.FieldCondition(key='language', match=models.MatchValue(value='en')),
    ]
    if filters.year is not None:
        conditions.append(models.FieldCondition(key='year', match=models.MatchValue(value=filters.year)))
    if filters.type is not None:
        conditions.append(models.FieldCondition(key='type', match=models.MatchValue(value=filters.type)))
    return models.Filter(must=conditions)


def lexical_search(request: SearchRequest):
    terms = list(dict.fromkeys(re.findall(r'\w+', request.query.lower())))
    terms = [term for term in terms if term not in STOP_WORDS]
    if not terms:
        return []
    return client().query_points(
        collection_name=COLLECTION,
        query=models.TextQuery(text=' '.join(terms)),
        using='text',
        query_filter=search_filter(request.filters),
        limit=request.top_k,
        with_payload=True,
    ).points


def semantic_search(request: SearchRequest):
    base_url, model, _ = embedding_settings()
    if client().get_collection(COLLECTION).config.metadata != {'embedding_key': model_key(base_url, model)}:
        raise SearchUnavailable('Qdrant index uses a different embedding deployment; rebuild it')
    filters = search_filter(request.filters)
    if client().count(COLLECTION, count_filter=filters, exact=True).count == 0:
        return []
    query_vector = embed_texts([request.query])[0]
    return client().query_points(
        collection_name=COLLECTION,
        query=query_vector.tolist(),
        query_filter=filters,
        limit=request.top_k,
        with_payload=True,
    ).points


def search(request: SearchRequest) -> SearchResponse:
    if not client().collection_exists(COLLECTION):
        raise SearchUnavailable('Run POST /ingest first')
    ranked = lexical_search(request) if request.mode == 'lexical' else semantic_search(request)
    hits = []
    for point in ranked:
        payload = point.payload
        locator = str(payload['start_page']) if payload['start_page'] == payload['end_page'] else f"{payload['start_page']}-{payload['end_page']}"
        hits.append(SearchHit(**{field: payload[field] for field in (
            'chunk_id', 'document_id', 'title', 'text', 'source_url', 'content_url',
            'primary_category', 'year', 'type', 'status', 'article_number')}, locator=locator, score=point.score))
    LOGGER.info('Search complete: mode=%s results=%s', request.mode, len(hits))
    return SearchResponse(mode=request.mode, score_type='bm25' if request.mode == 'lexical' else 'cosine_similarity',
        score_direction='higher_is_better', results=hits)
