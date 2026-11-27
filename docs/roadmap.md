# MtoU 产品范围与下一阶段

当前正式发布为 [1.0.0](https://github.com/Alexis-Li/MtoULiveLink/releases/tag/v1.0.0)
（2026-10-30）。该版本的用法和协议见
[发布版手册](https://github.com/Alexis-Li/MtoULiveLink/blob/v1.0.0/README_CN.md)及
[协议 v11](https://github.com/Alexis-Li/MtoULiveLink/blob/v1.0.0/protocol/README.md)。
1.1 已验收并合入 `main`，跳过单独发布。1.2 从该里程碑继续，开发分支为
`dev/maya-led-preview`。当前用法、未发布变更和验收证据分别见
[手册](../README_CN.md)、[CHANGELOG](../CHANGELOG.md#unreleased)和[项目历史](history/README.md)。

## 开发分支现状

- **实时预览**：Animation 与 Model 两种工作流，单角色或主角色加一个独立
  骨骼对象（道具或副角色），Additional Parts 属于同一角色的组成。
- **缓存播放**：Maya 捕获并完整上传单角色缓存，由 UE Binding Actor 播放、
  暂停、定位、循环和停止（[ADR 0013](adr/0013-upload-the-cache-before-local-replay.md)）。
- **服装比较**：同一 Model 会话中切换原始服装与生成服装。
- **镜头联动**：Maya 跟随 UE Level Sequence 的时间与求值相机，互斥时间控制，
  整组姿势一致显示，两端都可返回实时或结束预览
  （[ADR 0014](adr/0014-one-time-authority-and-staged-pose-groups.md)）。

宿主：Windows 64 位，Maya 2024，stock UE 5.7.4；
仅编辑器内使用。开发分支使用尚未发布的[协议 v12](../protocol/README.md)。

## 持续约束

- 最终效果以 UE 场景和镜头为准，动画在 Maya 制作；沿用既有导出、导入与
  Sequencer 组装流程，产品不在 UE 中创建动画或预览资产。
- 独立道具和不同骨架角色不套用 Additional Parts，也不自动重定向。
- 根骨骼即可捕获角色；衣服由当前可见的蒙皮网格集合确定，Display 枚举仅作可选标签。
- 同一主体的真实输入变化结束旧流，在恢复资源后自动发起新的完整协商；主体更换、
  手动停止或失败均需用户明确连接。Model 仍需显式刷新建立 Preview readiness
  （[ADR 0002](adr/0002-end-streaming-when-preview-inputs-change.md)）。
- 预览网格保持瞬态（[ADR 0001](adr/0001-keep-preview-meshes-transient.md)）。

## 已知限制

- 从含中文的路径编译 UE 插件源码会失败（MSVC 无法打开 PCH），属于工具链
  限制；已编译的插件可在此类工程中加载。
- [预览工作流联合验收](history/preview-workflow-integration-acceptance.md)未覆盖
  交互视口逐帧观察和生产规模下生成预览后的失败内存测量。

## 1.2 开发计划

首批工作是 Maya 面板重做（#47）与 UE 独立面板和首次设置检查（#48），两项可并行。
之后依次进行镜头导入（#40）、世界坐标对齐（#41）、姿势传输重做（#49）、
由 Maya 控制时间的镜头联动（#50）和交付收尾（#52）。直接驱动关卡角色的调研（#51）
排在 1.2 之后。各项需求、依赖和验收入口见[产品方向总工单 #43](https://github.com/Alexis-Li/MtoULiveLink/issues/43)。

- Maya 面板重做包含面板配置记忆（[#47](https://github.com/Alexis-Li/MtoULiveLink/issues/47)）。
- UE 的 Level Sequence 镜头通过镜头包单向导入 Maya，独立于角色和连接。
  镜头与附带场景使用 UE 关卡世界坐标，画框无法精确对应的参数列为近似项。
  场景由用户勾选，结果与 UE「导出选中项」后在 Maya 默认导入一致，可带材质
  分配及外部图片路径引用，图片数据不随包交付（[#40](https://github.com/Alexis-Li/MtoULiveLink/issues/40)）。
- 连接后 Maya 的角色世界坐标将默认对应 UE 关卡世界坐标，用户可选以 Binding
  为原点（[#41](https://github.com/Alexis-Li/MtoULiveLink/issues/41)）。

## 下一阶段候选

以下方向尚未立项，排序和取舍由下一阶段的需求讨论决定：

- 多对象缓存播放，以及由 Sequencer 时间驱动的缓存；
- 更多对象（超过一个副对象）与更广的内容类型支持；
- 生成服装的预览刷新性能优化、固定检查姿势／书签、问题区域可视化；
- 新的动画交付流程或录制 UE 动画；
- PIE／打包运行、跨机器连接；
- 更多宿主版本认证（其他 Maya 版本、UE 版本或第三方引擎分支）。
