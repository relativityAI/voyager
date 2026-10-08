# Agent evals

Black-box, consumer-level evals of the Voyager API. Each file is a **fixed spec an agent follows** — not code. They call the live API, verify the numbers against public sources, and report tables + numbers.

**Naming:** `NN-<topic>.md`. The number keeps the suite ordered; the topic is what you say out loud ("run the financial metrics eval").

## Running one

```
run the financial metrics eval                                  # by name
run evals/01-financial-metrics-accuracy.md                      # by path
```

Agent needs `VOYAGER_BASE_URL` (default `https://voyager-1hpq.onrender.com`) and `VOYAGER_API_KEY`. Output lands in `evals/runs/`, which is gitignored — results are never committed.

## Evals

| # | File | Endpoint | Question it answers |
|---|---|---|---|
| 01 | [`01-financial-metrics-accuracy.md`](01-financial-metrics-accuracy.md) | `GET /financial-metrics` | How accurate is every computed ratio, where does it drift, what is silently wrong vs merely missing |
| 02 | [`02-technical-report-completeness.md`](02-technical-report-completeness.md) | `GET /technicals` | Does the 61-section technical report deliver every section, is each backed by real data, and does degradation say why |

## Rules every eval follows

- **Read-only.** No `POST /pull`, no DB writes. Staleness is reported, never fixed by the eval.
- **No invented truth.** A number with no cited source is `unverified`, not a pass.
- **≥2 independent sources** per verified value, priority ordered per market.
- **Record the dissent.** Source conflicts are reported, not averaged into a fake consensus.
- **Tables over prose.** Findings are keywords. The report should be glanceable in 30 seconds.
- **No code changes.** Evals report; they do not fix.

## Adding an eval

One markdown file, same skeleton: run contract → preflight → universe → ground truth + source priority → dimensions → tolerance bands + weights → report tables → JSON artifact → appendices (field inventory, risk hypotheses). Copy the structure of `01`, don't reinvent it.
