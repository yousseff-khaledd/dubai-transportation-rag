"""Search, fusion, and reranking nodes for the retrieval graph."""
import json
import logging
import os
from typing import Literal, TypedDict

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
    ranking_stage: Literal['retrieval', 'reranked']


def select_search_branches(state: RetrievalState) -> list[str]:
    mode = state['request'].mode
    LOGGER.info('Retrieval routing: mode=%s', mode)
    # Hybrid schedules both search nodes in parallel; the other modes run one.
    return ['lexical_search', 'semantic_search'] if mode == 'hybrid' else [f'{mode}_search']


def lexical_search(state: RetrievalState) -> dict:
    # BM25 uses Qdrant text matches; this node does not call an AI model.
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
    lexical_hits = state.get('lexical_results', [])
    semantic_hits = state.get('semantic_results', [])
    if state['request'].mode == 'hybrid':
        reciprocal_rank_scores: dict[str, float] = {}
        hits_by_chunk_id: dict[str, SearchHit] = {}
        for ranked_hits in (lexical_hits, semantic_hits):
            for rank, hit in enumerate(ranked_hits, start=1):
                # One entry per chunk ID; add a contribution from each ranked list.
                # Rank 1 in both lists: 1/61 + 1/61 ≈ 0.0328.
                # Rank 1 in one list only: 1/61 ≈ 0.0164.
                # These RRF scores rank candidates; they are not percentages.
                hits_by_chunk_id[hit.chunk_id] = hit
                reciprocal_rank_scores[hit.chunk_id] = reciprocal_rank_scores.get(hit.chunk_id, 0.0) + 1 / (60 + rank)
        candidates = [hits_by_chunk_id[chunk_id].model_copy(update={'score': reciprocal_rank_scores[chunk_id]})
                      for chunk_id in sorted(reciprocal_rank_scores,
                                             key=lambda chunk_id: (-reciprocal_rank_scores[chunk_id], chunk_id))]
    else:
        candidates = lexical_hits if state['request'].mode == 'lexical' else semantic_hits
    # Bound the number of chunks sent to the reranking model.
    candidates = candidates[:20]
    LOGGER.info('Join node completed: %s lexical, %s semantic, %s candidates after merging',
                len(lexical_hits), len(semantic_hits), len(candidates))
    return {'candidates': candidates, 'before_rerank': [chunk.chunk_id for chunk in candidates]}


def rank_candidates(query: str, candidates: list[SearchHit]) -> list[SearchHit]:
    if not candidates:
        return []
    base_url = os.getenv('OPENAI_BASE_URL', '').rstrip('/')
    api_key = os.getenv('OPENAI_API_KEY', '')
    # CHAT_MODEL is configured in .env (currently the gpt-4.1-mini deployment).
    model_name = os.getenv('CHAT_MODEL', '')
    if not all((base_url, api_key, model_name)):
        raise SearchUnavailable('Chat model settings are missing from .env')
    passages = [{'id': index, 'title': hit.title, 'year': hit.year,
                 'article_number': hit.article_number, 'locator': hit.locator, 'text': hit.text}
                for index, hit in enumerate(candidates)]
    try:
        with OpenAI(base_url=base_url, api_key=api_key, timeout=60, max_retries=1) as client:
            response = client.chat.completions.create(
                model=model_name, temperature=0, response_format={'type': 'json_object'},
                messages=[
                    {'role': 'system', 'content': RERANK_PROMPT},
                    {'role': 'user', 'content': json.dumps({'question': query, 'passages': passages})},
                ],
            )
    except OpenAIError as error:
        raise RerankFailure('Reranking provider request failed') from error
    try:
        # The model returns an ordered list of IDs, not relevance scores.
        ranking = json.loads(response.choices[0].message.content)['ranking']
        if not isinstance(ranking, list) or any(type(item) is not int for item in ranking) or sorted(ranking) != list(range(len(candidates))):
            raise ValueError('invalid ranking')
    except (ValueError, TypeError, KeyError, IndexError) as error:
        raise RerankFailure('Reranking response did not contain a complete ranking') from error
    return [candidates[index] for index in ranking]


def rerank_candidates_node(state: RetrievalState) -> dict:
    candidates = state['candidates']
    LOGGER.info('Rerank node started: %s candidates', len(candidates))
    if not candidates:
        LOGGER.info('Rerank model call skipped: no candidates')
    ranking_stage = 'retrieval'
    ranked = candidates
    if candidates:
        try:
            ranked = rank_candidates(state['request'].query, candidates)
            ranking_stage = 'reranked'
        except RerankFailure as error:
            # Retrieval already produced filtered, ranked evidence. An optional
            # model reorder must not discard it or block the answer agent.
            LOGGER.warning('Reranking failed; keeping retrieval order: reason=%s cause=%s',
                           str(error), type(error.__cause__).__name__)
    LOGGER.info('Rerank node completed: %s results selected', min(len(ranked), state['request'].top_k))
    return {'results': ranked[:state['request'].top_k],
            'after_rerank': [chunk.chunk_id for chunk in ranked], 'ranking_stage': ranking_stage}


