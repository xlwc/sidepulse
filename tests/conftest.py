from __future__ import annotations

import sys


def pytest_configure(config) -> None:
    # Homebrew's framework Python runs as Python.app, so the first AppKit
    # object a test creates gives the test process a bouncing Dock icon. The
    # status bar avoids that on launch with the same accessory policy.
    if sys.platform != "darwin":
        return
    try:
        from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
    except ImportError:
        return
    NSApplication.sharedApplication().setActivationPolicy_(
        NSApplicationActivationPolicyAccessory
    )
