# Route — local chat page

A single-page HTML/CSS/JavaScript interface for the existing Dubai transport API. No frontend build, npm packages, or backend edits are required.

Keep the existing API running on `127.0.0.1:8000`. In another terminal, from the project directory:

```sh
python3 frontend/server.py
```

Open **http://127.0.0.1:3000**. Stop this frontend server with Ctrl+C.

The small Python standard-library server serves only the three frontend assets and forwards `/api/health`, `/api/chat`, and `/api/chat/resume` to the existing backend. This avoids needing to change backend CORS settings. It binds only to localhost and contains no API credentials. Model calls remain in the existing backend. Google Fonts is optional; local fallback fonts work offline.

## Using the page

- Choose hybrid, lexical, or semantic search; the default is hybrid with 10 chunks.
- Optionally filter by year or document type.
- Type a message and click Send, or press Enter. Shift+Enter adds a line break.
- A clarification response automatically switches the next message to `/chat/resume` using the returned interrupt ID. Search settings stay fixed until clarification ends.
- Source cards show the document ID and PDF page locator supplied by the API. Expand Response details for chunk IDs, request IDs, status, and the full JSON response. The existing API does not provide citation download URLs, so these cards are references rather than links.
- New conversation creates a fresh thread ID. Copy ID copies the current ID for debugging.
- Conversation messages and a pending interrupt survive refreshes in the same tab using session storage. New conversation clears the visible conversation; backend checkpoints are retained.
- Avoid refreshing while a request is running: the API has no endpoint to recover a response or interrupt ID that the browser never received. The UI warns about this situation and lets you start a new conversation.

The connection badge checks `/health` at startup and after requests; click it to check again. A healthy index does not guarantee the model service is available. API errors are displayed without silently retrying model calls. The proxy allows up to 900 seconds for a chat network operation because the existing backend can make multiple model calls; closing the page does not cancel backend work.
