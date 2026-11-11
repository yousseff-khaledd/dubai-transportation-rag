# Ragas evaluation

Run: `20261111_205750`. Corpus: 30 documents, 1136 chunks. Chat model: `gpt-4.1-mini`; embedding model: `text-embedding-3-small`; Ragas: `0.4.3`; final top_k: 5.

## First-attempt results

- Expected status: 11/13 cases.
- API/server errors: 2 cases.
- Expected document in selected chat top 5: 8/10 answer cases.
- Valid citations to selected chunks: 8/10 answer cases.
- Chat request elapsed time: mean 5.136s, median 6.108s (local API and model calls, excluding Ragas judging).
- Follow-up continuity and thread isolation: pass.

## Ragas scores

| Metric | Scored cases | Mean | Median |
| --- | ---: | ---: | ---: |
| faithfulness | 8 | 0.9271 | 1.0 |
| answer_correctness | 8 | 0.6504 | 0.528 |
| context_precision | 8 | 0.934 | 1.0 |
| context_recall | 8 | 1.0 | 1.0 |
| answer_relevancy | 8 | 0.9207 | 0.9313 |

Scores are judge estimates, not search probabilities. Unanswered and failed requests have no Ragas score; the denominator for each mean is shown above. Review individual cases before acting on a small difference.

## Search mode comparison

| Mode | Expected document in final top 5 | Errors |
| --- | ---: | ---: |
| lexical | 10/10 | 0 |
| semantic | 10/10 | 0 |
| hybrid | 10/10 | 0 |

These mode calls use the same original question. Chat may rewrite the question; its Ragas contexts come from the actual checkpointed chat evidence, not from these separate search calls.

## Cases

| ID | Expected / actual first status | Document rank | Faithfulness | Answer correctness | Context precision | Context recall | Answer relevancy |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Q01 | answered / answered | 1 | 1.000 | 0.964 | 1.000 | 1.000 | 1.000 |
| Q02 | answered / answered | 1 | 1.000 | 0.512 | 0.806 | 1.000 | 0.918 |
| Q03 | answered / answered | 1 | 1.000 | 0.428 | 0.750 | 1.000 | 0.882 |
| Q04 | answered / answered | 1 | 1.000 | 0.951 | 1.000 | 1.000 | 0.996 |
| Q05 | answered / answered | 1 | 1.000 | 0.988 | 1.000 | 1.000 | 1.000 |
| Q06 | answered / answered | 1 | 0.667 | 0.390 | 1.000 | 1.000 | 0.944 |
| Q07 | answered / answered | 1 | 0.750 | 0.544 | 0.917 | 1.000 | 0.708 |
| Q08 | answered / error | - | - | - | - | - | - |
| Q09 | answered / error | - | - | - | - | - | - |
| Q10 | answered / answered | 1 | 1.000 | 0.425 | 1.000 | 1.000 | 0.916 |
| N01 | insufficient_evidence / insufficient_evidence | - | - | - | - | - | - |
| N02 | out_of_scope / out_of_scope | - | - | - | - | - | - |
| N03 | needs_clarification / needs_clarification | - | - | - | - | - | - |

## Failures and judge errors

- **Q08**: first HTTP 502, status `error`; metric errors `{}`. Retry HTTP 502, status `error`.
- **Q09**: first HTTP 502, status `error`; metric errors `{}`. Retry HTTP 502, status `error`.

## Lowest answer-correctness scores for review

### Q06 - 0.390

- Question: Which Schedule (5) railway violation items require a written warning before a fine under Administrative Resolution No. (817) of 2024?
- Reference: Items (3), (4), and (5) of Schedule (5) require a written warning before a fine.
- Answer: Under Administrative Resolution No. (817) of 2024, the Schedule 5 railway violation items that require a written warning before a fine are specifically the violations set out in Items (3), (4), and (5) of Schedule (5) attached to Executive Council Resolution No. (1) of 2017 and its amendments.
- Selected chunks: 48f24bb9df8edb1d13b7, fd840cbe301a1477e203, bc713b6910b665da44f4, 6d9c8e5904f747c06b4d, ec6cd26cf0e65a1a6b78

### Q10 - 0.425

- Question: How does Regulation No. (1) of 2007 define a taxicab?
- Reference: A taxicab is a vehicle designated for transporting passengers in return for a specific fare.
- Answer: According to Regulation No. (1) of 2007 Concerning Passenger Transport by Taxicabs in the Emirate of Dubai, a Taxicab is defined as vehicles designated for transporting passengers in return for a specific fare.
- Selected chunks: ccb4498ed146b396a8bb, 2ebedd5e941422b42087, 963ee6bd1d167e90a5b9, ab8b1254f90091cb6b70, dae7a52ef7cd78cc4b66

### Q03 - 0.428

- Question: What departure fee does Law No. (13) of 2022 charge passengers leaving through Dubai airports?
- Reference: The departure fee is AED 75.00 per passenger departing the UAE through Dubai airports, subject to the law's exemptions.
- Answer: Law No. (13) of 2022 charges a departure fee of seventy-five dirhams (AED 75.00) from every passenger departing the United Arab Emirates through Dubai airports. However, exemptions apply to passengers below the age of two years, on-duty pilots and cabin crew, transit passengers whose arrival and departure flight number is the same, and transfer passengers whose final destination is not the UAE and whose departure is within 24 hours from the time of arrival.
- Selected chunks: 23d885d0bfc09fdf430f, 143f6a89d24557b4f6b0, 42c2646c0435d4bc6322, 3185909575071b59b65e, f3881128ebf56f71b107


## Interpretation

The ten source-grounded questions are a small, corpus-derived set. Human review of low-scoring examples is required: an LLM judge can misread legal exceptions or table labels, especially when it uses the same model family as the answerer. Do not treat these averages as accuracy on unseen questions. `results.json` retains each answer, selected chunk text, citation, model error, and score for audit.
