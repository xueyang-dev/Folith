# Explicit batch strategy contract

`JobConfig.batch_strategy` carries `sentence` or `paragraph` through the existing shared settings/adapter contract. Desktop presets keep their historical sentence strategy; bare new core calls retain the paragraph default.

At pipeline entry, a valid saved task strategy wins over explicit input, then legacy inference from existing pairs/batch arguments, then the core default. Invalid nonempty JobConfig values are rejected. Existing batches, pair IDs, prompts and required review policy are not migrated. Configured/effective diagnostics keep unknown values unknown.

This contract enables controlled Paragraph 2k/4k experiments; it makes no latency or quality claim and does not change production batching defaults. Validation: batch strategy, JobConfig, effective settings and paragraph batching suites.
