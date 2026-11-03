| Run | Questions | Overall | Task-averaged | single-session-user | single-session-assistant | single-session-preference | multi-session | knowledge-update | temporal-reasoning | Abstention |
|---|---|---|---|---|---|---|---|---|---|---|
| Superhistory s-without-summaries-test-midpoint | 250 | 92.4% | 92.7% | 94.3% | 100.0% | 86.7% | 86.4% | 94.9% | 94.0% | 93.3% |

| Run | rows | stores | empty stores | items summarized | summary failures | check searched | check made no search | lookup errors | lookup added records | searched, added nothing | answers that ran kb tools | empty replies asked again | empty answers | evidence in lookup | evidence in lookup or tools | lookup p50 s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| s-without-summaries-test-midpoint | 250 | 250 | 0 | 0 | 0 | 222 | 28 | 0 | 201 | 21 | 216 | 0 | 0 | 79.2% | 100.0% | 3.0 |

| Run | Wrong (answerable) | Evidence never reached the model | Evidence reached, still wrong | Judge replies not yes or no |
|---|---|---|---|---|
| s-without-summaries-test-midpoint | 18 | 0 | 18 | 0 |

| System | Score | Set | Answer model | Judge | Label | Published or checked | Source |
|---|---|---|---|---|---|---|---|
| Mem0 platform, 2026 algorithm | 94.4% (472/500) | S, cleaned | gpt-5 | gpt-5, Mem0's own single prompt adapted from LongMemEval's | vendor | published 2026-04-16, checked 2026-10-27 | https://github.com/mem0ai/memory-benchmarks (README, results/platform/longmemeval_results.json) |
| Mem0, before the 2026 algorithm | 67.8% | not stated | not stated | not stated | vendor | published 2026-04-16, checked 2026-10-27 | https://mem0.ai/blog/mem0-the-token-efficient-memory-algorithm |
| Supermemory | 85.2% | S | gemini-3-pro | gpt-4o, LongMemEval's prompts | vendor | page undated, checked 2026-10-27 | https://supermemory.ai/research/longmembench/ |
| Supermemory | 84.6% | S | gpt-5 | gpt-4o, LongMemEval's prompts | vendor | page undated, checked 2026-10-27 | https://supermemory.ai/research/longmembench/ |
| Supermemory, rerun by Maximem | 71.3% | not stated | gpt-5, five seeds | not named | independent of Supermemory, run by a rival vendor | published 2026-09, checked 2026-10-27 | https://www.maximem.ai/evals |
| Zep | 90.2% (451/500) | not stated | gpt-5.4, reasoning medium | gpt-5.4 with chain-of-thought grading | vendor | page modified 2026-09-29, checked 2026-10-27 | https://www.getzep.com/research/ |
| Zep | 71.2% | S, before the cleanup | gpt-4o | gpt-4o, LongMemEval's prompts | vendor | published 2025-01-20, checked 2026-10-27 | https://arxiv.org/abs/2501.13956 |
| Full context, no memory layer | 60.6% | S, before the cleanup | gpt-4o | gpt-4o, LongMemEval's prompts | independent, the benchmark's authors | v2 2025-03-04, checked 2026-10-27 | https://arxiv.org/abs/2410.10813 (Figure 3b) |
| Full context, no memory layer | 87.0%; 92.4% with chain-of-note | oracle, before the cleanup | gpt-4o | gpt-4o, LongMemEval's prompts | independent, the benchmark's authors | v2 2025-03-04, checked 2026-10-27 | https://arxiv.org/abs/2410.10813 (Figure 3b) |
| Letta | none published |  |  |  |  | searched 2026-10-27 | https://www.letta.com/research/ |
