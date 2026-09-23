# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`sidepulse` is the macOS/CLI companion for the SidePulse Pro (8-LED SD card) and
SidePulse Dot (2-LED USB-C) devices. The devices mount as disk volumes under
`/Volumes` and are driven entirely by writing a small DSL program to
`LEDS.LED` — there is no driver and no USB protocol. The main product is a
PyObjC menu-bar app that turns AI agent hook events (Codex, Claude, Grok,
Cursor, Junie) into a glanceable LED state.

The iOS app lives outside this repo. `links.py` pairs an iPhone and pushes LED
programs to it through the bridge; `relay.py` and `service.py` forward hook
events between machines through the same bridge.

## Commands

```sh
python3 -m pip install -e '.[test]'         # dev install
python3 -m pytest tests -q                  # full suite
python3 -m pytest tests/test_sidepulse.py -k aggregates_highest_priority -v  # single test
SIDEPULSE_SKIP_CLEAN_INSTALL=1 python3 -m pytest tests -q      # skip the ~15s venv builds
SIDEPULSE_REQUIRE_UI_TESTS=1 python3 -m pytest tests/test_status_bar_ui.py -v  # fail instead of skip without AppKit
```

Tests are `unittest.TestCase` classes run under pytest.

```sh
sidepulse setup                             # install hooks + LaunchAgents + eject guard
sidepulse agent-monitor doctor              # what hook config is actually on disk
sidepulse status-bar start --foreground     # run the menu-bar app attached to the terminal
sidepulse agent-monitor leds --once --dry-run  # print the LED program instead of writing it
./scripts/macos-sd-diagnostics.sh           # SD reader / mount troubleshooting
./scripts/install-user.sh                   # isolated venv install into ~/.local
```

Console scripts: `sidepulse` (main), `agent-monitor` (legacy name for the same
subcommands), `agent-status-bar`, `sidepulse-reply`.

## Architecture

**Event path.** Agent hook → `hook_entry.py` → `hook.hook_log_main()`, which
re-infers the real provider from the payload (Grok payloads arrive through
Claude's hook shape), annotates origin, pushes the event over a Unix socket to
the running status-bar app, appends the provider `*.jsonl` log, and appends
`event-status.jsonl` (the decision log: hook event → interpreted status).

**Two monitors, deliberately.** `LiveAgentMonitor` is push-fed by the socket and
is what the status bar uses; it keeps state in memory and persists only
`latest.json` for restart recovery. `AgentMonitor` scans files and is what the
CLI (`status`, `live`, `leds`) uses; it can optionally read Codex/Claude
transcripts as a recovery fallback. Both funnel through the same
`status_from_event` / `should_ignore_status_transition` logic in `collector.py`,
so status rules live there and not in either caller.

**Status pipeline.** `providers.parse_log_line` → `HookEvent` →
`collector.mode_for_event` → one of 8 `AgentMode`s → `aggregate_status` picks
the highest-priority non-stale mode via `MODE_PRIORITY` →
`led_status.display_state_for_mode` collapses that to 4 `LedDisplayState`s
(Idle/Working/Done/Ask) → `program_for_display_state` renders LEDS.LED text →
`device_writer` validates and writes. Adding a mode means touching all four
layers.

Explicit `<!-- sidepulse:ask|done|working|blocked|idle -->` markers in a final
assistant message beat every heuristic; text-based question detection is the
last resort and deliberately conservative.

**Layering rule.** The hook path (`hook_entry`, `hook`, `providers`,
`collector`, `audit`, `origin`, `ipc`, `jsonl_log`) is standard-library only and
must stay importable with PyObjC absent — a hook that raises breaks somebody's
agent session. `status_bar.py` and `virtual_device.py` are the AppKit-heavy
modules; `battery.py`, `lid_sleep.py`, `keep_awake.py` shell out to
`ioreg`/`pmset`/`caffeinate` rather than importing frameworks.

**LEDS.LED is the device contract.** The DSL is specified in `LEDS_FORMAT.md`;
the controller's 512-byte / 20-line limits are enforced in
`device_writer.validate_led_text`. `src/sidepulse/resources/sdled.wasm` is the same parser the
firmware and web simulator use — `led_wasm.py` runs it through JavaScriptCore so
the Screen Bar virtual device animates identical programs instead of
reimplementing the DSL.

**macOS system integration.** `status_bar_launch.py` writes the
`io.sidepulse.agentstatus` LaunchAgent (and cleans up the older
`com.pixiepulse.*` label). `sd_eject_guard_launch.py` compiles
`src/sidepulse/resources/sd_eject_guard.c` on demand and installs it as a per-user
LaunchAgent or a system LaunchDaemon (`--sd-eject-guard-scope`) to keep the SD
reader attached after sleep. `lid_sleep.py` installs
a narrow sudoers rule for exactly `/usr/bin/pmset -a disablesleep 0|1` so the
app can toggle closed-lid sleep without prompting.

**Paths.** Settings: `${XDG_CONFIG_HOME:-~/.config}/sidepulse/agent-monitor/settings.json`,
loaded as one `AgentMonitorSettings` dataclass. State/logs:
`${XDG_STATE_HOME:-~/.local/state}/sidepulse/agent-monitor/`.

`src/agent_monitor/` and `src/sidepulse_cli/` are compatibility shims for
pre-rename installs whose hook commands point at those paths — keep them
working.

## Invariants the test suite enforces

- **Declared imports.** Every module-level third-party import must resolve to a
  distribution declared in `pyproject.toml` (`tests/test_packaging.py`), and
  `tests/test_environment.py` re-checks it in a fresh venv built from
  `pyproject.toml` alone. A missing `pyobjc-framework-ScriptingBridge` shipping
  to users is why both files exist.
- **Hooks fail open.** Exit 0 and silent stdout for every input and every
  internal failure, with PyObjC unavailable. Installed commands are suffixed
  `; true` (`install.fail_open_command`).
- **Live selectors.** Every PyObjC selector string in a menu or window must
  resolve to a real method (`tests/test_status_bar_ui.py`) — menus that build
  but crash on click are otherwise invisible.
- **Log hygiene.** The JSONL logs under the state dir hold verbatim hook
  payloads, meaning tool inputs and file contents. They go through
  `jsonl_log.append_jsonl_line`, which keeps them `0600` and rotates them at a
  size cap; failing to do either must still leave the hook exiting 0. New log
  writers belong on that helper, not on a bare `open(..., "a")`.
- **The git tag is the version.** `pyproject.toml` declares the version dynamic
  (setuptools-scm, `fallback_version = "0"`) and `sidepulse.__version__` reads
  the installed distribution metadata. Do not hardcode a version in either file.

## Releasing

Versions are calendar versions of the form `1.YYYYMMDD.SECONDS`.
`scripts/release.sh` builds and verifies the release files locally; it does not
tag or publish. It needs Python 3.10 or later: set `PYTHON_BIN` when the
`python3` on `PATH` is older. Pushing a `v<version>` tag gates on the full suite,
then publishes and reinstalls the artifact **from PyPI** on clean macOS and Linux
runners to re-run the clean-room tests against what users download. Point those
tests at any build with `SIDEPULSE_INSTALL_SPEC='sidepulse==<version>'`.

Signed/notarized PKG: `packaging/build_macos_pkg.sh` (see `packaging/README.md`;
`ALLOW_UNSIGNED=1` for local verification only).
