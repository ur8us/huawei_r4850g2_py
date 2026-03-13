# AGENTS.md

## Project Purpose

This repository contains `huawei_r4850g2_py.py`, a small Python utility for reading telemetry from a Huawei `R4850G2` power supply over CAN bus.

The implementation reads telemetry and supports limited write operations for output on/off and stored voltage when explicitly requested on the command line. Do not add further write or control commands unless explicitly requested by the user.

## Primary Files

- `huawei_r4850g2_py.py`: main program
- `read.sh`: helper script for the default read command
- `on.sh`: one-shot helper script for output on
- `off.sh`: one-shot helper script for output off
- `set-58v.sh`: one-shot helper script for stored voltage `58.0` V
- `set-50v.sh`: one-shot helper script for stored voltage `50.0` V
- `README.md`: GitHub-facing documentation and setup instructions
- `requirements.txt`: Python dependencies
- `99-canalystii.rules`: Linux udev rule for CANalyst-II USB adapter access

## Protocol Reference

Use this repository as the protocol reference source:

- https://github.com/craigpeacock/Huawei_R4850G2_CAN

Do not invent frame IDs, scaling factors, or message meanings when the reference can be checked.

## Working Rules

- Preserve the current limited-write behavior unless the user explicitly asks for additional CAN write support.
- Prefer small, direct changes over large refactors.
- Keep the script dependency-light.
- Support both backends already implemented:
  - `socketcan`
  - `canalystii`
- Assume Linux as the target platform.
- Keep setup instructions in `README.md` current when behavior changes.
- If adapter-specific permissions are relevant, keep `99-canalystii.rules` in sync with the documentation.

## Code Style

- Use straightforward Python.
- Avoid unnecessary abstraction.
- Keep output human-readable for terminal use.
- Add comments only when they clarify protocol handling or a non-obvious implementation detail.

## Validation

After code changes, at minimum run:

```bash
python3 -m py_compile huawei_r4850g2_py.py
python3 huawei_r4850g2_py.py --help
```

If hardware access is available, also test against the actual CAN adapter and PSU.

## Safety

- Treat CAN write operations as potentially dangerous.
- Do not add commands beyond output on/off and stored voltage without explicit user approval.
- If a requested change could alter PSU behavior, call that out clearly.
