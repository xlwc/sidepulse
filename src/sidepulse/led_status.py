from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Iterable, Sequence

from .device_writer import (
    DEFAULT_FILE_NAME,
    DeviceWriteError,
    resolve_target_path,
    write_led_program,
)
from .led_slots import SLOT_EMPTY, Slot, SlotAllocator
from .models import AgentMode, AgentStatus


class LedDisplayState(str, Enum):
    IDLE = "idle"
    WORKING = "working"
    DONE = "done"
    ASK = "ask"


LED_STATE_LABELS: dict[LedDisplayState, str] = {
    LedDisplayState.IDLE: "Idle",
    LedDisplayState.WORKING: "Working",
    LedDisplayState.DONE: "Done",
    LedDisplayState.ASK: "Ask",
}


ASK_AMBER = "#FF3A00"
WORKING_CYAN = "#00E5FF"
DONE_GREEN = "#00FF66"
IDLE_DIM = "#020204"
DEVICE_LED_COUNTS = {
    "sidepulsedot": 2,
    "sidepulsepro": 8,
}


@dataclass(frozen=True)
class LedStatusWrite:
    state: LedDisplayState
    target: Path | None
    program: str
    changed: bool
    error: str | None = None

    @property
    def label(self) -> str:
        return LED_STATE_LABELS[self.state]


def display_state_for_mode(mode: AgentMode) -> LedDisplayState:
    if mode in {AgentMode.WAITING_FOR_INPUT, AgentMode.BLOCKED_ERROR}:
        return LedDisplayState.ASK
    if mode in {
        AgentMode.WORKING,
        AgentMode.TOOL_RUNNING,
        AgentMode.LONG_TASK_PROGRESS,
    }:
        return LedDisplayState.WORKING
    if mode == AgentMode.COMPLETED:
        return LedDisplayState.DONE
    return LedDisplayState.IDLE


def program_for_display_state(
    state: LedDisplayState,
    *,
    led_count: int = 8,
    brightness: int | float = 255,
) -> str:
    if state == LedDisplayState.IDLE:
        return apply_brightness(
            "\n".join(
                [
                    "off",
                    f"{IDLE_DIM} 6s pulse",
                    "repeat",
                ]
            ),
            brightness,
        )
    if state == LedDisplayState.ASK:
        return apply_brightness(
            "\n".join(
                [
                    "off",
                    f"{ASK_AMBER} 1.6s pulse",
                    "repeat",
                ]
            ),
            brightness,
        )
    if state == LedDisplayState.DONE:
        return apply_brightness(DONE_GREEN, brightness)
    if state == LedDisplayState.WORKING:
        return apply_brightness(rolling_program(WORKING_CYAN, led_count=led_count), brightness)
    raise ValueError(f"Unknown LED display state: {state}")


def rolling_program(color: str, *, led_count: int = 8) -> str:
    count = max(2, min(8, int(led_count)))
    delay_ms = 260 if count == 2 else 95
    duration_ms = 760
    segments: list[str] = []
    for active_index in range(count):
        delay = active_index * delay_ms
        segments.append(f"{active_index}:{color} {duration_ms}ms pulse {delay}ms")
    return "\n".join(
        [
            "off 160ms cosine",
            "; ".join(segments),
            "repeat",
        ]
    )


def write_mode_to_leds(
    mode: AgentMode,
    *,
    device_path: Path | None = None,
    file_name: str = DEFAULT_FILE_NAME,
    dry_run: bool = False,
    brightness: int | float = 255,
) -> LedStatusWrite:
    target = resolve_target_path(device_path=device_path, file_name=file_name)
    state = display_state_for_mode(mode)
    program = program_for_display_state(
        state,
        led_count=led_count_for_target(target),
        brightness=brightness,
    )
    written_target = write_led_program(
        program,
        device_path=target,
        file_name=file_name,
        dry_run=dry_run,
    )
    return LedStatusWrite(
        state=state,
        target=written_target,
        program=program,
        changed=True,
    )


def led_count_for_target(target: Path) -> int:
    name = normalized_device_name(target.parent.name)
    for hint, led_count in DEVICE_LED_COUNTS.items():
        if hint in name:
            return led_count
    return 8


def normalized_device_name(name: str) -> str:
    return "".join(char for char in name.lower() if char.isalnum())


def normalize_brightness(value: int | float | None) -> int:
    if value is None:
        return 255
    return max(0, min(255, int(round(float(value)))))


def brightness_percent(value: int | float | None) -> int:
    return round(normalize_brightness(value) / 255 * 100)


def apply_brightness(program: str, brightness: int | float = 255) -> str:
    value = normalize_brightness(brightness)
    if value >= 255:
        return program
    return f"brightness {value}\n{program}"


