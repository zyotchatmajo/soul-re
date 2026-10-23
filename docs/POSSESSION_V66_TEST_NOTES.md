# Possession v66 test notes

Recorded 2026-10-23 after installing `sr1-possession-v66-aim-flash-fix.dll`.

## Results

- The projectile now travels generally toward the intended aim direction.
- The projectile itself is visually oriented in a different direction from its travel path. This may indicate that the launch yaw/velocity and the projectile model's rotation are using different coordinate conventions.
- The white flash now appears.
- The camera still switches to the possessed enemy immediately instead of waiting for the flash/transition peak.

## Next investigation

1. Separate projectile travel heading from projectile instance/model rotation and align the visual orientation with the velocity direction.
2. Find the camera-focus/state transition that runs immediately after the projectile handoff; suppress or defer it until the delayed presentation phase.
3. Preserve the current v66 DLL and its v65 backup while testing.
