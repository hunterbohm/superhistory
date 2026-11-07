| Arm | Questions | Handled right | Right, of the answerable | Quiet on the nothing-to-find | Mean tokens added | Latency to an answer, p50 / mean | Questions with a tool call | Mean tool calls |
|---|---|---|---|---|---|---|---|---|
| 1. Push and pull (ships today) | 171 | 157 (91.8%) | 119 of 131 (90.8%) | 38 of 40 (95.0%) | 2,090 | 9.5 s / 10.4 s | 89.5% | 2.38 |
| 2. Pull only (search, get) | 171 | 160 (93.6%) | 121 of 131 (92.4%) | 40 of 40 (100.0%) | 3,941 | 8.2 s / 8.9 s | 89.5% | 3.57 |
| 3. Pull with lookup (search, get, lookup) | 171 | 162 (94.7%) | 122 of 131 (93.1%) | 40 of 40 (100.0%) | 2,929 | 9.7 s / 10.9 s | 89.5% | 3.39 |
| 4. Push only (cards, no tools) | 171 | 88 (51.5%) | 49 of 131 (37.4%) | 39 of 40 (97.5%) | 277 | 7.5 s / 8.1 s | 0.0% | 0.00 |

Handled right counts a correct answer and, where nothing fits, nothing added before the message (bench/run.py's rule). Where an agent can pull, the strict columns below also count a tool call that showed it records as added.

| Arm | Handled right, strict | Quiet on the nothing-to-find, strict | Said it did not know or answered correctly, of the nothing-to-find | Mean model tokens of lookups | Lookup errors |
|---|---|---|---|---|---|
| 1. Push and pull (ships today) | 137 (80.1%) | 18 of 40 (45.0%) | 40 of 40 | 2,981 | 0 |
| 2. Pull only (search, get) | 139 (81.3%) | 18 of 40 (45.0%) | 39 of 40 | 0 | 0 |
| 3. Pull with lookup (search, get, lookup) | 140 (81.9%) | 18 of 40 (45.0%) | 40 of 40 | 2,545 | 0 |
| 4. Push only (cards, no tools) | 88 (51.5%) | 39 of 40 (97.5%) | 40 of 40 | 2,823 | 1 |

Each arm against 1. Push and pull (ships today), question by question. Change is handled right in points, arm minus reference, with a 95% interval from 10,000 paired resamples; the McNemar column counts the questions only the arm got right against those only the reference got right.

| Arm | Questions | Handled right change (95% interval) | Only the arm right vs only the reference | McNemar p | Strict: change (95% interval) | Strict: only the arm vs only the reference | Strict: McNemar p | Right of the answerable, change (95% interval) | Only the arm vs only the reference | McNemar p | Tokens added change |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2. Pull only (search, get) | 171 | +1.8 (-1.2 to +4.7) | 5 vs 2 | 0.453 | +1.2 (-1.8 to +4.1) | 4 vs 2 | 0.688 | +1.5 (-2.3 to +5.3) | 4 vs 2 | 0.688 | +89% (+70 to +110) |
| 3. Pull with lookup (search, get, lookup) | 171 | +2.9 (-0.6 to +6.4) | 7 vs 2 | 0.180 | +1.8 (-1.2 to +4.7) | 5 vs 2 | 0.453 | +2.3 (-1.5 to +6.1) | 5 vs 2 | 0.453 | +40% (+28 to +54) |
| 4. Push only (cards, no tools) | 171 | -40.4 (-48.0 to -32.7) | 2 vs 71 | 0.000 | -28.7 (-38.6 to -18.1) | 22 vs 71 | 0.000 | -53.4 (-61.8 to -45.0) | 1 vs 71 | 0.000 | -87% (-89 to -84) |

Tool definitions add, per request, by the gateway's token count: 1. Push and pull (ships today): 543, 2. Pull only (search, get): 614, 3. Pull with lookup (search, get, lookup): 772, 4. Push only (cards, no tools): 0 tokens.
