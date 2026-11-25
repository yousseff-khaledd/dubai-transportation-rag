# SQLite baseline and Qdrant comparison

Baseline: [20261111_205750](../20261111_205750/report.md). New run: [20261125_133409](report.md). Both use 30 documents, 1,136 chunks, final top_k=5, gpt-4.1-mini, text-embedding-3-small, and Ragas 0.4.3. Case questions, references, expected statuses, source identifiers, filters, and modes were compared and match.

## End-to-end outcomes

| Measure | SQLite baseline | Qdrant run |
| --- | ---: | ---: |
| Expected status | 11/13 | 12/13 |
| First-attempt server/model errors | 2/13 | 1/13 |
| Expected document in chat top five | 8/10 | 10/10 |
| Valid cited answers | 8/10 | 9/10 |
| First-attempt chat reranker fallbacks | Not recorded | 2 |

Q06 retrieved the expected document but failed on both attempts because the answer agent exceeded its excerpt-tool limit. Q07 and Q09 answered after reranker fallback. These outcomes are preserved; retries do not replace first attempts.

## Quality scores with their actual denominators

| Metric | SQLite mean (8 answers) | Qdrant mean (9 answers) |
| --- | ---: | ---: |
| faithfulness | 0.9271 | 0.7685 |
| answer_correctness | 0.6504 | 0.7288 |
| context_precision | 0.9340 | 0.9241 |
| context_recall | 1.0000 | 1.0000 |
| answer_relevancy | 0.9207 | 0.9051 |

The groups above differ: Q06 was scored only in the old run; Q08 and Q09 were scored only in the new run. For a more comparable descriptive view, the following means use only the seven cases scored in both runs: Q01, Q02, Q03, Q04, Q05, Q07, Q10.

| Metric | SQLite, common 7 | Qdrant, common 7 |
| --- | ---: | ---: |
| faithfulness | 0.9643 | 0.7857 |
| answer_correctness | 0.6876 | 0.7613 |
| context_precision | 0.9246 | 0.9024 |
| context_recall | 1.0000 | 1.0000 |
| answer_relevancy | 0.9173 | 0.9142 |

## Separate search-mode comparison

| Mode | SQLite document hits | Qdrant document hits | Qdrant errors | Qdrant reranker fallbacks |
| --- | ---: | ---: | ---: | ---: |
| lexical | 10/10 | 10/10 | 0 | 0 |
| semantic | 10/10 | 10/10 | 0 | 0 |
| hybrid | 10/10 | 10/10 | 0 | 1 |

## Conclusions

- The current system completed more expected responses in this run, but still has an answer-agent tool-limit failure.
- Retrieval found the expected documents on this small set; it does not prove every answer claim is correct or establish that one search mode is superior.
- Judge scores are mixed. Source review flags a continuing attribution issue in Q02 and apparent judge mismatches in Q04 and Q10. Keep the raw scores; see [review.md](review.md).
- Qdrant and the reranker fallback changed together, so this is a before/after system comparison, not an isolated database experiment. Model variability and different scored-case sets also limit causal claims.
- Do not claim a speed improvement: first-attempt mean chat latency was 5.136s before and 6.817s now. These single-run timings include remote model calls and are not a database benchmark.
- Next work should address the tool-call failure and source attribution, then repeat the same evaluation and expand the reviewed question set.
