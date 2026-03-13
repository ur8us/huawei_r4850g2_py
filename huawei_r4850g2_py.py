#!/usr/bin/env python3
"""Read telemetry from supported Huawei rectifiers over CAN.

Protocol reference:
https://github.com/craigpeacock/Huawei_R4850G2_CAN
"""

from __future__ import annotations

import argparse
import re
import select
import signal
import socket
import struct
import sys
import time
from dataclasses import dataclass, field


CAN_EFF_FLAG = 0x80000000
CAN_EFF_MASK = 0x1FFFFFFF
CAN_FRAME_FORMAT = "=IB3x8s"
CAN_FRAME_SIZE = struct.calcsize(CAN_FRAME_FORMAT)

QUERY_ID = 0x108040FE
DATA_ID = 0x1081407F
ELABEL_REQUEST_ID = 0x1081D2FE
DESCRIPTION_ID = 0x1081D27F
DESCRIPTION_END_ID = 0x1081D27E
ACK_ID = 0x1081807E
AMP_HOUR_ID = 0x1001117E
KEEPALIVE_ID = 0x100011FE
STATUS_ID = 0x108111FE
OUTPUT_ENABLE_ID = 0x108081FE
REGISTER_SET_ID = 0x108180FE

REGISTER_OUTPUT_VOLTAGE = 0x0100
REGISTER_STORED_VOLTAGE = 0x0101
REGISTER_STANDBY = 0x0132

ACK_ERROR_FLAG = 0x20
MODEL_PATTERN = re.compile(r"\bR\d{4}[A-Z]\d\b")
MODEL_NOMINAL_MAX_CURRENT_A = {
    "R4850G2": 50.0,
    "R4875G1": 75.0,
    "R4875G5": 75.0,
}

R4850G2_STORED_VOLTAGE_MIN_V = 48.0
R4850G2_STORED_VOLTAGE_MAX_V = 58.5

PARAMETERS = {
    0x70: ("input_power_w", "Input Power", "W"),
    0x71: ("input_frequency_hz", "Input Frequency", "Hz"),
    0x72: ("input_current_a", "Input Current", "A"),
    0x73: ("output_power_w", "Output Power", "W"),
    0x74: ("efficiency_ratio", "Efficiency", "%"),
    0x75: ("output_voltage_v", "Output Voltage", "V"),
    0x76: ("max_output_current_ratio", "Max Output Current", "%"),
    0x78: ("input_voltage_v", "Input Voltage", "V"),
    0x7F: ("output_temperature_c", "Output Temperature", "C"),
    0x80: ("input_temperature_c", "Input Temperature", "C"),
    0x81: ("output_current_a", "Output Current", "A"),
    0x82: ("output_current_alt_a", "Output Current Alt", "A"),
}


@dataclass
class RectifierState:
    input_voltage_v: float | None = None
    input_frequency_hz: float | None = None
    input_current_a: float | None = None
    input_power_w: float | None = None
    input_temperature_c: float | None = None
    output_voltage_v: float | None = None
    output_current_a: float | None = None
    output_current_alt_a: float | None = None
    output_power_w: float | None = None
    output_temperature_c: float | None = None
    efficiency_ratio: float | None = None
    max_output_current_ratio: float | None = None
    output_enabled: bool | None = None
    model: str | None = None
    description: str = ""
    amp_seconds: float = 0.0
    startup_info_printed: bool = False
    e_label_parts: dict[int, str] = field(default_factory=dict)
    unknown_frames: dict[int, bytes] = field(default_factory=dict)

    @property
    def amp_hours(self) -> float:
        return self.amp_seconds / 3600.0


@dataclass(frozen=True)
class WriteRequest:
    description: str
    payload: bytes


