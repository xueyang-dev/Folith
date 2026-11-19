# ruff 的 bug 类静态检查（#460）

## 它做什么

`ruff` 只开**能抓真错**的规则，并把今天已有的违例冻结成基线：

| 规则 | 抓什么 |
| --- | --- |
| `F`（pyflakes 全家） | 未定义名（F821）、未使用导入（F401）、未使用变量（F841）、重复定义（F811）、f-string 缺占位符（F541）等 |
| `E722` | bare `except:` |
| `B017` | `pytest.raises(Exception)` |
| `B023` | 函数闭包用了循环变量 |
| `B030` | `except` 挂了非异常类 |
| `B039` | `ContextVar` 默认值用可变对象 |
| `B904` | `except` 里 `raise` 没写 `from` |

**不是**格式化器，也不开风格/命名/排序规则：那类建议一次性上千条，没人修，只会让门变成噪声。

## 怎么跑

```bash
python scripts/check_ruff_baseline.py                  # 检查（CI 同款）
python scripts/check_ruff_baseline.py --update-baseline # 重写基线（只收不放）
```

CI 在 `PR Fast Checks` 的 `classify changes` 作业里跑（与其它静态棘轮检查同一处），
`ruff` 版本钉死在 `pyproject.toml` 的 `test` extra 与工作流里（**同一条版本**）。

## 基线怎么读

`docs/ruff-baseline.txt` 每行一条：`代码 <tab> 文件 <tab> 出错那一行的内容`。

- **不含行号**：改一处代码不该让几百条基线同时失效；
- **按次数比较**：同一行同一规则出现两次是一条基线条目 + 一条新违例；
- 新违例 → PR 红，提示「新逻辑放新模块」（与 `tests/line_budget_test.py` 同一口径）；
- 修掉旧违例 → 通过；顺手 `--update-baseline` 把基线收紧，这就是棘轮。

2026-11-19 冻结时共 **640** 条，分布在 F401（未使用导入）最多，其次是 F811 / F841 / B023。
这些是历史存量，**不在引入 ruff 的这个 PR 里大面积改代码**；后续谁碰哪个文件，就顺手把自己
那段清掉。

## 加新规则

先加进 `pyproject.toml` 的 `[tool.ruff.lint].select` 和 `scripts/check_ruff_baseline.py` 的
`RULES`（两处必须一致），跑 `--update-baseline` 让存量进基线，再单独提一个 PR 说明为什么这条
规则值得开。
