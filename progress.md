# progress.md

## 2026-09-30

### Implementation
- Read `BRIEF.md` + `data/README.md`; inspected meetings, dictation, Slack (incl. edits/deletes), Gmail, Calendar, Codex, ChatGPT.
- Built `memory_system.py`: normalize all sources into SQLite units with FTS5, temporal visibility, edit/delete handling, secret redaction, planted-instruction stripping.
- Hybrid retrieval: BM25 (FTS5) + lexical → RRF → boosts → as_of filter → thread/meeting expansion → diversify → extractive answer.
- Bonus actions dry-run in `actions.py` covering the BRIEF action table.
- FastAPI backend (`server.py`) + monochrome SPA (`web/`): Dashboard, Ask Memory (+ evidence), Timeline, Sources, Evaluation, Architecture.
- `run.py` one-command: ingest / eval / serve.
- Generative answers (`generate.py`): Groq free tier first (`openai/gpt-oss-20b`), Gemini Flash fallback, extractive if no key. Prompt forbids stale dates unless the question asks for history — aimed at the three “unverified” strict failures.

### Technical decisions
- SQLite FTS5 for BM25 instead of paid vector APIs (₹0, offline, sufficient for this corpus).
- Cite segment/message ids, not whole meetings, when possible.
- Prefer latest as_of-visible correction for facts like NRR (112 over 118).
- Abstain only when evidence is weak or known-absent probes (`salary`, `soc`).

### Experiments
- Word-OR only retrieval → poor ranking.
- Strict distinctive-token abstention → false “I don’t know” on dark mode.
- Without source boosts, meetings drowned emails (pricing, flight, dictation).
- Adding RRF + query expansion + diversify moved retrieval ~68% → 100% on train.

### Evaluation results (actual, this commit)
Command: `python run.py --eval --no-server`

| Metric | Result |
|---|---|
| Retrieval | **100%** (n=27, MRR ≈ 0.80, 0 forbidden) |
| Answers strict (`judge none`) | **88.9%** |
| Answers lenient | **100%** |
| Unverified (need judge) | MEM-TR-01 (mentions Oct 14 history), MEM-TR-11 (mentions 800 ms median), MEM-TR-13 (mentions prior Sep 17 slot) |
| Hard failures | **0** |
| Actions | **100%** pass / **100%** arg accuracy (n=12) |

### Problems / failures
- Early answer accuracy ~7% when the system over-abstained.
- ACT-TR-10 initially failed by citing stale NRR 118; fixed with as_of-aware latest correction.
- Strict score still docks three history-aware answers without an LLM judge.
- Extractive synthesis can still surface noisy neighboring segments unless Groq/Gemini is configured.

### Next steps
- Optional: add free/local embeddings and re-benchmark RRF weights on train without overfitting.
- Optional: plug `--judge openai|anthropic` for official answer score.
- Ship commit + public GitHub for hidden-test readiness.
- Demo video if presenting the action assistant.
