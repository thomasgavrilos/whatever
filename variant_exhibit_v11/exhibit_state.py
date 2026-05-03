#!/usr/bin/env python3
"""
exhibit_state.py — ExhibitState
Single source of truth for all runtime state and the button monitor loop.
"""

import logging
import threading
import time
from typing import Callable, TYPE_CHECKING

from config import cfg

if TYPE_CHECKING:
    from motor_controller import MotorController
    from sequence_runner import SequenceRunner

log = logging.getLogger(__name__)


class State:
    """Exhibit state machine values."""
    IDLE              = "IDLE"
    LAUNCH_SLOW       = "LAUNCH_SLOW"
    LAUNCH_FAST       = "LAUNCH_FAST"
    FLIGHT            = "FLIGHT"
    ADJUSTING_HEIGHT  = "ADJUSTING_HEIGHT"
    VIRUS             = "VIRUS"
    POST_VIRUS_FLIGHT = "POST_VIRUS_FLIGHT"
    RETURNING_HOME    = "RETURNING_HOME"

    # LED mode fired when entering each state via transition_to()
    _LED_MAP: dict = {
        IDLE:              'idle_pulse',
        LAUNCH_SLOW:       'launch_slow',
        LAUNCH_FAST:       'launch_fast',
        FLIGHT:            'flight',
        ADJUSTING_HEIGHT:  'flight',        # keep flight LEDs on during slider move
        VIRUS:             'virus_pulse_all',
        POST_VIRUS_FLIGHT: 'security_crash_m3',
        RETURNING_HOME:    'crash_fade_all',
    }


