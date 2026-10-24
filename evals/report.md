# Eval report

- backend: `mock`  
- spec: `commitments`  
- cases: 12  
- generated: 2026-10-25 00:33:01

| arm | accuracy (all) | accuracy (answered) | coverage | wrong delivered | $/case | p95 latency |
|---|---|---|---|---|---|---|
| baseline | 83.3% | 83.3% | 100.0% | 2 | $0.00198 | 1400 ms |
| cascade | 83.3% | 90.9% | 91.7% | 1 | $0.00105 | 2300 ms |

Headline: wrong answers delivered went from 2 to 1.
