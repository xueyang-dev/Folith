# 第三方样本来源与授权（根级索引）

本文件记录仓库里所有**第三方真实样本**的来源、许可证与入库字节指纹，供发布评审与
合规检查使用。它只陈述**可核对的事实**；不构成法律意见。若需要正式的法律结论，
须由人复核。

最后更新：2026-11-12。两类方言样本的固定上游链接和许可副本见各目录 README。

## 它是什么 / 不是什么

- **是**：入库样本的上游仓库、路径、许可证、上游版本、以及**入库 blob 的 SHA-256**。
- **不是**：Folith 支持某个格式的声明。样本用于只读结构审计与 round-trip 基线，
  详见各目录 README 与 `docs/*-gate-a-compatibility-matrix.md`。

## SDLXLIFF（`tests/fixtures/sdlxliff/`，Issue #145 Gate A）

上游为 **Apache-2.0**（可再分发，保留原始许可证条款）。来源仓库：
`RWS/Sdl-Community`、`RWS/trados-studio-api-samples`。

| 文件 | 上游路径 | 入库 blob SHA-256 | 上游原件 SHA-256 |
|---|---|---|---|
| `QuotesSample.docx.sdlxliff` | `RWS/Sdl-Community` → `XLIFF Manager for Trados Studio/XLIFF.Manager.UnitTests/TestFiles/Sdlxliff/QuotesSample.docx.sdlxliff` | `95a9514d5f5b7e7349639621dedc8bc3a63c75866e6721000fefbeed1800870b` | `95a9514d5f5b7e7349639621dedc8bc3a63c75866e6721000fefbeed1800870b` |
| `TTSExample.docx.sdlxliff` | `RWS/Sdl-Community` → `SDLTM Import Plus/SDLTMImportUnitTests/TestingFiles/TTSExample.docx.sdlxliff` | `ab3fcbcb32a91ad894c25636be6970c70c218f674e3d57b87c8eaf4b909b8710` | `ab3fcbcb32a91ad894c25636be6970c70c218f674e3d57b87c8eaf4b909b8710` |
| `quickplace.docx.sdlxliff` | `RWS/Sdl-Community` → `SDLTM Import Plus/SDLTMImportUnitTests/TestingFiles/#1 - quickplace.docx.sdlxliff` | `85e67619be7594ff80aaae03f17adbbe85f048680f39b74b31e36e19fff1cb4b` | `85e67619be7594ff80aaae03f17adbbe85f048680f39b74b31e36e19fff1cb4b` |
| `SamplePhotoPrinter.doc.sdlxliff` | `RWS/trados-studio-api-samples` → `LanguagePlatform/TMAutomation/SamplePhotoPrinter.doc.sdlxliff` | `b27a14cb95e058d3c1007c6ced037f656fe190f0ebc4c6b509e48db488f57dd3` | `d9fc637cdb18d6577ceb3908f3da42286d7135b7473f31a55ee04cf905bcb1ef` |

## MQXLIFF（`tests/fixtures/mqxliff/`，Issue #146 Gate A）

上游为 **MIT**（可再分发，保留原许可证条款）。文件由 memoQ 在 Windows 导出
（`tool-id="MQ"`、`tool-company="Kilgray"`）。

| 文件 | 上游仓库 | 上游版本 | 入库 blob SHA-256 | 上游原件 SHA-256 |
|---|---|---|---|---|
| `document.mqxliff` | `ShunSakurai/check_forbidden` → `Test/document.mqxliff` | 7.8.124 | `c547f55e4f22e2d31762bd8037351afdd9d4dd981a3d888ff59a0aa2109afa1e` | `384533689d1bce18845d2762bc89eb7e3bfe5e2b5549add6b10bf65b5025cccb` |
| `locked.mqxliff` | `bb-io/OpenAI` → `Tests.OpenAI/Input/locked.mqxliff` | 10.3.12 | `5b3de4d08266a8b378e048f2fc0dfa16fee2165e40e9108cdd57f456fa98f66e` | `5b3de4d08266a8b378e048f2fc0dfa16fee2165e40e9108cdd57f456fa98f66e` |
| `mt.mqxliff` | `ikuehirata/MT-quality-review` → `sample_and_materials/mt.mqxliff` | 11.6.8 | `9c228773a9cf10d2954e9578b45e46ff76491294cdd9233ceb4d1fe367eb9754` | `9c228773a9cf10d2954e9578b45e46ff76491294cdd9233ceb4d1fe367eb9754` |
| `pe.mqxliff` | `ikuehirata/MT-quality-review` → `sample_and_materials/pe.mqxliff` | 11.6.8 | `12b338dc75b8083161072c83b774015cc9d5842ff677a8d1108ff42686921566` | `12b338dc75b8083161072c83b774015cc9d5842ff677a8d1108ff42686921566` |
| `translation_status.mqxliff` | `ikuehirata/mqxliff-viewer` → `sample/translation_status_sample.xlsx_jpn.mqxliff` | 11.6.8 | `381f331e8c000a8bd76a422662c52c541611fd5d1b26cbcc4aaba81d7bcf2e44` | `381f331e8c000a8bd76a422662c52c541611fd5d1b26cbcc4aaba81d7bcf2e44` |

## 入库时的换行规范化

