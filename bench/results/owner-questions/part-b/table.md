| Arm | Questions | Handled right | Right, of the answerable | Quiet on the nothing-to-find | Mean tokens added | Latency to an answer, p50 / mean | Questions with a tool call | Mean tool calls |
|---|---|---|---|---|---|---|---|---|
| 1. Shipped: haiku summaries and atoms | 171 | 160 (93.6%) | 122 of 131 (93.1%) | 38 of 40 (95.0%) | 2,039 | 9.6 s / 10.2 s | 89.5% | 2.32 |
| 2. Sonnet summaries and atoms | 171 | 160 (93.6%) | 122 of 131 (93.1%) | 38 of 40 (95.0%) | 2,027 | 10.3 s / 10.8 s | 89.5% | 2.35 |
| 3. Haiku summaries and atoms, plus sonnet notes | 171 | 158 (92.4%) | 119 of 131 (90.8%) | 39 of 40 (97.5%) | 2,192 | 9.6 s / 10.3 s | 89.5% | 2.37 |
| 4. Facts only (atoms and notes) | 171 | 62 (36.3%) | 24 of 131 (18.3%) | 38 of 40 (95.0%) | 3,098 | 11.0 s / 11.7 s | 89.5% | 3.43 |
| 4b. Facts only, notes for every item (beyond the brief) | 171 | 136 (79.5%) | 101 of 131 (77.1%) | 35 of 40 (87.5%) | 2,017 | 8.5 s / 9.5 s | 89.5% | 2.72 |

Handled right counts a correct answer and, where nothing fits, nothing added before the message (bench/run.py's rule). Where an agent can pull, the strict columns below also count a tool call that showed it records as added.

| Arm | Handled right, strict | Quiet on the nothing-to-find, strict | Said it did not know or answered correctly, of the nothing-to-find | Mean model tokens of lookups | Lookup errors |
|---|---|---|---|---|---|
| 1. Shipped: haiku summaries and atoms | 139 (81.3%) | 17 of 40 (42.5%) | 40 of 40 | 2,877 | 0 |
| 2. Sonnet summaries and atoms | 140 (81.9%) | 18 of 40 (45.0%) | 40 of 40 | 2,997 | 0 |
| 3. Haiku summaries and atoms, plus sonnet notes | 137 (80.1%) | 18 of 40 (45.0%) | 40 of 40 | 2,759 | 1 |
| 4. Facts only (atoms and notes) | 43 (25.1%) | 19 of 40 (47.5%) | 40 of 40 | 1,586 | 0 |
| 4b. Facts only, notes for every item (beyond the brief) | 119 (69.6%) | 18 of 40 (45.0%) | 40 of 40 | 1,881 | 0 |

Each arm against 1. Shipped: haiku summaries and atoms, question by question. Change is handled right in points, arm minus reference, with a 95% interval from 10,000 paired resamples; the McNemar column counts the questions only the arm got right against those only the reference got right.

| Arm | Questions | Handled right change (95% interval) | Only the arm right vs only the reference | McNemar p | Strict: change (95% interval) | Strict: only the arm vs only the reference | Strict: McNemar p | Right of the answerable, change (95% interval) | Only the arm vs only the reference | McNemar p | Tokens added change |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2. Sonnet summaries and atoms | 171 | +0.0 (-2.9 to +2.9) | 3 vs 3 | 1.000 | +0.6 (-1.8 to +2.9) | 3 vs 2 | 1.000 | +0.0 (-3.1 to +3.1) | 2 vs 2 | 1.000 | -1% (-6 to +6) |
| 3. Haiku summaries and atoms, plus sonnet notes | 171 | -1.2 (-4.1 to +1.8) | 2 vs 4 | 0.688 | -1.2 (-4.1 to +1.8) | 2 vs 4 | 0.688 | -2.3 (-6.1 to +0.8) | 1 vs 4 | 0.375 | +7% (+1 to +15) |
| 4. Facts only (atoms and notes) | 171 | -57.3 (-64.3 to -49.7) | 0 vs 98 | 0.000 | -56.1 (-63.7 to -48.5) | 2 vs 98 | 0.000 | -74.8 (-81.7 to -67.2) | 0 vs 98 | 0.000 | +52% (+29 to +78) |
| 4b. Facts only, notes for every item (beyond the brief) | 171 | -14.0 (-20.5 to -8.2) | 4 vs 28 | 0.000 | -11.7 (-17.5 to -5.8) | 4 vs 24 | 0.000 | -16.0 (-23.7 to -9.2) | 3 vs 24 | 0.000 | -1% (-13 to +12) |

Model tokens spent at ingest.

| Ingest step | Model | Calls | Input tokens | Output tokens | Total tokens | Total per 1,000 items (of the month's 471) |
|---|---|---|---|---|---|---|
| 1. Haiku summaries and atoms | claude-haiku-5-5 | 42 | 144,334 | 37,367 | 181,701 | 385,777 |
| 2. Sonnet summaries and atoms | claude-sonnet-5-5 | 42 | 144,334 | 35,977 | 180,311 | 382,826 |
| 3. Notes (sonnet), on top of arm 1 | claude-sonnet-5-5 | 81 | 172,193 | 47,591 | 219,784 | 466,633 |
| 3. Arm 1 and the notes together | haiku and sonnet | 123 | 316,527 | 84,958 | 401,485 | 852,410 |
| 4. Facts only, cut from arm 3's store | none beyond arm 3 | 123 | 316,527 | 84,958 | 401,485 | 852,410 |
| 4b. Notes for the other 390 items (sonnet) | claude-sonnet-5-5 | 390 | 233,241 | 73,432 | 306,673 | 651,110 |
| 4b. Arm 3 and these notes together | haiku and sonnet | 513 | 549,768 | 158,390 | 708,158 | 1,503,520 |
