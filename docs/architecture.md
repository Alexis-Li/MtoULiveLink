# MtoU 架构导航

本文是当前代码的入口与职责地图。术语由 [CONTEXT](../CONTEXT.md) 定义，
产品范围由 [roadmap](roadmap.md) 维护，任务要求在 Issue 中，完整线上契约在
[protocol](../protocol/README.md)。本文不重复这些定义，也不替代相关 ADR。

## 两端边界

```text
Maya 场景与面板 → 角色快照、求值姿势 → Streaming session
                                      ↕ 线上协议
UE Live Link Source → 连接协商 → Binding / Actor → 角色预览
                                      ↕
                            Editor 预览生成与 Sequencer 联动
```

Maya 与 Unreal 分别安装，只通过线上协议协作，不读取另一端的源码或配置。
`protocol/`、`docs/`、`tools/`、`tests/` 不随插件安装。协议语料是双端检查输入，
不是共享运行时。运行时行为与 UE 编辑器专用行为分属两个模块。

## Maya 入口与状态

以下文件位于 `maya/MtoULiveLink/scripts/`：

| 入口 | 职责与排查方向 |
| --- | --- |
| `MtoULiveLink.py` | 用户及工具架启动入口，唤起唯一主窗 |
| `mtou_livelink/controller.py` | 无窗口的 Maya 操作入口。协调场景、连接、缓存、联动和资源释放，并提供供面板读取的状态快照 |
| `mtou_livelink/scene.py` | Character scene 与角色快照相关逻辑 |
| `mtou_livelink/session.py` | 一条实际连接的 Streaming session、采样和资源；会话拥有实时去重和采样统计，发送线程处理协议、协商、独立截止检查并记录实际发送 |
| `mtou_livelink/protocol.py` | Maya 端消息构造与验证；契约正文在 protocol/ |
| `mtou_livelink/cache.py` | Cached Playback |
| `mtou_livelink/linkage.py`、`timecontrol.py`、`camera.py` | 联动会话、时间控制和相机相关行为 |
| `mtou_livelink/preferences.py` | Maya 用户偏好中的场景设置，按规范化的场景路径分开保存 |
| `mtou_livelink/diagnostics.py`、`strings.py` | 最近操作结果和中文界面、诊断与恢复文案 |
| `mtou_livelink/ui/`、`qt_compat.py` | 原生 Qt 四页主窗和完整诊断窗口，Qt 绑定和 Maya 主窗包装集中在兼容层 |

排查时从面板动作追到控制器、对应会话及场景资源的所有者。控制器在换场景前释放旧资源，在换完后恢复设置。
重复启动和关闭遵守 AGENTS 的窗口、回调、scriptJob 与临时节点清理规则。

## Unreal 入口与状态

以下路径相对于 `unreal/MtoULiveLink/Source/`，表内实现文件在模块的 `Private/`：

| 模块 / 文件 | 职责与排查方向 |
| --- | --- |
| `MtoULiveLink` / `MtoULiveLinkSource.*` | Live Link Source、连接与收流；拥有终止原因、双端协调、一次性重连许可、新连接主体准入及实时预览性能统计 |
| `MtoULiveLink` / `MtoUPreviewRemapAsset.*` | 实时预览的骨骼求值与完成回报；Source 聚合同一姿势的显示组件完成事件 |
| `MtoULiveLink` / `MtoUConnectionNegotiator.*` | Character snapshot 与目标的连接协商 |
| `MtoULiveLink` / `MtoULiveLinkProtocol.*` | UE 端线上消息与验证 |
| `MtoULiveLink` / `MtoULiveLinkBinding.cpp`、`MtoULiveLinkActor.cpp` | Binding 配置及 Actor 的预览显示与生命周期 |
| `MtoULiveLink` / `MtoUCharacterComposition.cpp` | Primary Driver 与 Additional Parts 的角色组成 |
| `MtoULiveLink` / `MtoUCacheSession.*`、`MtoULinkageSession.*` | 缓存播放与联动会话状态 |
| `MtoULiveLinkEditor` / `MtoULiveLinkPreview.cpp`、`MtoUDriverGarmentSurface.*` | 编辑器预览生成及 Driver garment surface 解析 |
| `MtoULiveLinkEditor` / `MtoULinkageEditor.*` | 编辑器联动边界 |
| `MtoULiveLinkEditor` / `MtoULiveLinkActorDetails.*` | Actor Details 界面 |

修改状态前核对调用者、线程边界和资源所有者；不要在面板或下游显示处复制会话
状态或绕过统一终止路径。Actor/Binding 分类输入变化与主体更换并请求终止；
缓存与联动各自恢复资源，Source 在两端准备完成后才授权新连接。Maya 面板与 UE
Details 读取所有者的协调状态，不另建重连计时器。Public 头文件仅承载有意提供的接口。

## 常用决定与验证入口

- 预览网格保持瞬态：[ADR 0001](adr/0001-keep-preview-meshes-transient.md)。
- 输入变化结束流：[ADR 0002](adr/0002-end-streaming-when-preview-inputs-change.md)。
- 网格处理仅使用公开 API：[ADR 0006](adr/0006-use-only-public-mesh-processing-apis.md)。
- Preview Morph transfer：[ADR 0010](adr/0010-project-driver-morphs-during-preview-refresh.md)。
- 缓存完整上传后播放：[ADR 0013](adr/0013-upload-the-cache-before-local-replay.md)。
- 单一时间权威与整组姿势：[ADR 0014](adr/0014-one-time-authority-and-staged-pose-groups.md)。

默认检查见 [AGENTS](../AGENTS.md)，真实宿主与生产夹具验收见
[testing](testing.md)。先选择覆盖改动行为的检查，再查需要复用的历史证据。
