# Copyright (C) 2026 Alexis-Li
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under the GPL with additional terms; see NOTICE.
"""Chinese interface and diagnostic text for the Maya component."""

REALTIME_STAGE_LABELS = (
    ("inputs", "角色输入检查"),
    ("matrices", "骨骼矩阵读取"),
    ("curves", "曲线读取"),
    ("compare", "姿势变化检查"),
    ("transforms", "局部化、分解与坐标转换"),
    ("validation", "有限值检查"),
    ("encoding", "JSON 编码"),
    ("sample_total", "采样总耗时"),
    ("send", "发送耗时"),
)


DIAGNOSTICS = {
    "PROTOCOL_VERSION_MISMATCH": (
        "连接失败 · 插件协议版本不匹配",
        "请从同一发行包重新安装两端组件，重启 Maya 和 UE 后连接。"),
    "UNREAL_NOT_REACHABLE": (
        "无法连接 Unreal Editor",
        "请确认 UE 已启动、MtoU_LiveLink 已启用，并检查 54321 端口。"),
    "SECOND_CLIENT_REJECTED": (
        "Unreal 已连接另一个 Maya",
        "请先断开另一个 Maya 会话，再重新连接。"),
    "NO_BINDING_ACTOR": (
        "UE 关卡中没有 MtoU Binding Actor",
        "请在 UE 中将当前服装的 Binding 拖入关卡后重试。"),
    "MULTIPLE_BINDING_ACTORS": (
        "UE 关卡中有多个 MtoU Binding Actor",
        "请删除旧 Actor，仅保留当前服装的一个 Binding Actor。"),
    "INVALID_BINDING": (
        "UE Binding 资产无效",
        "请确认 Binding 已指定有效的 Skeletal Mesh。"),
    "SKELETON_MISMATCH": (
        "骨架与 Unreal Skeletal Mesh 不匹配",
        "请按 UE 详情检查首个未映射路径、父级与导入改名候选；"
        "确认资产确实不一致后再重新导出或导入。"),
    "BLENDSHAPE_MISMATCH": (
        "BlendShape 不完全匹配，连接可用但表情可能不完整",
        "请查看详情中 Maya 和 UE 各自缺少的名称，并确认 UE 服装是否正确。"),
    "NO_VISIBLE_SKINNED_MESH": (
        "没有找到可见的蒙皮网格",
        "请确认 Display 控制器已选择正确服装，并检查角色、头发和服装的显示状态。"),
    "INVALID_FRAME_RATE": (
        "Maya 场景帧率不受支持",
        "请将 Maya Time Unit 设置为 1–60 fps 范围内的动画帧率。"),
    "INVALID_MESSAGE": (
        "Maya 与 UE 的传输数据无效",
        "请确认两端插件版本一致；若仍失败，请复制完整诊断。"),
    "STREAM_INTERRUPTED": (
        "Live Link 连接已中断",
        "请确认 UE 仍在运行且 Binding Actor 有效，然后重新连接。"),
    "SUBJECT_CHANGED": (
        "连接对象已变更，连接已停止",
        "请确认当前 Maya 角色和 UE 目标，然后手动连接。"),
    "RESTART_TIMEOUT": (
        "自动重新连接超时，连接已停止",
        "请查看诊断中的等候阶段，然后手动连接。"),
    "RESTART_CANCELLED": (
        "已取消自动重新连接",
        "可在确认当前角色和目标后手动连接。"),
    "SAMPLING_FAILED": (
        "Maya 动画采样失败",
        "请检查骨架、BlendShape 和当前服装是否在连接期间被修改。"),
    "BIND_POSE_INVALID": (
        "无法读取可靠的 Maya 绑定姿势",
        "请检查绑定矩阵是否包含非有限值、不可逆矩阵，或存在无法可靠决胜的冲突。"),
    "INCOMPLETE_SKELETON": (
        "角色骨架存在未能发布的关节分支",
        "请检查详情中的首个缺失关节路径及原因；只有 joint 与通向关节的 transform"
        "才能发布，中间出现其他类型节点时请整理绑定后重试。"),
    "INTERNAL_ERROR": (
        "MtoU_LiveLink 发生内部错误",
        "请复制完整诊断并重新启动连接。"),
    "ROLE_SETUP_FAILED": (
        "角色设置未完成",
        "请按弹窗中的提示检查根骨骼和 Display 控制器。"),
    "DUPLICATE_BONE_NAMES": (
        "骨架中存在重名骨骼",
        "可以继续连接并由 UE 尝试映射；也可点击“选中重名骨骼”定位整理。"),
    "BONE_NAME_REMAP": (
        "UE 已自动映射重名骨骼",
        "连接可用；建议后续统一 Maya 与 UE 的骨骼名称，并保留本次映射详情。"),
    "PAIRING_REQUIRED": (
        "UE 尚未配对镜头联动目标",
        "请在 UE 详情面板中配对主角色（有副对象时也配对副对象），"
        "然后重新连接或进入镜头联动。"),
    "TARGET_UNKNOWN": (
        "UE 配对目标已不存在",
        "请确认 Binding Actor 仍在关卡中且未被删除或卸载，然后重新配对。"),
    "WRITER_NOT_ISOLATABLE": (
        "UE 目标的写入者无法安全隔离",
        "请按 UE 详情中的来源处理该轨道或动画后重新进入镜头联动；工具不会暂停整个镜头 Sequence。"),
    "SHARED_WRITER": (
        "UE 目标与其他对象共享写入者",
        "请解除共享轨道或改用独立目标，再重新进入镜头联动。"),
    "MOVING_SOCKET": (
        "UE 目标挂在会移动的 socket 上",
        "请把目标改为世界锚定的 Actor，避免父级运动被应用两次。"),
    "ANCHOR_MISMATCH": (
        "副对象与主角色在 UE 中的放置不一致",
        "请把副对象的绑定 Actor 放到与主角色相同的位置和朝向；"
        "联动期间不要移动任一目标。"),
    "OBJECT_SET_UNSUPPORTED": (
        "当前操作只支持单个角色",
        "缓存播放和模型工作流只支持主角色；请先在连接页移除副对象。"),
    "MODE_CONFLICT": (
        "镜头联动模式冲突",
        "请等待当前转换结束，或先返回实时预览后重试。"),
    "CACHE_NOT_CLEARED": (
        "缓存播放尚未清除，无法进入镜头联动",
        "请先返回实时预览并等待缓存清除完成，再进入镜头联动。"),
    "SET_REVISION_STALE": (
        "角色集合修订号不一致",
        "请重新连接以建立当前集合，再进入镜头联动。"),
    "SPACE_REVISION_STALE": (
        "UE 使用了过期的共同空间",
        "请重新建立会话；仅修改版本号不能继续联动。"),
    "EVAL_STALE": (
        "UE 已取消该求值目标",
        "无需处理；工具会丢弃该结果并使用最新目标。"),
    "RESOURCE_LIMIT": (
        "镜头联动数据超出冻结的资源上限",
        "请缩短角色或减少 BlendShape 后重试；预算在测试前固定，不会为通过而放宽。"),
    "RECOVERY_REFUSED": (
        "之前的状态无法安全恢复",
        "请按完整诊断中的残留路径处理后重试。"),
    "LINKAGE_NOT_ACTIVE": (
        "当前不在镜头联动状态",
        "请先进入镜头联动，再执行该操作。"),
    "CACHED_PLAYBACK_NOT_READY": (
        "缓存播放需要已连接的 Unreal",
        "请先完成角色设置并连接 Unreal，再进入缓存播放。"),
    "CACHED_PLAYBACK_NO_CACHE": (
        "没有可上传的缓存",
        "请先捕获当前 Maya 播放范围或自定义范围。"),
    "CACHED_PLAYBACK_SPACE": (
        "临时磁盘空间不足",
        "请缩短 Playback Range 或清理当前用户的临时磁盘空间后重试。"),
    "CACHED_PLAYBACK_CANCELLED": (
        "缓存捕获已取消",
        "已删除未完成缓存并恢复到实时预览。"),
    "CACHED_PLAYBACK_INCOMPATIBLE": (
        "缓存与当前角色快照不兼容",
        "请为当前角色和服装重新捕获缓存。"),
    "CACHED_UPLOAD_FAILED": (
        "缓存上传被 Unreal 拒绝",
        "Unreal 已丢弃未完成的缓存；请重新捕获后再试。"),
    "CACHED_UPLOAD_TIMEOUT": (
        "Unreal 未在时限内确认缓存上传",
        "已恢复实时预览，连接保持。请再次切换到“缓存播放”并点击“捕获并上传”。"),
    "CACHED_UPLOAD_INTERRUPTED": (
        "缓存上传未能完成",
        "已恢复实时预览，连接保持。请再次切换到“缓存播放”并点击“捕获并上传”。"),
    "CACHED_PLAYBACK_FRAME_LIMIT": (
        "缓存帧数超过 20,000 上限，捕获已提前停止",
        "请缩短 Playback Range 后重新捕获。"),
    "CACHED_PLAYBACK_PAYLOAD_LIMIT": (
        "缓存编码体积超过 1 GiB 上限，捕获已提前停止",
        "请缩短 Playback Range 或减少 BlendShape 数量后重新捕获。"),
    "CACHED_PLAYBACK_PERFORMANCE": (
        "缓存回放跟不上捕获帧率，已停止",
        "已完整保留缓存；请关闭占用性能的程序后在 UE 中再次播放。"),
    "CACHED_PLAYBACK_CAPTURE_RANGE": (
        "捕获范围无效",
        "请输入整数开始和结束帧，结束帧不得早于开始帧。"),
    "PREVIEW_NOT_READY": (
        "模型预览尚未生成，无法连接模型工作流",
        "请先在 UE 中对该 Binding Actor 执行 Refresh Preview 生成预览，再重新连接。"),
    "PREVIEW_BUILD_FAILED": (
        "模型预览生成失败",
        "请按完整诊断修复输入后，在 UE 中重新执行 Refresh Preview。"),
    "PREVIEW_MORPH_MISMATCH": (
        "Maya 与模型预览的 BlendShape 没有交集",
        "请确认当前服装与 UE Binding 一致并重新生成预览；也可关闭“传递 BS”进行仅骨骼对比。"),
    "PREFERENCES_READ_FAILED": (
        "无法读取场景偏好",
        "已使用默认设置。重新设置后会保存到 Maya 用户偏好。"),
    "PREFERENCES_WRITE_FAILED": (
        "无法保存场景偏好",
        "当前设置仍可使用。请检查 Maya 用户偏好目录的写入权限。"),
    "PREFERENCES_ROLE_MISSING": (
        "此场景有角色设置需要重设",
        "确认绑定引用已加载，或在连接页重新设置根骨骼或 Display 控制器。"),
}



