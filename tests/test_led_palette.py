from __future__ import annotations

import unittest

from scripts import led_palette as palette
from sidepulse.device_writer import validate_led_text
from sidepulse.led_slots import BAND_ALERT, BAND_DONE, BAND_WORKING


class RampTests(unittest.TestCase):
    def test_a_ramp_runs_from_the_first_colour_to_the_last(self) -> None:
        colours = palette.ramp("#000000", "#FF1000", 8)

        self.assertEqual(len(colours), 8)
        self.assertEqual(colours[0], "#000000")
        self.assertEqual(colours[-1], "#FF1000")
        reds = [int(colour[1:3], 16) for colour in colours]
        self.assertEqual(reds, sorted(reds))

    def test_a_two_led_ramp_is_just_the_ends(self) -> None:
        self.assertEqual(palette.ramp("#102030", "#405060", 2), ["#102030", "#405060"])


class HueTests(unittest.TestCase):
    def test_a_hue_sweep_hits_the_primaries_at_full_value(self) -> None:
        self.assertEqual(
            palette.hues(0, 240, 3, value=100),
            ["#FF0000", "#00FF00", "#0000FF"],
        )

    def test_value_scales_every_channel(self) -> None:
        self.assertEqual(palette.hues(0, 0, 1, value=50), ["#800000"])


class BandTests(unittest.TestCase):
    def test_bands_show_every_settled_and_peak_colour_in_order(self) -> None:
        self.assertEqual(
            palette.band_colours(8),
            [
                BAND_ALERT.steady,
                BAND_ALERT.flash_peak,
                BAND_WORKING.steady,
                BAND_DONE.steady,
                BAND_DONE.flash_peak,
                "#000000",
                "#000000",
                "#000000",
            ],
        )

    def test_bands_truncate_to_a_two_led_dot(self) -> None:
        self.assertEqual(
            palette.band_colours(2), [BAND_ALERT.steady, BAND_ALERT.flash_peak]
        )


class CommandTests(unittest.TestCase):
    def test_set_pads_with_off_and_normalizes_hex(self) -> None:
        self.assertEqual(
            palette.colours_for_command("set ff0000 #00ff00", 4),
            ["#FF0000", "#00FF00", "#000000", "#000000"],
        )

    def test_ramp_command(self) -> None:
        self.assertEqual(
            palette.colours_for_command("ramp #000000 #FF0000", 2),
            ["#000000", "#FF0000"],
        )

    def test_hues_command_takes_an_optional_value(self) -> None:
        self.assertEqual(palette.colours_for_command("hues 0 0 50", 1), ["#800000"])

    def test_a_bad_command_raises_with_usage(self) -> None:
        for command in ("nope", "ramp #000000", "set #GG0000", "hues red blue"):
            with self.subTest(command=command):
                with self.assertRaises(ValueError):
                    palette.colours_for_command(command, 8)

    def test_the_program_is_one_indexed_line_the_device_accepts(self) -> None:
        program = palette.program_for(["#FF0000", "#000000"])

        validate_led_text(program)
        self.assertEqual(program, "0:#FF0000 1:#000000")


if __name__ == "__main__":
    unittest.main()
