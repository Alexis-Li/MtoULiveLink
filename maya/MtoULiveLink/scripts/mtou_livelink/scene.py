# Copyright (C) 2026 Alexis-Li
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under the GPL with additional terms; see NOTICE.
"""Maya character scene capture, bind pose and evaluated sampling."""

import math
import re
from time import perf_counter

from . import mayaapi
from .config import BIND_MATRIX_TOLERANCE, CURVE_VALUE_TOLERANCE
from .diagnostics import DIAGNOSTICS, make_diagnostic
from .mayaapi import cmds, om, oma
from .protocol import validate_pose_values
from .space import centimeters_per_unit, convert_transform


def normalize_name(path):
    return path.rsplit("|", 1)[-1].rsplit(":", 1)[-1]


class DuplicateBoneNamesError(ValueError):
    def __init__(self, duplicate_paths):
        self.duplicate_paths = duplicate_paths
        self.paths = [path for name in sorted(duplicate_paths)
                      for path in duplicate_paths[name]]
        names = ", ".join(sorted(duplicate_paths))
        super(DuplicateBoneNamesError, self).__init__(
            "duplicate normalized bone names: {0}".format(names))


def duplicate_bone_paths(records):
    paths_by_name = {}
    for record in records:
        paths_by_name.setdefault(record["name"], []).append(record["path"])
    return {
        name: paths for name, paths in paths_by_name.items() if len(paths) > 1
    }


def build_hierarchy(root, children, allow_duplicates=False):
    records = []
    stack = [(root, -1)]
    while stack:
        path, parent = stack.pop()
        index = len(records)
        records.append({"path": path, "name": normalize_name(path), "parent": parent})
        child_paths = list(children(path) or [])
        stack.extend((child, index) for child in reversed(child_paths))
    duplicates = duplicate_bone_paths(records)
    if duplicates and not allow_duplicates:
        raise DuplicateBoneNamesError(duplicates)
    return records


def duplicate_bone_diagnostic(error):
    lines = ["Duplicate normalized bone names:"]
    for name in sorted(error.duplicate_paths):
        lines.append("{0}:".format(name))
        lines.extend("  " + path for path in error.duplicate_paths[name])
    diagnostic = make_diagnostic(
        "DUPLICATE_BONE_NAMES", str(error), details="\n".join(lines))
    diagnostic["duplicate_paths"] = list(error.paths)
    return diagnostic


def require_maya():
    if cmds is None or om is None:
        raise RuntimeError("MtoU_LiveLink must be run inside Maya 2022.4")


def dag_path(path):
    selection = om.MSelectionList()
    selection.add(path)
    return selection.getDagPath(0)


def transform_leads_to_joint(path):
    return bool(cmds.listRelatives(
        path, allDescendents=True, fullPath=True, type="joint"))


def maya_children(path):
    children = cmds.listRelatives(path, children=True, fullPath=True) or []
    result = []
    for child in children:
        try:
            node_type = cmds.nodeType(child)
        except (RuntimeError, ValueError):
            continue
        if node_type == "joint":
            result.append(child)
        elif node_type == "transform":
            if transform_leads_to_joint(child):
                result.append(child)
        # Other DAG types (mesh shapes, constraints, controllers) never
        # publish; joints beneath them are reported by
        # ensure_complete_capture instead of being silently pruned.
    return result


def describe_incomplete_joint(root, joint_path, published):
    current = joint_path
    while current != root:
        parents = cmds.listRelatives(current, parent=True, fullPath=True) or []
        if not parents:
            return ("joint {0} has no parent chain reaching selected root {1}".format(
                joint_path, root))
        parent = parents[0]
        if parent not in published:
            try:
                parent_type = cmds.nodeType(parent)
            except (RuntimeError, ValueError):
                parent_type = "unknown"
            if parent_type not in ("joint", "transform"):
                return ("joint {0} passes through {1} ({2}); "
                        "only joint/transform intermediates can be published".format(
                    joint_path, parent, parent_type))
            return ("joint {0} via unpublished {1} ({2}) was not captured".format(
                joint_path, parent, parent_type))
        current = parent
    return "joint {0} was not captured".format(joint_path)


def ensure_complete_capture(root, bones):
    published = set(bone["path"] for bone in bones)
    joints = cmds.listRelatives(
        root, allDescendents=True, fullPath=True, type="joint") or []
    for joint_path in sorted([root] + list(joints)):
        if joint_path in published:
            continue
        reason = describe_incomplete_joint(root, joint_path, published)
        message = "skeleton under {0} is missing joint {1}".format(root, joint_path)
        details = "{0}; {1}".format(message, reason)
        raise CharacterSceneError(
            "INCOMPLETE_SKELETON", message, details=details,
            context={"root": root, "missing_joint": joint_path})


def selected_root():
    selected = cmds.ls(selection=True, long=True) or []
    if len(selected) != 1 or cmds.nodeType(selected[0]) != "joint":
        raise ValueError("Select exactly one deformation root joint before connecting.")
    return selected[0]


def long_transform(node):
    matches = cmds.ls(node, long=True) or []
    if len(matches) != 1:
        raise ValueError("Expected exactly one Maya node: {0}".format(node))
    path = matches[0]
    if cmds.nodeType(path) in ("mesh", "nurbsCurve"):
        parents = cmds.listRelatives(path, parent=True, fullPath=True) or []
        if not parents:
            raise ValueError("The selected shape has no transform parent: {0}".format(path))
        path = parents[0]
    if cmds.nodeType(path) != "transform":
        raise ValueError("The Display controller must be a transform or curve shape.")
    return path


