# #100: bounded live reviewer evaluation

This follow-up adds a live-reviewer path to the existing evaluation harness.
It does not change the production review policy. The first connectivity probes
on 2026-10-26 were blocked by the saved provider, so **no live quality result is
claimed** from those probes.

## Method

- Reuse the 18 existing synthetic English/Chinese source/draft pairs, their
  glossary, the real `translate_stage`, and the `always` / `risk_selective` gates.
- Feed the same fixed draft targets offline to both arms. Only independent
  review requests use the real configured model. This isolates reviewer/gate
  behavior from generation variance; it is not an end-to-end translation run.
- Use the exact application review prompt. Do not send annotation IDs, gold
  labels, or expected errors to the reviewer.
- Record live responses, provider-reported token counts and request duration.
  Token counts not returned by the provider remain null. Monetary cost remains
  unknown without billing/pricing evidence. Existing pipeline ledger estimates
  are not presented as exact provider usage.
- Check that skipped segments remain non-reviewed/non-approved/non-TM-eligible.
- Produce a shuffled worksheet without arm/sample IDs for human adjudication,
  plus a separate answer key. A script must not score live findings by requiring
  the hidden gold IDs that only the old perfect scripted reviewer could return.
- Stop further network calls after a provider failure. A logical-call cap limits
  the experiment; the production transport's own request/retry behavior is kept.

## Run

From the repository root, with the ordinary Folith Python environment:

```powershell
python eval/metrics/review_evidence_live.py `
  --profile "$env:LOCALAPPDATA/Folith/data/provider_config.json" `
  --output "D:/03_Development/Folith/verification/issue-100-live" `
  --max-calls 36
```

`--sample-limit 1 --max-calls 2` is a small connectivity pilot. `--model MODEL`
is an evaluator-only override and never saves desktop configuration. By default
the script resolves the current reviewer/translation role just as the app does.
Keys are read locally into memory. Keep raw responses and local trace material
outside the tracked dataset directory; inspect/redact before publishing evidence.

Output: `report.json`, per-arm `calls-*.json`, `blind-worksheet.json` and the
separate `blind-key.json`. The report deliberately does not claim human annotation
or a gold-scored recall before adjudication. Failed provider calls yield a failed
execution status and nonzero command exit, not an apparent successful comparison.

## Initial connection probes

| Saved connection/model | Provider result | Interpretation |
| --- | --- | --- |
| OpenCode Go / `longcat-2.5-preview-free` (selected role model) | HTTP 410, `ModelDeprecated` | Provider explicitly reports the model deprecated. |
| OpenCode Go / `deepseek-v4.1-flash` (same connection's saved model) | HTTP 429, `GoUsageLimitError` | Provider explicitly reports Go usage limit exceeded. |

No successful review response or exact usage was returned. This does not prove
zero billed cost. Desktop settings and user tasks were not modified. The owner
will switch to an available connection before the live experiment resumes.

## Live run 2026-10-26 (dots3-note-prev via owner-supplied temporary relay)

An owner-supplied temporary relay endpoint (evaluation-only; credentials kept in
a local temp profile, never serialized into the repo, desktop settings or this
document) completed the full two-arm run. Artifacts live outside the tracked
tree in `verification/issue-100-live-dots3-full/` (`report.json`,
`calls-always.json`, `calls-risk_selective.json`, `blind-worksheet.json`,
`blind-key.json`) with the run log in `verification/issue-100-live-dots3-full.log`.

All 28 live review calls returned HTTP 200 with provider-reported usage; no
call failed, no call was aborted. Results:

| Metric | `always` | `risk_selective` |
| --- | --- | --- |
| Live review calls | 19 (18 batches + 1 post-repair re-review) | 9 |
| Review wall time | 270.6 s | 221.5 s |
| Tokens (prompt / completion / total) | 21,783 / 19,054 / 40,837 | 11,256 / 17,231 / 28,487 |
| Segments submitted to review | 18 / 18 | 9 / 18 (9 skipped `clean_deterministic`) |
| Outcomes | 8 clean, 7 with findings, 3 `review_failed` | 0 clean, 6 with findings, 3 `review_failed` |
| Findings recorded | 26 | 23 |
| Skip violations | 0 | 0 |

Observations, all bounded by the synthetic 18-sample corpus and the lack of
independent human annotation:

- Against this set the risk gate skipped 9 of 10 finding-bearing segments'
  complement correctly, but it did skip 2 segments (`lit-02`, `lit-03`) that
  the `always` arm's reviewer found actionable problems in. Gate recall on
  this corpus is therefore 8/10 finding-bearing segments reviewed; no skipped
  segment that was reviewed elsewhere came back clean, so gate precision on
  reviewed-elsewhere evidence is 9/9 — both numbers are far too small to
  generalize.
- `risk_selective` saved 10 review calls, ~30% of tokens and ~18% of review
  wall time versus `always` on this corpus, partly inflated by `always`'s
  post-repair re-review call.
- Both arms ended with `readiness.status = failed` because 3 segments each
  were marked `review_failed`. An offline replay of the recorded responses
  (no network calls) reproduced every status exactly and attributed the
  failures to reviewer-output validation, not transport: one response was
  invalid JSON (an unquoted string value), two failed finding-field
  validation. A further 9 "自动修复失败：模型返回格式异常" events per arm came
  from auto-repair consuming malformed reviewer suggestions, and did not
  consume extra review calls. The pipeline's fail-closed behavior (blocked
  readiness instead of silently accepting unreviewable segments) worked as
  designed.
- Per-segment reviewer latency ranged 3.5–202 s; the relay reported cached
  prompt tokens only sporadically, and reasoning tokens were never reported,
  so those fields stay null in the report.

`execution_status` is `completed`. `blind-worksheet.json` (36 shuffled rows,
arm/sample IDs removed) plus the separate `blind-key.json` were generated, but
per-case scoring was **waived by owner decision on 2026-10-26**: the owner had
a translator review the blinded worksheet overall and reported broadly no
problems ("大体上没有问题，现在就很好"). This is recorded in
`report.json` as `human_adjudication.status = accepted_by_owner`. It is an
owner-level acceptance, not a scored human evaluation: no per-finding
verdicts, no recall denominators, and no per-valid-finding cost were computed.
The observational points above remain bounded by the synthetic corpus and this
reduced adjudication.

## Acceptance still required

After a successful live run, review the blinded source/draft/findings, record
valid findings, serious errors and misses, and calculate denominators and cost /
quality tradeoffs per mode. These original 18 annotations remain agent-authored
synthetic examples, not independent human annotations or a representative book
corpus. Keep #100 open until its human/real-corpus requirements are either met
with recorded evidence or explicitly revised by the owner. No production-default
change or unfreezing of #19/#20 follows from this tooling.

On 2026-10-26 the owner revised the human-adjudication requirement to the
overall translator pass recorded above (no per-case scoring). The
real-corpus limitation still stands: any further claim about review quality in
production needs either a real-document run with recorded evidence or an
explicit owner revision of that requirement. #19/#20 remain frozen either way.
