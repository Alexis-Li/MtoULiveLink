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
| `MtoULiveLink.py` | 用户启动入口 |
| `mtou_livelink/controller.py` | 面板、会话与场景状态的产品控制入口；查操作触发和生命周期 |
| `mtou_livelink/scene.py` | Character scene 与角色快照相关逻辑 |
| `mtou_livelink/session.py` | 实时 Streaming session、发送线程和协商事件 |
| `mtou_livelink/protocol.py` | Maya 端消息构造与验证；契约正文在 protocol/ |
| `mtou_livelink/cache.py` | Cached Playback |
| `mtou_livelink/linkage.py`、`timecontrol.py`、`camera.py` | 联动会话、时间控制和相机相关行为 |
| `mtou_livelink/ui/` | 面板与附属窗口 |

排查时从用户操作追到控制器、对应会话及场景资源的所有者。重复启动和关闭须
遵守 AGENTS 的窗口、回调、scriptJob 与临时节点清理规则。

## Unreal 入口与状态

以下路径相对于 `unreal/MtoULiveLink/Source/`，表内实现文件在模块的 `Private/`：

| 模块 / 文件 | 职责与排查方向 |
| --- | --- |
| `MtoULiveLink` / `MtoULiveLinkSource.*` | Live Link Source、连接与收流；Source 和 Actor 共用会话终止边界 |
| `MtoULiveLink` / `MtoUConnectionNegotiator.*` | Character snapshot 与目标的连接协商 |
| `MtoULiveLink` / `MtoULiveLinkProtocol.*` | UE 端线上消息与验证 |
| `MtoULiveLink` / `MtoULiveLinkBinding.cpp`、`MtoULiveLinkActor.cpp` | Binding 配置及 Actor 的预览显示与生命周期 |
| `MtoULiveLink` / `MtoUCharacterComposition.cpp` | Primary Driver 与 Additional Parts 的角色组成 |
| `MtoULiveLink` / `MtoUCacheSession.*`、`MtoULinkageSession.*` | 缓存播放与联动会话状态 |
| `MtoULiveLinkEditor` / `MtoULiveLinkPreview.cpp`、`MtoUDriverGarmentSurface.*` | 编辑器预览生成及 Driver garment surface 解析 |
| `MtoULiveLinkEditor` / `MtoULinkageEditor.*` | 编辑器联动边界 |
| `MtoULiveLinkEditor` / `MtoULiveLinkActorDetails.*` | Actor Details 界面 |

修改状态前核对调用者、线程边界和资源所有者；不要在面板或下游显示处复制会话
状态或绕过统一终止路径。Public 头文件仅承载有意提供的接口。

## 常用决定与验证入口

- 预览网格保持瞬态：[ADR 0001](adr/0001-keep-preview-meshes-transient.md)。
- 输入变化结束流：[ADR 0002](adr/0002-end-streaming-when-preview-inputs-change.md)。
- 网格处理仅使用公开 API：[ADR 0006](adr/0006-use-only-public-mesh-processing-apis.md)。
- Preview Morph transfer：[ADR 0010](adr/0010-project-driver-morphs-during-preview-refresh.md)。
- 缓存完整上传后播放：[ADR 0013](adr/0013-upload-the-cache-before-local-replay.md)。
- 单一时间权威与整组姿势：[ADR 0014](adr/0014-one-time-authority-and-staged-pose-groups.md)。

默认检查见 [AGENTS](../AGENTS.md)，真实宿主与生产夹具验收见
[testing](testing.md)。先选择覆盖改动行为的检查，再查需要复用的历史证据。
