"""Configured chat model shared by planning and answering."""
import os

from langchain_openai import ChatOpenAI

from app.rag.search import SearchUnavailable


def chat_model() -> ChatOpenAI:
    base_url = os.getenv('OPENAI_BASE_URL', '').rstrip('/')
    api_key = os.getenv('OPENAI_API_KEY', '')
    model = os.getenv('CHAT_MODEL', '')
    if not all((base_url, api_key, model)):
        raise SearchUnavailable('Chat model settings are missing from .env')
    return ChatOpenAI(model=model, base_url=base_url, api_key=api_key, temperature=0, timeout=60, max_retries=1)