def namespace(path):
    leaf = path.rsplit("|", 1)[-1]
    return leaf.rsplit(":", 1)[0] if ":" in leaf else ""


def top_level(path):
    parts = [part for part in path.split("|") if part]
    return "|" + parts[0] if parts else path


def enum_attributes(node):
    candidates = []
    for attribute in cmds.listAttr(node, userDefined=True) or []:
        plug = node + "." + attribute
        if cmds.getAttr(plug, type=True) != "enum":
            continue
        definitions = cmds.attributeQuery(attribute, node=node, listEnum=True) or []
        entries = definitions[0].split(":") if definitions else []
        if any(re.match(r"(?i)^clothes", entry or "") for entry in entries):
            candidates.append({"attribute": attribute, "entries": entries, "plug": plug})
    return candidates


def display_candidates(root):
    top = top_level(root)
    descendants = cmds.listRelatives(top, allDescendents=True, fullPath=True,
                                     type="transform") or []
    expected_namespace = namespace(root)
    candidates = []
    for node in descendants:
        leaf = node.rsplit("|", 1)[-1]
        if normalize_name(leaf).lower() != "display_ctrl":
            continue
        if namespace(node) == expected_namespace:
            candidates.append(node)
    return sorted(candidates)


def current_enum_label(display):
    value = int(cmds.getAttr(display["plug"]))
    entries = display["entries"]
    return entries[value] if 0 <= value < len(entries) else str(value)


def effective_mesh_transform(geometry):
    paths = cmds.ls(geometry, long=True) or []
    if not paths:
        return None
    path = paths[0]
    if cmds.nodeType(path) == "mesh":
        shape = path
        parents = cmds.listRelatives(shape, parent=True, fullPath=True) or []
        if not parents:
            return None
        transform = parents[0]
    else:
        transform = path
        shapes = cmds.listRelatives(transform, shapes=True, noIntermediate=True,
                                    fullPath=True, type="mesh") or []
        if not shapes:
            return None
        return transform if any(effective_mesh_transform(shape) for shape in shapes) else None
    if cmds.getAttr(shape + ".intermediateObject"):
        return None
    current = shape
    while current:
        if cmds.attributeQuery("visibility", node=current, exists=True) \
                and not cmds.getAttr(current + ".visibility"):
            return None
        if cmds.attributeQuery("lodVisibility", node=current, exists=True) \
                and not cmds.getAttr(current + ".lodVisibility"):
            return None
        if cmds.attributeQuery("overrideEnabled", node=current, exists=True) \
                and cmds.getAttr(current + ".overrideEnabled"):
            if cmds.attributeQuery("overrideVisibility", node=current, exists=True) \
                    and not cmds.getAttr(current + ".overrideVisibility"):
                return None
        parents = cmds.listRelatives(current, parent=True, fullPath=True) or []
        current = parents[0] if parents else None
    return transform


def skinned_mesh_candidates(bone_paths):
    """All skin outputs, including hidden outfits, for evaluated visibility checks."""
    skin_clusters = set()
    for path in bone_paths:
        skin_clusters.update(cmds.listConnections(path, type="skinCluster") or [])
    geometries = set()
    for skin_cluster in sorted(skin_clusters):
        for geometry in cmds.skinCluster(skin_cluster, query=True, geometry=True) or []:
            geometries.update(cmds.ls(geometry, long=True) or [])
    return sorted(geometries)


def visible_skinned_meshes(bone_paths):
    return mesh_transforms(visible_mesh_shapes(skinned_mesh_candidates(bone_paths)))


def mesh_transforms(shapes):
    return sorted(set(parent for shape in shapes for parent in
                      (cmds.listRelatives(shape, parent=True, fullPath=True) or [])))


def visible_mesh_shapes(candidates):
    """Keep skin output shape identity, including multi-shape transforms."""
    visible = set()
    for path in candidates:
        shapes = ([path] if cmds.nodeType(path) == "mesh" else cmds.listRelatives(
            path, shapes=True, noIntermediate=True, fullPath=True, type="mesh") or [])
        visible.update(shape for shape in shapes if effective_mesh_transform(shape))
    return sorted(visible)


def mesh_identities(mesh_paths):
    return tuple(sorted(set(cmds.ls(path, uuid=True)[0] for path in mesh_paths)))


def mesh_label(mesh_paths):
    """Use the shortest unambiguous DAG suffix, in deterministic path order."""
    paths = sorted(set(mesh_paths))
    labels = []
    for path in paths:
        parts = path.strip("|").split("|")
        for length in range(1, len(parts) + 1):
            suffix = "|".join(parts[-length:])
            if sum(other == "|" + suffix or other.endswith("|" + suffix)
                   for other in paths) == 1:
                break
        labels.append(suffix)
    return ", ".join(labels)


def skin_clusters_for_meshes(mesh_paths):
    skin_clusters = set()
    for mesh_path in mesh_paths:
        skin_clusters.update(
            node for node in cmds.listHistory(mesh_path, pruneDagObjects=True) or []
            if cmds.nodeType(node) == "skinCluster")
    return sorted(skin_clusters)


def matrix_attr(plug):
    value = cmds.getAttr(plug)
    if isinstance(value, (list, tuple)) and len(value) == 1:
        value = value[0]
    return om.MMatrix(value)


def matrix_is_finite(matrix):
    return all(math.isfinite(float(value)) for value in matrix)


def matrix_is_equivalent(left, right, tolerance=BIND_MATRIX_TOLERANCE):
    return all(abs(float(a) - float(b)) <= tolerance for a, b in zip(left, right))


