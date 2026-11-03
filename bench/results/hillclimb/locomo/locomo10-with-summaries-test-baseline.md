| Run | Score | Overall (778) | single-hop (423) | multi-hop (140) | temporal (165) | open-domain (50) |
|---|---|---|---|---|---|---|
| Superhistory locomo10-with-summaries-test-baseline | J | 90.2% | 93.9% | 90.7% | 85.5% | 74.0% |
|  | F1 | 65.6 | 74.5 | 49.6 | 64.6 | 37.5 |

| Run | rows | stores | empty stores | items summarized | summary failures | check searched | check made no search | lookup errors | lookup added records | searched, added nothing | answers that ran kb tools | empty replies asked again | empty answers | evidence in lookup | evidence in lookup or tools | lookup p50 s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| locomo10-with-summaries-test-baseline | 778 | 5 | 0 | 26 | 0 | 764 | 13 | 1 | 602 | 162 | 778 | 29 | 0 | 51.0% | 96.6% | 3.7 |

| Run | Wrong by J | Evidence never reached the model | Evidence reached, still wrong |
|---|---|---|---|
| locomo10-with-summaries-test-baseline | 76 | 13 | 63 |

| System | Score | Metric | Answer model | Judge | Label | Published or checked | Source |
|---|---|---|---|---|---|---|---|
| Mem0 platform, 2026 algorithm | 92.5% (1425/1540) | J, categories 1-4 | gpt-5 | gpt-5, Mem0's 2026 prompt: one of several gold items is enough, dates within 14 days pass | vendor | published 2026-04-16, checked 2026-10-27 | https://github.com/mem0ai/memory-benchmarks (README, results/platform/locomo_results.json, which merges reruns of 156 questions) |
| Mem0, before the 2026 algorithm | 71.4% | not stated | not stated | not stated | vendor | published 2026-04-16, checked 2026-10-27 | https://mem0.ai/blog/mem0-the-token-efficient-memory-algorithm |
| Mem0 graph variant (paper) | 68.44% ± 0.17 | J, categories 1-4 | gpt-4o-mini | not named; the ACCURACY_PROMPT this run uses | vendor | published 2025-04-28, checked 2026-10-27 | https://arxiv.org/abs/2504.19413 (Table 2) |
| Mem0 (paper) | 66.88% ± 0.15 | J, categories 1-4 | gpt-4o-mini | not named; the ACCURACY_PROMPT this run uses | vendor | published 2025-04-28, checked 2026-10-27 | https://arxiv.org/abs/2504.19413 (Table 2) |
| Mem0 (paper) | 38.72 single-hop, 28.64 multi-hop, 47.65 open-domain, 48.93 temporal | F1, categories 1-4, the paper's own category names | gpt-4o-mini | none, token F1 | vendor | published 2025-04-28, checked 2026-10-27 | https://arxiv.org/abs/2504.19413 (Table 1) |
| Full context, no memory layer | 72.90% ± 0.19 | J, categories 1-4 | gpt-4o-mini | not named; the ACCURACY_PROMPT this run uses | vendor, a baseline Mem0 ran | published 2025-04-28, checked 2026-10-27 | https://arxiv.org/abs/2504.19413 (Table 2) |
| Zep | 94.7% (1,459/1,540) | J, four categories | gpt-5.4 | gpt-5.4 with chain-of-thought grading | vendor | page modified 2026-09-29, checked 2026-10-27 | https://www.getzep.com/research/ (its category counts, 670/325/323/221, are not LoCoMo's 841/321/282/96) |
| Zep | 75.14% ± 0.17 | J, categories 1-4 | not stated | not stated | vendor | published 2025-05-06, corrected, modified 2026-06-03, checked 2026-10-27 | https://www.getzep.com/blog/lies-damn-lies-statistics-is-mem0-really-sota-in-agent-memory/ |
| Zep, run by Mem0 | 65.99% ± 0.16 | J, categories 1-4 | gpt-4o-mini | not named | independent of Zep, run by a rival vendor | published 2025-04-28, checked 2026-10-27 | https://arxiv.org/abs/2504.19413 (Table 2) |
| Letta filesystem agent | 74.0% | accuracy; categories not stated | gpt-4o-mini | not stated | vendor | published 2025-08-12, checked 2026-10-27 | https://www.letta.com/blog/benchmarking-ai-agent-memory/ |
| Supermemory | none published; the README claims first place without a score |  |  |  | vendor | checked 2026-10-27 | https://github.com/supermemoryai/supermemory |
