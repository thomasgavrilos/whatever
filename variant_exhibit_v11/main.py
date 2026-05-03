#!/usr/bin/env python3
"""
main.py — Variant Security Drone Exhibit v11
Entry point. Wires all modules, starts threads, runs PyGame event loop.

Run
---
  sudo -E DISPLAY=:0 XAUTHORITY=/home/newlab/.Xauthority python3 main.py
"""

import logging
import sys
import os

from config import cfg
from exhibit_state import ExhibitState
from lighting_controller import LightingController
from motor_controller import MotorController
from sequence_runner import SequenceRunner
from touch_ui import TouchUI


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def setup_logging() -> None:
    """
    File handler (with /tmp fallback) + stream handler on stdout.
    Level and format defined in Config.
    """
    root_log = logging.getLogger()
    root_log.setLevel(cfg.LOG_LEVEL)
    fmt = logging.Formatter(fmt=cfg.LOG_FORMAT, datefmt=cfg.LOG_DATE_FORMAT)

    # File handler — try primary path, fall back to /tmp
    fh = None
    for path in (cfg.LOG_PATH, cfg.LOG_PATH_FALLBACK):
        try:
            fh = logging.FileHandler(path, encoding='utf-8')
            fh.setFormatter(fmt)
            root_log.addHandler(fh)
            break
        except (PermissionError, OSError):
            continue

    # Stream handler always present
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root_log.addHandler(sh)

    logging.getLogger(__name__).debug("Logging initialised — level=%s",
                                      logging.getLevelName(cfg.LOG_LEVEL))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    setup_logging()
    log = logging.getLogger(__name__)

    log.info("=" * 60)
    log.info("  VARIANT SECURITY DRONE EXHIBIT  v11")
    log.info("=" * 60)

    # Hide SDL debug noise on the DSI touchscreen
    os.environ.setdefault('SDL_VIDEO_ALLOW_SCREENSAVER', '0')
    os.environ.setdefault('SDL_MOUSE_TOUCH_EVENTS', '1')

    # ── Instantiate all layers ────────────────────────────────────────────────
    state  = ExhibitState()
    motors = MotorController()
    lights = LightingController()
    lights.set_state(state)
    seq    = SequenceRunner(state=state, motors=motors, lights=lights)

    # Wire LED observer: state transitions automatically call lights.set_mode()
    state.register_led_callback(lights.set_mode)

    # ── Initialise GPIO ───────────────────────────────────────────────────────
    try:
        motors.setup()
        motors.enable()
        motors.set_relay(True)   # button LED = green (system ready)
        log.info("Motors enabled")
    except PermissionError as exc:
        log.critical(
            "GPIO permission denied: %s\n"
            "  → Run with sudo, or: sudo usermod -aG gpio $USER && re-login", exc)
        sys.exit(1)
    except RuntimeError as exc:
        log.critical("GPIO hardware init failed: %s", exc)
        sys.exit(1)

    # Log pin assignments for diagnostics
    for m_id, pins in cfg.MOTORS.items():
        log.info("  Motor %d — DIR=GPIO%-2d PUL=GPIO%-2d ENA=GPIO%d",
                 m_id, pins.DIR, pins.PUL, pins.ENA)
    log.info("  LAUNCH   — GPIO%d", cfg.LAUNCH_BUTTON_GPIO)
    log.info("  Relay    — GPIO%d", cfg.RELAY_GPIO)
    log.info("  Lighting — %s @ %d baud", cfg.LIGHTING_PORTS, cfg.LIGHTING_BAUD)

    # ── Start button monitor daemon ───────────────────────────────────────────
    state.start_button_monitor(motors, seq)
    lights.set_mode('idle_pulse')

    log.info("-" * 60)
    log.info("  SYSTEM READY — press LAUNCH to begin")
    log.info("-" * 60)

    # ── Build UI and enter event loop ─────────────────────────────────────────
    ui = TouchUI(state=state, seq=seq)

    try:
        ui.run()

    except KeyboardInterrupt:
        log.info("KeyboardInterrupt received — beginning safe shutdown …")

    finally:
        # ── Safe shutdown ──────────────────────────────────────────────────────
        log.info("Shutdown: stopping all motion …")
        state.stop_motion = True          # signal any running sequence to stop
        state.subset_flight_stop.set()    # stop any subset flight

        # Wait briefly for sequences to notice the stop signal
        state.flight_exited.wait(timeout=2.0)
        state.subset_flight_exited.wait(timeout=2.0)
        state.height_adjust_exited.wait(timeout=2.0)

        log.info("Shutdown: returning motors to HOME …")
        try:
            motors.return_home(list(cfg.MOTORS.keys()), rpm=cfg.CRASH_PROTECTED_RPM)
        except Exception as exc:
            log.error("return_home failed during shutdown: %s", exc)

        log.info("Shutdown: disabling motors …")
        try:
            motors.disable()
            motors.set_relay(False)   # button LED off
        except Exception as exc:
            log.error("Motor disable error: %s", exc)

        log.info("Shutdown: closing lighting and GPIO …")
        lights.shutdown()
        motors.close()

        log.info("Shutdown complete — goodbye.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    main()
