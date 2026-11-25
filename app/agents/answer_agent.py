"""Evidence-bound LangChain answer agent and excerpt tool."""
import json
import logging
import time

from langchain.agents import create_agent
from langchain.agents.middleware import (
    ModelCallLimitMiddleware, ModelRequest, ModelResponse, ToolCallLimitMiddleware, wrap_model_call,
)
from langchain.agents.structured_output import ToolStrategy
from langchain_core.tools import tool

from app.agents.chat_models import AnswerDraft, ChatState
from app.agents.model import chat_model
from app.agents.prompts.draft_answer import ANSWER_PROMPT
from app.rag.search import SearchHit
from app.rag.store import COLLECTION, client, point_id

LOGGER = logging.getLogger('uvicorn.error')
MAX_MODEL_CALLS = 4
MAX_TOOL_CALLS = 4


@wrap_model_call
def log_model_timing(request: ModelRequest, handler) -> ModelResponse:
    started = time.monotonic()
    try:
        return handler(request)
    finally:
        LOGGER.info('Answer agent middleware: model call completed in %.2fs', time.monotonic() - started)


def draft_answer(state: ChatState) -> dict:
    evidence = [SearchHit.model_validate(hit) for hit in state['evidence']]
    if not evidence:
        return {'draft': AnswerDraft(status='insufficient_evidence', answer=None, citations=[]).model_dump()}
    selected_chunk_ids_by_source = {(hit.document_id, hit.locator): [] for hit in evidence}
    for hit in evidence:
        selected_chunk_ids_by_source[(hit.document_id, hit.locator)].append(hit.chunk_id)

    @tool
    def get_document_excerpt(document_id: str, locator: str) -> str:
        """Read selected indexed chunks for a document ID and PDF page locator, such as '3' or '3-4'."""
        allowed_chunk_ids = selected_chunk_ids_by_source.get((document_id, locator), [])
        if not allowed_chunk_ids:
            return 'No selected indexed excerpt exists for that document and locator.'
        stored_points = client().retrieve(COLLECTION, ids=[point_id(chunk_id) for chunk_id in allowed_chunk_ids],
                                          with_payload=True)
        excerpts = []
        for stored_point in stored_points:
            payload = stored_point.payload
            stored_locator = (str(payload['start_page']) if payload['start_page'] == payload['end_page']
                              else f"{payload['start_page']}-{payload['end_page']}")
            if payload['document_id'] == document_id and stored_locator == locator:
                excerpts.append({'chunk_id': payload['chunk_id'], 'text': payload['text']})
        LOGGER.info('Excerpt tool called: document=%s locator=%s chunks=%s', document_id, locator, len(excerpts))
        return json.dumps(excerpts)

    source_list = [{'chunk_id': hit.chunk_id, 'document_id': hit.document_id, 'locator': hit.locator,
                    'title': hit.title, 'text': hit.text} for hit in evidence]
    agent = create_agent(model=chat_model(), tools=[get_document_excerpt], middleware=[
                             log_model_timing,
                             ModelCallLimitMiddleware(run_limit=MAX_MODEL_CALLS, exit_behavior='error'),
                             ToolCallLimitMiddleware(tool_name='get_document_excerpt', run_limit=MAX_TOOL_CALLS,
                                                     exit_behavior='error'),
                         ],
                         response_format=ToolStrategy(AnswerDraft),
                         system_prompt=ANSWER_PROMPT)
    LOGGER.info('Answer agent started: %s selected chunks', len(evidence))
    result = agent.invoke({'messages': [('user', json.dumps({'question': state['plan']['search_query'], 'evidence': source_list}))]},
                          config={'recursion_limit': 40})
    excerpt_calls = [call for message in result['messages'] for call in getattr(message, 'tool_calls', [])
                     if call.get('name') == 'get_document_excerpt']
    LOGGER.info('Answer agent completed: %s model-issued excerpt calls', len(excerpt_calls))
    return {'draft': AnswerDraft.model_validate(result.get('structured_response')).model_dump()}


