# ⟨PROJECT NAME⟩

> **This README is the pitch.** Judges read repositories; most of them never run
> one. Everything that earns points has to be visible on this page: what it
> does, how it is built, and the numbers that back the claim. Replace every
> ⟨placeholder⟩ and delete this block before the defence.

⟨One sentence: who has the problem, and what this does about it.⟩

![demo](docs/demo.gif) <!-- record this in hour 2, not hour 7 -->

## The 30-second version

⟨Problem in one sentence.⟩ A single large model answering every request is the
obvious approach; it is expensive on the easy majority and confidently wrong on
the hard minority. This routes by difficulty, verifies before answering, and
declines when neither tier convinces the verifier.

| arm | accuracy (all) | accuracy (answered) | coverage | wrong delivered | $/case | p95 latency |
|---|---|---|---|---|---|---|
| baseline | 83.3% | 83.3% | 100.0% | 2 | $0.00198 | 1400 ms |
| cascade | 83.3% | 90.9% | 91.7% | 1 | $0.00105 | 2300 ms |

<!-- Regenerate with `python3 -m evals.run` and paste evals/report.md here.
     The row above is from the shipped example domain on the mock backend. -->

**Read the table honestly, and say this out loud before a judge says it for you:**
overall accuracy did not move. What moved is the number of wrong answers
delivered, which halved, at 47% lower cost — and the price was 64% higher tail
latency and one case in twelve left unanswered. That is the trade, and naming
it yourself is worth more than a number that looks too good.

## How it works

```
            ┌───────────┐
  input ───▶│   route   │  cheap string/heuristic scan, no model call
            └─────┬─────┘
                  │  easy ──▶ cheap tier          hard ──▶ strong tier
                  ▼
            ┌───────────┐
            │   solve   │  structured output, schema-validated,
            └─────┬─────┘  retried with the validation error in the prompt
                  ▼
            ┌───────────┐
            │  ground   │  every quote must appear verbatim in the source.
            └─────┬─────┘  mechanical, free, catches invented evidence
                  ▼
            ┌───────────┐
            │  verify   │  separate prompt, cheap tier — judging is easier
            └─────┬─────┘  than generating, so don't pay generator prices
                  ▼
            ┌───────────┐
            │   gate    │  pass AND confidence ≥ floor?
            └─────┬─────┘
          ┌───────┴────────┬──────────────────┐
          ▼                ▼                  ▼
       answer      escalate to strong      abstain
                   (retry the whole        (say so, don't guess)
                    chain once)
```

Four quality gates, in increasing order of cost:

1. **Schema validation** — invalid output is re-asked with the specific error,
   not a generic "try again".
2. **Quote grounding** — a claimed quote either appears in the source or it does
   not. No model, no cost, and it catches fabricated evidence outright, which is
   the failure a verifier model is worst at spotting.
3. **Model verifier** — a different prompt on the cheap tier. Self-verification
   with the same prompt mostly reproduces the original error.
4. **Abstention** — below the confidence floor the system declines. ⟨Say what
   happens then in your product: escalate to a human, ask a follow-up, flag it.⟩

## Run it

```bash
pip install -r requirements.txt     # core needs nothing; this is for the API and real calls
python3 -m evals.run                # mock backend: no key, no network, real numbers
uvicorn api.server:app --port 8000  # demo UI + API at http://localhost:8000
```

Switch backends by copying `.env.example` to `.env` and setting `LLM_BACKEND`:

| value | what runs | needs |
|---|---|---|
| `mock` | deterministic fixtures | nothing — this is the default |
| `anthropic` | API models on both tiers | `ANTHROPIC_API_KEY` |
| `mlx` | local models on both tiers (Apple Silicon) | `pip install mlx-lm` + weights |
| `hybrid` | local cheap tier, API strong tier | both of the above |

`hybrid` is the arrangement worth showing a jury: the easy majority costs
nothing, and only escalations reach a paid model. See `RUNBOOK.md` for
pre-downloading the weights — do that at home, not at the venue.

## Integrate it

```bash
curl -s localhost:8000/run -H 'content-type: application/json' \
  -d '{"text": "Timur: I'\''ll deploy the hotfix tonight."}' | python3 -m json.tool
```

Returns the answer, the confidence, and the full trace — every node, the model
it used, what it cost and what it decided.

## Layout

| path | what lives there | owner |
|---|---|---|
| `core/` | provider adapter, cascade, gates, trace — domain-independent | ⟨name⟩ |
| `domains/` | **the only file the theme changes**: prompts, schema, router, grader | ⟨name⟩ |
| `evals/` | cases and the harness that produces the table above | ⟨name⟩ |
| `api/`, `web/` | HTTP surface and the demo page | ⟨name⟩ |

One owner per row, enforced by not editing other people's rows. With several
people driving agents at once, merge conflicts come from two agents touching one
file, not from the work being hard.

## Honest limits

⟨Fill these in. Naming your own weaknesses before the jury finds them reads as
competence; being caught hiding one reads as the opposite.⟩

- The shipped numbers come from a mock backend with 12 fixtures. ⟨Replace with
  your real run and say how many cases.⟩
- The grader is exact-match on ⟨fields⟩, which is only fair because ⟨reason⟩.
- The verifier runs on the cheap tier: it waves through ⟨N⟩ wrong answer(s) in
  the eval. A strong-tier verifier catches more and erases the cost advantage.
- ⟨Where it breaks: input types, languages, lengths you did not handle.⟩