# --- multiagent slot rendering -------------------------------------------------
#
# The strip becomes N independent slots instead of one aggregate indicator. Line
# 1 is every slot's steady colour; line 2 dips only the recently-changed slots to
# black and back. `pulse` returns to the line's start colour, so each cycle is a
# blink against the baseline, and slots the flash line does not mention hold
# rock steady. After the repeat count is exhausted the device holds its final
# state, which is the baseline -- i.e. it settles to steady on its own.

SLOT_BRIGHTNESS = 128
SLOT_FLASH_PULSE_MS = 900
# Measured against resources/sdled.wasm, not derived from the pulse duration: a
# `900ms pulse` blink completes every 917ms. Sampling `repeat 1|2|3` put the last
# pixel movement at 912ms, 1829ms and 2746ms.
SLOT_FLASH_CYCLE_MS = 917
# `repeat 0` is rejected with bad-repeat and `repeat 65536` overflows, so the
# count is clamped rather than merely computed.
SLOT_MIN_REPEAT = 1
SLOT_MAX_REPEAT = 65535


def slot_repeat_count(slots: Sequence[Slot]) -> int:
    """Repeat cycles covering the longest remaining flash on the strip.

    `repeat N` is program-global, so per-slot five-minute timers are not
    expressible in one program. Sizing N to the longest remaining flash means
    nothing stops blinking early, and as each slot individually crosses its own
    five-minute mark the next refresh drops it from the flash line. It also
    fails well: if the app dies, the device stops flashing on its own instead of
    blinking forever.
    """
    longest_ms = max(
        (slot.remaining_flash_seconds * 1000.0 for slot in slots if slot.flashing),
        default=0.0,
    )
    cycles = math.ceil(longest_ms / SLOT_FLASH_CYCLE_MS)
    return max(SLOT_MIN_REPEAT, min(SLOT_MAX_REPEAT, cycles))


def program_for_slots(
    slots: Sequence[Slot],
    *,
    brightness: int | float = SLOT_BRIGHTNESS,
) -> str:
    if not slots:
        raise ValueError("Cannot render an empty slot vector.")

    # Indexed segments need an explicit colour; `0:off` is rejected as bad-index.
    baseline = " ".join(
        f"{index}:{slot.color}" for index, slot in enumerate(slots)
    )
    lines = [baseline]

    flashing = [index for index, slot in enumerate(slots) if slot.flashing]
    if flashing:
        lines.append(
            "; ".join(
                f"{index}:{SLOT_EMPTY} {SLOT_FLASH_PULSE_MS}ms pulse"
                for index in flashing
            )
        )
        lines.append(f"repeat {slot_repeat_count(slots)}")

    return apply_brightness("\n".join(lines), brightness)


def slot_render_key(
    slots: Sequence[Slot], brightness: int | float
) -> tuple[object, ...]:
    """Dedupe key for the rendered strip.

    Deliberately excludes the repeat count. Remaining flash time falls on every
    refresh, so keying on it -- or on the program text, which contains it --
    would rewrite LEDS.LED every 15s forever.
    """
    return (normalize_brightness(brightness),) + tuple(
        (slot.color, slot.flashing) for slot in slots
    )


@dataclass(frozen=True)
class MultiAgentLedWrite:
    target: Path | None
    program: str
    changed: bool
    slots: tuple[Slot, ...] = ()
    error: str | None = None


