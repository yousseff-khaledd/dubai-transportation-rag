"""Work around Ragas 0.4.3's removed Vertex AI import for OpenAI-only evaluations."""
import sys
from types import ModuleType

from langchain_community.llms import VertexAI

# https://github.com/vibrantlabsai/ragas/issues/2995
# Ragas only uses this class for an isinstance check; this evaluator uses OpenAI.
vertex_module = ModuleType('langchain_community.chat_models.vertexai')
vertex_module.ChatVertexAI = VertexAI
sys.modules.setdefault(vertex_module.__name__, vertex_module)
