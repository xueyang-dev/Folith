# Folith Architecture Foundation — Phase 4

Scope of this pass: **one frontend-neutral effective-settings resolution path
before configuration becomes `JobConfig`.**

Baseline for this pass: the Phase 3 accepted state
(`arch/phase3-accepted`). Previous phases:
[Phase 0–2](architecture-foundation-phase0-2.md) ·
[Phase 3](architecture-foundation-phase3.md).

Status: **Phase 4 PASS**

---

## 1. Settings sources and final precedence

The product has six configuration origins.  Phase 4 did **not** unify storage —
each origin keeps its file, its owner and its format — it unified *resolution*.

| origin | storage | owner | runtime-relevant content |
| --- | --- | --- | --- |
| explicit user choice | Streamlit `st.session_state`; Qt `EngineConfig` (in-process) | frontends | connection, target language, task switches, OCR/visual source, research inputs |
| stored user defaults | Streamlit strategy/delivery presets (session); Qt `QSettings` `task/*` + `delivery/*` | frontends | batching profile, review/terminology preset, delivery products, the desktop task defaults (see §3.2) |
| provider profile | `<output>/provider_config.json` (0600) | core | provider, model, api key, base URL, reasoning effort, separate review model |
| persisted task state | `<job>/state.json` → `pipeline_config`, `glossary`, `segmentation`, `delivery_config` | core | everything the task decided when it was created |
| environment | `FOLIOTHREAD_*` (≈25 runtime variables) | core | OCR language/workers/queue, cleanup tuning, concurrency, segmentation, journal/snapshot cadence |
| application defaults | `folith/application/` | this layer | the neutral defaults of the contract |

### Precedence

```text
task-scoped settings        connection settings
(batching, review,          (provider, model, api_key,
 terminology, OCR,           translator_base_url)
 research, delivery,
 target language)

1. job_state               1. explicit
2. job_state (fallback key) 2. provider_profile
3. job_state_derived       3. job_state (legacy flat / translator role)
4. persisted_default       4. user_defaults
5. explicit                5. application_defaults
6. user_defaults           6. default
7. application_defaults
8. default
```

Two rules make the split real:

* **task-scoped settings are decided by the task.**  A persisted job keeps its
  own decision; a later change to a global preference cannot rewrite it.
* **connection settings are decided live.**  A stale credential is never
  replayed, and an empty live value falls *through* to the profile instead of
  winning with `""`.

The environment layer is deliberately *not* duplicated: the core applies those
variables itself when the resolved value is absent.  `ENVIRONMENT_FALLBACKS`
names the eight variables that overlap this contract and a test asserts every one
of them is still read by `core.py`, so the list cannot silently drift.

Presentation-only preferences stay frontend-owned and were **not** moved:
`QSettings` (`appearance/theme`, `geometry`), `ui_preferences.json` (`theme`,
`locale`), panel/session UI state, `annotation_colors`.

## 2. EffectiveSettings design

```text
raw values, by origin
        ↓
SettingsSources(explicit, user_defaults, provider_profile,
                job_state, environment, application_defaults)
        ↓
resolve_effective_settings()      ← the one precedence policy
        ↓
EffectiveSettings(values, provenance, state, sources)
        ↓  .to_job_config()
JobConfig.from_settings()         ← normalization, validation, cross-field rules
        ↓
JobConfig.to_pipeline_kwargs() → existing core runtime
```

* `SettingsSources` — plain mappings, one per origin.  Frontends decide *where* a
  value came from; they never decide who wins.  `with_state(...)` re-resolves
  against a different job state (task creation knows its identity only after the
  document has been hashed).
* `EffectiveSettings` — the winning value for every runtime setting plus
  `provenance[field] = the layer that won`.  `winner(field)` answers "why is this
  value what it is" in one call.
* `describe(redact=True)` — a diagnostics-safe view: `{field: {value, source}}`
  with `api_key`/`reviewer_api_key` replaced by `***`.  This is the only shape
  diagnostics, fixtures and logs may print.
* `JobConfig` is unchanged as *the* execution contract.  `from_frontend(values,
  state)` is now a two-line shim over the resolver, and `from_settings(effective)`
  holds the normalization/validation/cross-field rules that were already there
  (reviewer endpoint inheritance, reasoning-effort support, the academic
  "report stays on" rule, delivery normalization, tri-state fields).

## 3. Qt / Streamlit changes

**Qt** (`folith/qt/adapter.py`)

* new `settings_sources(config, state)` — the named layers for one desktop action
  (`explicit` = `EngineConfig`; `provider_profile` = `core.load_provider_config()`;
  `job_state` = the task);
* `pipeline_kwargs(config, state)` now resolves through
  `resolve_effective_settings(...)` instead of `JobConfig.from_frontend(...)`;
* `EngineConfig.delivery_config(state)` is no longer consulted when building the
  contract — the resolver's "saved delivery wins" rule does it.  The method stays
  as public API but is no longer a second precedence implementation;
* `DEFAULT_STYLE_RULES` is now an alias of the application-layer default;
* `import_documents` / `start_job` pass `SettingsSources` to the shared commands.

**Streamlit** (`app.py`)

* `_job_config_values()` (a flat dict with defaults baked in) became
  `_settings_sources(state)` returning layers: `explicit` (session/current
  choices), `user_defaults` (strategy + delivery presets), `provider_profile`
  (`core.load_provider_config()`), `job_state`;
* the inline default chains (`or "standard"`, `or "auto"`, `"online"`,
  `"official"`, `or ""`) are gone — the contract's documented defaults apply;
* `enable_source_cleanup` / `ocr_dpi` / `ocr_psm` are passed as explicit `None`
  (tri-state "let the core decide") instead of being silently dropped;