class ExhibitState:
    """
    All mutable runtime state, thread-safe via _lock.

    Attributes
    ----------
    current          : str       current State value
    stop_motion      : bool      global abort flag for all motion threads
    launch_available : bool      whether LAUNCH button is armed
    security_active  : bool      whether any drone is protected
    protected_drones : set[int]  drone IDs shielded from virus
    current_height_rot : float   live height in rotations from HOME
    target_height_rot  : float   requested height target

    Events (threading.Event)
    ------------------------
    flight_exited        — set when flight() has fully exited
    subset_flight_stop   — set to request clean exit of flight_subset()
    subset_flight_exited — set when flight_subset() has fully exited
    height_adjust_stop   — set to cancel / retarget adjust_height()
    height_adjust_exited — set when adjust_height() has fully exited
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()

        self.current:          str  = State.IDLE
        self.stop_motion:      bool = False
        self.launch_available: bool = True
        self.security_active:  bool = False
        self.protected_drones: set[int] = set()

        self.current_height_rot: float = float(cfg.SLIDER_DEFAULT_ROT)
        self.target_height_rot:  float = float(cfg.SLIDER_DEFAULT_ROT)

        # Coordination events — all start in the "not running" state
        self.flight_exited        = threading.Event(); self.flight_exited.set()
        self.subset_flight_stop   = threading.Event()
        self.subset_flight_exited = threading.Event(); self.subset_flight_exited.set()
        self.height_adjust_stop   = threading.Event()
        self.height_adjust_exited = threading.Event(); self.height_adjust_exited.set()

        # LED observer wired by main()
        self._on_state_change: Callable[[str], None] | None = None

        log.debug("ExhibitState initialised")

    # ── LED callback ─────────────────────────────────────────────────────────

    def register_led_callback(self, callback: Callable[[str], None]) -> None:
        """Wire in LightingController.set_mode. Called once by main()."""
        self._on_state_change = callback
        log.debug("LED callback registered")

    # ── State machine ─────────────────────────────────────────────────────────

    def transition_to(self, new_state: str) -> None:
        """
        Update current state AND fire the LED observer atomically.
        All code that changes state must use this — never write self.current directly.
        """
        with self._lock:
            old = self.current
            self.current = new_state

        led_mode = State._LED_MAP.get(new_state, 'idle_pulse')
        if new_state not in State._LED_MAP:
            log.warning("transition_to: unknown state '%s'", new_state)

        log.info("State  %s  →  %s  (LED: %s)", old, new_state, led_mode)

        if self._on_state_change is not None:
            try:
                self._on_state_change(led_mode)
            except Exception as exc:
                log.error("LED callback error: %s", exc)

    def reset(self) -> None:
        """
        Full reset to IDLE — called in finally blocks so it ALWAYS runs.
        Clears protections, restores height defaults, re-arms LAUNCH.
        """
        log.info("=" * 50)
        log.info("SYSTEM RESET → IDLE")
        log.info("=" * 50)
        with self._lock:
            self.stop_motion       = False
            self.launch_available  = True
            self.security_active   = False
            self.protected_drones.clear()
            self.current_height_rot = float(cfg.SLIDER_DEFAULT_ROT)
            self.target_height_rot  = float(cfg.SLIDER_DEFAULT_ROT)
        self.transition_to(State.IDLE)

    # ── Drone protection ──────────────────────────────────────────────────────

    def toggle_drone(self, drone_id: int) -> bool:
        """
        Toggle protection for drone_id. Returns the new protection state.
        Only valid in FLIGHT — logs and returns unchanged state otherwise.
        Fires 'security_flash' LED when first drone is protected.
        """
        with self._lock:
            if self.current != State.FLIGHT:
                log.warning(
                    "toggle_drone(%d) ignored — not in FLIGHT (state=%s)",
                    drone_id, self.current,
                )
                return drone_id in self.protected_drones

            if drone_id in self.protected_drones:
                self.protected_drones.discard(drone_id)
                log.info("Drone %d unprotected (protected=%s)",
                         drone_id, self.protected_drones)
                new_state = False
            else:
                self.protected_drones.add(drone_id)
                log.info("Drone %d protected (protected=%s)",
                         drone_id, self.protected_drones)
                new_state = True

            self.security_active = len(self.protected_drones) > 0

        # Fire security_flash directly — the State value stays FLIGHT
        if self.security_active and self._on_state_change is not None:
            try:
                self._on_state_change('security_flash')
            except Exception as exc:
                log.error("security_flash LED error: %s", exc)

        return new_state

    # ── Button monitor ────────────────────────────────────────────────────────

    def start_button_monitor(
        self,
        motor_ctrl: "MotorController",
        seq_runner: "SequenceRunner",
    ) -> None:
        """Spawn the button polling daemon thread."""
        t = threading.Thread(
            target=self._button_monitor_loop,
            args=(motor_ctrl, seq_runner),
            daemon=True,
            name="button-monitor",
        )
        t.start()
        log.info("Button monitor started")

    def _button_monitor_loop(
        self,
        motor_ctrl: "MotorController",
        seq_runner: "SequenceRunner",
    ) -> None:
        """
        Stable-N debounce + released-then-pressed gating.

        Per button:
          press_count   — consecutive PRESSED readings, resets on release
          release_count — consecutive RELEASED readings, resets on press
          armed         — True only after a stable release; gates next fire

        A press fires only when:
          press_count >= STABLE_POLLS  (real press, not a bounce)
          armed == True                (we saw a stable release first)

        Warmup drains STABLE_POLLS × 2 reads so a pin held at boot
        doesn't fire immediately.
        """
        btn_map = {'LAUNCH': cfg.LAUNCH_BUTTON_GPIO}

        press_count   = {b: 0 for b in btn_map}
        release_count = {b: cfg.STABLE_POLLS for b in btn_map}  # assume released
        armed         = {b: True for b in btn_map}

        log.debug("Button monitor: warmup …")
        for _ in range(cfg.STABLE_POLLS * 2):
            for b, pin in btn_map.items():
                if motor_ctrl.is_button_pressed(pin):
                    armed[b] = False
                    press_count[b] += 1
                    release_count[b] = 0
                else:
                    press_count[b] = 0
                    release_count[b] += 1
            time.sleep(cfg.BUTTON_POLL_SEC)

        log.debug("Button monitor: warmup complete — armed=%s", armed)

        while True:
            for b, pin in btn_map.items():
                pressed_now = motor_ctrl.is_button_pressed(pin)

                if pressed_now:
                    press_count[b] += 1
                    release_count[b] = 0
                else:
                    release_count[b] += 1
                    press_count[b] = 0

                stable_pressed  = press_count[b]   >= cfg.STABLE_POLLS
                stable_released = release_count[b] >= cfg.STABLE_POLLS

                if stable_released and not armed[b]:
                    armed[b] = True
                    log.debug("Button %s re-armed", b)

                if stable_pressed and armed[b]:
                    armed[b] = False   # disarm immediately — prevents double-fire
                    log.debug("Button %s FIRE (state=%s)", b, self.current)
                    self._handle_press(b, motor_ctrl, seq_runner)

            time.sleep(cfg.BUTTON_POLL_SEC)

    def _handle_press(
        self,
        btn:        str,
        motor_ctrl: "MotorController",
        seq_runner: "SequenceRunner",
    ) -> None:
        """Dispatch a debounced, gated button press to the correct sequence."""
        with self._lock:
            state_now = self.current

        if btn == 'LAUNCH':
            if state_now == State.IDLE and self.launch_available:
                with self._lock:
                    self.launch_available = False
                motor_ctrl.enable()
                motor_ctrl.set_relay(False)   # switch button LED to red (active)
                threading.Thread(
                    target=seq_runner.launch,
                    daemon=True,
                    name="launch-seq",
                ).start()
            else:
                log.debug("LAUNCH ignored — state=%s available=%s",
                          state_now, self.launch_available)

        elif btn == 'VIRUS':
            # Touchscreen VIRUS uses seq_runner.trigger_virus() directly;
            # this branch handles any future physical VIRUS button re-addition.
            if state_now != State.FLIGHT:
                log.debug("VIRUS (physical) ignored — state=%s", state_now)
                return
            if len(self.protected_drones) >= 4:
                log.info("VIRUS refused — all 4 drones protected")
                return
            self.stop_motion = True
            if not self.flight_exited.wait(timeout=cfg.FLIGHT_EXIT_TIMEOUT):
                log.warning("VIRUS: flight_exited not set within %.1fs",
                            cfg.FLIGHT_EXIT_TIMEOUT)
            self.stop_motion = False
            threading.Thread(
                target=seq_runner.virus,
                daemon=True,
                name="virus-seq",
            ).start()
