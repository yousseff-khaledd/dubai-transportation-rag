# Offline Ragas evaluation

Run from the project root after indexing the PDFs and configuring the existing `.env`:

```sh
uv sync --frozen --group eval
uv run --group eval python -m evals.ragas.run
```

A short smoke test runs one case and only the three core metrics:

```sh
uv run --group eval python -m evals.ragas.run --limit 1 --metrics core --skip-modes
```

`cases.json` contains each evaluation question and its expected answer or response status in one file. Ten source-grounded cases also contain their expected document, chunk, PDF locator, and source phrase; three more cover unsupported, unrelated, and ambiguous questions. The runner validates each positive case against the index before making model calls. Edit reference answers only after checking the linked PDF page. This set is intentionally small and corpus-derived; it is a starting baseline, not an estimate of performance on unseen questions.

For each case, the runner calls the real FastAPI `/chat` handler through `TestClient` with a unique thread ID. It reads the resulting LangGraph SQLite checkpoint to capture the **exact selected evidence** used by the answer agent. A separate `/search` call could have different evidence because chat may rewrite the question and the reranker is model-based. The runner retries one HTTP 502/504 answer request in a new thread, retaining the first attempt and labeling the retry separately.

Answered cases receive these Ragas scores:

| Metric | Inputs | What it measures |
| --- | --- | --- |
| Faithfulness | answer, selected chunks | Whether answer claims follow from the selected evidence. |
| Answer correctness | answer, reviewed reference | Factual and semantic agreement with the expected answer. |
| Context precision | question, reference, selected chunks | Whether useful chunks appear early in the selected list. |
| Context recall | question, reference, selected chunks | Whether reference claims are supported by the selected chunks. |
| Answer relevancy | question, answer | Whether the answer addresses the question; it does not check factual accuracy. |

The script scores non-answers separately by expected status. It never treats a model failure, an abstention, or a judge error as a Ragas score of zero. It also checks citation IDs against the selected chunks and compares lexical, semantic, and hybrid `/search` results using the same original questions and final `top_k=5`. A separate follow-up checks thread continuity and isolation. Search-mode results are not substituted for the chat graph's evidence.

Each run writes `results/<UTC run ID>/results.json` with raw answers, evidence, scores, errors, timings, and model names, plus `report.md` with counts, means, medians, per-case outcomes, and failures. The evaluator uses the configured `gpt-4.1-mini` and `text-embedding-3-small` deployments. Scores from this LLM judge need human review, especially for legal exceptions and table rows. Set performance thresholds only after several reviewed baseline runs.

Ragas 0.4.3 currently imports a Vertex AI path removed from current LangChain packages even when only OpenAI is used. `ragas_compat.py` supplies that optional type for Ragas' `isinstance` check; it does not change production imports or call Vertex AI. Remove this shim when a Ragas release fixes [the upstream import issue](https://github.com/vibrantlabsai/ragas/issues/2995).
