# 连接期间的后台更新验收

2026-11-13 的普通可渲染 Editor 验收覆盖 AC-01 至 AC-04，默认检查及双语手册
覆盖 AC-05。此记录保留以后回归后台更新、退出恢复和偏好保持时需要的输入身份、
测量基线和观察边界。要求及最终审核、集成状态由工单和 PR 维护。
[Issue #44](https://github.com/Alexis-Li/MtoULiveLink/issues/44)、[PR #56](https://github.com/Alexis-Li/MtoULiveLink/pull/56)

## 基线与输入

- 被测代码为 `74d004c26c0b289b8a77472c20323742092b46da`，比较基线为
  `ca32727b4e819712dcd139bb39c86f838ed4d0c5`。后续验收记录提交只增加文档。
- Windows、stock UE 5.7.4 CL 51494982、Maya 2024.2（24.2.0.1186），D3D12、
  NVIDIA GeForce RTX 4060。Editor 使用独立临时工程、当前 checkout 和专用端口。
  普通进程未传 `-unattended`，未运行 Automation，逐阶段快照确认两个标志均为 false。
- 中性样本为共享 `background-updates.json` 中的单 Primary 身体与服装网格，
  Additional Parts 为零，观察四个已蒙皮骨骼。夹具 SHA-256 为
  `40450926170796a7e3598281690055c1f188e0855c1f6cd524b4003cf7d6d891`。
- 每次 Maya 均复制已初始化的最小偏好模板，`pluginPrefs.mel` SHA-256 为
  `6aa456abf66e689a0fe584fa4344ed0d7ccb3082048088c75961276693132fa0`。
  每个新 peer 先逐帧确认 `motion_frames` 的 100 个输入，准备过程单独记录。
- 测量前固定的门槛为连接至少 10 Hz、producer 至少 8 Hz、后台 Editor 帧率
  至少保留同轮前台的 0.8、未连接后台至多 4 Hz、恢复容差 1.25、姿势保留率 0.8。
  夹具记录各项依据，正式窗口均为约 20 秒，实际采样时长见下表。

## 保留的实现决定

Runtime 的公开时间控制快照表达完整协商会话是否仍可发布。Editor 的私有 owner
在 `OnBeginFrame` 期间登记本插件的 CPU 降频回调，并对当前关卡视口调用
`RequestRealTimeFrames(1)`。CPU 回调再次读取会话有效性，结束门立即关闭需求，
下一帧或 Stop 按确切 handle 回收登记。模块关闭和 engine pre-exit 共用幂等清理。

该实现使用公开引擎接口，沿用已有会话桥。每帧的一帧请求能越过引擎的后台非实时
override，并随帧号到期，配置中的实时值和后台 CPU 偏好均不需要改写。
这也是移除原有持久 realtime override、统一由 Editor 拥有刷新权的理由。
引擎能力依据在 `<UE>/Engine/Source` 下的 `EditorEngine.h/.cpp`、
`EditorViewportClient.h`、`LaunchEngineLoop.cpp` 和 `CoreDelegates.h`。

## 普通宿主结果

下表的 Editor Hz 是实际引擎帧号增量除以单调时钟时长。Producer Hz 来自 Maya
完成求值的确认时间，Display Hz 来自真实组件四个骨骼姿势发生变化的次数。
所有阶段的真实前台 PID 占比为 1，连接后台阶段的前台均为本轮 Maya。
删除 Actor 和替换世界后的空显示集以空单元表示。

| 阶段 | 实际秒数 | Editor Hz | Producer Hz | Display Hz |
| --- | ---: | ---: | ---: | ---: |
| 前台连接参考 | 20.030 | 98.901 | 10.192 | 10.135 |
| Maya 前台、未连接基线 | 19.995 | 3.001 | 0 | 0 |
| Maya 前台、后台连接 | 20.041 | 97.152 | 11.368 | 11.327 |
| 正常断开后 | 19.988 | 3.002 | 0 | 0 |
| 正常断开后重连 | 20.091 | 96.164 | 11.253 | 11.149 |
| 致命协议错误后 | 19.993 | 3.001 | 0 | 0 |
| 协议错误后重连 | 20.017 | 96.966 | 11.197 | 11.090 |
| 删除 Binding Actor 后 | 19.993 | 3.001 | 0 | |
| 删除 Actor 后重建并重连 | 20.021 | 96.747 | 11.144 | 11.088 |
| 无保存替换世界后 | 19.990 | 3.001 | 0 | |
| 替换世界后重建并重连 | 20.073 | 97.094 | 11.175 | 11.059 |
| 真实 Maya 进程退出后 | 19.990 | 3.002 | 0 | 0 |
| 新 Maya peer 重连 | 20.024 | 97.183 | 11.132 | 11.087 |
| 移除 Live Link source 后 | 19.992 | 3.001 | 0 | 0 |

第二次独立普通宿主运行在连接中关闭 Editor。前台参考为 94.727 Hz，后台连接为
96.796 Hz，后台 producer 为 11.359 Hz，Display 为 11.276 Hz，未连接基线为
3.002 Hz。关闭前快照确认会话仍连接且本插件登记为 1。两次运行的 Editor、Maya
及第一次运行中被替换的 Maya peer 均正常退出，退出码为 0。

连接时，本插件 CPU 登记为 1、CPU 不降频、视口实时。每个退出恢复阶段登记为 0、
CPU 恢复降频、后台视口非实时。各视口配置中的基础实时值前后相同。
稳定帧 300 和 900 的组件世界位置与 Maya 坐标转换后的最大误差为
`0.000004795 cm`，低于夹具的 `0.1 cm` 门槛。

每个普通进程通过命令行把 **Use Less CPU when in Background** 设置为 true，
每个样本及原生快照均读回 true。用户配置中完整 `EditorPerformanceSettings`
节前后相同。完整文件哈希也有记录，唯一无关变化是 Editor 自动更新最近工程的
`LastOpenTime`。

对话逐阶段核对，只出现该夹具的 `BLENDSHAPE_MISMATCH`、致命协议帧对应的
`INVALID_MESSAGE` 和销毁目标、替换世界、移除 source、退出 Editor 对应的
`STREAM_INTERRUPTED`。

## 可见视口与最小化

独立视觉运行复用同一 Maya driver、真实 Streaming session 和组件观测入口。
原生窗口操作保持 Maya 前台，并让后台 UE 的透视视口露出。对测试场景内完整角色
Group 的 Y 平移解锁后，从 0 改为 40 cm，UE 角色表面明显向上移动。原生快照中
四个骨骼的 Z 坐标均增加 40 cm，位置与 Maya 的误差约 `0.0000047 cm`。
该过程通过 Maya 主线程文件命令改变实际输入，UE 端仅被动观测。

随后最小化 Editor，Win32 `IsIconic` 为 true，关卡视口从可见桌面消失。
期间将输入偏移恢复到 0 并设置 900 帧。最小化时的单次组件观测仍保留上一个
40 cm 偏移，该样本只用于窗口状态。恢复窗口并重新让 Maya 前台后，角色表面回到
地面，组件正确显示最新 900 帧与零偏移。两个测试进程正常退出，退出码为 0。

这些观察由原生 GUI 截图与窗口标志共同确认。最小化期间的 GPU 绘制提交次数没有
直接测量，引擎在编辑器窗口全隐藏时跳过常规视口绘制的代码作为机制依据。
该样本仍有材质传递依赖缺失，视觉观察使用回落灰材质的无光照几何，范围为位置与
更新响应。缓存、镜头联动、完整材质外观和多部件可见性没有在此单独认证。

## 回归与复现

最终 stock Development Editor 构建成功。`MtoULiveLink` 默认 Automation
109/109 成功，warnings、failed、notRun 均为 0，退出码为 0。项目校验通过，
项目 Python 测试 53/53 成功。Maya 产品与 driver 没有本轮增量，复用已独立核实的
纯 Python 517/517、Maya 2024 与 2022.4 host 各 39/39 的历史证据。

普通宿主复现入口和共享夹具模板由当前测试文档维护。使用 `--seconds 20`，分别
运行默认退出路径和 `--scenario editor_exit`，按工具公布的 PID 切换真实前台。
默认 Automation 从正常临时世界启动，避免把 `/Engine/Maps/Entry` 作为
Preview 生成测试的资产 outer。[宿主验收步骤](../testing.md#ordinary-editor-background-updates)

本记录的数值来自 `ordinary-exits-20261113-05`、
`ordinary-editor-exit-20261113-01` 和 `ordinary-visual-20261113-03` 的本地原始记录，
经独立审核重新计算。早期未满足 producer 门槛或输入焦点失效的视觉运行没有计入
通过结论。生产图像与资产保持本地，后续回归使用共享输入重新生成过程记录。
