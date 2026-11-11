"""Ragas metrics over one actual chatbot answer and its selected evidence."""
import os

from dotenv import load_dotenv
from openai import AsyncOpenAI

from app.rag.ingest import PROJECT_DIR

# Ragas 0.4.3 imports a removed Vertex AI module even for OpenAI evaluations.
from evals.ragas import ragas_compat  # noqa: F401
from ragas.embeddings.base import embedding_factory
from ragas.llms import llm_factory
from ragas.metrics.collections import (
    AnswerCorrectness, AnswerRelevancy, ContextPrecision, ContextRecall, Faithfulness,
)

CORE_METRICS = ('faithfulness', 'answer_correctness', 'context_precision')
ALL_METRICS = CORE_METRICS + ('context_recall', 'answer_relevancy')


def build_metrics(names: tuple[str, ...]):
    load_dotenv(PROJECT_DIR / '.env')
    base_url = os.getenv('OPENAI_BASE_URL', '')
    api_key = os.getenv('OPENAI_API_KEY', '')
    chat_model = os.getenv('CHAT_MODEL', '')
    embedding_model = os.getenv('EMBEDDING_MODEL', '')
    if not all((base_url, api_key, chat_model, embedding_model)):
        raise ValueError('Evaluation model settings are missing from .env')

    client = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=60, max_retries=1)
    llm = llm_factory(chat_model, provider='openai', client=client)
    embeddings = embedding_factory('openai', model=embedding_model, client=client)
    metrics = {
        'faithfulness': Faithfulness(llm=llm),
        'answer_correctness': AnswerCorrectness(llm=llm, embeddings=embeddings),
        'context_precision': ContextPrecision(llm=llm),
        'context_recall': ContextRecall(llm=llm),
        'answer_relevancy': AnswerRelevancy(llm=llm, embeddings=embeddings),
    }
    return client, {name: metrics[name] for name in names}


async def score_answer(metrics: dict, question: str, answer: str, reference: str,
                       contexts: list[str]) -> tuple[dict[str, float | None], dict[str, str]]:
    scores = {}
    errors = {}
    arguments = {
        'faithfulness': {'user_input': question, 'response': answer, 'retrieved_contexts': contexts},
        'answer_correctness': {'user_input': question, 'response': answer, 'reference': reference},
        'context_precision': {'user_input': question, 'reference': reference, 'retrieved_contexts': contexts},
        'context_recall': {'user_input': question, 'reference': reference, 'retrieved_contexts': contexts},
        'answer_relevancy': {'user_input': question, 'response': answer},
    }
    for name, metric in metrics.items():
        try:
            scores[name] = float((await metric.ascore(**arguments[name])).value)
        except Exception as error:
            scores[name] = None
            errors[name] = type(error).__name__
    return scores, errors
