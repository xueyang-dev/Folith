# Logo 探索：两页 + 骑缝印

探索稿，尚未替换 `folith/resources/brand/` 里的正式素材。

**构思**：保留原 logo 里两张错叠的页（名字里的"页"）；去掉指针和"文/A"；在原文页与译文页的接缝上压一枚朱红方印。骑缝章的意思是"这两页属于同一份、经人确认"，对应产品的"AI 起稿，你来定稿"。

| 文件 | 用途 |
| --- | --- |
| `folith-mark-seal.svg` | 主图标，64 px 以上 |
| `folith-mark-seal-small.svg` | 16–32 px 简化版：去掉文字行和印框，加大间隙和印 |
| `folith-mark-seal-dark.svg` | 深色背景版：前页改为浅色 |
| `folith-app-icon-seal.svg` | App 图标，1024 画布、824 白色连续曲线底板 |
| `folith-app-icon-seal-small.svg` | 小尺寸 App 图标 |
| `preview.png` | 各尺寸、深浅背景，与现有图标对照 |

**颜色**：墨蓝 `#0B1F3B`（译文页）、界面蓝 `#2B5FD1`（原文页）、朱红 `#C8372D`（印，只用于 logo，界面里红色表示危险）。

**做法**：页之间和印周围的间隙由 `QPainterPath` 布尔运算算成真实镂空，不用 `mask`，所以 Qt 的 SVG 渲染器、浏览器和设计软件里显示一致。修改参数后重跑：

```bash
QT_QPA_PLATFORM=offscreen venv/bin/python docs/logo-exploration/build_logo.py
```

## 原 logo 的 1:1 矢量复刻

`folith-mark-replica.svg` 是 `folith/resources/brand/folith-mark-hires.png` 的矢量复刻，画布同样是 1254×1254，可以直接替换位图使用。它是从原图描摹出来的，不是凭印象重画：

- 按颜色把像素分成浅色页、深色页和白色图形，再用位置约束修正少数判断错的像素。
- 白色图形（文、指针、A）以红色通道作为抗锯齿覆盖度，在 4 倍分辨率下描边，所以边缘落在亚像素位置。
- 轮廓拟合成平滑的三次贝塞尔曲线，A 的尖角、文的笔画端点这类真正的转角保持锐利。
- 两页各用一条按实测颜色拟合的线性渐变，7 个色标。

与原图逐像素对比：平均误差 1.31/255，误差超过 40 的像素占 0.06%，基本都在抗锯齿边缘上（见 `replica-compare.png`：原图、复刻、差异×4）。

```bash
python3 docs/logo-exploration/trace_original.py        # 需要 cv2 + numpy
venv/bin/python docs/logo-exploration/compare_replica.py
```

**还没做**：中英文组合字标（需要把字形转成轮廓）、`.icns` 打包、在真实 Dock 和侧栏里的对照截图。
