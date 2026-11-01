# TU Contract v1 fixture corpus 来源记录

本目录是 TU Contract v1（Issue #143）的共享测试资产，供后续所有 CAT 双语
格式适配器 Issue（XLIFF / SDLXLIFF / MQXLIFF）复用。规范见
`docs/tu-contract-v1.md`。

## 来源与授权

**本目录当前全部样本均为合成样本**：由
`generate_fixtures.py` 在仓库内生成，内容为最小化的人工构造结构（骨架形状
参考 XLIFF，但契约层将其中一切负载视为不透明字符串），不含任何真实项目、
客户或第三方材料。因此**无需外部使用授权**，随本仓库许可一同分发。

真实导出样本（Trados / memoQ 导出的 SDLXLIFF、MQXLIFF 等）只有在取得
书面使用授权、并在下表登记来源与授权范围之后，才允许加入本目录。无授权
来源的样本不得进入仓库。

## 样本清单

| 文件 | 内容 | 用途 |
| --- | --- | --- |
| `synthetic-minimal.json` | 两个无标签 TU（untranslated / translated） | 状态映射、最小导入 |
| `synthetic-nested-tags.json` | 嵌套成对标签（b 含 i）+ 自闭合占位符 | 标签配对/嵌套校验、标签子集检查 |
| `synthetic-locked.json` | locked 段（translate=no）+ 普通段 | 锁定段 → 排除账本映射 |
| `synthetic-notes.json` | TU 内注释与上下文组 | 元数据保全（原样携带、导出写回） |
| `synthetic-unknown-namespace.json` | 单元内未知命名空间节点 | 单元级拒绝路径（UNKNOWN_NAMESPACE） |
| `synthetic-track-changes.json` | 含未决修订的单元 | 单元级拒绝路径（PENDING_TRACK_CHANGES） |
| `synthetic-noop-roundtrip.json` | 源树与重导出树完全一致 | 结构 diff 工具：no-op 判空 |
| `synthetic-tampered-roundtrip.json` | 故意篡改的导出（属性改动/丢节点/文本变化） | 结构 diff 工具：精确差异黄金输出 |

## 重建方式

```bash
python tests/fixtures/tu_contract/generate_fixtures.py
```

生成器是样本唯一权威来源；手工编辑 JSON 会在下次重建时被覆盖。黄金 diff
（`synthetic-tampered-roundtrip.json` 的 `expected.expected_diff`）由生成器
调用契约层的 diff 工具补算，保证 fixture 与工具定义同步。
