# MtoU 产品范围与下一阶段

当前发布：**1.0.0**（2026-10-30，协议 v11）。用户用法见
[README](../README.md)，版本变化见 [CHANGELOG](../CHANGELOG.md)，各项验收
证据见[项目历史](history/README.md)。本文只记录现行范围、仍然有效的产品约束
和下一阶段的候选方向；候选项立项时在 Issue 中定义需求与验收。

## 1.0.0 范围

- **实时预览**：Animation 与 Model 两种工作流，单角色或主角色加一个独立
  骨骼对象（道具或副角色），Additional Parts 属于同一角色的组成。
- **缓存播放**：Maya 捕获并完整上传单角色缓存，由 UE Binding Actor 播放、
  暂停、定位、循环和停止（[ADR 0013](adr/0013-upload-the-cache-before-local-replay.md)）。
- **服装比较**：同一 Model 会话中切换原始服装与生成服装。
- **镜头联动**：Maya 跟随 UE Level Sequence 的时间与求值相机，互斥时间控制，
  整组姿势一致显示，两端都可返回实时或结束预览
  （[ADR 0014](adr/0014-one-time-authority-and-staged-pose-groups.md)）。
- **场景参考**：UE 按显式关卡范围导出静态参考几何，Maya 校验后导入，
  与角色和相机处于同一共同空间。

宿主：Windows 64 位，Maya 2024（Maya 2022.4 支持），stock UE 5.7.4 与
Topia 5.7.4；仅编辑器内使用。线上协议见 [protocol/](../protocol/README.md)。

## 持续约束

- 最终效果以 UE 场景和镜头为准，动画在 Maya 制作；沿用既有导出、导入与
  Sequencer 组装流程，产品不创建动画或预览资产。
- 独立道具和不同骨架角色不套用 Additional Parts，也不自动重定向。
- 场景参考范围必须显式，不能悄悄改成 Actor 选择；不交付图片数据，允许材质
  分配及外部图片路径引用。
- 共同原点／摆放同时适用于相机、参考和动画对象。
- 真实输入变化结束流（[ADR 0002](adr/0002-end-streaming-when-preview-inputs-change.md)）；
  预览网格保持瞬态（[ADR 0001](adr/0001-keep-preview-meshes-transient.md)）。

## 已知限制

- Maya 2022.4 只做过安装与语法检查，未运行交互联动验收；Topia 5.7.4 的
  支持声明来自[插件构建与加载验收](history/topia-engine-acceptance.md)。
- 从含中文的路径编译 UE 插件源码会失败（MSVC 无法打开 PCH），属于工具链
  限制；已编译的插件可在此类工程中加载。
- 宿主验收由脚本驱动真实 Maya GUI 与 UnrealEditor；交互视口逐帧观测、生产
  规模的晚期刷新失败内存测量未做。详见
  [联合验收记录](history/preview-workflow-integration-acceptance.md)。

## 下一阶段候选

以下方向尚未立项，排序和取舍由下一阶段的需求讨论决定：

- 多对象缓存播放，以及由 Sequencer 时间驱动的缓存；
- 更多对象（超过一个副对象）与更广的内容类型支持；
- 预览刷新性能优化、固定检查姿势／书签、问题区域可视化；
- 面板配置记忆、自动恢复／自动重连；
- 新的动画交付流程或录制 UE 动画；
- PIE／打包运行、跨机器连接；
- 更多宿主版本认证（其他 Maya 版本、UE 版本或第三方引擎分支）。
