# Review-evidence evaluation dataset (#100)

This directory holds the annotated sample and raw results for the
selective-vs-full review evaluation. It is evaluation tooling, not production
data.

## Files

| File | Purpose |
| --- | --- |
| `samples.jsonl` | 18 annotated segments: 6 literary, 6 academic, 6 technical; 12 carry one serious error, 6 are clean controls |
| `glossary.json` | 3 locked terms used by two terminology samples |
| `annotations.json` | schema, counts, category list and the annotation provenance block |
| `results/` | raw JSON + Markdown report retained from a run |

## Sample schema

```json
{
  "id": "lit-01",
  "domain": "literary",
  "source": "English source text",
  "draft_target": "deliberately flawed Chinese target",
  "serious_errors": [
    {"id": "lit-01-e1", "category": "number",
     "note": "why this is a serious error", "detect_tokens": ["1898"]}
  ],
  "minor_errors": [],
  "glossary_terms": ["the Reach"]
}
```

Categories: `number`, `unit`, `tag`, `placeholder`, `terminology`, `omission`,
`mistranslation`. `detect_tokens` is used only to match a deterministic finding
to an annotation; it is not a substitute for the error description.

## Provenance (read before quoting results)

The annotations were authored by the implementing agent for this evaluation.
They are **synthetic**, short, and **not** independent expert annotation; there
is no inter-annotator agreement. The scripted reviewer is perfect on the gold
errors it sees, so the reported recall measures the routing policy, not reviewer
model quality. Run with:

```bash
python eval/metrics/review_evidence.py            # writes results + report
python eval/metrics/review_evidence.py --no-write # print only
```

Never add real user corpora, API keys, or raw model payloads here.
