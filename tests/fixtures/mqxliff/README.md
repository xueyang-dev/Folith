# MQXLIFF 测试样本(Issue #146 Gate A)

真实 memoQ 导出文件(Windows 导出,`tool-id="MQ"`、`tool-company="Kilgray"`),
来自 MIT 许可的公开仓库(允许再分发,保留原许可证条款)。仅作只读结构审计与
将来的 Gate B round-trip 测试基线,不得当作 Folith 已支持 MQXLIFF 的依据。

| 文件 | 来源(MIT) | memoQ 版本 | 入库 blob SHA-256 | 上游原件 SHA-256 |
|---|---|---|---|---|
| `document.mqxliff` | [`ShunSakurai/check_forbidden/Test/document.mqxliff`](https://github.com/ShunSakurai/check_forbidden/blob/0d5615bd824fe2d81a8be34fbbea18072c243b02/Test/document.mqxliff) | 7.8.124 | `c547f55e4f22e2d31762bd8037351afdd9d4dd981a3d888ff59a0aa2109afa1e` | `384533689d1bce18845d2762bc89eb7e3bfe5e2b5549add6b10bf65b5025cccb` |
| `locked.mqxliff` | [`bb-io/OpenAI/Tests.OpenAI/Input/locked.mqxliff`](https://github.com/bb-io/OpenAI/blob/253b614638b2d0f34f69b2c1d8fe9b6ffa095d6d/Tests.OpenAI/Input/locked.mqxliff) | 10.3.12 | `5b3de4d08266a8b378e048f2fc0dfa16fee2165e40e9108cdd57f456fa98f66e` | `5b3de4d08266a8b378e048f2fc0dfa16fee2165e40e9108cdd57f456fa98f66e` |
| `mt.mqxliff` | [`ikuehirata/MT-quality-review/sample_and_materials/mt.mqxliff`](https://github.com/ikuehirata/MT-quality-review/blob/0fc8e5a695a6c1da87570614b6dc534d04f7542b/sample_and_materials/mt.mqxliff) | 11.6.8 | `9c228773a9cf10d2954e9578b45e46ff76491294cdd9233ceb4d1fe367eb9754` | `9c228773a9cf10d2954e9578b45e46ff76491294cdd9233ceb4d1fe367eb9754` |
| `pe.mqxliff` | [`ikuehirata/MT-quality-review/sample_and_materials/pe.mqxliff`](https://github.com/ikuehirata/MT-quality-review/blob/0fc8e5a695a6c1da87570614b6dc534d04f7542b/sample_and_materials/pe.mqxliff) | 11.6.8 | `12b338dc75b8083161072c83b774015cc9d5842ff677a8d1108ff42686921566` | `12b338dc75b8083161072c83b774015cc9d5842ff677a8d1108ff42686921566` |
| `translation_status.mqxliff` | [`ikuehirata/mqxliff-viewer/sample/translation_status_sample.xlsx_jpn.mqxliff`](https://github.com/ikuehirata/mqxliff-viewer/blob/616d3afce618d1a75457497bee1e8ae902e93421/sample/translation_status_sample.xlsx_jpn.mqxliff) | 11.6.8 | `381f331e8c000a8bd76a422662c52c541611fd5d1b26cbcc4aaba81d7bcf2e44` | `381f331e8c000a8bd76a422662c52c541611fd5d1b26cbcc4aaba81d7bcf2e44` |

表格中的哈希由 `tests/fixture_provenance_test.py` 守护：入库 blob 的 SHA-256
必须等于"入库 blob SHA-256"列；"上游原件 SHA-256"列给出上游固定 commit 的原始
字节指纹。后四个文件上游与入库逐字节相同。

仅审计、未入库的参考样本(许可证不适配,存仓库外
`verification/issue-146-mqxliff-gatea/audit-only-samples/`):

| 文件 | 来源 | 许可证 | 用途 |
|---|---|---|---|
| `test_bitext_en_es.mqxliff` | [luismond/tm2tb](https://github.com/luismond/tm2tb) → `tests/data/test_bitext_en_es.mqxliff` | GPL-3.0 | memoQ 10.4 交叉验证(commitinfos、historical-unit 结构一致性) |

结构结论与证据见 `docs/mqxliff-gate-a-compatibility-matrix.md`;
全量审计输出可用 `python scripts/audit_mqxliff.py` 复现。

## 上游许可与入库差异

- ShunSakurai/check_forbidden 的[上游许可文件](https://github.com/ShunSakurai/check_forbidden/blob/0d5615bd824fe2d81a8be34fbbea18072c243b02/license.md)保存在 [`ShunSakurai-check_forbidden.txt`](licenses/ShunSakurai-check_forbidden.txt)。
- bb-io/OpenAI 的[上游许可文件](https://github.com/bb-io/OpenAI/blob/253b614638b2d0f34f69b2c1d8fe9b6ffa095d6d/LICENSE)保存在 [`bb-io-OpenAI.txt`](licenses/bb-io-OpenAI.txt)。
- ikuehirata/MT-quality-review 的[上游许可文件](https://github.com/ikuehirata/MT-quality-review/blob/0fc8e5a695a6c1da87570614b6dc534d04f7542b/LICENSE)保存在 [`ikuehirata-MT-quality-review.txt`](licenses/ikuehirata-MT-quality-review.txt)。
- ikuehirata/mqxliff-viewer 的[上游许可文件](https://github.com/ikuehirata/mqxliff-viewer/blob/616d3afce618d1a75457497bee1e8ae902e93421/LICENSE)保存在 [`ikuehirata-mqxliff-viewer.txt`](licenses/ikuehirata-mqxliff-viewer.txt)。

[mqxliff-viewer 的上游 README](https://github.com/ikuehirata/mqxliff-viewer/blob/616d3afce618d1a75457497bee1e8ae902e93421/README.md#sample)把该文件列为测试样本，并声明仓库采用 [MIT 许可](https://opensource.org/license/mit)。上游 LICENSE 只有 `MIT` 字样，原样保留。其余三个来源的许可副本含上游版权声明。

### 换行口径（2026-11-12 复核，结论：差异只来自换行）

`document.mqxliff` 的上游原件是 **CRLF** 行尾（71684 B，485 个回车），入库 blob
是 **LF**（71199 B，0 个回车）。两者**只差换行**：把入库 blob 的 485 个 LF
程序化还原为 CRLF 后，SHA-256 为
`384533689d1bce18845d2762bc89eb7e3bfe5e2b5549add6b10bf65b5025cccb`，与上游原件
逐字节一致，解码文本相同。**不是上游版本不同**：本文件的结构事实（16 段、7.8
时代的 `mq:warnings40`、`tool-id="MQ"`）与上游固定 commit 一致，且还原后的字节
哈希相同。

复核依据：2026-11-12 按上表链接的固定 commit
`0d5615bd824fe2d81a8be34fbbea18072c243b02` 直接取回上游原件（71684 B，CRLF），
其 SHA-256 与上表"上游原件"列一致；对入库 blob 做 LF→CRLF 还原得到同一串字节。

其余四个样本的上游原件本身就是 LF，与入库 blob 逐字节相同。

复核方式（不联网的回归看门）：`python -m pytest tests/fixture_provenance_test.py -q`。