class BindPoseError(ValueError):
    pass


class BindMatrixCandidate(object):
    SKIN_CLUSTER = "skinCluster"
    DAG_POSE = "dagPose"

    def __init__(self, kind, source, matrix, influence_count=0):
        self.kind = kind
        self.source = source
        self.matrix = matrix
        self.influence_count = int(influence_count)


def matrix_inverse(matrix, bone_path, source):
    if not matrix_is_finite(matrix):
        raise BindPoseError(
            "bone {0} has non-finite bind matrix from {1}".format(
                bone_path, source))
    determinant = float(matrix.det4x4())
    if not math.isfinite(determinant) or determinant == 0.0:
        raise BindPoseError(
            "bone {0} has non-invertible bind matrix from {1}".format(
                bone_path, source))
    inverse = matrix.inverse()
    if not matrix_is_finite(inverse):
        raise BindPoseError(
            "bone {0} has non-finite inverse bind matrix from {1}".format(
                bone_path, source))
    return inverse


def highest_priority_bind_candidates(records):
    dag_pose_records = [
        record for record in records
        if record.kind == BindMatrixCandidate.DAG_POSE]
    if dag_pose_records:
        return dag_pose_records
    largest_influence_count = max(record.influence_count for record in records)
    return [record for record in records
            if record.influence_count == largest_influence_count]


def select_bind_world_candidate(bone_path, records):
    preferred = highest_priority_bind_candidates(records)
    selected = preferred[0]
    if any(not matrix_is_equivalent(selected.matrix, record.matrix)
           for record in preferred[1:]):
        sources = ", ".join(sorted(record.source for record in preferred))
        raise BindPoseError(
            "bone {0} has conflicting equally ranked bind matrices: {1}".format(
                bone_path, sources))
    conflict = any(not matrix_is_equivalent(selected.matrix, record.matrix)
                   for record in records)
    return selected.matrix, conflict


def bind_world_candidates(subject):
    candidates = dict((bone["path"], []) for bone in subject["bones"])
    bone_paths = set(candidates)
    for skin_cluster in skin_clusters_for_meshes(subject.get("mesh_shapes", subject["meshes"])):
        selection = om.MSelectionList()
        selection.add(skin_cluster)
        function = oma.MFnSkinCluster(selection.getDependNode(0))
        influences = function.influenceObjects()
        for influence in influences:
            path = influence.fullPathName()
            if path not in bone_paths:
                continue
            logical_index = function.indexForInfluenceObject(influence)
            plug = "{0}.bindPreMatrix[{1}]".format(skin_cluster, logical_index)
            candidates[path].append(BindMatrixCandidate(
                BindMatrixCandidate.SKIN_CLUSTER,
                plug,
                matrix_inverse(matrix_attr(plug), path, plug),
                influence_count=len(influences)))

    for bone in subject["bones"]:
        if cmds.nodeType(bone["path"]) != "joint":
            # Structural transforms have no joint bindPose/dagPose; when they
            # are not skin influences they fall back to the capture-time
            # local re-anchored to the parent bind frame in
            # _capture_bind_local_transforms.
            continue
        plug = bone["path"] + ".bindPose"
        for pose_plug in cmds.listConnections(
                plug, source=False, destination=True, plugs=True) or []:
            pose = pose_plug.split(".", 1)[0]
            if cmds.nodeType(pose) == "dagPose" and cmds.getAttr(pose + ".bindPose"):
                # The joint-connected dagPose is the pose Go to Bind Pose
                # restores and the most likely Skeletal Mesh export pose.
                matrix = matrix_attr(pose_plug)
                matrix_inverse(matrix, bone["path"], pose_plug)
                candidates[bone["path"]].append(BindMatrixCandidate(
                    BindMatrixCandidate.DAG_POSE, pose_plug, matrix))
    return candidates


def _capture_bind_local_transforms(subject, previous_fallbacks):
    candidates = bind_world_candidates(subject)
    bones = subject["bones"]
    bind_world = []
    bind_world_inverse = []
    bind_local = []
    fallbacks = {}
    conflicts = 0
    for bone in bones:
        parent = bone["parent"]
        records = candidates[bone["path"]]
        if records:
            # Skin clusters can legitimately disagree when outfits were bound
            # at different poses; the joint-connected dagPose wins, otherwise
            # the skin cluster with the most influences. Equally ranked
            # candidates must agree so node naming cannot choose the pose.
            matrix, conflict = select_bind_world_candidate(bone["path"], records)
            conflicts += int(conflict)
            local = matrix * bind_world_inverse[parent] if parent >= 0 else matrix
        else:
            # With no stored bind data, the capture-time local is the bind
            # input. Keep it for the same Maya node and parent across refreshes.
            node = om.MFnDependencyNode(bone["dag_path"].node()).uuid().asString()
            parent_node = None
            if parent >= 0:
                parent_node = om.MFnDependencyNode(
                    bones[parent]["dag_path"].node()).uuid().asString()
            key = (node, parent_node)
            local = previous_fallbacks.get(key)
            if local is None:
                local = bone["dag_path"].inclusiveMatrix()
                if parent >= 0:
                    parent_inverse = matrix_inverse(
                        bones[parent]["dag_path"].inclusiveMatrix(), bone["path"],
                        "capture-time parent matrix")
                    local = local * parent_inverse
            fallbacks[key] = local
            matrix = local * bind_world[parent] if parent >= 0 else local
        bind_world.append(matrix)
        bind_world_inverse.append(
            matrix_inverse(matrix, bone["path"], "resolved bind world matrix"))
        bind_local.append(sample_matrix(local, subject["unit_scale"]))
    return bind_local, conflicts, fallbacks


