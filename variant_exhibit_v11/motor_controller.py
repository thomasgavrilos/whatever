#!/usr/bin/env python3
"""
motor_controller.py — MotorController
The ONLY module that touches gpiod or writes GPIO pins.
All Pi 5 RP1 retry quirks are contained here.
"""

import logging
import threading
import time
from typing import Callable, Sequence

from config import cfg

log = logging.getLogger(__name__)

try:
    import gpiod
    from gpiod.line import Bias, Direction, Value
    _GPIOD_AVAILABLE = True
except ImportError:
    _GPIOD_AVAILABLE = False
    log.warning("gpiod unavailable — MotorController running in stub mode")


class MotorController:
    """
    Owns the single gpiod LineRequest for all motor and button GPIO.

    motor_positions : dict[int, int]
        Cumulative step offset from HOME per motor.
        Updated by every step primitive; zeroed by return_home().
    """

    def __init__(self) -> None:
        self._request = None
        self._gpio_lock = threading.Lock()
        self.motor_positions: dict[int, int] = {m: 0 for m in cfg.MOTORS}

        self._all_ids: list[int] = list(cfg.MOTORS.keys())

        self._pul_pins: dict[int, int] = {m: cfg.MOTORS[m].PUL for m in cfg.MOTORS}
        self._dir_pins: dict[int, int] = {m: cfg.MOTORS[m].DIR for m in cfg.MOTORS}
        self._ena_pins: dict[int, int] = {m: cfg.MOTORS[m].ENA for m in cfg.MOTORS}
        self._pin_map: dict[str, dict[int, int]] = {
            'PUL': self._pul_pins,
            'DIR': self._dir_pins,
            'ENA': self._ena_pins,
        }

        log.debug("MotorController created — GPIO not yet initialised")

    # ── Initialisation ───────────────────────────────────────────────────────

    def setup(self) -> None:
        """
        Open /dev/gpiochip0 and configure all motor, button, and relay lines.

        Raises
        ------
        PermissionError  on GPIO access denied
        RuntimeError     on any other hardware failure
        """
        if not _GPIOD_AVAILABLE:
            log.warning("setup() skipped — gpiod not available")
            return

        log.info("Initialising GPIO …")
        try:
            config = {}
            for pins in cfg.MOTORS.values():
                config[pins.DIR] = gpiod.LineSettings(
                    direction=Direction.OUTPUT, output_value=Value.INACTIVE)
                config[pins.PUL] = gpiod.LineSettings(
                    direction=Direction.OUTPUT, output_value=Value.INACTIVE)
                config[pins.ENA] = gpiod.LineSettings(
                    direction=Direction.OUTPUT, output_value=Value.INACTIVE)
            config[cfg.LAUNCH_BUTTON_GPIO] = gpiod.LineSettings(
                direction=Direction.INPUT, bias=Bias.PULL_UP)
            config[cfg.RELAY_GPIO] = gpiod.LineSettings(
                direction=Direction.OUTPUT, output_value=Value.INACTIVE)

            self._request = gpiod.request_lines(
                "/dev/gpiochip0",
                consumer="drone-exhibit-v11",
                config=config,
            )
            log.info("✓ GPIO initialised (%d motors, button GPIO%d, relay GPIO%d)",
                     len(cfg.MOTORS), cfg.LAUNCH_BUTTON_GPIO, cfg.RELAY_GPIO)

        except PermissionError as exc:
            log.critical("GPIO permission denied: %s", exc)
            raise
        except Exception as exc:
            log.critical("GPIO init failed: %s", exc)
            raise RuntimeError(f"GPIO init failed: {exc}") from exc

    def close(self) -> None:
        """Release the gpiochip request on shutdown."""
        if self._request is not None:
            try:
                self._request.release()
                log.info("GPIO request released")
            except Exception as exc:
                log.warning("GPIO release error: %s", exc)
            finally:
                self._request = None

    # ── Enable / disable ─────────────────────────────────────────────────────

    def enable(self, ids: list[int] | None = None) -> None:
        """Assert ENA (active-LOW) — energises coils, motors hold position."""
        ids = ids or self._all_ids
        log.debug("Enabling motors %s", ids)
        self._batch_set('ENA', ids, Value.INACTIVE)

    def disable(self, ids: list[int] | None = None) -> None:
        """Deassert ENA — de-energises coils, motors free-wheel."""
        ids = ids or self._all_ids
        log.debug("Disabling motors %s", ids)
        self._batch_set('ENA', ids, Value.ACTIVE)

    # ── Stepping primitives ──────────────────────────────────────────────────

    def step_sync(
        self,
        ids:        Sequence[int],
        steps:      int,
        direction:  bool,
        delay:      float,
        stop_check: Callable[[], bool] | None = None,
    ) -> None:
        """All listed motors step in the SAME direction. Wraps step_dirs."""
        self.step_dirs(ids, steps, {m: direction for m in ids}, delay, stop_check)

    def step_dirs(
        self,
        ids:        Sequence[int],
        steps:      int,
        directions: dict[int, bool],
        delay:      float,
        stop_check: Callable[[], bool] | None = None,
    ) -> None:
        """
        Core stepping primitive — per-motor directions, simultaneous pulses.

        Sets DIR pins once, then loops *steps* times:
          PUL HIGH → delay → PUL LOW → delay → update positions → check stop.
        """
        if not _GPIOD_AVAILABLE or self._request is None:
            return
        with self._gpio_lock:
            self._batch_set_dirs(ids, directions)
        time.sleep(0.001)

        increments = {m: (1 if directions.get(m, True) else -1) for m in ids}
        positions = self.motor_positions
        abs_steps = abs(steps)

        for _ in range(abs_steps):
            if stop_check and stop_check():
                return
            with self._gpio_lock:
                self._batch_set('PUL', ids, Value.ACTIVE)
            time.sleep(delay)
            with self._gpio_lock:
                self._batch_set('PUL', ids, Value.INACTIVE)
            time.sleep(delay)
            for m, inc in increments.items():
                positions[m] += inc

    # ── Return to home ───────────────────────────────────────────────────────

    def return_home(self, ids: list[int] | None = None, rpm: int = 60) -> None:
        """
        Return listed motors to position 0 using recorded motor_positions.
        Computes travel distance and direction per motor; pulses only motors
        with remaining travel each step; zeros positions on completion.
        """
        ids = ids or self._all_ids
        log.info("return_home motors=%s @ %d RPM", ids, rpm)

        if not _GPIOD_AVAILABLE or self._request is None:
            for m in ids:
                self.motor_positions[m] = 0
            return

        delay = self.calculate_delay(rpm)
        remaining: dict[int, int] = {}
        direction: dict[int, bool] = {}
        increment: dict[int, int] = {}
        max_steps = 0
        for m in ids:
            pos = self.motor_positions[m]
            abs_pos = abs(pos)
            remaining[m] = abs_pos
            d = pos < 0
            direction[m] = d
            increment[m] = 1 if d else -1
            if abs_pos > max_steps:
                max_steps = abs_pos

        if max_steps == 0:
            log.debug("return_home: already at HOME")
            return

        dir_pins = self._dir_pins
        dir_vals = {
            dir_pins[m]: (Value.ACTIVE if direction[m] else Value.INACTIVE)
            for m in ids if remaining[m] > 0
        }
        if dir_vals:
            try:
                with self._gpio_lock:
                    self._request.set_values(dir_vals)
            except Exception:
                time.sleep(0.001)
                try:
                    with self._gpio_lock:
                        self._request.set_values(dir_vals)
                except Exception:
                    log.warning("return_home: could not set initial directions")
        time.sleep(0.001)

        positions = self.motor_positions
        active = [m for m in ids if remaining[m] > 0]

        for _ in range(max_steps):
            if not active:
                break
            with self._gpio_lock:
                self._batch_set('PUL', active, Value.ACTIVE)
            time.sleep(delay)
            with self._gpio_lock:
                self._batch_set('PUL', active, Value.INACTIVE)
            still_active = []
            for m in active:
                remaining[m] -= 1
                positions[m] += increment[m]
                if remaining[m] > 0:
                    still_active.append(m)
            active = still_active
            time.sleep(delay)

        for m in ids:
            positions[m] = 0
        log.info("✓ Motors %s at HOME", ids)

    # ── Relay ────────────────────────────────────────────────────────────────

    def set_relay(self, on: bool) -> None:
        """Drive RELAY_GPIO to swap bicolor launch-button LED polarity."""
        if not _GPIOD_AVAILABLE or self._request is None:
            return
        try:
            with self._gpio_lock:
                self._request.set_value(
                    cfg.RELAY_GPIO,
                    Value.ACTIVE if on else Value.INACTIVE,
                )
        except Exception as exc:
            log.warning("set_relay(%s) error: %s", on, exc)

    # ── Button read ──────────────────────────────────────────────────────────

    def is_button_pressed(self, gpio_pin: int) -> bool:
        """True when pin reads INACTIVE (active-LOW pull-up logic). Safe on error."""
        if not _GPIOD_AVAILABLE or self._request is None:
            return False
        try:
            return self._request.get_value(gpio_pin) == Value.INACTIVE
        except Exception:
            return False

    # ── Math helper ──────────────────────────────────────────────────────────

    @staticmethod
    def calculate_delay(rpm: int) -> float:
        """
        Convert RPM to PUL half-period in seconds.
        half_period = 1 / (2 × (rpm × STEPS_PER_REV / 60))
        """
        if rpm <= 0:
            return 0.01
        return 1.0 / (2.0 * (rpm * cfg.STEPS_PER_REV / 60.0))

    # ── Internal GPIO batch helpers ──────────────────────────────────────────

    def _batch_set(self, pin_key: str, ids: Sequence[int], value) -> None:
        """
        Set pin_key for all ids in one ioctl. Must be called under _gpio_lock.
        Retries once on Pi 5 RP1 PermissionError/OSError/ValueError;
        silently drops on second failure (one skipped step is acceptable).
        """
        if self._request is None:
            return
        pin_lookup = self._pin_map.get(pin_key)
        if pin_lookup is None:
            return
        vals = {pin_lookup[m]: value for m in ids if m in pin_lookup}
        if not vals:
            return
        try:
            self._request.set_values(vals)
        except (PermissionError, OSError, ValueError):
            time.sleep(0.0005)
            try:
                self._request.set_values(vals)
            except Exception:
                pass

    def _batch_set_dirs(self, ids: Sequence[int], directions: dict[int, bool]) -> None:
        """Set per-motor DIR pins in one ioctl. Must be called under _gpio_lock."""
        if self._request is None:
            return
        dir_pins = self._dir_pins
        vals = {
            dir_pins[m]: (Value.ACTIVE if directions.get(m, True) else Value.INACTIVE)
            for m in ids if m in dir_pins
        }
        if not vals:
            return
        try:
            self._request.set_values(vals)
        except (PermissionError, OSError, ValueError):
            time.sleep(0.0005)
            try:
                self._request.set_values(vals)
            except Exception:
                pass
