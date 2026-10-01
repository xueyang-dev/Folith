# Runtime Details / HUD 验证报告

## Run Details Fixes

保留 Drawer 结构。HUD 与 Drawer 共用 runtime_presentation，读取同一 runtime snapshot；不再用 trace span 推测当前阶段。阶段和操作同时更新，近期活动按当前阶段筛选。翻译进度按已保存批次计数，消除报告的 11 项进度污染；0% 的填充宽度为 0，未知进度使用 indeterminate。时间标明任务运行和最近活动。并发编号等元数据收进技术详情。

## Runtime HUD Before / After

原状态栏侧重阶段名；现在以当前操作为标题，辅以阶段、已保存批次 / 总批次、百分比和模型等待 / 活动年龄。进度条 5px。参考旧结构含操作区高度约 132px，当前 HUD 含操作区约 115px（不含新增暂停入口时；入口保持同一行）。HUD 主体为 102px，八种运行状态高度不变。旧结构尺寸来自隔离参考样本，不是用户生产页面的历史实测。

新增轻量暂停任务 / 继续任务操作。暂停为协作式：在模型调用、批次调度或阶段说明边界阻止后续操作；已发出的请求可以返回，内存结果和已保存检查点保留。取消可唤醒暂停的线程。应用退出后从持久检查点恢复，未保存的内存工作不保留。

工作区高度：固定控件使用自然高度，只有包含三栏的祖先分支占据剩余视口。实际任务在 2048×982 视口实测，三栏从 y=212 延伸到 y=958；屏幕底部只余 24px，不再留下大片空白。截图 workspace-fixed.jpg。

## State Mapping

core 写入 stage_id / operation_id / operation_label / stage_progress / last_activity_at / model_wait_started_at。read_runtime_view 读取并核对 worker lease；HUD 与 Drawer 调用同一纯 presentation model。context_build 显示构建上下文和未知进度；translation_generation 显示段落范围；waiting_external 显示等待时间；state_commit 显示保存；retry 显示重试；pausing / paused 显示暂停过渡与继续入口。心跳不能替代真实活动。

## Browser Screenshots

- translating-hud.jpg
- waiting-hud.jpg
- context-build-hud.jpg
- workspace-fixed.jpg
- paused.jpg

## Tests

相关集成检查 89 passed；暂停 / 继续 / 取消的线程测试另在其中覆盖。浏览器隔离样本验证术语准备、上下文构建、翻译、等待模型、保存、重试、取消、完成，并追加暂停与继续点击。模型请求使用受控样本；没有暂停或取消用户正在运行的真实任务。实际工作区仅检查布局和按钮。

更广范围先前发现 4 项既有断言失败：建议标签的两项期望、编辑器高度期望、非选中行操作菜单。它们未纳入通过计数，未以本轮改动修复。

RUNTIME DETAILS POLISH = PASS

RUNTIME HUD UPGRADE = PASS

限制：八种状态的浏览器验证采用生产渲染代码与隔离状态样本，未进行全部真实模型端到端流程。已启动的旧 worker 需要进入带暂停检查点的新运行，才能使用新增控制逻辑；本轮没有重启真实任务。
