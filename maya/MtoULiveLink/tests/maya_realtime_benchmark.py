# Copyright (C) 2026 Alexis-Li
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under the GPL with additional terms; see NOTICE.
"""Measure Maya realtime sampling and loopback delivery in Maya 2024 mayapy."""

import argparse
import json
import math
import socket
import struct
import sys
import threading
from pathlib import Path
from statistics import mean, median
from time import perf_counter


def build_character(cmds, bone_count, curve_count, end_frame):
    cmds.file(new=True, force=True)
    cmds.currentUnit(linear="cm", time="film")
    joints = []
    for index in range(bone_count):
        name = "benchmarkJoint{0:04d}".format(index)
        options = {"parent": joints[(index - 1) // 3]} if index else {}
        joint = cmds.createNode("joint", name=name, **options)
        if index:
            cmds.setAttr(joint + ".translate", 1.0, (index % 4) * 0.2, 0.1)
        joints.append(joint)
    root = joints[0]
    mesh = cmds.polyCube(name="benchmarkBody")[0]
    if curve_count:
        targets = [cmds.duplicate(mesh, name="benchmarkCurve{0:04d}".format(index))[0]
                   for index in range(curve_count)]
        cmds.move(0.1, 0, 0, targets[0] + ".vtx[0]", relative=True)
        blend = cmds.blendShape(targets, mesh, name="benchmarkBlendShape")[0]
        cmds.delete(targets)
        for index in range(0, curve_count, max(1, curve_count // 12)):
            cmds.setKeyframe(blend + ".weight[{0}]".format(index), time=1, value=0.0)
            cmds.setKeyframe(blend + ".weight[{0}]".format(index), time=end_frame, value=0.8)
    cmds.skinCluster(root, mesh, toSelectedBones=True)
    for index in range(0, bone_count, max(1, bone_count // 16)):
        cmds.setKeyframe(joints[index] + ".rotateY", time=1, value=0.0)
        cmds.setKeyframe(joints[index] + ".rotateY", time=end_frame, value=24.0)
    cmds.currentTime(1)
    return root


def milliseconds(samples):
    if not samples:
        return None
    return {"mean_ms": 1000 * mean(samples), "p50_ms": 1000 * median(samples),
            "p95_ms": 1000 * sorted(samples)[math.ceil(len(samples) * 0.95) - 1]}


def run_case(cmds, scene, session_module, protocol, case, sample_count):
    worker = session_module.SenderWorker(None)
    worker._set_status("ready", "Loopback")
    session = session_module.StreamingSession(scene, 24.0, None, playback_state=lambda: False)
    session._worker = worker
    session._phase = "ready"
    session._revision = scene.snapshot().revision
    barrier_message = {"type": "benchmark_complete"}
    barrier_payload = protocol.encode_message(barrier_message)[8:]
    barrier = threading.Event()
    received = 0
    errors = []

    def receive_packets(sock):
        nonlocal received
        try:
            while True:
                (size,) = struct.unpack(">Q", protocol.recv_exact(sock, 8))
                payload = protocol.recv_exact(sock, size)
                if payload == barrier_payload:
                    break
                received += 1
        except (OSError, EOFError) as error:
            errors.append(error)
        finally:
            barrier.set()

    def send_packets(sock):
        try:
            worker._run_connected(sock)
        except Exception as error:
            errors.append(error)
            barrier.set()

    cmds.currentTime(1)
    sender, receiver = socket.socketpair()
    with sender, receiver:
        sender.setblocking(False)
        receiver.settimeout(10.0)
        outgoing = threading.Thread(target=send_packets, args=(sender,))
        incoming = threading.Thread(target=receive_packets, args=(receiver,))
        outgoing.start()
        incoming.start()
        evaluate_seconds, sample_seconds = [], []
        try:
            for index in range(sample_count):
                if case == "moving":
                    started = perf_counter()
                    cmds.currentTime(index + 1)
                    evaluate_seconds.append(perf_counter() - started)
                # mayapy has no idle loop. Step the normal realtime sampling
                # path once per evaluation, without forcing a full pose.
                session._last_sample_time = None
                started = perf_counter()
                session._sample_and_submit()
                sample_seconds.append(perf_counter() - started)
                if not session.is_ready:
                    raise RuntimeError("Maya stopped sampling the benchmark character")
            # Drain the latest slot through the ordered queue before ending
            # the measurement and reading the receiver's final count.
            worker.begin_ordered()
            worker.submit_ordered(barrier_message)
            if not barrier.wait(10.0):
                raise RuntimeError("loopback sender did not drain")
            metrics = session.realtime_metrics() if hasattr(session, "realtime_metrics") else None
        finally:
            worker.stop()
            sender.shutdown(socket.SHUT_WR)
            outgoing.join()
            incoming.join()
    if errors:
        raise RuntimeError("loopback delivery failed: {0}".format(errors[0]))
    if not received or (metrics is not None and received != metrics["sent_frames"]):
        raise RuntimeError("loopback counts disagree with the realtime session")
    if metrics is not None:
        if case == "static" and (received != 1 or metrics["unchanged_samples"] != sample_count - 1):
            raise RuntimeError("static poses did not collapse to one frame")
        if case == "moving" and metrics["unchanged_samples"]:
            raise RuntimeError("the moving rig did not change on every sample")
    return {"sample_total": milliseconds(sample_seconds),
            "maya_time_change": milliseconds(evaluate_seconds),
            "received_frames": received, "realtime": metrics}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bones", type=int, default=1399)
    parser.add_argument("--curves", type=int, default=108)
    parser.add_argument("--samples", type=int, default=120, help="eligible samples per case")
    parser.add_argument("--warmup", type=int, default=16)
    parser.add_argument("--scripts", type=Path, default=Path(__file__).resolve().parents[1] / "scripts",
                        help="Maya component scripts directory from the build to measure")
    args = parser.parse_args()
    if args.bones < 1 or args.curves < 0 or args.samples < 1 or args.warmup < 0:
        parser.error("bones and samples must be positive; curves and warmup cannot be negative")
    if not (args.scripts / "mtou_livelink" / "__init__.py").is_file():
        parser.error("--scripts must contain mtou_livelink/__init__.py")
    sys.path.insert(0, str(args.scripts.resolve()))

    try:
        import maya.standalone
    except ImportError:
        parser.error("run this benchmark with Maya 2024 mayapy")
    maya.standalone.initialize(name="python")
    try:
        from maya import cmds
        from mtou_livelink import protocol, scene, session

        root = build_character(cmds, args.bones, args.curves, max(args.warmup, args.samples) + 1)
        character = scene.CharacterScene.capture(root)
        try:
            snapshot = character.snapshot()
            if len(snapshot.bones) != args.bones or len(snapshot.curve_names) != args.curves:
                raise RuntimeError("Maya did not capture the requested bone and curve counts")
            if args.warmup:
                run_case(cmds, character, session, protocol, "moving", args.warmup)
            result = {"maya": cmds.about(version=True), "bones": args.bones, "curves": args.curves,
                      "samples_per_case": args.samples, "warmup_samples": args.warmup}
            for case in ("moving", "static"):
                result[case] = run_case(cmds, character, session, protocol, case, args.samples)
            print(json.dumps(result, ensure_ascii=True, allow_nan=False, sort_keys=True))
        finally:
            character.close()
    finally:
        maya.standalone.uninitialize()


if __name__ == "__main__":
    main()