def discover_curve_plugs(bone_paths, visible_meshes=None, previous_curves=()):
    if visible_meshes is None:
        visible_meshes = visible_mesh_shapes(skinned_mesh_candidates(bone_paths))
    deformers = set()
    for geometry in visible_meshes:
        deformers.update(node for node in cmds.listHistory(geometry, pruneDagObjects=True) or []
                         if cmds.nodeType(node) == "blendShape")
    curves = []
    for deformer in sorted(deformers):
        aliases = cmds.aliasAttr(deformer, query=True) or []
        if not aliases:
            continue
        deformer_uuid = cmds.ls(deformer, uuid=True)[0]
        for alias, attribute in zip(aliases[0::2], aliases[1::2]):
            match = re.match(r"(?:weight|w)\[(\d+)\]$", attribute)
            if match:
                index = int(match.group(1))
                curves.append({"name": alias, "plug": deformer + "." + attribute,
                               "sort": (deformer, index), "id": (deformer_uuid, index)})
    curves.sort(key=lambda curve: curve["sort"])
    previous_references = {curve["name"]: curve["reference_id"] for curve in previous_curves}
    by_name = {}
    for curve in curves:
        name, plug, plug_id = curve["name"], curve["plug"], curve["id"]
        group = by_name.get(name)
        if group is None:
            by_name[name] = {"name": name, "plugs": [plug], "reference_id": plug_id}
        elif plug_id == previous_references.get(name):
            # Sampling publishes the first plug and checks the others against it.
            group["plugs"].insert(0, plug)
            group["reference_id"] = plug_id
        else:
            group["plugs"].append(plug)
    if by_name.keys() == previous_references.keys():
        return [by_name[name] for name in previous_references]
    return list(by_name.values())


def cache_curve_handles(curves):
    """Resolve each blendShape attribute once for this captured subject."""
    for curve in curves:
        handles = []
        for plug in curve["plugs"]:
            selection = om.MSelectionList()
            selection.add(plug)
            handles.append(selection.getPlug(0))
        curve["handles"] = tuple(handles)


def capture_subject(root=None, bind_fallbacks=None, previous_curves=()):
    require_maya()
    root = root or selected_root()
    bones = build_hierarchy(root, maya_children, allow_duplicates=True)
    ensure_complete_capture(root, bones)
    for bone in bones:
        bone["dag_path"] = dag_path(bone["path"])
    shapes = visible_mesh_shapes(skinned_mesh_candidates([bone["path"] for bone in bones]))
    mesh_paths = mesh_transforms(shapes)
    if not mesh_paths:
        raise ValueError("NO_VISIBLE_SKINNED_MESH")
    subject = {"root": root, "bones": bones,
               "meshes": mesh_paths,
               "mesh_shapes": shapes,
               "curves": discover_curve_plugs(
                   [bone["path"] for bone in bones], shapes, previous_curves),
               "unit_scale": centimeters_per_unit(
                   cmds.currentUnit(query=True, linear=True))}
    cache_curve_handles(subject["curves"])
    bind_local, conflicts, fallbacks = _capture_bind_local_transforms(
        subject, bind_fallbacks or {})
    subject["bind_local_transforms"] = bind_local
    subject["bind_conflict_count"] = conflicts
    subject["bind_fallbacks"] = fallbacks
    return subject


def sample_matrix(matrix, unit_scale, *, validate=True):
    transform = om.MTransformationMatrix(matrix)
    translation = transform.translation(om.MSpace.kTransform)
    rotation = transform.rotation(asQuaternion=True)
    scale = transform.scale(om.MSpace.kTransform)
    return convert_transform(translation, rotation, scale, unit_scale, validate=validate)


def read_world_matrices(subject):
    return tuple(bone["dag_path"].inclusiveMatrix() for bone in subject["bones"])


def convert_pose_matrices(subject, world_matrices):
    transforms = []
    parent_inverses = {}
    unit_scale = subject["unit_scale"]
    for index, bone in enumerate(subject["bones"]):
        matrix = world_matrices[index]
        parent = bone["parent"]
        if parent >= 0:
            inverse = parent_inverses.get(parent)
            if inverse is None:
                inverse = parent_inverses[parent] = world_matrices[parent].inverse()
            matrix = matrix * inverse
        transforms.append(sample_matrix(matrix, unit_scale, validate=False))
    return transforms


def read_curve_values(subject):
    curves = []
    for curve in subject["curves"]:
        handles = curve["handles"]
        reference = handles[0].asDouble()
        if len(handles) > 1:
            aliases = [plug.asDouble() for plug in handles[1:]]
            if any(not math.isfinite(value) or abs(value - reference) > CURVE_VALUE_TOLERANCE
                   for value in aliases):
                details = ", ".join("{0}={1}".format(plug, value)
                                    for plug, value in zip(curve["plugs"], [reference] + aliases))
                raise ValueError("conflicting values for BlendShape alias {0}: {1}".format(
                    curve["name"], details))
        curves.append(reference)
    return tuple(curves)


def sample_pose(subject):
    return convert_pose_matrices(subject, read_world_matrices(subject)), read_curve_values(subject)


class CharacterSceneError(RuntimeError):
    def __init__(self, code, message="", details="", context=None,
                 duplicate_paths=None):
        self._code = code
        self._message = message or code
        self._details = details or self._message
        self._context = dict(context or {})
        self._duplicate_paths = tuple(duplicate_paths or ())
        super(CharacterSceneError, self).__init__(self._message)

    @property
    def code(self):
        return self._code

    @property
    def message(self):
        return self._message

    @property
    def details(self):
        return self._details

    @property
    def context(self):
        return dict(self._context)

    @property
    def duplicate_paths(self):
        return self._duplicate_paths


