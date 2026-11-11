"""HTTP entry point for the Roads and Transport knowledge assistant."""

import sqlite3
import logging
import re
from contextlib import closing
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError
from threading import Lock
from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langchain.agents.middleware.tool_call_limit import ToolCallLimitExceededError
from langchain.agents.structured_output import StructuredOutputError
from langchain_core.exceptions import OutputParserException
from langgraph.errors import GraphRecursionError

from app.rag.search import EmbeddingFailure, SearchUnavailable, SearchRequest, synchronize_search_indexes

from app.rag.ingest import INDEX_PATH, IngestResult, ingest_corpus
from app.agents.retrieval_graph import RerankFailure, RetrievalResponse, run_retrieval
from app.agents.chat import ChatConflict, ChatError, ChatRequest, ChatResponse, ResumeRequest, run_chat
from openai import APITimeoutError, OpenAIError


INGEST_LOCK = Lock()
LOGGER = logging.getLogger('uvicorn.error')

app = FastAPI(title="Roads and Transport Knowledge Assistant", version="0.1.0")


@app.middleware('http')
async def identify_request(request: Request, call_next):
    request.state.request_id = str(uuid4())
    response = await call_next(request)
    response.headers['X-Request-ID'] = request.state.request_id
    return response


def chat_error(request: Request, thread_id: str | None, status_code: int, code: str, message: str) -> JSONResponse:
    LOGGER.warning('Chat request failed: thread=%s request=%s code=%s', thread_id, request.state.request_id, code)
    response = ChatResponse(thread_id=thread_id, request_id=request.state.request_id, status='error',
                            error=ChatError(code=code, message=message))
    return JSONResponse(status_code=status_code, content=response.model_dump())


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, error: RequestValidationError):
    if request.url.path not in ('/chat', '/chat/resume'):
        return await request_validation_exception_handler(request, error)
    body = error.body if isinstance(error.body, dict) else {}
    thread_id = body.get('thread_id')
    if not isinstance(thread_id, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', thread_id):
        thread_id = None
    invalid_filters = any('filters' in issue['loc'] for issue in error.errors())
    return chat_error(request, thread_id, 422, 'invalid_filters' if invalid_filters else 'invalid_request',
                      'Check filter year (1900–2100) and type (Law, Resolution, Regulation).'
                      if invalid_filters else 'Invalid request body. Check the schema at /docs.')


def execute_chat(body: ChatRequest | ResumeRequest, request: Request):
    try:
        return run_chat(body, request_id=request.state.request_id)
    except ChatConflict as error:
        return chat_error(request, body.thread_id, 409, error.code, str(error))
    except SearchUnavailable:
        return chat_error(request, body.thread_id, 503, 'search_unavailable', 'The index or model configuration is unavailable.')
    except (APITimeoutError, TimeoutError):
        return chat_error(request, body.thread_id, 504, 'model_timeout', 'The model service timed out. Please try again.')
    except (sqlite3.Error, OSError):
        return chat_error(request, body.thread_id, 503, 'storage_unavailable', 'The index or conversation storage is unavailable.')
    except (ModelCallLimitExceededError, ToolCallLimitExceededError, GraphRecursionError):
        return chat_error(request, body.thread_id, 502, 'call_limit_exceeded', 'The agent exceeded its call limit.')
    except (EmbeddingFailure, RerankFailure, OpenAIError, ValidationError, OutputParserException, StructuredOutputError):
        return chat_error(request, body.thread_id, 502, 'model_failure', 'A model request failed or returned an invalid response.')
    except Exception as error:
        LOGGER.error('Unexpected chat failure: request=%s type=%s', request.state.request_id, type(error).__name__)
        return chat_error(request, body.thread_id, 500, 'internal_error', 'The chat request could not be completed.')


class HealthResponse(BaseModel):
    ready: bool
    index_available: bool


def has_indexed_chunks() -> bool:
    if not INDEX_PATH.is_file():
        return False

    try:
        with closing(sqlite3.connect(f"file:{INDEX_PATH}?mode=ro", uri=True)) as connection:
            return connection.execute("SELECT 1 FROM chunks LIMIT 1").fetchone() is not None
    except sqlite3.Error:
        return False


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(ready=True, index_available=has_indexed_chunks())


@app.post("/ingest", response_model=IngestResult)
def ingest() -> IngestResult:
    if not INGEST_LOCK.acquire(blocking=False):
        raise HTTPException(409, "Ingestion is already running")
    try:
        result = ingest_corpus()
        result.embeddings_written = synchronize_search_indexes()["embeddings_written"]
        return result
    except SearchUnavailable as error:
        raise HTTPException(503, str(error)) from error
    except (EmbeddingFailure, RerankFailure) as error:
        raise HTTPException(502, str(error)) from error
    finally:
        INGEST_LOCK.release()


@app.post("/search", response_model=RetrievalResponse)
def search_chunks(request: SearchRequest) -> dict:
    try:
        return run_retrieval(request)
    except SearchUnavailable as error:
        raise HTTPException(503, str(error)) from error
    except (EmbeddingFailure, RerankFailure) as error:
        raise HTTPException(502, str(error)) from error


@app.post("/chat", response_model=ChatResponse)
def chat(body: ChatRequest, request: Request):
    return execute_chat(body, request)


@app.post('/chat/resume', response_model=ChatResponse)
def resume_chat(body: ResumeRequest, request: Request):
    return execute_chat(body, request)
