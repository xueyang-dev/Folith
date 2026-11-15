"""Worker-thread entry points for importing and exporting bilingual files."""
from pathlib import Path

from . import adapter


BILINGUAL_SUFFIXES = frozenset({".xlf", ".xliff", ".sdlxliff", ".mqxliff"})


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
