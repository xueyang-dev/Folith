# 导入校验报告 / Import verification report

- schema `folith-import-report-v1`
- 格式 / Format: `tbx`
- 文件 / File: `metadata_rich.tbx`（2276 bytes，sha256 `898e541d72859f86dc7ca2bc09e36916c46c22e9207cf2df657b3bda41861149`）
- 生成时间 / Generated: `2026-11-10T21:18:14+00:00`
- 状态 / Status: `partial`

## 语言对 / Language pair

| field | value |
| --- | --- |
| expected_target | 简体中文 |
| expected_tag | zh-CN |
| declared_source | en |
| declared_targets | zh-CN, zh-TW |
| declared_target_tags | zh-CN, zh-TW |
| status | partial_mismatch |
| action | warned |
| mismatch_units | 1 |
| undeclared_target_units | 0 |

文件同时声明了 zh-CN, zh-TW，其中与任务目标 zh-CN 不符的 1 个单元已拒绝导入（不会改标为目标语言）。 / Units whose declared target differs were rejected, never retagged.

## 分类计数 / Classified counts

| count | meaning | value |
| --- | --- | --- |
| total | 总单元数 / Total units | 5 |
| imported | 已导入 / Imported | 2 |
| skipped_conflict | 冲突跳过（不覆盖已审校条目） / Skipped (conflict) | 0 |
| skipped_ineligible | 不合格（_tm_eligible 未通过） / Ineligible | 0 |
| skipped_unsupported_inline | 含无法处理的行内内容 / Unsupported inline content | 0 |
| skipped_language_unknown | 语言未知 / Language unknown | 1 |
| skipped_language_mismatch | 语言不符（文件声明 vs 目标） / Language mismatch | 1 |
| skipped_missing_pair | 缺语言对 / Missing language pair | 1 |
| skipped_write_failed | 写入失败（存储层错误） / Write failed | 0 |

## 元数据丢失 / Metadata loss

| category | meaning | affected | fields |
| --- | --- | --- | --- |
| tbx_term_entry_attributes | termEntry 属性（id 以外） | 0 | (none) |
| tbx_term_note | termNote | 2 | termStatus |
| tbx_extra_languages | 第三种及以后的语言 | 1 | fr |
| tbx_multiple_terms | 同语言多词条 | 1 | zh-CN |
| tbx_descrip | descrip（定义/领域等） | 1 | definition |

受影响条目总数 / Total affected entries: 2

## 警告 / Warnings

- `language_pair_mismatch` (1): 文件同时声明了 zh-CN, zh-TW，其中与任务目标 zh-CN 不符的 1 个单元已拒绝导入（不会改标为目标语言）。 / Units whose declared target differs were rejected, never retagged.
- `language_unknown_units` (1): 语言无法确定（缺 xml:lang 且调用方未给目标语言），未入库。
- `missing_language_pair_units` (1): 缺少双语（语言对）内容，未入库。
- `metadata_dropped` (2): 部分单元元数据未保留（见元数据丢失清单）。

## 失败与恢复 / Failure & recovery

- error: (none)
- recovery: 已写入 2 条；被跳过的单元没有入库，既有条目未被覆盖。同一文件重新导入不会重复写入（已存在的键计入冲突跳过）。 / Partial import: skipped units were not stored and no existing entry was overwritten. Re-importing the same file does not duplicate entries.

## 持久化 / Persistence

- directory: `docs/evidence/tm-migration-0.8.2`
- json: `docs/evidence/tm-migration-0.8.2/sample-tbx-report.json`
- markdown: `docs/evidence/tm-migration-0.8.2/sample-tbx-report.md`
