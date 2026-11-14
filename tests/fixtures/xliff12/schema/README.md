# XLIFF 1.2 校验用的 schema

这两份 XSD 是 OASIS XLIFF 1.2 标准原件，字节没有改过。标准允许为了实现而复制，
条件是带上版权声明和 Notices。声明在 [`OASIS-notices.txt`](OASIS-notices.txt)。

| 文件 | 来源 | SHA-256 |
|---|---|---|
| `xliff-core-1.2-strict.xsd` | <https://docs.oasis-open.org/xliff/v1.2/os/xliff-core-1.2-strict.xsd> | `9f37af564ecd27d5f69154d7cfe7ba8deb1f620c81b94cf0eab1f4205c74d5f8` |
| `xliff-core-1.2-transitional.xsd` | <https://docs.oasis-open.org/xliff/v1.2/os/xliff-core-1.2-transitional.xsd> | `aae679f3ff5016e72f1231f7e808397e065d0e8f1c8ef01c7c4a75a7a05a9248` |

两份 OASIS 文件在上游是 CR LF，入库保留。`tests/fixtures/**` 已标记 `-text`。

`xml.xsd` 不是 OASIS 的文件。XLIFF schema 用
`schemaLocation="http://www.w3.org/2001/xml.xsd"` 引用它，用来声明 `xml:lang`
和 `xml:space`。这里放一份未改过的副本，校验时不再访问网络。

| 文件 | 来源 | SHA-256 |
|---|---|---|
| `xml.xsd` | <https://www.w3.org/2001/xml.xsd> | `61960fb3131e38022caad5360e2f33a3382578ab3c80cd58bd74320ede61b20c` |

W3C 文档许可见 <https://www.w3.org/copyright/document-license/>。这份 schema
本身没有单独的版权段落。

校验用 lxml（libxml2）。不修改这两份 OASIS XSD 来改 `schemaLocation`。