class CharacterSnapshot(object):
    def __init__(self, revision, root, outfit, bones, curve_names,
                 duplicate_paths=(), mesh_paths=(), bind_local_transforms=(),
                 bind_conflict_count=0, mesh_identities=()):
        self._revision = int(revision)
        self._root = root
        self._outfit = outfit
        self._bones = tuple((name, int(parent)) for name, parent in bones)
        self._curve_names = tuple(curve_names)
        self._duplicate_paths = tuple(duplicate_paths)
        self._mesh_paths = tuple(mesh_paths)
        self._mesh_identities = tuple(mesh_identities)
        self._bind_conflict_count = int(bind_conflict_count)
        self._bind_local_transforms = tuple(
            tuple(value for value in transform) for transform in bind_local_transforms)
        if len(self._bind_local_transforms) != len(self._bones):
            raise ValueError("bind pose count must match skeleton bone count")

    @property
    def revision(self):
        return self._revision

    @property
    def root(self):
        return self._root

    @property
    def outfit(self):
        return self._outfit

    @property
    def bones(self):
        return self._bones

    @property
    def curve_names(self):
        return self._curve_names

    @property
    def duplicate_paths(self):
        return self._duplicate_paths

    @property
    def mesh_paths(self):
        return self._mesh_paths

    @property
    def mesh_identities(self):
        return self._mesh_identities

    @property
    def bind_local_transforms(self):
        return self._bind_local_transforms

    @property
    def bind_conflict_count(self):
        return self._bind_conflict_count


class CharacterFrame(object):
    def __init__(self, revision, transforms, curves, evaluated=None):
        self._revision = int(revision)
        self._transforms = tuple(map(tuple, transforms))
        self._curves = tuple(curves)
        validate_pose_values(self._transforms, self._curves)
        self._evaluated = evaluated

    @property
    def revision(self):
        return self._revision

    @property
    def transforms(self):
        return self._transforms

    @property
    def curves(self):
        return self._curves


class CharacterSceneEvent(object):
    def __init__(self, kind, snapshot=None, error=None):
        self._kind = kind
        self._snapshot = snapshot
        self._error = error

    @property
    def kind(self):
        return self._kind

    @property
    def snapshot(self):
        return self._snapshot

    @property
    def error(self):
        return self._error


