"""Build source-bound inputs for focused source-update transaction tests."""
import hashlib
import core


def source_args(job_id, plan, source=None):
    segments = [None] * plan.v2_count
    for entry in plan.entries:
        if entry.v2_index is not None:
            segments[entry.v2_index] = entry.v2_source
    data = source if source is not None else "\n\n".join(segments).encode("utf-8")
    _, segmentation = core.segment_source_paragraphs(segments, mode="paragraph")
    return {"new_source_bytes": data,
            "source_context": {"sha256": hashlib.sha256(data).hexdigest(),
                               "previous_sha256": hashlib.sha256(core.load_source(job_id)).hexdigest(),
                               "paragraphs": segments, "segments": segments,
                               "segmentation": segmentation, "report": {}, "anchors": []}}
