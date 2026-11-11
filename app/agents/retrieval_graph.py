"""Retrieval orchestrator and response assembly."""
import logging
from typing import Literal

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from app.agents.retrieval_nodes import (
    RerankFailure, RetrievalState, lexical_search, merge_candidates, rerank_candidates_node,
    select_search_branches, semantic_search,
)
from app.rag.search import SearchHit, SearchRequest

LOGGER = logging.getLogger('uvicorn.error')


class RetrievalResponse(BaseModel):
    mode: Literal["lexical", "semantic", "hybrid"]
    ranking_stage: Literal["reranked"]
    score_type: Literal["bm25", "cosine_similarity", "rrf"]
    score_direction: Literal["lower_is_better", "higher_is_better"]
    results: list[SearchHit]
    trace: list[str]
    before_rerank: list[str]
    after_rerank: list[str]


def build_retrieval_graph():
    """Connect retrieval nodes around their shared RetrievalState."""
    graph = StateGraph(RetrievalState)
    graph.add_node('lexical_search', lexical_search)
    graph.add_node('semantic_search', semantic_search)
    graph.add_node('merge_candidates', merge_candidates)
    graph.add_node('rerank_candidates', rerank_candidates_node)
    graph.add_conditional_edges(START, select_search_branches, ['lexical_search', 'semantic_search'])
    graph.add_edge('lexical_search', 'merge_candidates')
    graph.add_edge('semantic_search', 'merge_candidates')
    graph.add_edge('merge_candidates', 'rerank_candidates')
    graph.add_edge('rerank_candidates', END)
    return graph.compile()


retrieval_graph = build_retrieval_graph()


def run_retrieval(request: SearchRequest) -> dict:
    """Run the retrieval graph and collect its node trace and ranked chunks."""
    state = {'request': request}
    trace = []
    LOGGER.info('Retrieval graph started: mode=%s top_k=%s', request.mode, request.top_k)
    try:
        for update in retrieval_graph.stream(state, stream_mode='updates'):
            for node, values in update.items():
                trace.append(node)
                state.update(values)
    except Exception as error:
        LOGGER.error('Retrieval graph failed: %s', type(error).__name__)
        raise
    LOGGER.info('Retrieval graph completed: mode=%s results=%s', request.mode, len(state['results']))
    # Results are ordered by the LLM, but each score remains its retrieval score:
    # BM25, cosine similarity, or RRF. Scores across methods are not comparable.
    return {'mode': request.mode, 'ranking_stage': 'reranked',
            'score_type': {'lexical': 'bm25', 'semantic': 'cosine_similarity', 'hybrid': 'rrf'}[request.mode],
            'score_direction': 'lower_is_better' if request.mode == 'lexical' else 'higher_is_better',
            'results': state['results'], 'trace': trace,
            'before_rerank': state['before_rerank'], 'after_rerank': state['after_rerank']}
