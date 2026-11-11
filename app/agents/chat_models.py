"""Request, response, and graph state models for chat."""
from typing import Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.rag.search import SearchFilters


class ThreadMessage(BaseModel):
    model_config = ConfigDict(extra='forbid')
    thread_id: str = Field(min_length=1, max_length=100, pattern=r'^[a-zA-Z0-9_-]+$')
    message: str = Field(min_length=1, max_length=2000)

    @field_validator('message')
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError('message must contain text')
        return value.strip()


class ChatRequest(ThreadMessage):
    mode: Literal['lexical', 'semantic', 'hybrid'] = 'hybrid'
    top_k: int = Field(default=10, ge=1, le=20, strict=True)
    filters: SearchFilters = Field(default_factory=SearchFilters)


class ResumeRequest(ThreadMessage):
    interrupt_id: str = Field(min_length=1, max_length=200)


class Citation(BaseModel):
    chunk_id: str
    document_id: str
    locator: str


class AnswerDraft(BaseModel):
    status: Literal['answered', 'insufficient_evidence']
    answer: str | None
    citations: list[Citation]


class QueryPlan(BaseModel):
    route: Literal['retrieve', 'out_of_scope', 'needs_clarification']
    search_query: str = Field(description='Standalone search question, resolving references from conversation history')
    clarification: str | None = Field(description='A short clarifying question when needed')


class ChatError(BaseModel):
    code: str
    message: str


class ChatResponse(BaseModel):
    # Invalid JSON or a missing/invalid thread ID cannot supply a valid thread_id.
    thread_id: str | None
    request_id: str
    status: Literal['answered', 'insufficient_evidence', 'out_of_scope', 'needs_clarification', 'error']
    answer: str | None = None
    citations: list[Citation] = Field(default_factory=list)
    clarification: str | None = None
    interrupt_id: str | None = None
    error: ChatError | None = None


class ChatState(TypedDict, total=False):
    """Shared conversation state; LangGraph checkpoints it by thread_id."""
    request: dict
    request_id: str
    history: list[dict]
    plan: dict
    evidence: list[dict]
    draft: dict
    response: dict
    clarifications: list[dict]


