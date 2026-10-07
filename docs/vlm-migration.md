# Document understanding migration (in progress)

## Evidence-backed dependency map

- `core.extract_document_paragraphs_with_report` selects native PDF text using
  `text_layer_quality`, otherwise invokes `_ocr_pdf_text_with_warnings`.
- `pdf_ingestion.extract_layout_blocks` retains PDF points, styles and block IDs;
  `reconstruct_paragraph_records` already retains multi-block/page membership.
- `ocr_backends` supplies Tesseract/Paddle/online evidence to `ocr_cache`.
  Tesseract/Paddle have local geometry; online transcription has none.
- `core.run_pipeline` and `rescan_source` run `source_cleanup.cleanup_source_paragraphs`
  after extraction. That module's model retries repair text/paragraph boundaries.
- `_apply_source_segmentation` uses `segmentation.paragraph_ranges`; translation
  consumes `state.paras` and pairs. Project persistence serializes plain mappings.
- `pdf_preview.get_cached_segment_location` searches native text then cached OCR
  phrases. It cannot reliably locate semantic text that differs from those phrases.
- Segment editing (`mutate_translation_segments`) supports split/merge/exclude/undo;
  source spans must travel with pairs, not ordinal-only mappings.
- EPUB keeps its own source-node references; this migration must preserve them.

## Baseline and rollout gate

Before edits, 86 tests passed across pdf_preview, ocr_ingestion_contract,
segmentation and epub_export (6.59s). This is regression evidence, not a semantic
benchmark. Existing Arabic pilot results compare crop transcription; they are
not a benchmark of the new structured provider.

The saved online profile has no valid service URL. Live before/after model usage,
latency, semantic success and region accuracy remain unmeasured. The first live runs exposed truncation and fenced-JSON failures. Both are
recorded under `benchmark/vlm-migration/`. After repairs, the four-fixture live
run succeeded. New jobs now default to visual understanding; existing jobs
retain their saved extraction source. Explicit local selection remains legacy
compatibility. This is a local implementation, not a declaration that all
production release gates or the lower-cost objective have passed.

## OCR model-call audit

- REPLACE: the default scanned path (and explicitly selected online source) with
  structured visual understanding; native/scanned mixed PDFs retain page order.
- REMOVE from VLM runtime: source_cleanup model calls and their repair retries,
  in both initial ingestion and PDF rescan.
- KEEP TEMPORARILY: local legacy source cleanup for explicitly selected local
  engines and saved tasks, pending broader release gates.
- KEEP: Tesseract/Paddle local geometry evidence and source-quality human review.
- KEEP: native-text cleanup where explicitly enabled and all translation/review,
  terminology and document synopsis calls; those are not OCR reconstruction.

The implementation status, measured results and remaining release gates are in
[the engineering report](vlm-migration-report.md).
