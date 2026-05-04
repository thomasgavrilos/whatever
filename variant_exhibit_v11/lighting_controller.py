#!/usr/bin/env python3
"""
lighting_controller.py — LightingController
Serial bridge to the XIAO ESP32C3 driving 144 WS2812B LEDs in 4 sections
(36 LEDs per drone) via FastLED.

Protocol
--------
Each command is a single newline-terminated line:

    MODE:P1P2P3P4\n

  MODE = one of IDLE, LAUNCH, FLY, SHIELD, VIRUS, CRASH, OFF
  P1-P4 = per-drone role character:
      I = idle (blue pulse)
      L = launch (white/green chase upward)
      F = fly (green gentle pulse)
      S = shield (cyan steady glow — this drone is protected)
      V = virus (red strobe — this drone is infected)
      C = crash (red fade-out — this drone is returning home)
      O = off (all black)

Example:  "VIRUS:FFSV\n"
  → drone 1 fly, drone 2 fly, drone 3 shield, drone 4 virus

The MODE prefix tells the ESP32 about the global exhibit phase so it can
choose base timing parameters (strobe speed, chase speed, etc).  The
per-drone roles let each of the four 36-LED sections run its own effect.

set_mode() maps the legacy mode strings from ExhibitState into this
new protocol, using the current protected_drones set to assign roles.
"""

import logging
import threading
from typing import TYPE_CHECKING

from config import cfg

if TYPE_CHECKING:
    from exhibit_state import ExhibitState

log = logging.getLogger(__name__)

try:
    import serial
    _SERIAL_AVAILABLE = True
except ImportError:
    _SERIAL_AVAILABLE = False
    log.warning("pyserial not installed — LED control disabled. Run: pip install pyserial")


class LightingController:
    """
    Per-drone LED controller over USB serial to ESP32C3.

    The controller is state-aware: it holds a reference to ExhibitState so
    that set_mode() can read protected_drones and build the correct
    per-section role string without the caller needing to pass drone details.
    """

    _DRONE_IDS = (1, 2, 3, 4)

    def __init__(self) -> None:
        self._port = None
        self._lock = threading.Lock()
        self._last: str | None = None
        self._state: "ExhibitState | None" = None

        if _SERIAL_AVAILABLE:
            self._connect()
        else:
            log.warning("LightingController: running in stub mode (pyserial missing)")

    def set_state(self, state: "ExhibitState") -> None:
        """Wire in the ExhibitState reference. Called once by main()."""
        self._state = state

    def _connect(self) -> None:
        """Try each port in cfg.LIGHTING_PORTS. Logs result; never raises."""
        for port_name in cfg.LIGHTING_PORTS:
            try:
                self._port = serial.Serial(port_name, cfg.LIGHTING_BAUD, timeout=1)
                log.info("✓ Lighting on %s @ %d baud", port_name, cfg.LIGHTING_BAUD)
                return
            except Exception as exc:
                log.debug("Port %s unavailable: %s", port_name, exc)

        log.warning(
            "XIAO not found on %s — LEDs disabled. "
            "Check USB-C; run: sudo usermod -aG dialout $USER",
            cfg.LIGHTING_PORTS,
        )

    def send(self, command: str) -> None:
        """
        Write *command*\\n to serial. Thread-safe. Deduplicates consecutive sends.
        Serial errors are caught and logged — never raised.
        """
        with self._lock:
            if command == self._last:
                return
            self._last = command

            if self._port and self._port.is_open:
                try:
                    self._port.write(f"{command}\n".encode())
                    self._port.flush()
                    log.debug("LED → %s", command)
                except Exception as exc:
                    log.warning("LED serial error: %s", exc)
            else:
                log.debug("LED stub → %s", command)

    def set_mode(self, mode: str) -> None:
        """
        Build a per-drone protocol command from the legacy mode string.

        Uses self._state.protected_drones to determine which drones are
        shielded vs infected during virus-related modes.  Falls back to
        uniform roles when state is unavailable.
        """
        protected = set()
        if self._state is not None:
            protected = self._state.protected_drones

        command = self._build_command(mode, protected)
        self.send(command)

    def _build_command(self, mode: str, protected: set[int]) -> str:
        """Map a legacy mode string + protection set to a protocol command."""

        if mode in ('off',):
            return 'OFF:' + self._roles('O', 'O', protected)

        if mode == 'idle_pulse':
            return 'IDLE:' + self._roles('I', 'I', protected)

        if mode in ('launch_slow', 'launch_fast'):
            return 'LAUNCH:' + self._roles('L', 'L', protected)

        if mode == 'flight':
            return 'FLY:' + self._roles('F', 'F', protected)

        if mode == 'security_flash':
            return 'SHIELD:' + self._roles('F', 'S', protected)

        if mode == 'security_colors':
            return 'SHIELD:' + self._roles('F', 'S', protected)

        if mode == 'virus_pulse_all':
            return 'VIRUS:' + self._roles('V', 'V', protected)

        if mode == 'virus_pulse_z3':
            return 'VIRUS:' + self._roles('V', 'S', protected)

        if mode == 'crash_fade_all':
            return 'CRASH:' + self._roles('C', 'C', protected)

        if mode == 'security_crash_m3':
            return 'CRASH:' + self._roles('C', 'F', protected)

        log.warning("set_mode: unknown mode '%s' → IDLE", mode)
        return 'IDLE:' + self._roles('I', 'I', protected)

    def _roles(
        self, unprotected: str, protected_role: str, protected: set[int]
    ) -> str:
        """
        Build the 4-char role string (one char per drone, ordered 1-2-3-4).
        Drones in the protected set get protected_role; others get unprotected.
        """
        return ''.join(
            protected_role if d in protected else unprotected
            for d in self._DRONE_IDS
        )

    def shutdown(self) -> None:
        """Send OFF then close the serial port cleanly."""
        log.info("LightingController shutdown")
        self._last = None
        self.send('OFF:OOOO')
        with self._lock:
            if self._port:
                try:
                    self._port.close()
                    log.debug("Serial port closed")
                except Exception as exc:
                    log.warning("Serial close error: %s", exc)
                finally:
                    self._port = None
