from __future__ import annotations

import io
import unittest
from unittest.mock import patch

from sidepulse.cli import sidepulse_main
from sidepulse.update import update_installation


class UpdateTests(unittest.TestCase):
    """This fork publishes no build, so `update` must not install upstream's."""

    def test_update_runs_nothing_and_points_to_the_checkout_installer(self):
        with (
            patch('subprocess.run') as run,
            patch('sys.stderr', new_callable=io.StringIO) as stderr,
        ):
            self.assertEqual(update_installation(), 1)

        run.assert_not_called()
        self.assertIn('scripts/install-user.sh', stderr.getvalue())
        self.assertNotIn('sidepulse.io', stderr.getvalue())

    def test_dry_run_runs_nothing_either(self):
        with (
            patch('subprocess.run') as run,
            patch('sys.stderr', new_callable=io.StringIO),
        ):
            self.assertEqual(sidepulse_main(['update', '--dry-run']), 1)

        run.assert_not_called()

    def test_cli_dispatches_update(self):
        with patch('sidepulse.update.update_installation', return_value=7) as update:
            self.assertEqual(sidepulse_main(['update']), 7)
            update.assert_called_once_with(dry_run=False)