class MultiAgentLedController:
    def __init__(
        self,
        *,
        device_path: Path | None = None,
        file_name: str = DEFAULT_FILE_NAME,
        dry_run: bool = False,
        error_retry_seconds: float = 10.0,
        brightness: int | float = SLOT_BRIGHTNESS,
        rng: random.Random | None = None,
    ) -> None:
        self.device_path = device_path
        self.file_name = file_name
        self.dry_run = dry_run
        self.error_retry_seconds = error_retry_seconds
        self.brightness = normalize_brightness(brightness)
        self._rng = rng
        self.allocator: SlotAllocator | None = None
        self.last_key: tuple[object, ...] | None = None
        self.last_error: str | None = None
        self.last_target: Path | None = None
        self.last_attempt_monotonic = 0.0
        self.last_error_was_lookup = False

    def reset(self) -> None:
        """Forget what the strip is believed to show, not who is standing where.

        Callers reach this on a battery preview, a brightness edit, a lid
        animation and every ``/Volumes`` signature change -- routine events, none
        of which mean the sessions moved. Clearing the allocator here would
        reshuffle every position on all of them, and sticky position is the only
        reason placement is random in the first place. The slot map needs no
        explicit clearing anyway: ``SlotAllocator.update`` releases any occupant
        missing from the incoming set, so it cleans itself.
        """
        self.last_key = None
        self.last_error = None
        self.last_target = None
        self.last_attempt_monotonic = 0.0
        self.last_error_was_lookup = False

    def sync_statuses(
        self,
        statuses: Iterable[AgentStatus],
        *,
        now: datetime | None = None,
    ) -> MultiAgentLedWrite:
        moment = now or datetime.now(timezone.utc)
        monotonic_now = time.monotonic()

        # Backoff for the device lookup, separately from the write below. The
        # lookup has no dedupe key to gate on -- the key needs a slot count, which
        # needs the target -- so a failure here cannot fall through to the
        # `error_retry_seconds` gate the way a write failure does. Without this,
        # `leds --display multiagent` with nothing mounted re-ran
        # `discover_devices()` on every tick.
        if (
            self.last_error_was_lookup
            and monotonic_now - self.last_attempt_monotonic < self.error_retry_seconds
        ):
            return MultiAgentLedWrite(
                target=self.last_target,
                program="",
                changed=False,
                error=self.last_error,
            )

        try:
            target = resolve_target_path(
                device_path=self.device_path, file_name=self.file_name
            )
        except (DeviceWriteError, OSError) as exc:
            self.last_attempt_monotonic = monotonic_now
            self.last_error_was_lookup = True
            self.last_error = str(exc)
            return MultiAgentLedWrite(
                target=self.last_target,
                program="",
                changed=False,
                error=self.last_error,
            )

        self.last_error_was_lookup = False

        slot_count = led_count_for_target(target)
        if self.allocator is None or self.allocator.slot_count != slot_count:
            self.allocator = SlotAllocator(slot_count=slot_count, rng=self._rng)

        slots = self.allocator.update(statuses, moment)
        key = slot_render_key(slots, self.brightness)

        if key == self.last_key and self.last_error is None:
            return MultiAgentLedWrite(
                target=self.last_target,
                program="",
                changed=False,
                slots=slots,
            )
        if (
            key == self.last_key
            and self.last_error is not None
            and monotonic_now - self.last_attempt_monotonic < self.error_retry_seconds
        ):
            return MultiAgentLedWrite(
                target=self.last_target,
                program="",
                changed=False,
                slots=slots,
                error=self.last_error,
            )

        program = program_for_slots(slots, brightness=self.brightness)
        self.last_attempt_monotonic = monotonic_now
        try:
            written_target = write_led_program(
                program,
                device_path=target,
                file_name=self.file_name,
                dry_run=self.dry_run,
            )
        except (DeviceWriteError, OSError) as exc:
            self.last_key = key
            self.last_error = str(exc)
            return MultiAgentLedWrite(
                target=self.last_target,
                program="",
                changed=False,
                slots=slots,
                error=self.last_error,
            )

        self.last_key = key
        self.last_error = None
        self.last_target = written_target
        return MultiAgentLedWrite(
            target=written_target,
            program=program,
            changed=True,
            slots=slots,
        )


class AgentLedController:
    def __init__(
        self,
        *,
        device_path: Path | None = None,
        file_name: str = DEFAULT_FILE_NAME,
        dry_run: bool = False,
        error_retry_seconds: float = 10.0,
        brightness: int | float = 255,
    ) -> None:
        self.device_path = device_path
        self.file_name = file_name
        self.dry_run = dry_run
        self.error_retry_seconds = error_retry_seconds
        self.brightness = normalize_brightness(brightness)
        self.last_state: LedDisplayState | None = None
        self.last_brightness: int | None = None
        self.last_error: str | None = None
        self.last_target: Path | None = None
        self.last_attempt_monotonic = 0.0

    def reset(self) -> None:
        self.last_state = None
        self.last_brightness = None
        self.last_error = None
        self.last_target = None
        self.last_attempt_monotonic = 0.0

    def sync_mode(self, mode: AgentMode) -> LedStatusWrite:
        state = display_state_for_mode(mode)
        brightness = normalize_brightness(self.brightness)
        now = time.monotonic()
        if state == self.last_state and brightness == self.last_brightness and self.last_error is None:
            return LedStatusWrite(
                state=state,
                target=self.last_target,
                program="",
                changed=False,
            )
        if (
            state == self.last_state
            and brightness == self.last_brightness
            and self.last_error is not None
            and now - self.last_attempt_monotonic < self.error_retry_seconds
        ):
            return LedStatusWrite(
                state=state,
                target=self.last_target,
                program="",
                changed=False,
                error=self.last_error,
            )

        self.last_attempt_monotonic = now
        try:
            result = write_mode_to_leds(
                mode,
                device_path=self.device_path,
                file_name=self.file_name,
                dry_run=self.dry_run,
                brightness=brightness,
            )
        except (DeviceWriteError, OSError) as exc:
            self.last_state = state
            self.last_brightness = brightness
            self.last_error = str(exc)
            return LedStatusWrite(
                state=state,
                target=self.last_target,
                program="",
                changed=False,
                error=self.last_error,
            )

        self.last_state = state
        self.last_brightness = brightness
        self.last_error = None
        self.last_target = result.target
        return result
