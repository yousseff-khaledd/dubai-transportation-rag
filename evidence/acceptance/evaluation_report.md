# Acceptance evaluation and score guide

Run `20261111_202614` (UTC), against the 30-document English Roads and Transport index. The question set was written from inspected indexed passages before the search run. Raw requests, responses, ranked chunk IDs, and trace data are in [results.json](results.json); the fixed questions and expected sources are in [questions.json](questions.json).

## What was measured

For each of ten corpus-grounded questions, hybrid search returned a final `top_k=5` list after LLM reranking. A **document hit** means the expected `document_id` appears anywhere in those five returned chunks. The denominator is ten questions, not 50 chunks. The expected chunk ID and page locator were also recorded to make each source check auditable.

**First attempt: 9/10 document hits (90%).** Q03 returned HTTP 502 because the reranking model produced an invalid ranking, so it counts as a first-attempt failure. A separately recorded retry returned HTTP 200 with expected document `1432` and expected chunk `23d885d0bfc09fdf430f` at rank 1. Thus, **10/10 were hits after one retry**, but that is not the first-attempt score. The retry appears under `retry_after_model_failure` in the Q03 result and in [evaluation_retry.log](evaluation_retry.log).

| Case | Question subject | Expected document | First attempt | Expected document rank on a successful response |
| --- | --- | ---: | --- | ---: |
| Q01 | Category One on-street parking, one hour at peak | 1817 | Hit | 1 |
| Q02 | Autonomous-vehicle insurance | 1525 | Hit | 1 |
| Q03 | Airport departure fee | 1432 | HTTP 502; hit on retry | 1 on retry |
| Q04 | Operating without a permit fine | 1915 | Hit | 1 |
| Q05 | Al Mamzar–Deira Old Souk water taxi fare | 236 | Hit | 1 |
| Q06 | Railway violations requiring written warning | 1739 | Hit | 1 |
| Q07 | Traffic toll trigger | 13 | Hit | 1 |
| Q08 | Scope of unmanned aircraft law | 1718 | Hit | 1 |
| Q09 | Maritime activity permit | 1875 | Hit | 1 |
| Q10 | Taxicab definition | 190 | Hit | 1 |

Each expected source phrase, document ID, chunk ID, and locator matched the current index before evaluation (`question_sources_valid` in the raw results). Nine successful first attempts returned the exact expected chunk at rank 1; Q03 did so on the retry. This set measures source retrieval on known corpus questions. It does not measure general answer accuracy, unknown-user questions, or statistical performance on a larger sample. There is no required score threshold in section 9 of the requirements.

## What each retrieval score means

The API labels `score_type`, `score_direction`, and `ranking_stage`. **The numeric score is from retrieval and is preserved after reranking.** The reranker returns an ordering of candidate IDs; it does not assign a new numeric relevance score. Consequently, the final list need not be sorted numerically by its displayed score.

| Mode | Calculation | Better direction | Example from the same parking question | How to read it |
| --- | --- | --- | --- | --- |
| Lexical | SQLite FTS5 BM25 over chunk text | Lower is better | −45.2627 on final rank 1 | Term-match ranking value; it is not a probability. A more negative value ranked higher before reranking. |
| Semantic | Dot product of unit-length query and chunk embedding vectors, equal to cosine similarity | Higher is better | 0.6944 on final rank 1 | Embedding similarity; it is not calibrated answer confidence. |
| Hybrid | Reciprocal rank fusion (RRF): sum of `1 / (60 + rank)` over the lexical and semantic ranked lists for a chunk | Higher is better | 0.0318 on final rank 1 | Rank agreement across branches. About 0.0164 is one branch at rank 1; about 0.0328 is rank 1 in both. It is neither a percentage nor a confidence score. |

The semantic search normalizes both stored chunk vectors and the query vector to length one. That makes their dot product cosine similarity. Hybrid deduplicates by chunk ID, adds one RRF contribution per branch where the chunk appears, and sends at most 20 unique candidates to a separate GPT-4.1-mini reranking call. The reranker receives the question and candidate text and returns an ordered list of indices. The model can change the order without changing the displayed BM25, cosine, or RRF values. **Do not compare the numeric values across modes**: they have different definitions and scales.

