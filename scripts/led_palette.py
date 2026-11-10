#!/usr/bin/env python3
"""Show colour palettes across a SidePulse strip, for tuning the LED bands by eye.

    python3 scripts/led_palette.py                  # start on the current bands
    python3 scripts/led_palette.py ramp '#000000' '#FF1000'

Commands at the prompt:

    bands                 settled and peak colour of every band, side by side
    ramp FROM TO          blend between two hex colours across the strip
    hues H1 H2 [VALUE]    hue sweep in degrees at full saturation, VALUE in %
    set HEX [HEX ...]     explicit colours from LED 0; the rest turn off
    q                     quit and leave the last palette on the strip

Stop the menu-bar app first, or its next refresh overwrites the palette. The
script touches `keepalive` every 30 s while it runs, because the MacBook SD
reader powers the device off after three minutes without file activity.
"""

from __future__ import annotations

import argparse
import colorsys
import re
import shlex
import sys
import threading
from pathlib import Path

from sidepulse.device_writer import DeviceWriteError, resolve_target_path, write_led_program
from sidepulse.led_slots import BAND_ALERT, BAND_DONE, BAND_WORKING, SLOT_EMPTY
from sidepulse.led_status import led_count_for_target

KEEPALIVE_SECONDS = 30.0
HEX_COLOUR = re.compile(r"#?([0-9A-Fa-f]{6})")
USAGE = "commands: bands | ramp FROM TO | hues H1 H2 [VALUE] | set HEX [HEX ...] | q"


def parse_hex(text: str) -> str:
    match = HEX_COLOUR.fullmatch(text)
    if match is None:
        raise ValueError(f"not a hex colour: {text!r}")
    return "#" + match.group(1).upper()


def channels(colour: str) -> tuple[int, int, int]:
    return tuple(int(colour[index:index + 2], 16) for index in (1, 3, 5))


def to_hex(red: float, green: float, blue: float) -> str:
    return "#" + "".join(f"{round(channel):02X}" for channel in (red, green, blue))


def ramp(start: str, end: str, count: int) -> list[str]:
    if count == 1:
        return [parse_hex(start)]
    first, last = channels(parse_hex(start)), channels(parse_hex(end))
    return [
        to_hex(*(a + (b - a) * step / (count - 1) for a, b in zip(first, last)))
        for step in range(count)
    ]


def hues(first: float, last: float, count: int, *, value: float = 100.0) -> list[str]:
    degrees = [first] if count == 1 else [
        first + (last - first) * step / (count - 1) for step in range(count)
    ]
    return [
        to_hex(*(255 * channel for channel in colorsys.hsv_to_rgb((hue % 360) / 360, 1.0, value / 100)))
        for hue in degrees
    ]


def band_colours(count: int) -> list[str]:
    colours = [
        BAND_ALERT.steady,
        BAND_ALERT.flash_peak,
        BAND_WORKING.steady,
        BAND_DONE.steady,
        BAND_DONE.flash_peak,
    ]
    return (colours + [SLOT_EMPTY] * count)[:count]


def colours_for_command(command: str, count: int) -> list[str]:
    words = shlex.split(command)
    if not words:
        raise ValueError(USAGE)
    name, args = words[0].lower(), words[1:]
    try:
        if name == "bands" and not args:
            return band_colours(count)
        if name == "ramp" and len(args) == 2:
            return ramp(args[0], args[1], count)
        if name == "hues" and len(args) in (2, 3):
            value = float(args[2]) if len(args) == 3 else 100.0
            return hues(float(args[0]), float(args[1]), count, value=value)
        if name == "set" and 1 <= len(args) <= count:
            return ([parse_hex(arg) for arg in args] + [SLOT_EMPTY] * count)[:count]
    except ValueError as exc:
        raise ValueError(f"{exc}\n{USAGE}") from None
    raise ValueError(USAGE)


def program_for(colours: list[str]) -> str:
    return " ".join(f"{index}:{colour}" for index, colour in enumerate(colours))


def swatch_table(colours: list[str]) -> str:
    rows = []
    for index, colour in enumerate(colours):
        red, green, blue = channels(colour)
        rows.append(f"  {index}  \x1b[48;2;{red};{green};{blue}m      \x1b[0m  {colour}")
    return "\n".join(rows)


def keep_device_awake(root: Path, stop: threading.Event) -> None:
    while not stop.wait(KEEPALIVE_SECONDS):
        try:
            (root / "keepalive").touch()
        except OSError:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--device", type=Path, help="Mounted SidePulse drive.")
    parser.add_argument("command", nargs="*", help="First command to show; default: bands.")
    args = parser.parse_args(argv)

    try:
        target = resolve_target_path(device_path=args.device)
    except DeviceWriteError as exc:
        print(exc, file=sys.stderr)
        return 1
    count = led_count_for_target(target)
    print(f"{target.parent}: {count} LEDs\n{USAGE}")

    stop = threading.Event()
    threading.Thread(target=keep_device_awake, args=(target.parent, stop), daemon=True).start()
    command = shlex.join(args.command) if args.command else "bands"
    try:
        while command.strip().lower() not in ("q", "quit", "exit"):
            try:
                colours = colours_for_command(command, count)
                write_led_program(program_for(colours), device_path=target)
                print(swatch_table(colours))
            except (ValueError, DeviceWriteError, OSError) as exc:
                print(exc, file=sys.stderr)
            command = input("> ")
    except (EOFError, KeyboardInterrupt):
        print()
    finally:
        stop.set()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
