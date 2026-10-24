# Runbook — 8 hours

Read this once before the start. It is written for the condition that decides
everything: **a screening gate stands between you and the defence, and judges
pass it by looking at the repository while you are still working.**

So the repository has to look like a winner before the project is finished.
That inverts the normal order of work: the demo is not the last artefact, it is
a continuously updated one. First recording goes in at hour 2, ugly and on a
single case. Teams that build until hour 7 and then record something beautiful
get filtered out before anyone sees it.

## Hour by hour

| time | what | done means |
|---|---|---|
| 0:00–0:30 | Classify the theme, freeze scope, assign file zones | everyone can name the one thing being built and which files are theirs |
| 0:30–1:30 | Vertical slice on ONE case, ugly, end to end | input → output happens in a terminal |
| **1:30–2:00** | **First demo recording + README skeleton, committed** | **a stranger opening the repo sees a working thing** |
| 2:00–3:30 | Real `domains/<theme>.py`: prompts, schema, router | the cascade runs on real input |
| 3:30–4:30 | 20–30 eval cases + first real run | the comparison table has numbers in it |
| 4:30–5:30 | Second demo recording, README with the table | the repo now argues for itself |
| 5:30–6:30 | Integration surface, quality fixes the eval points at | curl works; the worst eval failure is addressed |
| **6:30–7:00** | **FREEZE. No new features. Ever.** | nothing is half-finished |
| 7:00–7:30 | Final recording, README polish, rehearse out loud | every claim on the page is one you can defend |
| 7:30–8:00 | Buffer | things break here — that is what it is for |

The freeze is the hardest rule and the one that saves hackathons. Every feature
started after hour 6:30 lands half-done and makes the repo look worse than
stopping would have.

## Theme types — decide this in the first 30 minutes

- **Agentic / LLM-shaped** (assistants, extraction, planning, routing): this kit
  is the whole backbone. Rewrite `domains/`, keep everything else.
- **Consumer-app-shaped** (feeds, matching, social): backend cleverness is
  invisible and unscored. Take `web/`, drop the cascade, spend the time on a
  demo that feels finished.
- **Realtime-CV-shaped** (camera, gestures, pose): latency is visible to the
  jury, so run in the browser and avoid the server round-trip entirely. This kit
  contributes the README, the runbook and the repo hygiene, not the core.

Picking the wrong type costs more than any implementation mistake, and it cannot
be fixed after hour 1.

## Zones

Give every person files nobody else touches. The constraint is not skill, it is
that two agents editing one file produce conflicts every ten minutes.

| person | owns | never touches |
|---|---|---|
| A — core | `core/`, `domains/<theme>.py` | `web/`, `README.md` |
| B — quality | `evals/`, the verifier prompt | `core/pipeline.py`, `web/` |
| C — product | `web/`, `api/`, `README.md`, recordings | `core/`, `evals/` |
| D — integration (4+) | the outward surface, slides | `core/`, `domains/` |

With two people: A takes core + domains, C takes everything else, and the eval
set shrinks to 10 cases rather than being dropped — the table is the pitch.

## Git

Settle this before the start; it cannot be fixed afterwards.

- Everyone commits from their **own** account. `git config user.email` on every
  machine, checked at hour 0. Commit history is the only objective evidence of
  how work was divided, and that is a scored criterion.
- Small commits. A single "final" commit at hour 8 looks like one person did
  everything at the last minute, whether or not that is true.
- `.env` is gitignored from the first commit. A key that reaches the history is
  compromised even in a private repo — rotate it, do not try to delete it.

## When you are behind — cut in this order

1. The second model tier (run everything on one model; the gates still work).
2. The web UI (a CLI that prints the trace demos fine).
3. Eval down to 10 cases (say the number out loud; a small honest set beats a
   large invented one).
4. The router (send everything to the strong tier).

**Never cut:** the demo recording, the comparison table, or abstention. Those
three are what distinguish you from teams that also wrote prompts.

## Defence

Rehearse answers to these. They get asked almost every time.

- **"Why not one big model with one prompt?"** — the table. This is why the eval
  exists; without it you answer with adjectives and lose to a team with numbers.
- **"Isn't this just prompts?"** — show the trace: a rejected answer, an
  escalation, an abstention. Orchestration you cannot see scores zero.
- **"How did you measure that?"** — open `evals/cases.jsonl`, say how many cases,
  who labelled them, and what the grader counts as correct.
- **"Can I try my own input?"** — have the demo accept live input. Teams that can
  only replay a recorded script lose the room here.
- **"Where does it break?"** — answer first, before they find it. Name the case
  type you fail and what you would do with another day.

## Checklist, hour 7:30

- [ ] `pip install -r requirements.txt && python3 -m evals.run` works from a fresh clone
- [ ] README table matches the latest run (no stale numbers)
- [ ] Demo recording is current and under ~60 seconds
- [ ] Every ⟨placeholder⟩ in the README is replaced
- [ ] No `.env`, no keys anywhere in `git log -p`
- [ ] Every team member has commits under their own name
- [ ] Someone has said the whole pitch out loud, timed

## Local models on Apple Silicon

A local model is not a gimmick here — it is the cheap tier of the cascade with
zero marginal cost and no rate limit. That turns the cost row of your table from
a percentage into "the easy 70% of traffic costs nothing", which is a much
harder claim to argue with. It is also demo insurance: the thing that kills live
demos is the API — a quota, a key, the venue network.

```bash
python3 scripts/prefetch.py                      # what is cached, what is missing
python3 scripts/prefetch.py --download llm asr   # fetch — the night before, at home
python3 scripts/prefetch.py --verify             # load each one and actually run it

LLM_BACKEND=mlx    python3 -m evals.run          # both tiers local
LLM_BACKEND=hybrid python3 -m evals.run          # local cheap tier + API strong tier
```

Three rules, learned the expensive way:

1. **Download at home.** Venue wi-fi is shared by every team in the room. A 5 GB
   pull at 09:00 does not finish.
2. **Verify before, not during.** `--verify` loads each model and runs it. A
   model that downloaded but does not load is worse than one you never had,
   because you will discover it at hour three.
3. **Size for the machine.** On 24 GB of unified memory a 4-bit 7–8B model
   (~4.5 GB) runs comfortably beside a browser and an IDE. A 1.5B model is fast
   but follows a JSON schema poorly — fine for routing, weak for extraction.

Ask the organizers whether local models are allowed. They almost always are, but
if the rules mandate a specific API, the cascade still works: the local tier is
a backend swap in `core/providers.py`, not an architecture change.