* `strict_terminology_governance` moved to the preset layer it actually belongs
  to;
* the duplicated `style_rules` and target-language literals now come from the
  application default / `core.DEFAULT_TARGET_LANG`.

No new product functionality was added to Streamlit anywhere in this pass.

### 3.2 Desktop task defaults (follow-up product task)

Phase 4 exposed the `user_defaults` layer but deliberately left desktop storage
alone.  A follow-up task persisted the task defaults the settings dialog actually
shows, into the **existing** `QSettings` store (`Folith` / `QtDesktop`):

```text
task/target_lang  task/auto_term  task/enable_review  task/use_tm
task/enable_understanding  task/enable_source_cleanup  task/enable_report
task/ocr_language  task/ocr_mode  task/ocr_backend
```

Startup: `QSettings → EngineConfig → SettingsSources.user_defaults →
resolve_effective_settings()`.  A value that still equals the stored default is
reported from the `user_defaults` layer (not as a live edit); a dialog change in
the current session is the `explicit` layer and wins; a persisted task keeps its
own configuration (`job_state`).

Deliberately **not** persisted: the connection and review model (already in
`provider_config.json`, and an API key must never enter this store), the delivery
flags (already have `delivery/*` keys), and the fields the dialog preserves but
never displays — `translation_theory`, `style_rules`, `segmentation_mode`,
`translation_concurrency`, `enable_annotate`, `reasoning_effort` — which must not
be frozen into a global default.

## 4. Resume semantics

* A persisted task's `pipeline_config` **always** wins for task-scoped settings;
  a later global preference change is ignored (`test_a_persisted_task_ignores_later_global_preference_changes`
  asserts the winner is `job_state` for ten fields).
* Job-state fallbacks still work for legacy shapes: `state["glossary"]`,
  `state["theory"]`, `state["target_lang"]`, `state["quality_mode"]`,
  `state["report_enabled"]`, `state["enable_annotate"]`, and a *locked*
  `state["segmentation"]["mode"]` (`job_state_derived`).
* Persisted-only defaults: document understanding follows the task's own
  progress, source cleanup follows the core's PDF rule.
* The connection is the one thing a resume *does* re-read, because it is a global
  user setting — an empty live credential falls through to the provider profile
  rather than replacing a working one.
* `target_lang` is task-scoped on purpose: it is part of task identity.
* Resolution is deterministic: the same sources resolve to the same
  `JobConfig` fingerprint every time.

## 5. Cross-frontend equivalence

`test_qt_and_streamlit_equivalent_inputs_resolve_identically` builds equivalent
inputs on both sides — a Qt `EngineConfig` through `adapter.settings_sources(...)`
and app.py's real `_settings_sources(...)` executed against equivalent session
globals — and asserts:

* the only fields that differ are `research_settings` / `literature_sources`,
  the documented "desktop does not expose research products yet" divergence;
* with those two fed through the desktop layer, `to_dict()` and the contract
  fingerprint match **byte for byte**.

Additional structural evidence: `test_streamlit_builder_emits_named_layers` and
`test_qt_builder_emits_named_layers` assert that both builders really emit the
named layers (not one flattened dict), and `test_job_config_from_frontend_still_delegates_to_the_resolver`
asserts the legacy entry point goes through the resolver.

## 6. Locale

`folith/ui/i18n.py` no longer imports Streamlit at module scope:

* `catalog(locale)` / `tr(source, locale=...)` were already pure;
* `current_locale()` now tries the Streamlit session (`session_locale()`), then
  falls back to a process locale set through `set_locale(...)`;
* no Streamlit context → the documented default, exactly as before;
* `import folith.ui.i18n` + `catalog("en")` + `tr("…", locale="en")` now work in a
  process where `streamlit` and `PySide6` cannot be imported at all (proved in the
  headless subprocess test).

No Qt localization was built, as instructed.

## 7. Verification

```bash
venv/bin/python -m pytest tests/effective_settings_test.py -q    # 20 passed
venv/bin/python -m pytest tests/application_job_config_test.py -q # 30 passed (golden fixture unchanged)
venv/bin/python -m pytest tests/application_commands_test.py -q   # 52 passed
venv/bin/python -m pytest tests/state_mutation_lock_test.py -q    # 19 passed
venv/bin/python scripts/run_regression.py --tsv /tmp/phase4.tsv   # broad suite
```

The Phase 1 golden contract fixture was **not** regenerated: the resolver refactor
had to preserve every resolved value, and the frozen fingerprints prove it did.

Baseline: `/private/tmp/phase3-final.tsv` (167 files, 2 399 cases, 6 failing).
Phase 4: `/private/tmp/phase4-final.tsv` (168 files, 2 419 cases, 6 failing).

```text
FAIL baseline : 6
FAIL final    : 6
NEW failures  : []            <- none
new file      : effective_settings_test.py (+20 cases)

pre-existing failures, reproduced with identical results:

  brand_regression_test.py      1 failed, 2 passed
  desktop_handshake_test.py     3 failed, 11 passed
  ocr_language_rtl_test.py      1 failed, 22 passed
  ocr_sources_test.py           1 failed, 25 passed
  qt_extraction_error_test.py   1 failed, 4 passed
  scenario_gate_test.py         1 failed, 11 passed

pytest cases passed: 2 399 -> 2 419
```

**Conclusion: Phase 4 introduces no unexplained new failure.**

### Behaviour-preservation evidence

Two independent checks back the "resolution moved, values did not" claim:

1. the Phase 1 **golden contract fixture was not regenerated** — its frozen
   fingerprints still match after the resolver refactor;
2. `Qt EngineConfig.delivery_config()` was compared against the pre-Phase-4
   implementation (extracted from `arch/phase3-accepted`) over six state/dialog
   combinations: **0 mismatches**.