class CharacterScene(object):
    """Owns one configured, sampleable Maya character scene."""

    def __init__(self, root, display, display_attribute, on_event):
        self._root = root
        self._display_path = display
        self._display_attribute = display_attribute
        self._on_event = on_event
        self._subject = None
        self._snapshot = None
        self._callback_ids = []
        self._input_callback_ids = []
        self._candidate_paths = []
        self._graph_dirty = False
        self._paths_dirty = False
        self._refresh_token = None
        self._root_dag = None
        self._display_dag = None
        self._refreshing = False
        self._closed = False
        self._terminal_error = None
        self._cleanup_error = None

    @classmethod
    def capture(cls, root, display=None, display_attribute=None, on_event=None,
                display_optional=True, saved_display_uuid=None, discover_display=True):
        """Capture one object scene.

        Display is an optional label source for both primary and secondary
        objects. ``display_optional`` remains accepted for existing callers.
        """
        require_maya()
        scene = None
        try:
            root = cls._resolve_root(root)
            if saved_display_uuid is not None:
                display, display_attribute = cls._resolve_saved_display(
                    root, saved_display_uuid, display_attribute)
            elif display is not None or discover_display:
                display, display_attribute = cls._resolve_display(
                    root, display, display_attribute, display_optional)
            scene = cls(root, display, display_attribute, on_event)
            scene._commit_capture(revision=1)
            scene._register_callbacks()
            return scene
        except CharacterSceneError:
            if scene is not None:
                scene.close()
            raise
        except (RuntimeError, ValueError) as exc:
            if scene is not None:
                scene.close()
            raise cls._wrap_error("ROLE_SETUP_FAILED", exc)

    @staticmethod
    def _resolve_root(root):
        matches = cmds.ls(root, long=True) or []
        if len(matches) != 1 or cmds.nodeType(matches[0]) != "joint":
            raise CharacterSceneError(
                "INVALID_CHARACTER_ROOT",
                "Select exactly one deformation root joint before connecting.")
        return matches[0]

    @staticmethod
    def _resolve_display(root, display, display_attribute, display_optional=True):
        del display_optional
        if display is None:
            candidates = [(node, attributes[0]["attribute"])
                          for node in display_candidates(root)
                          for attributes in [enum_attributes(node)]
                          if len(attributes) == 1]
            if len(candidates) != 1:
                return None, None
            display, inferred_attribute = candidates[0]
            if display_attribute is None:
                display_attribute = inferred_attribute
        else:
            try:
                display = long_transform(display)
            except (RuntimeError, ValueError) as exc:
                raise CharacterScene._wrap_error("INVALID_DISPLAY", exc)
        role_top = top_level(root)
        if display != role_top and not display.startswith(role_top + "|"):
            raise CharacterSceneError(
                "INVALID_DISPLAY",
                "The Display controller must be inside the character's top-level group.")
        if namespace(display) != namespace(root):
            raise CharacterSceneError(
                "INVALID_DISPLAY",
                "The Display controller and character root must use the same namespace.")
        attributes = enum_attributes(display)
        by_name = dict((item["attribute"], item) for item in attributes)
        if display_attribute is None:
            if len(attributes) != 1:
                raise CharacterSceneError(
                    "AMBIGUOUS_DISPLAY_ATTRIBUTE",
                    "Unable to identify exactly one Clothes enum attribute.",
                    context={"attribute_candidates": tuple(sorted(by_name))})
            display_attribute = attributes[0]["attribute"]
        if display_attribute not in by_name:
            raise CharacterSceneError(
                "INVALID_DISPLAY_ATTRIBUTE",
                "The selected Display attribute is not a Clothes enum.",
                context={"attribute_candidates": tuple(sorted(by_name))})
        return display, display_attribute

    @classmethod
    def _resolve_saved_display(cls, root, display_uuid, display_attribute):
        # Recovery may follow a rename, deletion, or replacement of the
        # explicitly selected label source. Only the same usable node qualifies.
        matches = cmds.ls(display_uuid, long=True) or []
        if len(matches) == 1:
            try:
                return cls._resolve_display(root, matches[0], display_attribute)
            except CharacterSceneError as error:
                if error.code not in ("INVALID_DISPLAY", "INVALID_DISPLAY_ATTRIBUTE",
                                      "AMBIGUOUS_DISPLAY_ATTRIBUTE"):
                    raise
        return None, None

    @staticmethod
    def _wrap_error(code, error):
        if isinstance(error, CharacterSceneError):
            return error
        return CharacterSceneError(code, str(error), details=str(error))

    def _display_record(self):
        if self._display_path is None:
            return None
        for record in enum_attributes(self._display_path):
            if record["attribute"] == self._display_attribute:
                return record
        return None

    def _capture_values(self, revision):
        try:
            previous_subject = self._subject or {}
            subject = capture_subject(
                self._root, previous_subject.get("bind_fallbacks"),
                previous_subject.get("curves", ()))
            record = self._display_record()
            outfit = (current_enum_label(record) if record is not None
                      else mesh_label(subject["meshes"]))
        except BindPoseError as exc:
            raise self._wrap_error("BIND_POSE_INVALID", exc)
        except (RuntimeError, ValueError) as exc:
            code = str(exc) if str(exc) in DIAGNOSTICS else "ROLE_SETUP_FAILED"
            raise self._wrap_error(code, exc)
        duplicates = duplicate_bone_paths(subject["bones"])
        duplicate_paths = [path for name in sorted(duplicates)
                           for path in duplicates[name]]
        snapshot = CharacterSnapshot(
            revision,
            self._root,
            outfit,
            [(bone["name"], bone["parent"]) for bone in subject["bones"]],
            [curve["name"] for curve in subject["curves"]],
            duplicate_paths,
            subject["meshes"],
            subject["bind_local_transforms"],
            subject.get("bind_conflict_count", 0),
            mesh_identities(subject.get("mesh_shapes", subject["meshes"])))
        return subject, snapshot

    def _commit_capture(self, revision):
        subject, snapshot = self._capture_values(revision)
        self._subject = subject
        self._snapshot = snapshot

    def _register_callbacks(self):
        try:
            self._root_dag = self._subject["bones"][0]["dag_path"]
            self._callback_ids.append(om.MNodeMessage.addNodeDestroyedCallback(
                self._root_dag.node(),
                self._on_root_destroyed))
            if self._display_path is not None:
                self._display_dag = dag_path(self._display_path)
            self._callback_ids.append(om.MDagMessage.addAllDagChangesCallback(
                self._on_graph_changed))
            for node_type in ("skinCluster", "mesh", "joint", "blendShape", "displayLayer"):
                self._callback_ids.append(om.MDGMessage.addNodeAddedCallback(
                    self._on_graph_changed, node_type))
                self._callback_ids.append(om.MDGMessage.addNodeRemovedCallback(
                    self._on_graph_changed, node_type))
            for event in ("Undo", "Redo"):
                self._callback_ids.append(om.MEventMessage.addEventCallback(
                    event, self._on_graph_changed))
            for event in (om.MSceneMessage.kBeforeNew, om.MSceneMessage.kBeforeOpen):
                self._callback_ids.append(om.MSceneMessage.addCallback(
                    event, self._on_root_destroyed))
            self._rebuild_input_observers()
        except (RuntimeError, ValueError) as exc:
            self._remove_callbacks()
            raise self._wrap_error("ROLE_SETUP_FAILED", exc)

    def snapshot(self):
        self._ensure_available()
        return self._snapshot

    def refresh_for_restart(self):
        """Capture the current evaluated inputs before preparing a restart."""
        if not self._refreshing:
            self._ensure_available()
            self._check_evaluated_inputs()
        if self._refresh_token is not None or self._refreshing:
            self._refresh_inputs()
        return self.snapshot()

    def set_display_controller(self, display, display_attribute=None):
        """Choose a label source without replacing the captured character."""
        self._ensure_available()
        display, attribute = self._resolve_display(self._root, display, display_attribute)
        self._display_path, self._display_attribute = display, attribute
        self._display_dag = dag_path(display) if display is not None else None
        self._rebuild_input_observers()
        self._queue_refresh()

    def sample(self, previous=None, timings=None):
        """Return a full frame or reuse a prior frame with identical inputs.

        Only realtime passes a prior frame. Cached Playback and linkage always
        evaluate and convert the complete pose they requested.
        """
        started = perf_counter()
        self._ensure_available()
        self._check_evaluated_inputs()
        self._ensure_available()
        revision, subject = self._snapshot.revision, self._subject
        try:
            if timings is None:
                transforms, curves = sample_pose(subject)
                return CharacterFrame(revision, transforms, curves)
            timings("inputs", perf_counter() - started)
            started = perf_counter()
            matrices = read_world_matrices(subject)
            timings("matrices", perf_counter() - started)
            started = perf_counter()
            curves = read_curve_values(subject)
            timings("curves", perf_counter() - started)
            started = perf_counter()
            evaluated = subject["unit_scale"], matrices
            unchanged = (previous is not None and previous.revision == revision
                         and previous.curves == curves and previous._evaluated == evaluated)
            timings("compare", perf_counter() - started)
            if unchanged:
                return previous
            started = perf_counter()
            transforms = convert_pose_matrices(subject, matrices)
            timings("transforms", perf_counter() - started)
            started = perf_counter()
            frame = CharacterFrame(revision, transforms, curves, evaluated=evaluated)
            timings("validation", perf_counter() - started)
            return frame
        except (RuntimeError, ValueError) as exc:
            raise self._wrap_error("SAMPLING_FAILED", exc)

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._refresh_token = None
        self._refreshing = False
        self._remove_callbacks()
        self._subject = None
        if self._terminal_error is None:
            self._terminal_error = CharacterSceneError(
                "CHARACTER_SCENE_CLOSED", "The character scene is closed.")

    def _ensure_available(self):
        if self._terminal_error is not None:
            raise self._terminal_error
        if self._closed:
            raise CharacterSceneError(
                "CHARACTER_SCENE_CLOSED", "The character scene is closed.")
        if self._refreshing:
            raise CharacterSceneError(
                "CHARACTER_SCENE_REFRESHING", "The character scene is refreshing.")

    def _remove_callbacks(self):
        callback_ids = self._callback_ids + self._input_callback_ids
        self._callback_ids, self._input_callback_ids = [], []
        self._candidate_paths = []
        self._remove_callback_ids(callback_ids)

    def _remove_callback_ids(self, callback_ids):
        for callback_id in callback_ids:
            try:
                om.MMessage.removeCallback(callback_id)
            except RuntimeError as exc:
                if self._cleanup_error is None:
                    self._cleanup_error = exc

    def _emit(self, event):
        if self._on_event is None:
            return
        try:
            self._on_event(event)
        except Exception as exc:
            if cmds is not None:
                try:
                    cmds.warning(
                        "MtoU_LiveLink character event callback failed: {0}".format(exc))
                except Exception:
                    pass

    def _rebuild_input_observers(self):
        old_ids, self._input_callback_ids = self._input_callback_ids, []
        self._remove_callback_ids(old_ids)
        self._update_paths()
        bones = build_hierarchy(self._root, maya_children, allow_duplicates=True)
        candidates = skinned_mesh_candidates([bone["path"] for bone in bones])
        self._candidate_paths = [dag_path(path) for path in candidates]
        nodes = set(candidates + [bone["path"] for bone in bones])
        if self._display_path is not None:
            nodes.add(self._display_dag.fullPathName())
            self._input_callback_ids.append(om.MNodeMessage.addNodeDestroyedCallback(
                self._display_dag.node(), self._on_display_destroyed))
        for path in list(nodes):
            current = path
            while current:
                nodes.add(current)
                parents = cmds.listRelatives(current, parent=True, fullPath=True) or []
                current = parents[0] if parents else None
        # Observe outputs, ancestors, hidden variants and their deformation
        # inputs. Layer membership itself can change, so observe layers too.
        for path in candidates:
            nodes.update(cmds.listHistory(path, pruneDagObjects=True) or [])
        nodes.update(cmds.ls(type="displayLayer") or [])
        for path in sorted(nodes):
            selection = om.MSelectionList()
            selection.add(path)
            node = selection.getDependNode(0)
            self._input_callback_ids.append(om.MNodeMessage.addAttributeChangedCallback(
                node, self._on_input_changed))
            self._input_callback_ids.append(om.MNodeMessage.addNameChangedCallback(
                node, self._on_name_changed))

    def _on_name_changed(self, node, *unused):
        if self._closed:
            return
        if any(node == bone["dag_path"].node() for bone in self._subject["bones"]):
            self._on_graph_changed()
            return
        self._paths_dirty = True
        self._queue_refresh()

    def _on_input_changed(self, message, plug, other_plug, client_data):
        del other_plug, client_data
        if self._closed or self._terminal_error is not None:
            return
        graph_messages = (om.MNodeMessage.kConnectionMade | om.MNodeMessage.kConnectionBroken
                          | om.MNodeMessage.kAttributeAdded | om.MNodeMessage.kAttributeRemoved
                          | om.MNodeMessage.kAttributeRenamed)
        if message & graph_messages:
            self._on_graph_changed()
            return
        if not message & om.MNodeMessage.kAttributeSet:
            return
        attribute = plug.partialName(useLongNames=True).split("[", 1)[0].split(".", 1)[0]
        if attribute in ("bindPreMatrix", "bindPose"):
            self._on_graph_changed()
            return
        if attribute in ("worldMatrix", "matrix"):
            # Bone animation changes worldMatrix continually; bind inputs are
            # meaningful only on dependency nodes (skinCluster / dagPose).
            if om.MFnDependencyNode(plug.node()).typeName in ("skinCluster", "dagPose"):
                self._on_graph_changed()
            return
        if attribute not in ("visibility", "lodVisibility", "overrideEnabled",
                             "overrideVisibility", "intermediateObject", "drawInfo",
                             "displayType", "overrideDisplayType", self._display_attribute):
            return
        self._queue_refresh()

    def _on_graph_changed(self, *unused):
        if self._closed:
            return
        self._graph_dirty = True
        self._queue_refresh()

    def _queue_refresh(self):
        if self._closed or self._refresh_token is not None:
            return
        token = self._refresh_token = object()
        mayaapi.run_deferred(lambda: self._refresh_inputs(token), cmds.evalDeferred)

    def _begin_change(self):
        if not self._refreshing:
            self._refreshing = True
            self._emit(CharacterSceneEvent(
                "character_change_started", snapshot=self._snapshot))

    def _visible_inputs(self):
        self._update_paths()
        shapes = visible_mesh_shapes([path.fullPathName() for path in self._candidate_paths])
        meshes = mesh_transforms(shapes)
        record = self._display_record()
        outfit = current_enum_label(record) if record is not None else mesh_label(meshes)
        return meshes, mesh_identities(shapes), outfit

    def _update_paths(self):
        self._root = self._root_dag.fullPathName()
        if not self._root or not cmds.objExists(self._root):
            raise CharacterSceneError(
                "CHARACTER_ROOT_DESTROYED", "The character root was deleted or unloaded.")
        if self._display_dag is not None:
            path = self._display_dag.fullPathName()
            self._display_path = path if path and cmds.objExists(path) else None

    def _check_evaluated_inputs(self):
        # Visibility can be animated or driven without an attribute-set event.
        # Only cached candidates are queried on the normal per-frame path.
        if self._graph_dirty or self._paths_dirty:
            self._refresh_inputs()
            return
        try:
            meshes, identities, outfit = self._visible_inputs()
            if identities != self._snapshot.mesh_identities:
                self._begin_change()
            if (self._refreshing or tuple(meshes) != self._snapshot.mesh_paths
                    or outfit != self._snapshot.outfit):
                self._queue_refresh()
        except (RuntimeError, ValueError) as exc:
            self._invalidate(self._wrap_error("ROLE_SETUP_FAILED", exc))

    def _refresh_inputs(self, token=None):
        if self._closed or (token is not None and token is not self._refresh_token):
            return
        # A synchronous refresh consumes the queued request as well. Its
        # deferred callback must leave any later request's dirty state alone.
        self._refresh_token = None
        previous = self._snapshot
        try:
            graph_dirty, self._graph_dirty = self._graph_dirty, False
            paths_dirty, self._paths_dirty = self._paths_dirty, False
            if graph_dirty or paths_dirty:
                self._rebuild_input_observers()
            meshes, identities, outfit = self._visible_inputs()
            if identities != previous.mesh_identities:
                self._begin_change()
            if self._closed:
                return
            if graph_dirty or identities != previous.mesh_identities:
                subject, candidate = self._capture_values(previous.revision + 1)
            else:
                subject = self._subject
                duplicates = previous.duplicate_paths
                if paths_dirty:
                    subject = dict(subject)
                    subject["bones"] = [dict(bone, path=bone["dag_path"].fullPathName())
                                        for bone in subject["bones"]]
                    subject["root"], subject["meshes"] = self._root, meshes
                    subject["curves"] = discover_curve_plugs(
                        [bone["path"] for bone in subject["bones"]],
                        visible_mesh_shapes([
                            path.fullPathName() for path in self._candidate_paths]),
                        subject["curves"])
                    cache_curve_handles(subject["curves"])
                    grouped = duplicate_bone_paths(subject["bones"])
                    duplicates = [path for name in sorted(grouped) for path in grouped[name]]
                candidate = CharacterSnapshot(
                    previous.revision + 1, self._root, outfit, previous.bones,
                    [curve["name"] for curve in subject["curves"]], duplicates, meshes,
                    previous.bind_local_transforms, previous.bind_conflict_count, identities)
        except (RuntimeError, ValueError) as error:
            self._invalidate(self._wrap_error("ROLE_SETUP_FAILED", error))
            return
        changed = (
            candidate.mesh_identities != previous.mesh_identities
            or candidate.bones != previous.bones
            or candidate.curve_names != previous.curve_names
            or candidate.bind_local_transforms != previous.bind_local_transforms)
        if changed:
            self._begin_change()
        if self._closed:
            return
        if not changed:
            candidate = CharacterSnapshot(
                previous.revision, candidate.root, candidate.outfit,
                candidate.bones, candidate.curve_names, candidate.duplicate_paths,
                candidate.mesh_paths, candidate.bind_local_transforms,
                candidate.bind_conflict_count, candidate.mesh_identities)
        self._subject = subject
        self._snapshot = candidate
        was_refreshing, self._refreshing = self._refreshing, False
        if changed or was_refreshing:
            self._emit(CharacterSceneEvent("outfit_changed", snapshot=candidate))
        elif (candidate.outfit != previous.outfit or candidate.mesh_paths != previous.mesh_paths
              or candidate.root != previous.root
              or candidate.duplicate_paths != previous.duplicate_paths
              or candidate.bind_conflict_count != previous.bind_conflict_count):
            self._emit(CharacterSceneEvent("display_changed", snapshot=candidate))

    def _on_root_destroyed(self, *unused):
        self._invalidate(CharacterSceneError(
            "CHARACTER_ROOT_DESTROYED", "The character root was deleted or unloaded."))

    def _on_display_destroyed(self, *unused):
        self._display_path = self._display_dag = None
        self._display_attribute = None
        self._queue_refresh()

    def _invalidate(self, error):
        if self._closed or self._terminal_error is not None:
            return
        self._terminal_error = error
        self._closed = True
        self._refresh_token = None
        self._refreshing = False
        self._remove_callbacks()
        self._subject = None
        self._emit(CharacterSceneEvent("character_invalidated", error=error))
