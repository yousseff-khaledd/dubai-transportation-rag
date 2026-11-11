"""Search, fusion, and reranking nodes for the retrieval graph."""
import json
import logging
import os
from typing import TypedDict

from openai import OpenAI, OpenAIError

from app.agents.prompts.rerank_candidates import RERANK_PROMPT
from app.rag.search import SearchHit, SearchRequest, SearchUnavailable, search

LOGGER = logging.getLogger('uvicorn.error')


class RerankFailure(Exception):
    pass


class RetrievalState(TypedDict, total=False):
    """Shared state for one retrieval run; it is not checkpointed."""
    request: SearchRequest
    lexical_results: list[SearchHit]
    semantic_results: list[SearchHit]
    candidates: list[SearchHit]
    results: list[SearchHit]
    before_rerank: list[str]
    after_rerank: list[str]


def select_search_branches(state: RetrievalState) -> list[str]:
    mode = state['request'].mode
    LOGGER.info('Retrieval routing: mode=%s', mode)
    # Hybrid schedules both search nodes in parallel; the other modes run one.
    return ['lexical_search', 'semantic_search'] if mode == 'hybrid' else [f'{mode}_search']


def lexical_search(state: RetrievalState) -> dict:
    # BM25 uses SQLite word matches; this node does not call an AI model.
    request = state['request'].model_copy(update={'mode': 'lexical', 'top_k': max(10, state['request'].top_k)})
    LOGGER.info('Lexical node started')
    results = search(request).results
    LOGGER.info('Lexical node completed: %s candidates', len(results))
    return {'lexical_results': results}


def semantic_search(state: RetrievalState) -> dict:
    # The configured embedding model converts the query to a vector for similarity search.
    request = state['request'].model_copy(update={'mode': 'semantic', 'top_k': max(10, state['request'].top_k)})
    LOGGER.info('Semantic node started')
    results = search(request).results
    LOGGER.info('Semantic node completed: %s candidates', len(results))
    return {'semantic_results': results}


def merge_candidates(state: RetrievalState) -> dict:
    LOGGER.info('Join node started')
    lexical = state.get('lexical_results', [])
    semantic = state.get('semantic_results', [])
    if state['request'].mode == 'hybrid':
        scores: dict[str, float] = {}
        chunks: dict[str, SearchHit] = {}
        for ranking in (lexical, semantic):
            for rank, chunk in enumerate(ranking, start=1):
                # One entry per chunk ID; add a contribution from each ranked list.
                # Rank 1 in both lists: 1/61 + 1/61 ≈ 0.0328.
                # Rank 1 in one list only: 1/61 ≈ 0.0164.
                # These RRF scores rank candidates; they are not percentages.
                chunks[chunk.chunk_id] = chunk
                scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0.0) + 1 / (60 + rank)
        candidates = [chunks[chunk_id].model_copy(update={'score': scores[chunk_id]})
                      for chunk_id in sorted(scores, key=lambda item: (-scores[item], item))]
    else:
        candidates = lexical if state['request'].mode == 'lexical' else semantic
    # Bound the number of chunks sent to the reranking model.
    candidates = candidates[:20]
    LOGGER.info('Join node completed: %s lexical, %s semantic, %s candidates after merging',
                len(lexical), len(semantic), len(candidates))
    return {'candidates': candidates, 'before_rerank': [chunk.chunk_id for chunk in candidates]}


def rank_candidates(query: str, candidates: list[SearchHit]) -> list[SearchHit]:
    if not candidates:
        return []
    base_url = os.getenv('OPENAI_BASE_URL', '').rstrip('/')
    api_key = os.getenv('OPENAI_API_KEY', '')
    # CHAT_MODEL is configured in .env (currently the gpt-4.1-mini deployment).
    model = os.getenv('CHAT_MODEL', '')
    if not all((base_url, api_key, model)):
        raise SearchUnavailable('Chat model settings are missing from .env')
    documents = [{'id': index, 'title': chunk.title, 'year': chunk.year,
                  'article_number': chunk.article_number, 'locator': chunk.locator, 'text': chunk.text}
                 for index, chunk in enumerate(candidates)]
    try:
        with OpenAI(base_url=base_url, api_key=api_key, timeout=60, max_retries=1) as client:
            response = client.chat.completions.create(
                model=model, temperature=0, response_format={'type': 'json_object'},
                messages=[
                    {'role': 'system', 'content': RERANK_PROMPT},
                    {'role': 'user', 'content': json.dumps({'question': query, 'passages': documents})},
                ],
            )
        # The model returns an ordered list of IDs, not relevance scores.
        ranking = json.loads(response.choices[0].message.content)['ranking']
        if not isinstance(ranking, list) or any(type(item) is not int for item in ranking) or sorted(ranking) != list(range(len(candidates))):
            raise ValueError('invalid ranking')
    except (OpenAIError, ValueError, TypeError, KeyError, IndexError) as error:
        raise RerankFailure('Reranking service failed or returned an invalid ranking') from error
    return [candidates[index] for index in ranking]


def rerank_candidates_node(state: RetrievalState) -> dict:
    candidates = state['candidates']
    LOGGER.info('Rerank node started: %s candidates', len(candidates))
    if not candidates:
        LOGGER.info('Rerank model call skipped: no candidates')
    ranked = rank_candidates(state['request'].query, candidates)
    LOGGER.info('Rerank node completed: %s results selected', min(len(ranked), state['request'].top_k))
    return {'results': ranked[:state['request'].top_k],
            'after_rerank': [chunk.chunk_id for chunk in ranked]}