def open_can_socket(interface: str) -> socket.socket:
    sock = socket.socket(socket.PF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
    sock.bind((interface,))
    return sock


def pack_frame(can_id: int, data: bytes) -> bytes:
    payload = data.ljust(8, b"\x00")
    return struct.pack(CAN_FRAME_FORMAT, can_id | CAN_EFF_FLAG, len(data), payload)


def unpack_frame(frame: bytes) -> tuple[int, bytes]:
    can_id, can_dlc, payload = struct.unpack(CAN_FRAME_FORMAT, frame)
    return can_id & CAN_EFF_MASK, payload[:can_dlc]


def send_query(sock: socket.socket) -> None:
    sock.send(pack_frame(QUERY_ID, b"\x00" * 8))


def send_elabel_request(sock: socket.socket) -> None:
    sock.send(pack_frame(ELABEL_REQUEST_ID, b"\x00" * 8))


def send_frame(sock: socket.socket, can_id: int, data: bytes) -> None:
    sock.send(pack_frame(can_id, data))


def open_python_can_bus(backend: str, channel: int, device: int, bitrate: int):
    try:
        import can
    except ImportError as exc:
        raise RuntimeError(
            "python-can is not installed. For this adapter use: "
            'python3 -m pip install "python-can[canalystii]"'
        ) from exc

    try:
        return can.Bus(
            interface=backend,
            channel=channel,
            device=device,
            bitrate=bitrate,
        )
    except Exception as exc:  # pragma: no cover - backend-specific
        raise RuntimeError(
            f"Failed to open {backend} channel {channel}: {exc}\n"
            "If this is a USB permission error, install the udev rule from "
            "`99-canalystii.rules` and reconnect the adapter."
        ) from exc


def send_query_python_can(bus) -> None:
    import can

    bus.send(
        can.Message(
            arbitration_id=QUERY_ID,
            is_extended_id=True,
            data=b"\x00" * 8,
        )
    )


def send_elabel_request_python_can(bus) -> None:
    import can

    bus.send(
        can.Message(
            arbitration_id=ELABEL_REQUEST_ID,
            is_extended_id=True,
            data=b"\x00" * 8,
        )
    )


def send_frame_python_can(bus, can_id: int, data: bytes) -> None:
    import can

    bus.send(
        can.Message(
            arbitration_id=can_id,
            is_extended_id=True,
            data=data,
        )
    )


def close_python_can_bus(bus) -> None:
    # python-can backends are inconsistent here; CANalyst-II exposes shutdown().
    shutdown = getattr(bus, "shutdown", None)
    if callable(shutdown):
        shutdown()
        return

    close = getattr(bus, "close", None)
    if callable(close):
        close()


def decode_u32_be(data: bytes) -> int:
    return struct.unpack(">I", data)[0]


def decode_u16_be(data: bytes) -> int:
    return struct.unpack(">H", data)[0]


def pack_register_set(register_id: int, register_data: bytes) -> bytes:
    # Huawei register writes use a 2-byte register ID followed by 6 bytes of payload.
    if len(register_data) != 6:
        raise ValueError("Register set payloads must contain 6 data bytes")
    return struct.pack(">H", register_id) + register_data


def make_stored_voltage_request(voltage: float) -> WriteRequest:
    scaled = round(voltage * 1024.0)
    payload = pack_register_set(
        REGISTER_STORED_VOLTAGE,
        b"\x00\x00" + struct.pack(">I", scaled),
    )
    return WriteRequest(
        description=f"stored voltage -> {voltage:.2f} V",
        payload=payload,
    )


def make_output_voltage_request(voltage: float) -> WriteRequest:
    scaled = round(voltage * 1024.0)
    payload = pack_register_set(
        REGISTER_OUTPUT_VOLTAGE,
        b"\x00\x00" + struct.pack(">I", scaled),
    )
    return WriteRequest(
        description=f"output voltage -> {voltage:.2f} V",
        payload=payload,
    )


def make_output_request(state: str) -> WriteRequest:
    standby = 0x00 if state == "on" else 0x01
    payload = pack_register_set(
        REGISTER_STANDBY,
        bytes((0x00, standby, 0x00, 0x00, 0x00, 0x00)),
    )
    return WriteRequest(
        description=f"output -> {state}",
        payload=payload,
    )


def build_write_requests(args: argparse.Namespace) -> list[WriteRequest]:
    requests: list[WriteRequest] = []
    if args.set_output:
        requests.append(make_output_request(args.set_output))
    if args.set_stored_voltage is not None:
        # Match the live output voltage immediately and also persist it as the fallback value.
        requests.append(make_output_voltage_request(args.set_stored_voltage))
        requests.append(make_stored_voltage_request(args.set_stored_voltage))
    return requests


def describe_set_ack(data: bytes) -> str:
    if len(data) != 8:
        return "Received malformed register-set acknowledgement"

    # ACK frames mirror the register ID; bit 0x20 in the high byte signals an error.
    error = bool(data[0] & ACK_ERROR_FLAG)
    register_id = (((data[0] & 0xFF) & ~ACK_ERROR_FLAG) << 8) | data[1]
    status = "Error" if error else "Success"

    if register_id in {REGISTER_OUTPUT_VOLTAGE, REGISTER_STORED_VOLTAGE}:
        value = decode_u32_be(data[4:8]) / 1024.0
        if register_id == REGISTER_OUTPUT_VOLTAGE:
            return f"{status} setting output voltage to {value:.2f} V"
        return f"{status} setting stored voltage to {value:.2f} V"

    if register_id == REGISTER_STANDBY:
        standby = data[3] == 0x01
        output_state = "off (standby)" if standby else "on"
        return f"{status} setting output to {output_state}"

    hex_data = " ".join(f"{byte:02X}" for byte in data)
    return f"{status} setting register 0x{register_id:04X}: {hex_data}"


def validate_stored_voltage(text: str) -> float:
    try:
        value = float(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid voltage: {text!r}") from exc

    if not R4850G2_STORED_VOLTAGE_MIN_V <= value <= R4850G2_STORED_VOLTAGE_MAX_V:
        raise argparse.ArgumentTypeError(
            "stored voltage must be between "
            f"{R4850G2_STORED_VOLTAGE_MIN_V:.1f} V and "
            f"{R4850G2_STORED_VOLTAGE_MAX_V:.1f} V"
        )
    return value


def update_parameter(state: RectifierState, data: bytes) -> bool:
    if len(data) != 8:
        return False

    param_id = data[1]
    mapping = PARAMETERS.get(param_id)
    if not mapping:
        return False

    attr, _, _ = mapping
    value = decode_u32_be(data[4:8]) / 1024.0
    setattr(state, attr, value)
    return param_id == 0x81


def update_amp_hours(state: RectifierState, data: bytes) -> None:
    if len(data) < 8:
        return

    current_a = decode_u16_be(data[6:8]) / 20.0
    state.amp_seconds += current_a * 0.377


def get_nominal_max_current(model: str | None) -> float | None:
    if not model:
        return None
    return MODEL_NOMINAL_MAX_CURRENT_A.get(model)


def update_e_label(state: RectifierState, data: bytes) -> None:
    if len(data) < 8:
        return

    part_number = decode_u16_be(data[0:2])
    state.e_label_parts[part_number] = data[2:8].decode(
        "ascii",
        errors="replace",
    ).rstrip("\x00")

    e_label = "".join(state.e_label_parts[idx] for idx in sorted(state.e_label_parts))
    description_match = re.search(r"Description=([^\r\n\x00]+)", e_label)
    if description_match:
        state.description = description_match.group(1).strip()

    for candidate in (state.description, e_label):
        if not candidate:
            continue
        model_match = MODEL_PATTERN.search(candidate)
        if model_match:
            state.model = model_match.group(0)
            break


def maybe_print_startup_info(state: RectifierState) -> None:
    if state.startup_info_printed or not state.model:
        return

    nominal_max_current_a = get_nominal_max_current(state.model)
    if nominal_max_current_a is None:
        print(f"Detected model: {state.model}")
    else:
        print(
            f"Detected model: {state.model} "
            f"(nominal max current {nominal_max_current_a:.2f} A)"
        )
    state.startup_info_printed = True


def handle_frame(
    state: RectifierState,
    can_id: int,
    data: bytes,
    show_raw: bool,
    show_unknown: bool,
) -> bool:
    if show_raw:
        hex_data = " ".join(f"{byte:02X}" for byte in data)
        print(f"0x{can_id:08X} [{len(data)}] {hex_data}")

    if can_id == DATA_ID:
        return update_parameter(state, data)

    if can_id in {DESCRIPTION_ID, DESCRIPTION_END_ID}:
        # The D2 E-Label reply arrives as many numbered 6-byte ASCII chunks.
        update_e_label(state, data)
        return False

    if can_id == AMP_HOUR_ID:
        update_amp_hours(state, data)
        return False

    if can_id == ACK_ID:
        print(describe_set_ack(data))
        return False

    if can_id == STATUS_ID and len(data) >= 6:
        state.output_enabled = data[5] == 0
        return False

    if can_id == OUTPUT_ENABLE_ID:
        return False

    if can_id in {ACK_ID, KEEPALIVE_ID}:
        return False

    if show_unknown:
        state.unknown_frames[can_id] = data
    return False


def format_value(value: float | None, unit: str, *, scale: float = 1.0) -> str:
    if value is None:
        return "n/a"
    return f"{value * scale:.2f} {unit}".rstrip()


def print_summary(state: RectifierState) -> None:
    max_current_a = None
    if state.max_output_current_ratio is not None:
        nominal_max_current_a = get_nominal_max_current(state.model)
        if nominal_max_current_a is not None:
            max_current_a = state.max_output_current_ratio * nominal_max_current_a

    enabled = "n/a"
    if state.output_enabled is True:
        enabled = "enabled"
    elif state.output_enabled is False:
        enabled = "disabled"

    lines = [
        f"Input:  {format_value(state.input_voltage_v, 'V')}  "
        f"{format_value(state.input_frequency_hz, 'Hz')}  "
        f"{format_value(state.input_current_a, 'A')}  "
        f"{format_value(state.input_power_w, 'W')}",
        f"Output: {format_value(state.output_voltage_v, 'V')}  "
        f"{format_value(state.output_current_a, 'A')} of "
        f"{format_value(max_current_a, 'A')}  "
        f"{format_value(state.output_power_w, 'W')}",
        f"Temps:  in {format_value(state.input_temperature_c, 'C')}  "
        f"out {format_value(state.output_temperature_c, 'C')}",
        f"Limit:  {format_value(state.max_output_current_ratio, '%', scale=100.0)}  "
        f"Efficiency: {format_value(state.efficiency_ratio, '%', scale=100.0)}  "
        f"Ah: {state.amp_hours:.3f}",
        f"State:  {enabled}",
    ]
    if state.model:
        lines.append(f"Model:  {state.model}")
    if state.description:
        lines.append(f"Desc:   {state.description}")
    print("\n".join(lines))
    print()


def print_unknown_frames(state: RectifierState) -> None:
    if not state.unknown_frames:
        return

    print("Unknown frames seen:")
    for can_id in sorted(state.unknown_frames):
        data = state.unknown_frames[can_id]
        hex_data = " ".join(f"{byte:02X}" for byte in data)
        print(f"  0x{can_id:08X} [{len(data)}] {hex_data}")


def wait_for_socketcan_frames(
    sock: socket.socket,
    state: RectifierState,
    *,
    show_raw: bool,
    show_unknown: bool,
    duration: float,
) -> None:
    # One-shot writes still need a short receive window so the PSU ACK can be printed.
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        timeout = max(0.0, deadline - time.monotonic())
        readable, _, _ = select.select([sock], [], [], min(0.2, timeout))
        if not readable:
            continue

        frame = sock.recv(CAN_FRAME_SIZE)
        can_id, data = unpack_frame(frame)
        handle_frame(state, can_id, data, show_raw, show_unknown)


def wait_for_python_can_frames(
    bus,
    state: RectifierState,
    *,
    show_raw: bool,
    show_unknown: bool,
    duration: float,
) -> None:
    # One-shot writes still need a short receive window so the PSU ACK can be printed.
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        timeout = min(0.2, max(0.0, deadline - time.monotonic()))
        msg = bus.recv(timeout=timeout)
        if msg is None or not msg.is_extended_id:
            continue

        handle_frame(
            state,
            msg.arbitration_id,
            bytes(msg.data),
            show_raw,
            show_unknown,
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Read Huawei R4850G2/R4875G1/R4875G5 telemetry and optionally write "
            "selected settings."
        )
    )
    parser.add_argument(
        "target",
        nargs="?",
        default="can0",
        help="SocketCAN interface name when using socketcan, default: can0",
    )
    parser.add_argument(
        "--backend",
        choices=("socketcan", "canalystii"),
        default="socketcan",
        help="CAN backend, default: socketcan",
    )
    parser.add_argument(
        "--channel",
        type=int,
        default=0,
        help="CANalyst-II channel number, default: 0",
    )
    parser.add_argument(
        "--device",
        type=int,
        default=0,
        help="CANalyst-II USB device index, default: 0",
    )
    parser.add_argument(
        "--bitrate",
        type=int,
        default=125000,
        help="CAN bitrate in bit/s, default: 125000",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Polling interval in seconds, default: 1.0",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Stop after this many seconds",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Print every received CAN frame",
    )
    parser.add_argument(
        "--unknown",
        action="store_true",
        help="Remember and print non-decoded frame IDs on exit",
    )
    parser.add_argument(
        "--set-output",
        choices=("on", "off"),
        help="Set PSU output on or off using the standby register",
    )
    parser.add_argument(
        "--set-stored-voltage",
        type=validate_stored_voltage,
        metavar="VOLTS",
        help=(
            "Set the stored default voltage used after CAN timeout or power cycle "
            f"({R4850G2_STORED_VOLTAGE_MIN_V:.1f}-{R4850G2_STORED_VOLTAGE_MAX_V:.1f} V)"
        ),
    )
    args = parser.parse_args()

    state = RectifierState()
    write_requests = build_write_requests(args)
    # Any write request switches the command into send/ACK/exit mode instead of telemetry polling.
    write_only = bool(write_requests)
    deadline = time.monotonic() + args.timeout if args.timeout else None
    next_query = 0.0
    running = True

    def stop(_signum: int, _frame: object) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    if args.backend == "socketcan":
        try:
            sock = open_can_socket(args.target)
        except OSError as exc:
            print(
                f"Failed to open SocketCAN interface {args.target!r}: {exc}",
                file=sys.stderr,
            )
            print(
                "Your CANalyst-II adapter is not a SocketCAN netdevice on this host. "
                "Either use a real SocketCAN interface, or install python-can and run "
                f"`python3 {sys.argv[0]} --backend canalystii --channel 0`.",
                file=sys.stderr,
            )
            return 1

        try:
            for request in write_requests:
                try:
                    send_frame(sock, REGISTER_SET_ID, request.payload)
                except OSError as exc:
                    print(
                        f"Failed to send {request.description}: {exc}",
                        file=sys.stderr,
                    )
                    return 1
                print(f"Sent {request.description}")

            if write_only:
                wait_for_socketcan_frames(
                    sock,
                    state,
                    show_raw=args.raw,
                    show_unknown=args.unknown,
                    duration=args.timeout or 1.0,
                )
                if args.unknown:
                    print_unknown_frames(state)
                return 0

            send_elabel_request(sock)
            while running:
                now = time.monotonic()
                if now >= next_query:
                    send_query(sock)
                    next_query = now + args.interval

                if deadline and now >= deadline:
                    break

                timeout = 0.2
                if deadline:
                    timeout = min(timeout, max(0.0, deadline - now))

                readable, _, _ = select.select([sock], [], [], timeout)
                if not readable:
                    continue

                frame = sock.recv(CAN_FRAME_SIZE)
                can_id, data = unpack_frame(frame)
                if handle_frame(state, can_id, data, args.raw, args.unknown):
                    print_summary(state)
                maybe_print_startup_info(state)
        finally:
            sock.close()
    else:
        try:
            bus = open_python_can_bus(
                args.backend,
                channel=args.channel,
                device=args.device,
                bitrate=args.bitrate,
            )
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            print(
                "This adapter shows up on your machine as USB VID:PID 04d8:0053 with no "
                "kernel driver, so `--backend canalystii` is the expected mode.",
                file=sys.stderr,
            )
            return 1

        try:
            for request in write_requests:
                try:
                    send_frame_python_can(bus, REGISTER_SET_ID, request.payload)
                except Exception as exc:  # pragma: no cover - backend-specific
                    print(
                        f"Failed to send {request.description}: {exc}",
                        file=sys.stderr,
                    )
                    return 1
                print(f"Sent {request.description}")

            if write_only:
                wait_for_python_can_frames(
                    bus,
                    state,
                    show_raw=args.raw,
                    show_unknown=args.unknown,
                    duration=args.timeout or 1.0,
                )
                if args.unknown:
                    print_unknown_frames(state)
                return 0

            send_elabel_request_python_can(bus)
            while running:
                now = time.monotonic()
                if now >= next_query:
                    send_query_python_can(bus)
                    next_query = now + args.interval

                if deadline and now >= deadline:
                    break

                timeout = 0.2
                if deadline:
                    timeout = min(timeout, max(0.0, deadline - now))

                msg = bus.recv(timeout=timeout)
                if msg is None or not msg.is_extended_id:
                    continue

                if handle_frame(
                    state,
                    msg.arbitration_id,
                    bytes(msg.data),
                    args.raw,
                    args.unknown,
                ):
                    print_summary(state)
                maybe_print_startup_info(state)
        finally:
            close_python_can_bus(bus)

    if args.unknown:
        print_unknown_frames(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
