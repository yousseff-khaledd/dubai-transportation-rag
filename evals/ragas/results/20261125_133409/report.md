# Ragas evaluation

Database: `qdrant-local`. Collection: `roads_transport`.

Run: `20261125_133409`. Corpus: 30 documents, 1136 chunks. Chat model: `gpt-4.1-mini`; embedding model: `text-embedding-3-small`; Ragas: `0.4.3`; final top_k: 5.

## First-attempt results

- Expected status: 12/13 cases.
- API/server errors: 1 cases.
- First-attempt chat reranker fallbacks: 2.
- Expected document in selected chat top 5: 10/10 answer cases.
- Valid citations to selected chunks: 9/10 answer cases.
- Chat request elapsed time: mean 6.817s, median 7.769s (local API and model calls, excluding Ragas judging).
- Follow-up continuity and thread isolation: pass.

## Ragas scores

| Metric | Scored cases | Mean | Median |
| --- | ---: | ---: | ---: |
| faithfulness | 9 | 0.7685 | 1.0 |
| answer_correctness | 9 | 0.7288 | 0.8057 |
| context_precision | 9 | 0.9241 | 1.0 |
| context_recall | 9 | 1.0 | 1.0 |
| answer_relevancy | 9 | 0.9051 | 0.9472 |

Scores are judge estimates, not search probabilities. Unanswered and failed requests have no Ragas score; the denominator for each mean is shown above. Review individual cases before acting on a small difference.

## Search mode comparison

| Mode | Expected document in final top 5 | Errors | Reranker fallbacks |
| --- | ---: | ---: | ---: |
| lexical | 10/10 | 0 | 0 |
| semantic | 10/10 | 0 | 0 |
| hybrid | 10/10 | 0 | 1 |

These mode calls use the same original question. Chat may rewrite the question; its Ragas contexts come from the actual checkpointed chat evidence, not from these separate search calls.

## Cases

| ID | Expected / actual first status | Document rank | Faithfulness | Answer correctness | Context precision | Context recall | Answer relevancy |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Q01 | answered / answered | 1 | 1.000 | 0.964 | 0.867 | 1.000 | 1.000 |
| Q02 | answered / answered | 1 | 0.500 | 0.594 | 1.000 | 1.000 | 0.867 |
| Q03 | answered / answered | 1 | 1.000 | 0.429 | 0.700 | 1.000 | 0.880 |
| Q04 | answered / answered | 1 | 0.000 | 0.951 | 1.000 | 1.000 | 0.996 |
| Q05 | answered / answered | 1 | 1.000 | 0.988 | 1.000 | 1.000 | 1.000 |
| Q06 | answered / error | 1 | - | - | - | - | - |
| Q07 | answered / answered | 1 | 1.000 | 0.962 | 1.000 | 1.000 | 0.709 |
| Q08 | answered / answered | 1 | 0.667 | 0.424 | 1.000 | 1.000 | 0.971 |
| Q09 | answered / answered | 1 | 0.750 | 0.806 | 1.000 | 1.000 | 0.776 |
| Q10 | answered / answered | 1 | 1.000 | 0.440 | 0.750 | 1.000 | 0.947 |
| N01 | insufficient_evidence / insufficient_evidence | - | - | - | - | - | - |
| N02 | out_of_scope / out_of_scope | - | - | - | - | - | - |
| N03 | needs_clarification / needs_clarification | - | - | - | - | - | - |

## Failures and judge errors

- **Q06**: first HTTP 502, status `error`; metric errors `{}`. Retry HTTP 502, status `error`.

## Lowest answer-correctness scores for review

### Q08 - 0.424

- Question: Does Law No. (4) of 2020 apply to all types of unmanned aircraft regardless of their use or control systems?
- Reference: Yes. It applies to all types of unmanned aircraft regardless of use or control system. Military-purpose aircraft operated in coordination with the DCAA are excluded.
- Answer: Yes, Law No. (4) of 2020 applies to all types of unmanned aircraft regardless of their use or control systems in Dubai transport legislation. However, it does not apply to unmanned aircraft used for military purposes, provided they are operated in coordination with the DCAA to ensure safety of operations and civil aviation.
- Selected chunks: 0386faaeae372c54791f, a5155cccba2fb3b274ac, b17f0d606533738ee3e6, 9a9d07ce100a117842a4, 3cdb2f187edcbb04bb84

### Q03 - 0.429

- Question: What departure fee does Law No. (13) of 2022 charge passengers leaving through Dubai airports?
- Reference: The departure fee is AED 75.00 per passenger departing the UAE through Dubai airports, subject to the law's exemptions.
- Answer: Law No. (13) of 2022 charges a departure fee of seventy-five dirhams (AED 75.00) from every passenger departing the United Arab Emirates through Dubai airports. Exemptions include passengers below the age of two years, on-duty pilots and cabin crew, transit passengers whose arrival and departure flight number is the same, and transfer passengers whose final destination is not the UAE and whose departure is within 24 hours from the time of arrival.
- Selected chunks: 23d885d0bfc09fdf430f, 143f6a89d24557b4f6b0, 42c2646c0435d4bc6322, b681c9f3a39d50c3df3e, 3185909575071b59b65e

### Q10 - 0.440

- Question: How does Regulation No. (1) of 2007 define a taxicab?
- Reference: A taxicab is a vehicle designated for transporting passengers in return for a specific fare.
- Answer: According to Regulation No. (1) of 2007 in Dubai transport legislation, a taxicab is defined as "vehicles designated for transporting passengers in return for a specific fare."
- Selected chunks: ccb4498ed146b396a8bb, ab8b1254f90091cb6b70, dae7a52ef7cd78cc4b66, 2ebedd5e941422b42087, ffbaab8387bf7e543f6e


## Interpretation

The ten source-grounded questions are a small, corpus-derived set. Human review of low-scoring examples is required: an LLM judge can misread legal exceptions or table labels, especially when it uses the same model family as the answerer. Do not treat these averages as accuracy on unseen questions. `results.json` retains each answer, selected chunk text, citation, model error, and score for audit.
