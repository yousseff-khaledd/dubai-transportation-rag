"""Planning, routing, and validation nodes for the chat graph."""
import json
import logging

from langgraph.types import interrupt

from app.agents.answer_agent import draft_answer
from app.agents.chat_models import AnswerDraft, ChatRequest, ChatResponse, ChatState, QueryPlan
from app.agents.model import chat_model
from app.agents.prompts.question_guard import QUERY_PLAN_PROMPT
from app.agents.retrieval_graph import run_retrieval
from app.rag.search import SearchRequest

LOGGER = logging.getLogger('uvicorn.error')
MAX_CLARIFICATIONS = 2


def input_guard(state: ChatState) -> dict:
    request = ChatRequest.model_validate(state['request'])
    LOGGER.info('Chat validation started: thread=%s', request.thread_id)
    # Clear all per-question state; only history is carried to the next turn.
    return {'request': request.model_dump(), 'clarifications': [],
            'plan': {}, 'evidence': [], 'draft': {}, 'response': {}}


def question_guard(state: ChatState) -> dict:
    request = ChatRequest.model_validate(state['request'])
    plan = QueryPlan.model_validate(chat_model().with_structured_output(QueryPlan).invoke([
        ('system', QUERY_PLAN_PROMPT), ('user', json.dumps({'history': state.get('history', [])[-8:],
            'question': request.message, 'clarifications': state.get('clarifications', [])}))]))
    LOGGER.info('Chat query plan: route=%s', plan.route)
    return {'plan': plan.model_dump()}


def route_after_question_guard(state: ChatState) -> str:
    if state['plan']['route'] == 'needs_clarification' and len(state['clarifications']) >= MAX_CLARIFICATIONS:
        return 'clarification_limit'
    return state['plan']['route']


def ask_clarification(state: ChatState) -> dict:
    question = state['plan']['clarification'] or 'Could you clarify your question?'
    # This node restarts on resume. Keep all work before interrupt pure.
    reply = interrupt({'question': question, 'attempt': len(state['clarifications']) + 1})
    return {'clarifications': state['clarifications'] + [{'question': question, 'answer': reply['message']}],
            'request_id': reply['request_id']}


def retrieve_evidence(state: ChatState) -> dict:
    request = ChatRequest.model_validate(state['request'])
    LOGGER.info('Chat retrieval node started')
    result = run_retrieval(SearchRequest(query=state['plan']['search_query'], mode=request.mode,
                                         top_k=request.top_k, filters=request.filters))
    return {'evidence': [hit.model_dump() for hit in result['results']]}


def response_guard(state: ChatState) -> dict:
    request = ChatRequest.model_validate(state['request'])
    plan = QueryPlan.model_validate(state['plan'])
    if plan.route == 'out_of_scope':
        response = ChatResponse(thread_id=request.thread_id, request_id=state['request_id'], status='out_of_scope')
    elif plan.route == 'needs_clarification':
        LOGGER.info('Clarification limit reached: thread=%s', request.thread_id)
        response = ChatResponse(thread_id=request.thread_id, request_id=state['request_id'], status='insufficient_evidence')
    else:
        draft = AnswerDraft.model_validate(state['draft'])
        allowed = {(hit['chunk_id'], hit['document_id'], hit['locator']) for hit in state['evidence']}
        valid = (draft.status == 'answered' and bool(draft.answer and draft.answer.strip()) and bool(draft.citations)
                 and all((c.chunk_id, c.document_id, c.locator) in allowed for c in draft.citations))
        response = ChatResponse(thread_id=request.thread_id, request_id=state['request_id'],
                                status='answered' if valid else 'insufficient_evidence',
                                answer=draft.answer if valid else None, citations=draft.citations if valid else [])
    history = state.get('history', []) + [{'question': request.message, 'clarifications': state.get('clarifications', []),
                                          'answer': response.answer, 'status': response.status}]
    LOGGER.info('Chat response validated: status=%s citations=%s', response.status, len(response.citations))
    return {'response': response.model_dump(), 'history': history}


