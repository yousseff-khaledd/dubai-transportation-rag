"""Evidence-bound LangChain answer agent and excerpt tool."""
import json
import logging
import sqlite3
import time
from contextlib import closing

from langchain.agents import create_agent
from langchain.agents.middleware import (
    ModelCallLimitMiddleware, ModelRequest, ModelResponse, ToolCallLimitMiddleware, wrap_model_call,
)
from langchain.agents.structured_output import ToolStrategy
from langchain_core.tools import tool

from app.agents.chat_models import AnswerDraft, ChatState
from app.agents.model import chat_model
from app.agents.prompts.draft_answer import ANSWER_PROMPT
from app.rag.ingest import INDEX_PATH
from app.rag.search import SearchHit

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
    selected = {(hit.document_id, hit.locator): [] for hit in evidence}
    for hit in evidence:
        selected[(hit.document_id, hit.locator)].append(hit.chunk_id)

    @tool
    def get_document_excerpt(document_id: str, locator: str) -> str:
        """Read selected indexed chunks for a document ID and PDF page locator, such as '3' or '3-4'."""
        allowed_ids = selected.get((document_id, locator), [])
        if not allowed_ids:
            return 'No selected indexed excerpt exists for that document and locator.'
        placeholders = ','.join('?' for _ in allowed_ids)
        with closing(sqlite3.connect(f'file:{INDEX_PATH}?mode=ro', uri=True)) as connection:
            rows = connection.execute(f'SELECT chunk_id, text FROM chunks WHERE document_id = ? AND chunk_id IN ({placeholders})',
                                      [document_id, *allowed_ids]).fetchall()
        LOGGER.info('Excerpt tool called: document=%s locator=%s chunks=%s', document_id, locator, len(rows))
        return json.dumps([{'chunk_id': chunk_id, 'text': text} for chunk_id, text in rows])

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
    calls = [call for message in result['messages'] for call in getattr(message, 'tool_calls', [])
             if call.get('name') == 'get_document_excerpt']
    LOGGER.info('Answer agent completed: %s model-issued excerpt calls', len(calls))
    return {'draft': AnswerDraft.model_validate(result.get('structured_response')).model_dump()}


