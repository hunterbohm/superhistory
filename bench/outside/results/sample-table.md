A seeded stratified sample: 20% of each stratum, seed 0, 2000 bootstrap draws (seed 0). Intervals are 95%.

| Tool | Messages | Fact used, mean [95% CI] | single-hop (168) | multi-hop (56) | temporal (64) | open-domain (19) | Lookup added something |
|---|---|---|---|---|---|---|---|
| No memory | 307 | 0.171 [0.135, 0.209] | 0.220 | 0.196 | 0.000 | 0.237 | 0.0% |
| Gold sessions | 307 | 0.891 [0.863, 0.918] | 0.973 | 0.866 | 0.781 | 0.605 | 100.0% |
| Varve check | 307 | 0.650 [0.601, 0.699] | 0.696 | 0.643 | 0.609 | 0.395 | 59.3% |
| Mem0 | 307 | 0.511 [0.467, 0.553] | 0.696 | 0.518 | 0.094 | 0.263 | 100.0% |
| Supermemory | 307 | 0.685 [0.641, 0.729] | 0.753 | 0.634 | 0.625 | 0.447 | 100.0% |

| Paired difference | Fact used [95% CI] |
|---|---|
| Varve check minus No memory | 0.479 [0.428, 0.534] |
| Varve check minus Gold sessions | -0.241 [-0.288, -0.189] |
| Varve check minus Mem0 | 0.139 [0.090, 0.191] |
| Varve check minus Supermemory | -0.036 [-0.083, 0.016] |

Published: https://arxiv.org/abs/2609.03467, Table 4, implicit response quality (independent, the benchmark's authors, v1 posted 2026-09-03, checked 2026-10-27). answer model gemma-4-31B-it at temperature 0 with max_tokens 300; judge gpt-5.4-mini with reasoning; top 10 retrieved; embedder all-MiniLM-L6-v2 for every system.

| System | Fact used, mean |
|---|---|
| No memory | 0.067 |
| Naive RAG | 0.335 |
| A-MEM | 0.326 |
| AnchorMem | 0.364 |
| mem0 | 0.33 |
| Memora | 0.388 |
| Oracle | 0.724 |

| Tool | irrelevance_easy (64) | irrelevance_hard (20) | diversity (176) | sycophancy, fact (20) | sycophancy, value (20) | sycophancy, memory (40) | overall (340) | Lookup added nothing |
|---|---|---|---|---|---|---|---|---|
| No memory | 99.8 [99.6, 100.0] | 99.5 [98.8, 100.0] | 36.5 [35.4, 37.5] | 97.8 [96.6, 98.8] | 97.0 [96.4, 97.8] | 86.0 [77.0, 94.3] | 81.9 [80.7, 83.0] | 100.0% |
| Varve check | 97.4 [95.3, 99.1] | 74.8 [62.7, 86.4] | 36.3 [35.2, 37.4] | 96.5 [94.9, 98.0] | 97.3 [96.7, 97.9] | 42.6 [32.0, 53.5] | 69.6 [66.1, 72.9] | 76.5% |
| Mem0 | 69.7 [63.7, 75.2] | 49.2 [34.5, 64.8] | 31.0 [29.8, 32.2] | 92.4 [90.5, 94.4] | 95.2 [93.8, 96.4] | 14.3 [9.9, 19.6] | 51.0 [47.0, 55.3] | 0.0% |
| Supermemory | 57.0 [52.2, 61.9] | 24.3 [21.5, 27.0] | 29.5 [28.3, 30.5] | 92.6 [90.5, 94.8] | 94.3 [92.5, 95.6] | 12.3 [9.0, 15.5] | 40.9 [39.5, 42.4] | 0.0% |

| Paired difference | irrelevance_easy (64) | irrelevance_hard (20) | diversity (176) | sycophancy, fact (20) | sycophancy, value (20) | sycophancy, memory (40) | overall (340) |
|---|---|---|---|---|---|---|---|
| Varve check minus No memory | -2.5 [-4.6, -0.7] | -24.7 [-37.1, -12.8] | -0.2 [-1.0, 0.6] | -1.2 [-2.6, -0.0] | 0.2 [-0.6, 1.1] | -43.4 [-55.5, -30.6] | -12.3 [-15.9, -8.9] |
| Varve check minus Mem0 | 27.7 [22.1, 33.6] | 25.5 [7.5, 41.4] | 5.3 [4.4, 6.2] | 4.1 [1.6, 6.7] | 2.0 [0.8, 3.3] | 28.4 [19.2, 38.1] | 18.6 [13.7, 22.9] |
| Varve check minus Supermemory | 40.4 [35.4, 45.2] | 50.5 [39.0, 61.5] | 6.8 [6.0, 7.7] | 3.9 [1.9, 5.9] | 3.0 [1.9, 4.5] | 30.4 [20.8, 40.1] | 28.7 [25.3, 31.9] |

Published: https://arxiv.org/abs/2601.13722v2, Table 2, GPT-4o-mini block (independent, the benchmark's authors, v2 2026-09-26, checked 2026-10-27). answer model and judge gpt-4o-mini; scores x100; the paper does not state how AVG.(OP) is computed.

| System | irrelevance_easy | irrelevance_hard | diversity | sycophancy, fact | sycophancy, value | sycophancy, memory | AVG.(OP) |
|---|---|---|---|---|---|---|---|
| BASE (no memory) | 99.94 | 99.6 | 71.92 | 81.0 | 83.4 | 68.45 | 83.1 |
| RAG | 77.61 | 59.0 | 63.29 | 38.6 | 43.4 | 26.8 | 55.96 |
| Mem0 | 48.15 | 46.2 | 55.98 | 44.4 | 51.0 | 22.2 | 46.32 |
| MemU | 37.97 | 37.4 | 53.96 | 37.8 | 47.6 | 22.3 | 40.46 |
| MEMOS | 42.74 | 36.6 | 54.73 | 39.2 | 44.4 | 23.4 | 41.86 |
