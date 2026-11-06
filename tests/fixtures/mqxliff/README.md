# MQXLIFF 测试样本(Issue #146 Gate A)

真实 memoQ 导出文件(Windows 导出,`tool-id="MQ"`、`tool-company="Kilgray"`),
来自 MIT 许可的公开仓库(允许再分发,保留原许可证条款)。仅作只读结构审计与
将来的 Gate B round-trip 测试基线,不得当作 Folith 已支持 MQXLIFF 的依据。

| 文件 | 来源(MIT) | memoQ 版本 | SHA-256 |
|---|---|---|---|
| `document.mqxliff` | [ShunSakurai/check_forbidden](https://github.com/ShunSakurai/check_forbidden) → `Test/document.mqxliff` | 7.8.124 | `384533689d1bce18845d2762bc89eb7e3bfe5e2b5549add6b10bf65b5025cccb` |
| `locked.mqxliff` | [bb-io/OpenAI](https://github.com/bb-io/OpenAI) → `Tests.OpenAI/Input/locked.mqxliff` | 10.3.12 | `5b3de4d08266a8b378e048f2fc0dfa16fee2165e40e9108cdd57f456fa98f66e` |
| `mt.mqxliff` | [ikuehirata/MT-quality-review](https://github.com/ikuehirata/MT-quality-review) → `sample_and_materials/mt.mqxliff` | 11.6.8 | `9c228773a9cf10d2954e9578b45e46ff76491294cdd9233ceb4d1fe367eb9754` |
| `pe.mqxliff` | [ikuehirata/MT-quality-review](https://github.com/ikuehirata/MT-quality-review) → `sample_and_materials/pe.mqxliff` | 11.6.8 | `12b338dc75b8083161072c83b774015cc9d5842ff677a8d1108ff42686921566` |
| `translation_status.mqxliff` | [ikuehirata/mqxliff-viewer](https://github.com/ikuehirata/mqxliff-viewer) → `sample/translation_status_sample.xlsx_jpn.mqxliff` | 11.6.8 | `381f331e8c000a8bd76a422662c52c541611fd5d1b26cbcc4aaba81d7bcf2e44` |

仅审计、未入库的参考样本(许可证不适配,存仓库外
`verification/issue-146-mqxliff-gatea/audit-only-samples/`):

| 文件 | 来源 | 许可证 | 用途 |
|---|---|---|---|
| `test_bitext_en_es.mqxliff` | [luismond/tm2tb](https://github.com/luismond/tm2tb) → `tests/data/test_bitext_en_es.mqxliff` | GPL-3.0 | memoQ 10.4 交叉验证(commitinfos、historical-unit 结构一致性) |

结构结论与证据见 `docs/mqxliff-gate-a-compatibility-matrix.md`;
全量审计输出可用 `python scripts/audit_mqxliff.py` 复现。
