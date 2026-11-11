# Section 9 acceptance checklist

Evidence run: `20261111_202614`. The raw [results.json](results.json), [server.log](server.log), [unit_tests.txt](unit_tests.txt), fixed [questions.json](questions.json), and [evaluation report](evaluation_report.md) form the demonstration record.

| ID | Result | Evidence |
| --- | --- | --- |
| AT01 Corpus boundary | Pass | `corpus`: 30 distinct English Roads and Transport records, matching PDF hashes and saved portal filter; `test_out_of_category_record_is_not_indexed` rejects an excluded category. |
| AT02 Parsing and repeat ingestion | Pass | `parsing_samples`: three documents with text and page locators; two live ingestion calls skip 30 documents and write zero duplicates; `test_repeat_ingestion_does_not_duplicate_chunks`. |
| AT03 Three search modes | Pass | `search_modes`: same Q01 query, inspectable BM25, cosine, and RRF lists and pre/post rerank order; explanation in evaluation report. |
| AT04 Filters | Pass | `filters`: year, type, combined, and no-match for all three modes; every returned chunk matches; no-match lists empty. |
| AT05 Hybrid graph | Pass | `search_modes.hybrid.trace`, `before_rerank`, `after_rerank`, live node logs; `test_hybrid_branches_overlap_before_join` verifies true branch overlap before one join. |
| AT06 Supported cited answer | Pass | `supported_answer` and `cited_excerpts`: Category One on-street peak one-hour fee is 4.00 dirhams; citation resolves to document 1817, PDF page 5, matching table row. |
| AT07 Abstention and unrelated question | Pass | `abstention` returns `insufficient_evidence` with null answer and no citations; `out_of_scope` returns `out_of_scope`. |
| AT08 Conversation memory isolation | Pass | `memory`: thread A resolves follow-up; thread B asks for vehicle clarification instead of using A's context. |
| AT09 Interrupt and restart | Pass | `restart`: pending interrupt saved in SQLite, server PID changes, same-thread resume answers; wrong-thread and repeated resume return 409. |
| AT10 Tool, middleware, controlled failure | Pass | `tool_and_middleware_trace` plus `server.log`: model-issued excerpt calls, tool results as chunk counts, timing middleware; `test_answer_agent_call_limits` and `test_model_failure_is_controlled`. |
| AT11 API contracts and errors | Pass | `openapi` and `api_errors`: standard response fields and controlled 422/409 cases; error-envelope unit tests. |
| AT12 Ten-question retrieval evaluation | Completed with one first-attempt failure | Fixed `questions.json` validated against index; 9/10 expected documents in final top five on first attempt. Q03 returned HTTP 502 from invalid reranker output, then expected document/chunk ranked first on a labeled retry. See report. |

## Demonstration order

1. Run `uv run python -m scripts.run_acceptance`; inspect `ingestion` and `corpus` in `results.json`.
2. Inspect `search_modes` for one query across lexical, semantic, and hybrid. Compare `trace`, `before_rerank`, `after_rerank`, and score labels.
3. Inspect `supported_answer`, `cited_excerpts`, and `filters`.
4. Inspect `memory`, then `restart` and its saved interrupt/PID change.
5. Inspect `abstention`, `out_of_scope`, `api_errors`, `tool_and_middleware_trace`, and the ten `evaluation` cases.

The first-attempt Q03 failure is retained in the raw result. Its retry is separate evidence, not a replacement for the 9/10 score.
