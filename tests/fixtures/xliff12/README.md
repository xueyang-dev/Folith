# 通用 XLIFF 1.2 测试样本（G4-E）

真实的通用 XLIFF 1.2 文件，不是 SDLXLIFF，也不是 MQXLIFF。许可允许再分发，
许可文本原样放在 [`licenses/`](licenses/)。这些文件只证明内核的读入和写回，
不表示 Folith 已经在 Trados 或 memoQ 里验证过。

| 文件 | 来源 | 许可证 | 入库 blob SHA-256 | 上游原件 SHA-256 |
|---|---|---|---|---|
| `validators.en.xlf` | [`symfony/symfony` `src/Symfony/Component/Validator/Resources/translations/validators.en.xlf`](https://github.com/symfony/symfony/blob/681f04c9fff0b6a15d02962a9175161b38962171/src/Symfony/Component/Validator/Resources/translations/validators.en.xlf)，分支 8.2，提交 `681f04c9fff0b6a15d02962a9175161b38962171` | MIT，[`Symfony.txt`](licenses/Symfony.txt) | `45160ab0fb875a18ac52fd271f315849352b333d5bda601f2640f2f3609ba14f` | `45160ab0fb875a18ac52fd271f315849352b333d5bda601f2640f2f3609ba14f` |
| `messages.fr.xlf` | [`angular/angular` `adev/src/content/examples/i18n/src/locale/messages.fr.xlf`](https://github.com/angular/angular/blob/651c34735d011882b537c2ea2603467d00de8f67/adev/src/content/examples/i18n/src/locale/messages.fr.xlf)，提交 `651c34735d011882b537c2ea2603467d00de8f67` | MIT，[`Angular.txt`](licenses/Angular.txt) | `27edbf868bb8d98c82cba47008d4eece7f059fc13634d25501a34fa103680d10` | `27edbf868bb8d98c82cba47008d4eece7f059fc13634d25501a34fa103680d10` |
| `test1_es.xlf` | [`okapiframework/Okapi` `okapi/filters/xliff/src/test/resources/test1_es.xlf`](https://gitlab.com/okapiframework/Okapi/-/blob/51e134847b52532d6875e3985c842d45f39e1556/okapi/filters/xliff/src/test/resources/test1_es.xlf)，提交 `51e134847b52532d6875e3985c842d45f39e1556` | Apache-2.0，[`Okapi.txt`](licenses/Okapi.txt) | `c27fe5f204c8a0e256e6fcc58c74d503880e82139238b5ab5c962f1afbca2ba2` | `c27fe5f204c8a0e256e6fcc58c74d503880e82139238b5ab5c962f1afbca2ba2` |
| `manual-alttrans.xlf` | 同一 Okapi 提交的 `okapi/filters/xliff/src/test/resources/Manual-12-AltTrans.xlf`，入库时改名为 `manual-alttrans.xlf`，字节未改 | Apache-2.0，[`Okapi.txt`](licenses/Okapi.txt) | `703c334a66e57d7abb28582191322f2af2e7302ac13d550cb10135835fb9acaa` | `703c334a66e57d7abb28582191322f2af2e7302ac13d550cb10135835fb9acaa` |

四个上游文件的入库字节与取回的上游字节相同。`manual-alttrans.xlf` 的倒数第二行
在上游就是 CR LF，入库保留了这一个回车。`tests/fixtures/**` 已标记 `-text`，
检出不会再改行尾。

`test1_es.xlf` 的正文里有一处公开网页地址，发布门禁的 Linux 家目录正则会命中。
`scripts/release_preflight.py` 只按上表这个 SHA-256 豁免这一份字节。改任何一个
字节，豁免失效，门禁会重新扫描。这里不抄录那段地址。

## 派生文件（不是上游原件）

下面两份都从 `validators.en.xlf` 改来，用来观察内核对坏输入的反应。不要把它们
当成真实语料。

| 文件 | 改动 | 入库 blob SHA-256 | 所依据的上游 SHA-256 |
|---|---|---|---|
| `validators.en.damaged.xlf` | 删掉第一处 `</source>` 的结束 `>` | `4e51b6377704d9f10fa4caf44cd30263715ab4a8d51da79e2c276a0b4cced032` | `45160ab0fb875a18ac52fd271f315849352b333d5bda601f2640f2f3609ba14f` |
| `validators.en.unknown-namespace.xlf` | 在第一处 `</source>` 后插入 `<vendor:note xmlns:vendor="urn:example:not-xliff">foreign</vendor:note>` | `f5b2292d8fe84672c9967d1c9bba19f6f5a8b5efd4b9f5def2f8fcae74627e07` | `45160ab0fb875a18ac52fd271f315849352b333d5bda601f2640f2f3609ba14f` |

内核证据见 `docs/evidence/0.8.2-g4-xliff12-kernel.md`。
