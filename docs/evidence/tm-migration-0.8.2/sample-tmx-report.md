# 导入校验报告 / Import verification report

- schema `folith-import-report-v1`
- 格式 / Format: `tmx`
- 文件 / File: `metadata_rich.tmx`（2501 bytes，sha256 `fe7f7e432e3027d66a0ae2010c9efb0af8eaf9b161e1f037b692c1ebe4be31bd`）
- 生成时间 / Generated: `2026-11-10T21:58:23+00:00`
- 状态 / Status: `partial`

## 语言对 / Language pair

| field | value |
| --- | --- |
| expected_target | 简体中文 |
| expected_tag | zh-CN |
| declared_source | en |
| declared_targets | zh-CN |
| declared_target_tags | zh-CN |
| status | match |
| action | none |
| mismatch_units | 0 |
| undeclared_target_units | 1 |

语言对一致：文件声明 zh-CN，任务目标 zh-CN。 / Declared and expected target languages match.

## 分类计数 / Classified counts

| count | meaning | value |
| --- | --- | --- |
| total | 总单元数 / Total units | 8 |
| imported | 已导入 / Imported | 4 |
| skipped_conflict | 冲突跳过（不覆盖已审校条目） / Skipped (conflict) | 1 |
| skipped_ineligible | 不合格（_tm_eligible 未通过） / Ineligible | 1 |
| skipped_unsupported_inline | 含无法处理的行内内容 / Unsupported inline content | 1 |
| skipped_language_unknown | 语言未知 / Language unknown | 0 |
| skipped_language_mismatch | 语言不符（文件声明 vs 目标） / Language mismatch | 0 |
| skipped_missing_pair | 缺语言对 / Missing language pair | 1 |
| skipped_write_failed | 写入失败（存储层错误） / Write failed | 0 |

## 元数据丢失 / Metadata loss

| category | meaning | affected | fields |
| --- | --- | --- | --- |
| tmx_tu_attributes | TU 属性（creationdate/changeid 等） | 2 | changeid, creationdate, creationid |
| tmx_prop | TU 的 prop（客户/领域等） | 1 | client, domain |
| tmx_note | TU 的 note | 1 | explanation |
| tmx_tuv_attributes | TUV 属性（xml:lang 以外） | 1 | creationdate |
| tmx_extra_languages | 第三种及以后的语言 | 1 | fr |
| tmx_header | TMX header 元数据（文件级；1=该文件） | 1 | adminlang, creationdate, creationtool, creationtoolversion, datatype, note, o-tmf, segtype, srclang |

受影响条目总数 / Total affected entries: 3

## 警告 / Warnings

- `conflict_skipped` (1): 与现有翻译记忆冲突，未覆盖已审校条目。
- `ineligible_units` (1): 源文不含字母/数字或译文不可用，未入库。
- `unsupported_inline_content` (1): 含无法可靠处理的行内内容，整单元不导入。
- `missing_language_pair_units` (1): 缺少双语（语言对）内容，未入库。
- `metadata_dropped` (3): 部分单元元数据未保留（见元数据丢失清单）。

## 失败与恢复 / Failure & recovery

- error: (none)
- recovery: 已写入 4 条；被跳过的单元没有入库，既有条目未被覆盖。同一文件重新导入不会重复写入（已存在的键计入冲突跳过）。 / Partial import: skipped units were not stored and no existing entry was overwritten. Re-importing the same file does not duplicate entries.

## 持久化 / Persistence

- directory: `docs/evidence/tm-migration-0.8.2`
- json: `docs/evidence/tm-migration-0.8.2/sample-tmx-report.json`
- markdown: `docs/evidence/tm-migration-0.8.2/sample-tmx-report.md`
