"""Conversation orchestrator with checkpointed state and per-thread locking."""
import logging
import sqlite3
from contextlib import closing, contextmanager
from threading import Lock
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from app.agents.chat_models import ChatError, ChatRequest, ChatResponse, ChatState, ResumeRequest
from app.agents.chat_nodes import (
    ask_clarification, draft_answer, input_guard, question_guard, response_guard,
    retrieve_evidence, route_after_question_guard,
)
from app.rag.ingest import PROJECT_DIR

LOGGER = logging.getLogger('uvicorn.error')
CHECKPOINT_PATH = PROJECT_DIR / 'data' / 'checkpoints.sqlite'
ACTIVE_THREADS: set[str] = set()
THREAD_LOCK = Lock()


class ChatConflict(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@contextmanager
def reserve_thread(thread_id: str):
    # One Uvicorn worker: check and reserve atomically; never queue duplicate runs.
    with THREAD_LOCK:
        if thread_id in ACTIVE_THREADS:
            raise ChatConflict('thread_busy', 'This thread already has a running request.')
        ACTIVE_THREADS.add(thread_id)
    try:
        yield
    finally:
        with THREAD_LOCK:
            ACTIVE_THREADS.remove(thread_id)


def build_chat_graph(checkpointer):
    """Connect the conversation nodes and persist their shared ChatState."""
    graph = StateGraph(ChatState)
    graph.add_node('input_guard', input_guard)
    graph.add_node('question_guard', question_guard)
    graph.add_node('ask_clarification', ask_clarification)
    graph.add_node('retrieve_evidence', retrieve_evidence)
    graph.add_node('draft_answer', draft_answer)
    graph.add_node('response_guard', response_guard)
    graph.add_edge(START, 'input_guard')
    graph.add_edge('input_guard', 'question_guard')
    graph.add_conditional_edges('question_guard', route_after_question_guard, {
        'retrieve': 'retrieve_evidence',
        'out_of_scope': 'response_guard',
        'needs_clarification': 'ask_clarification',
        'clarification_limit': 'response_guard',
    })
    graph.add_edge('ask_clarification', 'question_guard')
    graph.add_edge('retrieve_evidence', 'draft_answer')
    graph.add_edge('draft_answer', 'response_guard')
    graph.add_edge('response_guard', END)
    return graph.compile(checkpointer=checkpointer)


def run_chat(request: ChatRequest | ResumeRequest, request_id: str | None = None) -> ChatResponse:
    """Run or resume the conversation graph for one thread."""
    request_id = request_id or str(uuid4())
    config = {'configurable': {'thread_id': request.thread_id}, 'recursion_limit': 40}
    CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with reserve_thread(request.thread_id), closing(sqlite3.connect(
            CHECKPOINT_PATH, check_same_thread=False, timeout=10)) as connection:
        graph = build_chat_graph(SqliteSaver(connection))
        pending = graph.get_state(config).interrupts
        if isinstance(request, ResumeRequest):
            if len(pending) != 1 or pending[0].id != request.interrupt_id:
                raise ChatConflict('invalid_resume', 'No matching pending interrupt exists on this thread.')
            graph_input = Command(resume={request.interrupt_id: {'message': request.message, 'request_id': request_id}})
            LOGGER.info('Chat resuming: thread=%s request=%s interrupt=%s', request.thread_id, request_id, request.interrupt_id)
        else:
            if pending:
                raise ChatConflict('clarification_pending', 'Resume the pending clarification using POST /chat/resume.')
            graph_input = {'request': request.model_dump(), 'request_id': request_id}
        result = graph.invoke(graph_input, config=config)
    if result.get('__interrupt__'):
        pending_interrupt = result['__interrupt__'][0]
        LOGGER.info('Chat paused: thread=%s request=%s interrupt=%s attempt=%s', request.thread_id,
                    request_id, pending_interrupt.id, pending_interrupt.value['attempt'])
        return ChatResponse(thread_id=request.thread_id, request_id=request_id, status='needs_clarification',
                            clarification=pending_interrupt.value['question'], interrupt_id=pending_interrupt.id)
    return ChatResponse.model_validate(result['response'])
