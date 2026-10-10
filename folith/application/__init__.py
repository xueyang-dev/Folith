"""Application layer — the frontend-neutral interface between UI surfaces and the core.

Architecture phases build it up in layers:

* **Phase 1** — one job configuration contract (:class:`JobConfig`), so Streamlit
  and Qt stop assembling the worker configuration independently.
* **Phase 3** — one application-facing product interface: commands for the
  product actions, canonical job/runtime read models, and the runtime action
  policy.

.. code-block:: text

    Qt  /  Streamlit  /  CLI
              ↓
    folith.application   (command · read model · action policy)
              ↓
    existing core runtime

The layer stays deliberately thin: it delegates every operation to an existing
core function and owns no persistence, no worker, no model routing and no
domain algorithm.  It imports without Streamlit or PySide6.
"""
from folith.application.commands import (
    ApplicationError,
    CommandRejected,
    ImportedDocument,
    JobNotFoundError,
    apply_task_setup,
    cancel_job,
    continue_job,
    delete_job,
    import_document,
    pause_job,
    resume_job,
    retry_job,
    start_job,
)
from folith.application.job_config import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_MAX_BATCH_CHARS,
    DEFAULT_OCR_BACKEND,
    DEFAULT_OCR_MODE,
    DEFAULT_OCR_QUALITY,
    DEFAULT_PADDLE_MODEL,
    DEFAULT_SEGMENTATION_MODE,
    DEFAULT_TARGET_LANG,
    DEFAULT_TRANSLATION_CONCURRENCY,
    INTERNAL_CORE_OPTIONS,
    JOB_CONFIG_FIELDS,
    JOB_CONFIG_KWARG_KEYS,
    MAX_SOURCE_BYTES,
    OPTIONAL_REVIEWER_KWARG_KEYS,
    SOURCE_SUFFIXES,
    JobConfig,
    JobConfigError,
    is_supported_source,
    normalize_source_suffix,
    source_suffix_problem,
)
from folith.application.read_models import (
    JobRuntimeView,
    JobSummary,
    UNREADABLE_ERROR,
    UNREADABLE_LABEL,
    UNREADABLE_STATUS,
    actions_for_status,
    get_available_actions,
    get_job,
    get_job_runtime,
    job_has_live_worker,
    job_is_active,
    list_jobs,
)
from folith.application.settings import (
    ENVIRONMENT_FALLBACKS,
    LAYERS,
    SECRET_FIELDS,
    EffectiveSettings,
    SettingsSources,
    resolve_effective_settings,
)

__all__ = [
    "ApplicationError",
    "CommandRejected",
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_MAX_BATCH_CHARS",
    "DEFAULT_OCR_BACKEND",
    "DEFAULT_OCR_MODE",
    "DEFAULT_OCR_QUALITY",
    "DEFAULT_PADDLE_MODEL",
    "DEFAULT_SEGMENTATION_MODE",
    "DEFAULT_TARGET_LANG",
    "DEFAULT_TRANSLATION_CONCURRENCY",
    "ENVIRONMENT_FALLBACKS",
    "EffectiveSettings",
    "INTERNAL_CORE_OPTIONS",
    "ImportedDocument",
    "JOB_CONFIG_FIELDS",
    "JOB_CONFIG_KWARG_KEYS",
    "LAYERS",
    "JobConfig",
    "JobConfigError",
    "JobNotFoundError",
    "JobRuntimeView",
    "JobSummary",
    "MAX_SOURCE_BYTES",
    "OPTIONAL_REVIEWER_KWARG_KEYS",
    "SECRET_FIELDS",
    "SOURCE_SUFFIXES",
    "SettingsSources",
    "UNREADABLE_ERROR",
    "UNREADABLE_LABEL",
    "UNREADABLE_STATUS",
    "actions_for_status",
    "apply_task_setup",
    "cancel_job",
    "continue_job",
    "delete_job",
    "get_available_actions",
    "get_job",
    "get_job_runtime",
    "import_document",
    "is_supported_source",
    "job_has_live_worker",
    "job_is_active",
    "list_jobs",
    "normalize_source_suffix",
    "pause_job",
    "resume_job",
    "resolve_effective_settings",
    "retry_job",
    "source_suffix_problem",
    "start_job",
]
