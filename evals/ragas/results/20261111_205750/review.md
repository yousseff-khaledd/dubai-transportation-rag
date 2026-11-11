# Manual review of this Ragas run

This review accompanies `results.json` and `report.md`. It does not replace the recorded Ragas values.

- **Q08 and Q09:** `/chat` returned `model_failure` (HTTP 502) on the first attempt and one labeled retry for each. The retrieval graph logged `RerankFailure`. Separate `/search` mode comparisons found the expected documents in all three modes for both questions. The end-to-end failures therefore need reranker reliability work; they are not proof that the source documents are absent.
- **Q06:** Ragas assigned faithfulness `0.667` and answer correctness `0.390`. The response names Schedule (5) Items (3), (4), and (5), matching the reference and cited chunk `48f24bb9df8edb1d13b7`. The low judge scores appear inconsistent with this direct passage check.
- **Q10:** Answer correctness was `0.425` although the answer repeats the cited taxicab definition with only introductory wording added. This is another apparent judge mismatch.
- **Q03:** The answer gives AED 75 and lists exemptions; the short reference says AED 75 subject to exemptions. Faithfulness was `1.0` while answer correctness was `0.428`. The reference is less detailed than the response, so this score should be reviewed manually before treating it as a factual error.
- **Q02:** The answer adds “comprehensive insurance policy” and cites two documents. The reviewed reference for Law No. (9) of 2023 only requires insurance with a UAE-licensed insurer as prescribed by the Director General. The extra wording may draw from another selected document; review this as a possible cross-document attribution problem even though Ragas faithfulness was `1.0` over all selected chunks.
- **Q07:** The answer adds a tag requirement beyond the question's toll trigger. Answer relevancy was `0.708`; that lower score is plausible because the response includes extra material.

All eight scored answers had context recall `1.0`, but most references express only one main claim. This metric has limited power on this small set. The judge uses the same `gpt-4.1-mini` deployment family as the answerer; the scores need source-based review and a larger evaluation set before setting thresholds.
