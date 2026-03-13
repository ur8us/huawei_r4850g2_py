# huawei_r4850g2_py

This repository contains `huawei_r4850g2_py.py`, a small Python program for reading live telemetry from Huawei `R4850G2` and `R4875G1` power supplies over CAN bus and, when explicitly requested, writing a limited set of settings.

The script polls the PSU using the documented Huawei CAN protocol and prints decoded values such as:

- Input voltage
- Input current
- Input frequency
- Input power
- Output voltage
- Output current
- Output power
- Input temperature
- Output temperature
- Efficiency
- Current limit
- Output enabled state
- Description text
- Approximate accumulated amp-hours

It can also send selected write commands from the command line:

- Output `on` / `off`
- Stored default voltage

The implementation is based on the protocol documentation and examples from:

- https://github.com/craigpeacock/Huawei_R4850G2_CAN

## Files

- `huawei_r4850g2_py.py`: main telemetry reader script
- `read.sh`: run the default CANalyst-II telemetry read command
- `on.sh`: turn PSU output on
- `off.sh`: turn PSU output off
- `set-58v.sh`: set stored default voltage to `58.0` V
- `set-50v.sh`: set stored default voltage to `50.0` V
- `requirements.txt`: Python dependencies
- `99-canalystii.rules`: udev rule for USB access to CANalyst-II adapters on Linux
- `.gitignore`: ignores local Python and editor artifacts
- `AGENTS.md`: instructions for coding agents working on this repository

## Supported CAN Backends

The program supports two ways of talking to the CAN bus:

- `socketcan`: for Linux CAN interfaces such as `can0`
- `canalystii`: for USB adapters like `04d8:0053 Microchip Technology, Inc. Chuangxin Tech USBCAN/CANalyst-II`

## Requirements

- Linux
- Python 3.10 or newer
- CAN bus bitrate set to `125000`
- A Huawei `R4850G2` or `R4875G1` connected to the CAN bus
- One of:
  - a working SocketCAN interface
  - a CANalyst-II compatible USB adapter

## Setup

### 1. Install Python dependencies

```bash
python3 -m pip install -r requirements.txt
```

### 2. If you use a CANalyst-II USB adapter

Install the provided udev rule so the adapter can be accessed without running the script as root:

```bash
sudo cp 99-canalystii.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger
```

After that, unplug and reconnect the adapter.

For the adapter used during development, Linux detected:

```text
04d8:0053 Microchip Technology, Inc. Chuangxin Tech USBCAN/CANalyst-II
```

This device is not exposed as a native `SocketCAN` interface on this host, so `--backend canalystii` is the expected mode.

### 3. If you use SocketCAN

Bring the interface up at the correct bitrate:

```bash
sudo ip link set can0 up type can bitrate 125000
```

## Usage

### CANalyst-II

Run on channel 0:

```bash
python3 huawei_r4850g2_py.py --backend canalystii --channel 0 --bitrate 125000
```

If channel 0 is quiet, try channel 1:

```bash
python3 huawei_r4850g2_py.py --backend canalystii --channel 1 --bitrate 125000
```

For a quick probe, run with a timeout:

```bash
python3 huawei_r4850g2_py.py --backend canalystii --channel 0 --bitrate 125000 --timeout 10
```

### SocketCAN

Run with `can0`:

```bash
python3 huawei_r4850g2_py.py can0
```

## Write Commands

These commands change PSU behavior. Use them carefully.

When any write option is used, the script sends the requested command, waits briefly for write acknowledgements, and exits without polling or printing live telemetry status.

Turn the output off:

```bash
python3 huawei_r4850g2_py.py --backend canalystii --channel 0 --bitrate 125000 --set-output off --timeout 3
```

Turn the output on:

```bash
python3 huawei_r4850g2_py.py --backend canalystii --channel 0 --bitrate 125000 --set-output on --timeout 3
```

Set the immediate output voltage and stored default voltage to the same value:

```bash
python3 huawei_r4850g2_py.py --backend canalystii --channel 0 --bitrate 125000 --set-stored-voltage 53.5 --timeout 3
```

You can combine write options in one command:

```bash
python3 huawei_r4850g2_py.py --backend canalystii --channel 0 --bitrate 125000 --set-output on --set-stored-voltage 53.5 --timeout 3
```

## Helper Scripts

`read.sh` starts the normal telemetry reader. The write helper scripts send one command, wait briefly for acknowledgements, and exit without printing live telemetry status.

Run the default telemetry reader:

```bash
./read.sh
```

Turn the output on:

```bash
./on.sh
```

Turn the output off:

```bash
./off.sh
```

Set the immediate output voltage and stored default voltage to `58.0` V:

```bash
./set-58v.sh
```

Set the immediate output voltage and stored default voltage to `50.0` V:

```bash
./set-50v.sh
```

## Useful Options

Show help:

```bash
python3 huawei_r4850g2_py.py --help
```

Poll once per second:

```bash
python3 huawei_r4850g2_py.py --backend canalystii --channel 0 --interval 1.0
```

Stop after 10 seconds:

```bash
python3 huawei_r4850g2_py.py --backend canalystii --channel 0 --timeout 10
```

Print every raw CAN frame:

```bash
python3 huawei_r4850g2_py.py --backend canalystii --channel 0 --raw
```

Record undecoded frames:

```bash
python3 huawei_r4850g2_py.py --backend canalystii --channel 0 --unknown
```

## Example Output

```text
Input:  230.10 V  50.02 Hz  11.40 A  2622.00 W
Output: 53.40 V  45.80 A of 50.00 A  2445.00 W
Temps:  in 28.00 C  out 34.00 C
Limit:  100.00 %  Efficiency: 93.20 %  Ah: 0.512
State:  enabled
```

## Troubleshooting

### `Cannot find device "can0"`

Your adapter is not exposed as a SocketCAN interface. Use:

```bash
python3 huawei_r4850g2_py.py --backend canalystii --channel 0 --bitrate 125000
```

### `Access denied (insufficient permissions)`

The USB device permissions are too restrictive. Install `99-canalystii.rules`, reload udev, and reconnect the adapter.

If needed, use these exact commands:

```bash
sudo cp 99-canalystii.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger
```

### No frames received

- Confirm the PSU is powered
- Confirm CAN H and CAN L are wired correctly
- Confirm the bitrate is `125000`
- Try the other CANalyst-II channel
- Use `--raw --unknown` to inspect bus traffic

## Notes

- Write support is limited to output on/off and stored default voltage
- Setting stored voltage also sets the immediate output voltage to the same value
- The write helper scripts are one-shot commands and do not print the current PSU telemetry state
- Stored voltage is validated to `48.0` to `58.5` V before transmission
- Output on/off is sent through the Huawei standby register used by the wider R48xx family protocol documentation
- The script was validated for syntax and CLI behavior locally
- Direct runtime testing against the USB adapter and PSU was not performed here

Everything was created with the help of GPT-5.4.
