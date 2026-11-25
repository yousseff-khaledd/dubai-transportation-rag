"""Local Qdrant collection shared by ingestion, search, and evidence lookup."""

import hashlib
import atexit
from functools import lru_cache
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import QdrantClient, models

PROJECT_DIR = Path(__file__).resolve().parents[2]
QDRANT_PATH = PROJECT_DIR / 'data' / 'qdrant'
COLLECTION = 'roads_transport'


@lru_cache
def _client(path: str) -> QdrantClient:
    database = QdrantClient(path=path)
    atexit.register(database.close)
    return database


def client() -> QdrantClient:
    QDRANT_PATH.parent.mkdir(parents=True, exist_ok=True)
    return _client(str(QDRANT_PATH))


def point_id(chunk_id: str) -> str:
    """Qdrant accepts UUIDs, while public citations retain the existing chunk IDs."""
    return str(uuid5(NAMESPACE_URL, f'dubai-transport/{chunk_id}'))


def chunk_payload(chunk_id: str) -> dict | None:
    if not client().collection_exists(COLLECTION):
        return None
    points = client().retrieve(COLLECTION, ids=[point_id(chunk_id)], with_payload=True)
    return points[0].payload if points else None


def index_counts() -> tuple[int, int]:
    if not client().collection_exists(COLLECTION):
        return 0, 0
    document_ids = set()
    offset = None
    while True:
        points, offset = client().scroll(COLLECTION, offset=offset, limit=256, with_payload=['document_id'])
        document_ids.update(point.payload['document_id'] for point in points)
        if offset is None:
            break
    return len(document_ids), client().count(COLLECTION, exact=True).count


def model_key(base_url: str, model_name: str) -> str:
    return hashlib.sha256(f'{base_url}/{model_name}'.encode()).hexdigest()


def document_filter(document_id: str) -> models.Filter:
    return models.Filter(must=[models.FieldCondition(key='document_id', match=models.MatchValue(value=document_id))])


def ensure_collection(vector_size: int, embedding_key: str) -> None:
    database = client()
    if database.collection_exists(COLLECTION):
        info = database.get_collection(COLLECTION)
        if info.config.metadata != {'embedding_key': embedding_key} or info.config.params.vectors.size != vector_size:
            raise ValueError('Qdrant index uses a different embedding deployment or vector size; rebuild it')
        return
    database.create_collection(
        collection_name=COLLECTION,
        vectors_config=models.VectorParams(size=vector_size, distance=models.Distance.COSINE),
        metadata={'embedding_key': embedding_key},
    )
    # Local Qdrant supports payload filtering, but only scored text indexes
    # affect its local query engine. Scalar payload indexes require a server.
    database.create_payload_index(
        COLLECTION, 'text', models.TextIndexParams(type=models.TextIndexType.TEXT, scoring=True)
    )
