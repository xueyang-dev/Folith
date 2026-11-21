"""Worker-thread entry points for importing and exporting bilingual files."""
from pathlib import Path

from . import adapter


BILINGUAL_SUFFIXES = frozenset({".xlf", ".xliff", ".sdlxliff", ".mqxliff"})


def picker_filters(source_suffixes):
    """Two named filters instead of one mixed 'documents' list (#526).

    Kept here (next to the suffix contract) so both the dialog and tests read
    the same definition; every suffix in ``BILINGUAL_SUFFIXES`` is advertised.
    """
    from . import i18n

    documents = " ".join(f"*{item}" for item in sorted(source_suffixes))
    bilingual = " ".join(f"*{item}" for item in sorted(BILINGUAL_SUFFIXES))
    return (i18n.tr("文档 ({v0})", v0=documents),
            i18n.tr("双语文件（XLIFF 1.2 / SDLXLIFF / MQXLIFF） ({v0})", v0=bilingual))


def import_file(path: str, *, project_id: str | None = None) -> dict:
    source = Path(path).expanduser()
    if source.stat().st_size > adapter.MAX_SOURCE_BYTES:
        raise ValueError("双语文件超过大小限制。")
    with source.open("rb") as stream:
        payload = stream.read(adapter.MAX_SOURCE_BYTES + 1)
    if len(payload) > adapter.MAX_SOURCE_BYTES:
        raise ValueError("双语文件超过大小限制。")
    return adapter.core.import_xliff_task(source.name, payload, project_id=project_id)


def import_files(paths: list[str], *, project_id: str | None = None) -> list[dict]:
    results = []
    for path in paths:
        try:
            results.append({"result": import_file(path, project_id=project_id), "error": ""})
        except Exception as exc:
            results.append({"result": None, "path": path, "error": str(exc)})
    return results


def export_file(job_id: str, destination: str, *, write_targets: bool = False) -> dict:
    return adapter.core.export_xliff_task(job_id, destination, write_targets=write_targets)
