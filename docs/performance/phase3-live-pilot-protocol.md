# Phase 3 live pilot：审批包与验收协议

状态：**Paragraph 预算上限 US$5 已获批；数据发送尚未授权，未执行真实请求**。Reasoning 预算须后续单独确认。本轮只交付工程收束和离线计划工具，当前没有选择真实连接、发送样本或产生 provider 费用。

## 先做 Paragraph 2k/4k

三个对照组：当前 sentence 基线（6 items /4800 chars）、paragraph 2k（20 items）、paragraph 4k（20 items）。不加入 6k、换模型、C4 推广或 translation/annotation overlap，以免混入其他因素。Paragraph 的 char budget 是组批预算；不能拆开的超长单元可能超出该预算，计划会明确标出。

使用同一组经过审批的代表性窗口，建议从既有研究的四个候选窗口（0–11、240–251、480–491、720–731，inclusive）开始审查其真实覆盖；这些数字不是已批准的样本，也不能替代实际 source/context/glossary 内容审阅。缺少长段、跨句指代、重复专名、术语、数字、占位符、URL、引用等覆盖时先调整样本，再冻结范围。不得将用户原文/译文提交到 Git。

每窗口做两个交错重复；顺序采用预先固定的 ABC/BCA/CAB 轮换，保存实际顺序。保持相同模型、最终 endpoint、语言、术语/风格、上下文范围、review/QA 政策和并发。并发建议上限 4，仍需服务商额度确认；若额度只支持更低并发，三个 variant 一起降低。2k/4k 在隔离实验进程中使用实际 core planner，不能通过改变应用全局默认来试验。

**已批准预算上限**：Paragraph US$5；服务端硬额度仍须核实并启用。建议最多 160 个所有 role 的 logical requests、320 个 HTTP attempts，具体运行计划仍须确认。翻译、review、repair、knowledge、探针和所有重试都计入，不能把辅助调用藏在预算之外。实际价格/最大输出未确定前不能声称这能跑完所有窗口；触及任一上限停止，缺失重复标为不完整，不自动加预算。

## 再做 Annotation / Knowledge Reasoning A/B

只测 annotation、knowledge 两个 role；每 role 4 个完全相同 payload 的配对，provider_default /thinking_disabled 共 16 个 logical requests，能力探针至多 2 个，总上限 18 logical /36 HTTP attempts。采样参数、输出 schema、模型与最终 endpoint 固定；不把 reviewer 自动设成 thinking_disabled。

运行控制要对应真实 role：annotation 使用 translator 的 `reasoning_mode`，knowledge 使用 `auxiliary_reasoning_mode`；reviewer 不继承这些开关。分别测实际 role 完成时间（包含解析和所需确定性处理），不能把单个调用差值直接外推为整书提速。Annotation 评估定位/说明的依据、幻觉与误标；Knowledge 评估名称/指代连续性、术语候选依据和错误知识污染，保留现有治理与人工裁定。

**单独预算提案**：provider-side 硬上限 US$3。若要自动化模型裁判，必须在预算内重算请求额度或另批；默认使用双语人工盲评。HTTP 200 或响应成功不证明 gateway 真正执行了 thinking_disabled；缺少 usage/provider 证据时，执行效果标 UNKNOWN。

## 真实请求之前必须确认

1. 真实 provider、model、最终 endpoint，以及实际计费规则/币种/日期。
2. 可复核的样本包：source segment IDs 和精确原文、请求会带出的前后文、术语/风格材料；reasoning 的 annotation target、knowledge payload 也在发送授权内。只批准文件名不足以隐含批准全书和所有上下文。
3. Paragraph US$5 上限已批；Reasoning 预算仍待单独确认；provider-side 硬额度已启用；支持的输出上界。客户端收到响应后再停止，不能单独保证美元硬上限。
4. 当前 runtime commit、样本顺序、policy 和运行参数冻结；人工质量 rubric 与停止条件明确。

`scripts/phase3_pilot_plan.py` 是 **offline-only**，没有 live mode，也不把 manifest 的自称授权视为真实授权。输入本地 JSON 包含 dataset_id 与 segments（id/source/paragraph_id）；输出只有 ID、字符数与 actual core 计划，不复制正文、译文或密钥。它是审批准备工具，不是已经通过费用控制验收的付费 runner。实际请求 driver 的计费预留/HTTP attempt gate 仍须在选定服务商后核对，未满足预检不能启动。

```sh
python scripts/phase3_pilot_plan.py --manifest /private/path/samples.json --out /private/path/pilot-plan.json
```

## 实际收益与质量门禁

主指标是 **相同 required quality policy 下到 translation_ready 的实际墙钟时间**，以及完整实验 run（包含必要 QA/review/repair/knowledge 收敛）的完成时间。记录实际排队、provider 等待、生成、修复、持久化与失败恢复；不能把各批耗时求和冒充墙钟，也不能在正文生成结束就停止计时。

每个 paired block 报告 baseline/candidate 秒数与 `Tbaseline / Tcandidate`；墙钟下降率为 `1 - Tcandidate / Tbaseline`。保持相同输入覆盖，公开全部重复的原始数字、范围/中位数与样本量；不足以估计稳定 P95 时明确 unknown。仅在质量通过的相同范围内报告吞吐。计划批次数、token 与费用是辅助指标。

**少 64% 批次不等于快 64%**：更长 decode、网关竞争、QA/知识反馈、失败修复和不同输出长度都会改变实际时间。不得用离线固定 service-time 回放代替真实 provider 结果。一次小 pilot 通过也不能证明全书质量非劣。

硬失败：漏/重复 required IDs、错位、截断、保护项丢失、新关键语义错误或不可接受的锁定术语错误，停止该 variant；结果保留供盲评。准确性、遗漏/增译、专名/指代、术语、语气和跨段连续性由双语盲评裁定，不制造综合评分替代关键错误。

停止：用户取消、存储失败、额度不足、用量/费用无法控制、输出上界不支持，立即停止新 dispatch；连续两次 429/5xx/连接错误停止，不提高并发补偿。baseline 负载明显漂移时暂停配对块，不把负载差算成优化收益。

只有真实完成时间可重复改善、质量通过且恢复/费用无不可接受回归，才讨论默认推广。C4 仍是显式实验选项；Paragraph 4k 和 thinking_disabled 也不因计划计数变好而自动成为默认。