LEGAL_TITLE = "关于 MtoU Live Link"
_LEGAL_TEXT = (
    "MtoU Live Link {0}\n"
    "版权所有 (C) 2026 Alexis-Li\n"
    "https://github.com/Alexis-Li/MtoULiveLink\n\n"
    "本程序是自由软件，你可以按 GNU 通用公共许可证第 3 版以及 NOTICE 中的附加条款"
    "再分发和修改。\n"
    "本程序不提供任何担保。\n\n"
    "查看许可证：发行包中的 LICENSE 和 NOTICE，或 "
    "https://www.gnu.org/licenses/gpl-3.0.html"
)



def legal_text(version):
    return _LEGAL_TEXT.format(version)


TEXT = {
    # Operation state and disabled actions
    "model_realtime_only": "模型工作流只提供实时预览。",
    "role_and_connection_required": "先设置角色并连接 Unreal。",
    "cache_no_secondary": "缓存播放只支持单个角色，请在连接页移除副对象。",
    "cache_leave_linkage": "请先返回实时或结束联动。",
    "cache_wait_capture": "等待捕获完成，或取消捕获。",
    "set_role_first": "请先设置角色。",
    "no_duplicate_bones": "当前角色没有重名骨骼。",
    "animation_always_passes_blendshapes": "动画工作流始终传递 BlendShape。",
    "cap_requires_realtime": "返回实时预览后可调整传输上限。",
    "realtime_unavailable": "请先结束联动或等待缓存操作完成。",
    "range_unavailable": "模型工作流不捕获缓存，捕获时无法改范围。",
    "capture_requires_cached": "先连接 Unreal 并进入缓存播放。",
    "upload_requires_retention": "仅在重连后有保留的完整本地缓存时可上传。",
    "cancel_requires_capture": "仅在捕获过程中可取消。",
    "leave_requires_linkage": "进入联动后可返回实时。",
    "end_requires_linkage": "进入联动后可结束联动。",
    "display_no_enum": "所选控制器没有包含 Clothes 选项的 enum 属性。",
    "cache_requires_role": "请先完成角色设置并连接 Unreal。",
    "cache_single_character": "缓存播放只支持单个角色。",
    "cache_remove_secondary": "在连接页移除副对象后，再进入缓存播放。",
    "cache_entered": "缓存播放：实时采样已暂停，点击“捕获并上传”",
    "mode_conflict_linkage": "镜头联动进行中，无法切换预览模式。",
    "exit_linkage_before_mode": "请先“返回实时”或“结束预览”，再切换实时预览与缓存播放。",
    "connected_realtime": "已连接：UE 显示实时姿势",
    "disconnected": "未连接",
    "cache_preparing": "准备捕获缓存…",
    "cache_capturing": "正在捕获 {0}/{1} 帧",
    "cache_uploading": "正在上传 {0}/{1} 帧",
    "cache_ready": "缓存已就绪，请在 Unreal 中开始播放",
    "cache_replaying": "缓存播放 {0}/{1} 帧",
    "cache_paused": "缓存已暂停（已应用 {0}/{1} 帧）",
    "cache_completed": "缓存播放完成，已停在最后一帧",
    "cache_stopped": "播放已停止，可再次回放",
    "cache_local_playback": "缓存回放中：Unreal 按捕获帧率本地播放 {0}/{1}",
    "cache_loop": "缓存循环播放（第 {2} 轮） {0}/{1}",
    "cache_seek": "已定位到源帧 {3}，缓存已暂停",
    "cache_paused_at": "缓存已暂停于源帧 {3}（已应用 {0}/{1} 帧）",
    "cache_segment_done": "播放段完成（源帧 {4}–{5}），已停在最后一帧",
    "cache_resumed_realtime": "{0}（已恢复实时预览）",
    "select_one_display": "请只选择一个 Display 曲线控制器。",
    "duplicates_missing": "重名骨骼已不存在，请重新设置角色",
    "duplicates_selected": "已选中 {0} 个重名骨骼",
    "primary_overlap": "主角色不能是副对象本身，也不能与副对象的骨骼层级重叠。",
    "role_captured": "角色已设置，可以连接",
    "duplicate_bones_found": "（检测到 {0} 个重名骨骼，将由 UE 尝试映射）",
    "choose_display_help": "：选中 Display 控制器后点击“选择 Display 控制器”",
    "root_required": "请先选择根骨骼并设置角色。",
    "display_source_set": "服装名称来源已设置",
    "display_changing": "正在更换 Display 控制器，请重新连接",
    "display_captured": "Display 控制器已设置，可以连接",
    "outfit_changed": "当前衣服已切换为 {0}。",
    "input_changed_connect": "连接输入已变更，请重新连接",
    "reconnecting_peer": "正在重新连接 Unreal…",
    "model_single_character": "模型工作流只支持单个角色。",
    "model_remove_secondary": "切换到动画工作流，或在连接页移除副对象后再连接。",
    "connecting_peer": "正在连接 Unreal…",
    "disconnected_status": "已断开连接",
    "linkage_details": "镜头联动：",
    "diagnostic_state": "状态：{0}",
    "unset_control": "控制方：—",
    "unset_mode": "模式：—",
    "unset_target": "目标时间：—",
    "target_eval": "目标求值：{0}",
    "no_applied": "最新完整应用：—",
    "applied_eval": "已应用求值：{0}",
    "sample_serial": "样本序号：{0}",
    "application_age": "应用年龄：{0}",
    "seconds_one": "{0:.1f} 秒",
    "unset_lag": "滞后：—",
    "applied_refused": "已应用 / 已拒绝：{0} / {1}",
    "last_application_failure": "最近应用失败：{0}",
    "none": "无",
    "peer_reason_missing": "（Unreal 未给原因）",
    "targets_in_flight": "在途目标：{0}",
    "detail_line": "详情：{0}",
    "resource_recovery": "逐资源恢复：{0}",
    "item_count": "{0} 项",
    "no_character": "未设置角色",
    "linkage_requires_connection": "镜头联动需要已连接的 Unreal 会话。",
    "linkage_setup_connection": "请先设置角色并连接 Unreal，并在 UE 详情面板中为该角色配对镜头联动目标。",
    "linkage_requires_role": "请先设置角色，再进入镜头联动。",
    "linkage_setup_root": "在 Maya 中选择变形根骨骼后点击“设置角色”。",
    "linkage_session_unknown": "Unreal 尚未通告本连接的会话身份。",
    "linkage_wait_handshake": "请重新连接 Unreal，确认握手完成后再进入镜头联动。",
    "removing_secondary": "正在移除副对象。",
    "linkage_wait_secondary": "等待 Unreal 确认移除副对象后，再进入镜头联动。",
    "linkage_following": "镜头联动中：跟随 UE 序列时间与摄影机",
    "linkage_remote_entry": "Unreal 发起了新的镜头联动",
    "linkage_remote_left": "已按 Unreal 的指令退出镜头联动：Maya 暂停在恢复帧，连接不变",
    "linkage_inactive": "当前不在镜头联动状态。",
    "linkage_first": "请先进入镜头联动，再返回实时预览或结束预览。",
    "linkage_returned": "已返回实时预览：Maya 暂停在恢复帧，连接不变",
    "linkage_ended": "已结束预览：Unreal 恢复原写入者，连接保持实时",
    "restoration_residual": "（恢复残留：{0}）",
    "linkage_not_confirmed": "Unreal 未确认镜头联动：Maya 已恢复原状态，连接不变",
    "linkage_not_started": "未进入镜头联动。",
    "secondary_overlap": "副对象不能是主角色本身，也不能在主角色的骨骼层级里。",
    "secondary_captured": "副对象已设置（{0} 根骨骼），连接后与主角色一起预览",
    "secondary_removed": "已移除副对象",
    "secondary_wait_restore": "正在移除副对象，等待 Unreal 恢复它原来的状态…",
    "secondary_restored": "已移除副对象，Unreal 已恢复它原来的状态",
    "primary_continues": "已移除副对象：主角色继续预览",
    "secondary_restore_incomplete": "移除副对象未能完整恢复：{0}",
    "secondary_unset_detail": "副对象骨骼：—",
    "primary_only": "只连接主角色",
    "no_display_source": "无 Display 控制器",
    "secondary_details_count": "副对象：{0} 根骨骼 · BlendShape {1} · 衣服 {2}",
    "secondary_details": "副对象：{0}",
    "secondary_removal_pending": "正在移除副对象，等待 Unreal 确认",
    "objects_connected": "已连接：主角色和副对象一起预览",
    "objects_remove_keep_primary": "移除副对象后主角色继续预览，不需要重新连接。",
    "objects_connecting": "正在连接主角色和副对象…",
    "objects_model_limit": "模型工作流只支持单个角色",
    "objects_switch_animation": "切换到动画工作流，或移除副对象后再连接。",
    "secondary_next_session": "副对象不在当前连接中：重新连接后生效",
    "secondary_pending_connect": "已设置副对象：连接后与主角色一起预览",
    "secondary_select_help": "先选择副对象的变形根骨骼（可同时选中它的 Display 控制器）。",
    "secondary_replace_help": "设置副对象会结束当前连接，之后需要重新连接。",
    "secondary_remove_live_help": "在当前连接内移除副对象：Unreal 恢复它原来的动画后，主角色继续预览。",
    "secondary_remove_help": "清除副对象设置。",
    "secondary_is_unset": "当前没有副对象。",
    "secondary_wait": "正在移除副对象，请稍候。",
    "set_details": "对象集合：",
    "configured": "已设置",
    "not_configured": "未设置",
    "set_revision_details": "连接中的对象：{0} · set：{1}",
    "last_secondary_removal": "上次移除：{0}",
    "realtime_preview": "实时预览",
    "cached_playback": "缓存播放",
    "linkage_label": "镜头联动",
    "secondary_select_root": "请只选择副对象的一个变形根骨骼（可同时选中它的 Display 控制器）。",
    "linkage_active_label": "镜头联动中",
    "unreal_control": "Unreal 控制时间",
    "linkage_retry_for": "应用失败，重试 {0} 秒",
    "linkage_retry": "应用失败，重试中",
    "linkage_retry_help": "Unreal 拒绝了最新一组姿势{0}；Maya 会自动重试。持续失败时打开“完整诊断”查看原因。",
    "maya_lag": "Maya 跟不上，滞后 {0} 秒",
    "maya_lag_help": "Maya 正在追赶 Unreal 的最新时间；可暂停 Sequencer 等待，或关闭占用性能的程序。",
    "linkage_synchronizing": "正在同步",
    "linkage_in_sync": "同步正常",
    "linkage_not_entered": "未进入联动",
    "cache_in_progress": "缓存播放中",
    "unreal_takeover": "Unreal 仍接管目标",
    "return_unreal_writers": "点击“结束预览”让 Unreal 交还原来的动画。",
    "recovery_failures": "恢复失败 {0} 项",
    "recovery_help": "打开“完整诊断”查看未恢复的项目，按记录在 Maya 中手动恢复（例如解锁相机或重设时间范围）。",
    "seconds_two": "{0:.2f} 秒",
    "not_converged": "（未收敛）",
    "control_label": "控制方：{0}",
    "mode_label": "模式：{0}",
    "target_time": "目标时间：{0}",
    "latest_applied": "最新完整应用：{0}",
    "lag_label": "滞后：{0}",
    "recovery_label": "恢复结果：{0}",
    "linkage_already_active": "镜头联动已在进行中。",
    "linkage_cache_busy": "缓存播放正在使用该连接：请先返回实时预览并等待缓存清除。",
    "linkage_wait_cache_clear": "正在等待 Unreal 确认清除缓存，确认后即可进入镜头联动。",
    "linkage_setup_target": "请先设置角色并连接 Unreal，并在 UE 中配对镜头联动目标。",

    # Panel labels and prompts
    "connect": "连接",
    "preview": "预览",
    "camera": "相机",
    "diagnostics": "诊断",
    "animation": "动画",
    "model": "模型",
    "workflow": "工作流",
    "character": "角色",
    "set_role": "设置角色",
    "bones": "骨骼",
    "outfit_meshes": "服装网格",
    "choose_display": "选择 Display 控制器",
    "select_duplicates": "选中重名骨骼",
    "pass_bs": "传递 BS",
    "secondary": "副对象",
    "secondary_empty": "未设置 · 选中道具或第二个角色的根骨骼",
    "set_secondary": "设为副对象",
    "remove_secondary": "移除副对象",
    "realtime": "实时",
    "mode": "模式",
    "playback_cap": "传输上限",
    "cache": "缓存",
    "playback_range": "播放范围",
    "custom_range": "自定义",
    "capture_range": "捕获范围",
    "start_frame": "开始帧",
    "end_frame": "结束帧",
    "frame": "帧",
    "until": "至",
    "capture_upload": "捕获并上传",
    "upload_retained": "上传保留缓存",
    "cancel_capture": "取消捕获",
    "cache_in_unreal": "缓存的播放、暂停、定位和循环在 UE 的 MtoU 面板中控制。切回“实时”会清除 UE 中的缓存。",
    "ue_camera": "UE 相机",
    "camera_updated": "UE 相机有更新",
    "update_camera": "更新相机",
    "import_camera": "从文件导入相机…",
    "camera_import_unavailable": "相机导入功能尚未开放。",
    "sequence_linkage": "Sequence 联动",
    "level": "关卡",
    "sublevels": "子关卡",
    "time": "时间",
    "stand_in": "替身",
    "start_linkage": "开始联动",
    "return_realtime": "返回实时",
    "end_linkage": "结束联动",
    "latest_operation": "最近一次操作",
    "performance": "性能",
    "maya_sample": "Maya 采样",
    "ue_apply": "UE 应用",
    "metric_help": "发送是最近一秒成功发出的帧数。跳过是被更新的待发送帧替换的累计帧数。",
    "previous_difference": "此前的连接差异",
    "full_diagnostics": "完整诊断",
    "popup_on_error": "出错时弹窗提示",
    "popup_help": "控制连接警告、连接失败及意外断开的提示；结果始终记入诊断。",
    "about": "关于",
    "connecting": "正在连接",
    "connected": "已连接",
    "reconnecting": "正在重新连接",
    "interrupted": "连接已中断",
    "ue_controls_time": "UE 控制时间",
    "maya_controls_time": "Maya 控制时间",
    "ue_playback": "UE 播放",
    "current_frame": "帧 {0:g}",
    "disconnect": "断开",
    "reset_role": "重设",
    "root_unselected": "尚未选择根骨骼",
    "select_duplicate_count": "选中重名骨骼（{0}）",
    "bs_explanation": "把当前服装的 BlendShape 值一起传给 UE",
    "bs_always": "动画工作流始终传递 BlendShape",
    "custom_range_help": "选择“自定义”以编辑帧范围。",
    "frames_count": "共 {0:,} 帧",
    "cache_empty": "尚未捕获",
    "camera_list_empty": "暂无 UE 相机清单",
    "last_camera_import": "上次导入 {0}",
    "camera_range": "{0} 台相机 · {1}–{2} · {3} fps",
    "level_unavailable": "UE 尚未提供关卡信息",
    "stand_in_unavailable": "UE 尚未提供替身信息",
    "camera_maya_follows": "UE 控制时间 · Maya 跟随",
    "camera_ue_follows": "Maya 控制时间 · UE 跟随",
    "camera_linkage_idle": "当前未联动",
    "no_connection_difference": "暂无连接差异",
    "quick_performance": "发送 {0} · 跳过 {1} · UE 编辑器 {2}\n每帧预算 {3}",
    "frame_unit": " 帧",
    "choose_outfit_attribute": "选择服装属性",
    "multiple_outfit_attributes": "找到多个候选属性，请选择：",
    "confirm_large_cache": "确认大型缓存",
    "large_cache_size": "本次捕获约 {0:.2f} GiB（{1} 帧），是否继续？",
    "connection_warning": "MtoU 连接警告",
    "connection_problem": "MtoU 连接问题",
    "view_full_diagnostics": "查看完整诊断",
    "ok": "确定",
    "continue": "继续",
    "cancel": "取消",
    "close": "关闭",

    # Diagnostic window and operation titles
    "set_display": "设置 Display 控制器",
    "restore_preferences": "恢复场景偏好",
    "save_preferences": "保存场景偏好",
    "reconnect": "重新连接",
    "enter_cached": "切换到缓存播放",
    "cache_operation": "缓存操作",
    "exit_linkage": "退出联动",
    "set_secondary_operation": "设置副对象",
    "result_failed": "失败",
    "result_warning": "有提示",
    "result_success": "成功",
    "no_operation_result": "暂无操作结果",
    "result_solution": "处理：{0}",
    "connection_versions": "Maya {0} · 插件 {1} · 协议 {2}\nUE {3} · 插件 {4} · 协议 {5}",
    "detailed_performance": "Maya 采样 {0} · 发送 {1} · 跳过 {2}\nUE 应用 {3} · 编辑器 {4}",
    "full_diagnostics_title": "MtoU 完整诊断",
    "details_update_time": "随最新结果刷新 · {0}",
    "recent_operation_card": "<table width=\"100%\" cellspacing=\"0\" cellpadding=\"10\" bgcolor=\"{0}\"><tr><td><span style=\"color:{1}\"><b>[最近一次操作] {2}</b></span>{3}</td></tr></table>",
    "diagnostic_character": "{0:,} 骨骼 · {1:,} BlendShape · {2} · {3}",
    "diagnostic_camera_count": "{0} 台",
    "sampling_details": "采样明细",

    # Diagnostic summaries and read-back
    "realtime_performance_title": "实时预览性能：",
    "realtime_sent_frequency": "实际发送频率：{0} 帧/秒（最近 1 秒）",
    "realtime_delivery": "成功发送：{0} 帧 · 被新帧替换：{1} 帧",
    "realtime_stage": "{0}：{1:.2f} ms（最近一次执行） / {2:.2f} ms（平均）",
    "cache_summary": "{0}–{1} · {2:,} 帧 · {3:g} fps\n捕获于 {4}",
    "peer_value_unknown": "未知（对端未提供）",
    "version_line": "{0} · 插件 {1}，协议 {2}",
    "maya_side": "Maya 端",
    "ue_side": "UE 端",
    "peer_error": "Unreal 报告了一个错误。",

    "connection_difference_details": "仅 Maya 有：{0}\n仅 Unreal 有：{1}\n骨骼名称映射：{2}",
    "blendshape_difference_count": "BlendShape 差异 · 仅 Maya {0} 个 · 仅 UE {1} 个",
    "bone_remap_count": "骨骼名称映射 {0} 处",

    # Connection reasons
    "secondary_warning_prefix": "副对象 · ",
    "secondary_reason_prefix": "副对象：",
    "secondary_remove_requires_connection": "移除副对象需要已连接的 Unreal 会话。",
    "secondary_not_in_session": "当前对象集合中没有副对象。",
    "secondary_description_changed": "副对象的描述在本次连接中发生了变化。",
    "resolved_bind_conflicts": "（已按绑定姿势解析 {0} 处蒙皮绑定矩阵冲突）",

    # Linkage results
    "object_set_updated": "对象集合已更新（{0} 个对象）",
    "linkage_started": "镜头联动已开始",
    "preview_ended": "已结束预览并恢复原状态",
    "realtime_restored": "已返回实时预览（Maya 保持暂停）",
    "linkage_applied": "已应用 {0}",
    "linkage_apply_failed": "应用失败：{0}",
    "maya_recovery_failed": "Maya 无法完整恢复联动前的状态",
    "unreal_ended_linkage": "Unreal 已结束镜头联动，Maya 已恢复并保持暂停",
    "unreal_refused_linkage": "Unreal 未接管镜头联动，Maya 已恢复原状态",
    "linkage_ack_timeout": "Unreal 未在 {0:g} 秒内确认镜头联动",
    "maya_recovered": "，Maya 已恢复原状态",
    "latest_unreal_detail": "（Unreal 最近回复：{0}）",
    "stale_result_discarded": "已丢弃过期结果：{0}",
    "sampled_pose": "已采样 {0}",
    "group_size_limit": "整组姿势超过冻结的消息上限",
    "disconnected_takeover_restored": "已断开连接并恢复预览接管",

    # Time-control refusals
    "mode_connection_closed": "连接已结束，无法切换预览模式。",
    "cache_connection_held": "缓存播放仍在使用该连接。",
    "cache_clear_unconfirmed": "Unreal 尚未确认清除缓存。",
    "cache_linkage_conflict": "镜头联动进行中，无法进入缓存播放。",
    "cache_already_entered": "缓存播放进行中，无法再次进入。",

}


def text(key):
    return TEXT[key]