For Q01, the lexical pre-rerank first chunk was `2ef082e6fd0cd9442314`; semantic pre-rerank first was `a25fbea55bf0a5bf74cc`; hybrid pre-rerank first was `2ef082e6fd0cd9442314`. All three final lists put `a25fbea55bf0a5bf74cc` first. The live hybrid trace is `lexical_search → semantic_search → merge_candidates → rerank_candidates`. The two search branches are scheduled together; their order in the trace reflects completion, not a sequential dependency. A barrier-based test verifies that both branches enter before either is allowed to finish. There is one join and one reranking pass per search. The three modes should be compared case by case; these results do not establish that hybrid is always best.

## Other acceptance results

- **Corpus and ingestion:** 30 unique manifest document IDs; all records have English Roads and Transport metadata, all local PDF hashes match the manifest, and the saved portal page shows the Roads and Transport category selected. Two live `POST /ingest` calls each skipped all 30 already indexed documents, wrote zero chunks and embeddings, and failed zero documents. A temporary-index test confirmed an out-of-category record is rejected and re-ingestion retains identical chunk IDs. Three indexed text/page samples are saved in `parsing_samples`.
- **Filters:** Year `2023`, type `Law`, their combination, and a no-match year `1900` were run in lexical, semantic, and hybrid modes. Every returned chunk satisfied its requested filters; all three no-match cases returned zero chunks. Filters apply before each branch ranks its results.
- **Cited answer:** The parking question returned `answered` with “4.00 dirhams” and citation `(document 1817, chunk a25fbea55bf0a5bf74cc, PDF page 5)`. The cited indexed table row explicitly states `On-street during peak hours: 4.00 dirhams` under Category One one-hour parking. The API also checks that returned citations belong to selected evidence.
- **Abstention and scope:** The in-scope question about Dubai vehicle permit fees in 1900, with year filter `1900`, returned `insufficient_evidence`, null answer, and no citations. “How do I cook pasta?” returned `out_of_scope`.
- **Conversation state:** Thread A answered an autonomous-vehicle question, then resolved “those vehicles” in a follow-up insurance question. A fresh thread B asked the same follow-up and requested clarification about the vehicle type instead of using A's history.
- **Durable clarification:** A question paused with `needs_clarification` and a saved interrupt ID. The API process changed from PID `22593` to `22642`; resuming on the original thread then returned an answer. Wrong-thread and repeated resumes returned HTTP 409. The saved checkpoint’s pending interrupt ID is captured in `restart.saved_state`.
- **Tool and middleware:** The live log records model-issued `get_document_excerpt` calls with document ID, locator, and returned chunk count, plus the real LangChain model-call timing middleware and completed tool-call counts. Tests exercise call limits and controlled model failures.
- **API validation:** Empty message, invalid `top_k`, unsupported mode, and invalid filter returned HTTP 422. A resume with no matching pending interrupt returned HTTP 409. The OpenAPI schema and full error envelopes are captured in the raw results.

The deterministic suite passed **27/27 tests**; see [unit_tests.txt](unit_tests.txt). The main live trace is [server.log](server.log). Model output can vary between runs: the previous full run found 10/10 expected documents on first attempt, while this run had one reranker format failure. The report uses this final run's first-attempt result and preserves the failure instead of replacing it with the retry.

## Reproduce

From the project root, with the configured Azure model credentials and existing PDF corpus:

```sh
uv sync --frozen
uv run python -m scripts.run_acceptance
```

The runner starts a temporary local Uvicorn server on `127.0.0.1:8011`, records its responses and logs under `evidence/acceptance/`, and stops it afterward. It does not alter a server on port 8000. A new run overwrites `results.json`, `server.log`, and `unit_tests.txt` with current evidence; model-dependent values may differ.
