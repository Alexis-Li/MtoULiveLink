# Real-time Preview performance (#45)

The Maya 2024 synthetic benchmark cut mean sampling and submission time from
13.735 to 7.562 ms for changing poses and from 14.997 to 0.652 ms for a
stationary pose. In the C04 production scene, the independent verification run
sent and applied new poses at 5.17 Hz and recorded no new sends or applications
after playback stopped and the final pose settled. This cleared the 2.9 Hz
send and application floors calibrated for C04. The editor ran at 119.50 FPS
with NullRHI. This is an editor tick measurement, and the scope of the gate is
the C04 reference configuration.

## Maya synthetic comparison

The driver was `maya/MtoULiveLink/tests/maya_realtime_benchmark.py`. It ran the
same generated rig and loopback receiver on Maya 2024, with 1,399 bones, 108
curves, 16 moving warmup samples and 120 measured samples per case. The base
scripts were from `a735a3e89d13bd175ebb367ecda09280909b2bd1` and the optimized
scripts from `415e007f60655dd62ab5dd1f61a8d3a8929f3d2b`. The same driver was
used with both sets of scripts. See the [benchmark command](../testing.md#real-time-preview-performance).

| Case | Base mean / p95 (ms) | Optimized mean / p95 (ms) | Loopback packets, base → optimized |
| --- | ---: | ---: | ---: |
| Changing pose | 13.735 / 15.300 | 7.562 / 8.474 | 102 → 57 |
| Stationary pose | 14.997 / 33.117 | 0.652 / 0.821 | 92 → 1 |

The measured time covers sampling through submission and excludes Maya's
`currentTime` call and the asynchronous socket send. The driver requests a
sample at every step, so its changing-pose delivery count depends on how
quickly the loopback thread drains the latest slot. In the optimized stationary
case, the single delivery was the initial pose and 119 samples were identical.

## Production cadence and stage timings

Measured 2026-11-19 on product code at
`5b5b188c4a25a472cec50257f40b23aa9975b5cf`. C04 is one production character
in Animation with the same scene, level and Binding across all three runs.
Each run started fresh Maya 2024 and stock Unreal 5.7.4 (CL 51494982) with
NullRHI and a dedicated port. Maya looped frames 120–400 with native playback
at its normal speed for a requested 10-second measurement after warmup. Maya
stopped before the stationary measurement. Each run's `ue-run.json` recorded
`maya-prefs-minimal` with `pluginPrefs.mel` SHA-256
`6aa456abf66e689a0fe584fa4344ed0d7ccb3082048088c75961276693132fa0`, verified
against the cloned file. All three Automation reports showed success.

| Run | Motion seconds, Maya / UE | Successful sends (Hz) | New pose applications (Hz) | Editor ticks (FPS) | Stationary sends / applications |
| --- | ---: | ---: | ---: | ---: | ---: |
| Calibration 1 | 10.202 / 10.202 | 39 (3.823) | 39 (3.823) | 1,219 (119.49) | 0 / 0 |
| Calibration 2 | 10.150 / 10.152 | 37 (3.645) | 38 (3.743) | 1,210 (119.19) | 0 / 0 |
| Verification | 10.057 / 10.059 | 52 (5.171) | 52 (5.170) | 1,202 (119.50) | 0 / 0 |

Each row uses changes between the start and end snapshots from
`realtime-performance.json`. Sends use Maya's clock and applications and editor
ticks use Unreal's clock. The hosts take their snapshots independently, so
boundary events can fall in only one host's window. The stationary windows
were 3.075/3.077, 2.220/2.220 and 2.059/2.058 seconds on Maya/Unreal,
respectively. All four snapshots in each run were on the same session.

Window means below are in milliseconds with the number of stage samples in
parentheses. They use deltas in accumulated stage time and sample count
between the motion endpoints.

| Host and stage | Calibration 1 | Calibration 2 | Verification |
| --- | ---: | ---: | ---: |
| Maya character input check | 66.544 (78) | 69.432 (75) | 63.383 (103) |
| Maya bone matrix reads | 9.146 (78) | 10.006 (75) | 9.619 (103) |
| Maya curve reads | 0.200 (78) | 0.196 (75) | 0.190 (103) |
| Maya pose comparison | 0.024 (78) | 0.027 (75) | 0.023 (103) |
| Maya localization, decomposition and conversion | 5.825 (39) | 5.681 (37) | 5.396 (51) |
| Maya finite-value validation | 0.400 (39) | 0.411 (37) | 0.395 (51) |
| Maya JSON encoding | 4.588 (39) | 4.608 (37) | 4.500 (51) |
| Maya sampling through submission | 81.459 (78) | 85.077 (75) | 78.432 (103) |
| Maya socket send | 0.257 (39) | 0.265 (37) | 0.228 (52) |
| Unreal parse and validation | 8.409 (39) | 8.594 (37) | 8.532 (52) |
| Unreal retarget | 0.384 (39) | 0.373 (38) | 0.396 (52) |
| Unreal application | 0.772 (39) | 0.737 (38) | 0.722 (52) |

The send and application floors were set before the third run. For each
metric, take 80% of the lower of the two calibration rates and round down to
0.1 Hz. The stationary requirement is zero new sends and zero new applications
on the same connection. See the [fixture, commands and gate](../testing.md#real-time-preview-performance).

## Design input for #49

The character input check was the longest measured stage in every C04 run,
at 63–69 ms out of 78–85 ms per sample through submission. The check runs
before pose comparison, including on stationary samples. It reads the
evaluated visibility of cached mesh candidates and checks outfit identity
because animation or drivers can change visibility without an attribute-set
event. This makes the evaluated input check the first place to profile for
#49. Use the same C04 motion and stationary windows to measure a change, and
check that animated visibility still triggers input renegotiation. Record send
and application cadence separately from editor ticks when assessing the result.