9 个样本里有 2 个的上游原件使用 CRLF，入库时统一转成了 LF；其余 7 个上游原件
本身就是 LF，与入库 blob 逐字节相同：

| 文件 | 上游原件 | 入库 blob | 差异 |
|---|---|---|---|
| `SamplePhotoPrinter.doc.sdlxliff` | 42996 B，CRLF（298 个回车），`d9fc637c…` | 42698 B，LF，`b27a14cb…` | 仅换行 |
| `document.mqxliff` | 71684 B，CRLF（485 个回车），`38453368…` | 71199 B，LF，`c547f55e…` | 仅换行 |

2026-11-12 已按各目录 README 里的固定 commit 取回上游原件复核：还原换行后的
字节哈希与上表"上游原件"列一致，解码文本相同。`document.mqxliff` 的差异**不是**
上游版本不同，只是 Git 检出/入库时的换行规范化。

上表记录提交内 blob 的 SHA-256 与上游原件的 SHA-256；逐文件说明和复核方式见
[SDLXLIFF README](tests/fixtures/sdlxliff/README.md#上游许可与入库差异) 和
[MQXLIFF README](tests/fixtures/mqxliff/README.md#上游许可与入库差异)。
`tests/fixture_provenance_test.py` 会在每次回归时重算这些哈希，并断言 CRLF
还原可复算上游指纹。

## 检出必须复现入库字节（`.gitattributes`）

上表的哈希记录的是**入库 blob**，而 `tests/fixture_provenance_test.py` 读的是
**检出后的工作区字节**。默认情况下两者并不相等：Windows 的
`core.autocrlf=true`（runner 与多数 Windows 开发机的默认值）会在检出时把每个
LF 换成 CRLF。2026-11-12 的 Windows 全量回归因此红了 22 条（节点 P0-8f），
`QuotesSample.docx.sdlxliff` 被检出成 35577 B / `4873e754…`；
`SamplePhotoPrinter.doc.sdlxliff` 更是被还原成上游原件的 CRLF 字节
（42996 B / `d9fc637c…`），于是"上游可复算"也一起失真。

修复是在 [`.gitattributes`](.gitattributes) 里把 `tests/fixtures/**` 标记为
`-text`，关闭行尾转换。`-text` 只影响换行，不影响 `diff`：这些 XML/JSON 仍按
文本 diff。规则覆盖 `licenses/*.txt`、golden JSON/TSV/CSV 与 TMX/TBX ——
不加规则时 56 个 fixture 文件里有 45 个会被 autocrlf 改写，下一个字节看门
迟早踩到同一坑。代价是从 CRLF 工作区提交会把 CRLF 存进 blob，而那正是逐字节
看门应该报红的情况。

**已有克隆**要重新检出这些文件（属性只影响下一次检出/写入）：

```sh
git pull
git checkout -- tests/fixtures     # 按入库字节重写本机文件
```

属性生效后，Windows 上的 `git status` 会先把这 45 个文件显示为已修改（工作区
还是旧的 CRLF 字节）；第二条命令执行完即恢复干净。**不要用
`git add --renormalize`**：`-text` 的含义是"不做任何转换"，renormalize 会把
CRLF 工作区字节原样写进暂存区（实测入库 blob 35384 B → 暂存 35577 B），等于
把要修的转换结果提交回去。macOS/Linux 的克隆本来就没有转换，`git status`
不会变脏，无需任何操作。

`tests/fixture_provenance_test.py::test_fixture_files_are_marked_no_eol_conversion`
守着这条属性：删掉 `.gitattributes` 里的规则会先在本地变红，不必等 Windows。

## 未入库的参考样本

| 文件 | 上游 | 许可证 | 为什么不入库 |
|---|---|---|---|
| `test_bitext_en_es.mqxliff` | `luismond/tm2tb` → `tests/data/test_bitext_en_es.mqxliff` | **GPL-3.0** | 许可证与本仓库不适配；只在本机 `verification/issue-146-mqxliff-gatea/audit-only-samples/` 做交叉验证 |

## 上游许可

[SDLXLIFF](tests/fixtures/sdlxliff/README.md) 与 [MQXLIFF](tests/fixtures/mqxliff/README.md)
目录列有固定上游提交、样本路径和许可文件副本。四份 SDLXLIFF 来自 Apache-2.0
仓库。五份 MQXLIFF 的来源声明采用 MIT。其中 `mqxliff-viewer` 的 README 明确列出
测试样本及 MIT 声明，许可文件以原样保存。另三份 MIT 许可文件保留其版权声明。

## 还缺什么（计划 D8 的要求）

- [ ] **至少 2 个真实的通用 XLIFF 1.2 文件**（不是 SDLXLIFF/MQXLIFF）：导入后不做修改
      直接导出，结构 diff 为空。目前仓库只有方言样本。

## 如何核对

```bash
# 逐文件重算入库指纹
for f in tests/fixtures/sdlxliff/*.sdlxliff tests/fixtures/mqxliff/*.mqxliff; do
  shasum -a 256 "$f"
done

# 结构审计（只读）
python scripts/audit_sdlxliff.py
python scripts/audit_mqxliff.py
```

目录级细节见 `tests/fixtures/sdlxliff/README.md`、`tests/fixtures/mqxliff/README.md`。
