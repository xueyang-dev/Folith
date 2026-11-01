# memoQ 支持边界声明

日期:2026-10-26 · Issue:#146 · 里程碑:v0.9.0
配套调查报告:`docs/mqxliff-gate-a-compatibility-matrix.md`(矩阵与证据细节以该文为准)

按 #146 验收标准,本文单独声明 Folith 对 memoQ 交换场景的支持边界,
不与 XLIFF / SDLXLIFF 的支持表述合并。

## 当前状态(0.9.0 时点)

**Folith 当前不承诺任何 MQXLIFF 支持。** `.mqxliff` 文件在 Folith 中没有
专用适配器;XLIFF 1.2/2.0 通用 round trip 由 #144 承载,截至本文撰写
尚未合入。本文声明的边界针对 #146 调查完成后、Gate B(如实施)之前的
窗口,并约束 Gate B 实施时的声明口径。

## 支持什么(如 Gate B 实施,且仅当对照实验要求方言层时)

- 基于 XLIFF 1.2 通用适配器(#144)叠加的**受限 MQXLIFF round trip**,
  范围以兼容性矩阵 §3 为准:P 类原样保全、M 类状态映射(7 种观测状态值)、
  R 类显式拒绝。
- 适用格式判定:`.mqxliff` 扩展名、根元素为 XLIFF 1.2 的 `xliff`
  （命名空间 `urn:oasis:names:tc:xliff:document:1.2`），且存在 URI 为
  字面量 `MQXliff` 的 memoQ 扩展。不能把扩展命名空间误认为根命名空间。

## 不承诺什么

- **未经 memoQ 回读验证**:全部结论来自 6 份真实 memoQ 导出样本的
  只读结构审计(样本为 memoQ 7.8–11.6 时代导出),没有任何一份导出文件
  在 memoQ 客户端中重新导入验证过。支持声明必须降级表述为
  「经样本集验证的受限 round trip,未经 memoQ 验证」。
- **MQOUT / MQBACK 项目包**:memoQ 项目包格式不在 #146 范围内
  (独立可行性审计:#147)。Folith 不读写 `.mqout`/`.mqback`。
- **memoQ 服务器 / ProjectServer / TMS 生态**:Web Service API、
  在线项目、同步工作流均不支持。
- **memoQ 的标准 XLIFF 交换路径**:是否覆盖 Gate 6 第三类试验场景,
  由对照实验(矩阵 §5 协议)判定;实验完成前,Folith 不对
  「memoQ ↔ Folith 经标准 XLIFF」作任何兼容性声明。
- **split/join 后的文档**、多轮审阅(Reviewer2/3)状态、`.mqxlz` 项目内
  文件:样本未覆盖,行为未知,不得声明支持。

## 样本与验证缺口(失败时的诚实口径)

- 对照实验被 #144 前置阻塞,截至 2026-10-26 未执行;在此之前,
  「Folith 支持 memoQ 交换」的任何说法都不成立。
- 若 Gate B 实施后导出文件无法通过 memoQ 回读,或用户报告状态丢失,
  正确口径是报告兼容性缺陷并回查矩阵 M 类映射,而非宣称完整支持。
