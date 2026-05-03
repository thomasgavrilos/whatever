#!/usr/bin/env python3
"""
lighting_controller.py — LightingController
Serial bridge to the XIAO ESP32C3 driving the WS2812B strip via FastLED.
"""

import logging
import threading

from config import cfg

log = logging.getLogger(__name__)

try:
    import serial
    _SERIAL_AVAILABLE = True
except ImportError:
    _SERIAL_AVAILABLE = False
    log.warning("pyserial not installed — LED control disabled. Run: pip install pyserial")


class LightingController:
    """
    Fire-and-forget serial command sender.

    Commands accepted by the XIAO sketch
    --------------------------------------
    IDLE    — slow blue pulse
    FLY     — green upward chase
    VARIANT — 3/4 strip green, 1/4 red
    VIRUS   — red strobe flash

    set_mode() accepts legacy LEDController mode strings for full
    backwards-compatibility with all existing call sites.
    Consecutive identical commands are deduplicated (XIAO loops the
    current effect — resending wastes serial bandwidth).
    """

    _MODE_MAP: dict[str, str] = {
        'off':               'IDLE',
        'idle_pulse':        'IDLE',
        'launch_slow':       'FLY',
        'launch_fast':       'FLY',
        'flight':            'FLY',
        'security_flash':    'VARIANT',
        'security_colors':   'VARIANT',
        'security_crash_m3': 'VARIANT',
        'virus_pulse_all':   'VIRUS',
        'virus_pulse_z3':    'VIRUS',
        'crash_fade_all':    'VIRUS',
    }

    def __init__(self) -> None:
        self._port = None
        self._lock = threading.Lock()
        self._last: str | None = None

        self._encoded_cache: dict[str, bytes] = {
            cmd: f"{cmd}\n".encode() for cmd in ('IDLE', 'FLY', 'VARIANT', 'VIRUS')
        }

        if _SERIAL_AVAILABLE:
            self._connect()
        else:
            log.warning("LightingController: running in stub mode (pyserial missing)")

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
                    data = self._encoded_cache.get(command) or f"{command}\n".encode()
                    self._port.write(data)
                    self._port.flush()
                    log.debug("LED → %s", command)
                except Exception as exc:
                    log.warning("LED serial error: %s", exc)
            else:
                log.debug("LED stub → %s", command)

    def set_mode(self, mode: str) -> None:
        """
        Drop-in replacement for old LEDController.set_mode().
        Maps legacy mode strings to the four XIAO commands.
        Unknown modes default to IDLE and log a warning.
        """
        command = self._MODE_MAP.get(mode, 'IDLE')
        if mode not in self._MODE_MAP:
            log.warning("set_mode: unknown mode '%s' → IDLE", mode)
        self.send(command)

    def shutdown(self) -> None:
        """Send IDLE then close the serial port cleanly."""
        log.info("LightingController shutdown")
        self.send('IDLE')
        with self._lock:
            if self._port:
                try:
                    self._port.close()
                    log.debug("Serial port closed")
                except Exception as exc:
                    log.warning("Serial close error: %s", exc)
                finally:
                    self._port = None
