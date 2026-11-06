| Tool | questions | handled right | accuracy | answerable | nothing fits | added nothing when nothing fits | evidence reached | tokens added per question | check model tokens per question | lookup p50 s | lookup p90 s | lookup errors |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| No lookup | 171 | 24.0% | 24.0% | 0.8% | 100.0% | 100.0% | n/a | 0 | 0 | 0 | 0 | 0 |
| Gold records | 171 | 97.1% | 97.1% | 96.2% | 100.0% | 100.0% | 100.0% | 550 | 0 | 0 | 0 | 0 |
| Mem0 open source | 171 | 59.6% | 83.0% | 77.9% | 100.0% | 0.0% | 94.7% | 450 | 0 | 0.23 | 0.35 | 0 |
| Superhistory check, with superhistory search and superhistory get | 171 | 93.0% | 94.7% | 93.1% | 100.0% | 92.5% | 96.9% | 2087 | 563 | 3.52 | 4.85 | 0 |
| Superhistory check, cards only | 171 | 55.0% | 56.7% | 43.5% | 100.0% | 92.5% | 87.0% | 288 | 558 | 3.63 | 4.53 | 0 |
| Supermemory self-hosted | 171 | 70.8% | 93.6% | 92.4% | 97.5% | 0.0% | 100.0% | 8834 | 0 | 0.18 | 0.22 | 0 |
| claude-mem | 171 | 11.1% | 34.5% | 14.5% | 100.0% | 0.0% | n/a | 7067 | 0 | 0.01 | 0.03 | 0 |
| claude-mem, per-prompt lookup on | 171 | 11.1% | 34.5% | 14.5% | 100.0% | 0.0% | n/a | 6418 | 0 | 0.23 | 0.55 | 0 |

| Tool | single | multi | update | temporal | none |
|---|---|---|---|---|---|
| No lookup | 1.0% | 0.0% | 0.0% | 0.0% | 100.0% |
| Gold records | 98.1% | 80.0% | 100.0% | 85.7% | 100.0% |
| Mem0 open source | 82.9% | 40.0% | 55.6% | 85.7% | 100.0% |
| Superhistory check, with superhistory search and superhistory get | 93.3% | 80.0% | 100.0% | 100.0% | 100.0% |
| Superhistory check, cards only | 49.5% | 20.0% | 11.1% | 28.6% | 100.0% |
| Supermemory self-hosted | 93.3% | 70.0% | 100.0% | 100.0% | 97.5% |
| claude-mem | 17.1% | 0.0% | 11.1% | 0.0% | 100.0% |
| claude-mem, per-prompt lookup on | 17.1% | 0.0% | 11.1% | 0.0% | 100.0% |
