#!/usr/bin/env python3
"""
sequence_runner.py — SequenceRunner
All high-level motion choreography. Delegates all GPIO to MotorController.
"""

import logging
import random
import threading
import time

from config import cfg
from exhibit_state import ExhibitState, State
from motor_controller import MotorController
from lighting_controller import LightingController

log = logging.getLogger(__name__)


class SequenceRunner:
    """
    Orchestrates the exhibit motion lifecycle:
      launch → flight → (adjust_height | virus) → reset

    Every public method runs in its own daemon thread (spawned by
    ExhibitState._handle_press or TouchUI event handlers).
    Stop signals flow via callables / threading.Events on ExhibitState.
    """

    def __init__(
        self,
        state:  ExhibitState,
        motors: MotorController,
        lights: LightingController,
    ) -> None:
        self._state  = state
        self._motors = motors
        self._lights = lights
        log.debug("SequenceRunner ready")

    # ── Launch ────────────────────────────────────────────────────────────────

    def launch(self) -> None:
        """
        Phase 1: LAUNCH_SLOW_ROT rotations @ LAUNCH_SLOW_RPM
        Phase 2: LAUNCH_FAST_ROT rotations @ LAUNCH_FAST_RPM
        Hands off to flight() on completion.
        """
        log.info("=" * 60)
        log.info("LAUNCH SEQUENCE")
        log.info("=" * 60)
        self._state.stop_motion = False
        ids = self._motors._all_ids

        # Phase 1
        log.info("[Phase 1] %d rot @ %d RPM", cfg.LAUNCH_SLOW_ROT, cfg.LAUNCH_SLOW_RPM)
        self._state.transition_to(State.LAUNCH_SLOW)
        self._motors.step_sync(
            ids,
            cfg.STEPS_PER_REV * cfg.LAUNCH_SLOW_ROT,
            True,
            MotorController.calculate_delay(cfg.LAUNCH_SLOW_RPM),
            stop_check=lambda: self._state.stop_motion,
        )
        if self._state.stop_motion:
            log.warning("Launch aborted after Phase 1")
            self._state.reset()
            return

        # Phase 2
        log.info("[Phase 2] %d rot @ %d RPM", cfg.LAUNCH_FAST_ROT, cfg.LAUNCH_FAST_RPM)
        self._state.transition_to(State.LAUNCH_FAST)
        self._motors.step_sync(
            ids,
            cfg.STEPS_PER_REV * cfg.LAUNCH_FAST_ROT,
            True,
            MotorController.calculate_delay(cfg.LAUNCH_FAST_RPM),
            stop_check=lambda: self._state.stop_motion,
        )
        if self._state.stop_motion:
            log.warning("Launch aborted after Phase 2")
            self._state.reset()
            return

        log.info("Launch complete — entering FLIGHT")
        self.flight()

    # ── Flight ────────────────────────────────────────────────────────────────

    def flight(self) -> None:
        """
        Continuous simultaneous jogging on all four motors.
        Alternates direction every random burst (30-50% of a full rev).
        Exits when stop_motion is set or FLIGHT_MAX_SEC elapses.
        Sets flight_exited in finally (always).
        """
        self._state.transition_to(State.FLIGHT)
        self._state.flight_exited.clear()
        timeout_triggered = False

        try:
            log.info("FLIGHT @ %d RPM — timeout %ds",
                     cfg.FLIGHT_RPM, int(cfg.FLIGHT_MAX_SEC))
            flight_start = time.time()
            delay = MotorController.calculate_delay(cfg.FLIGHT_RPM)
            max_pulse = int(cfg.STEPS_PER_REV * 0.5)
            min_pulse = int(max_pulse * 0.3)
            motor_dirs = {1: True, 2: False, 3: True, 4: False}
            ids = self._motors._all_ids
            deadline = flight_start + cfg.FLIGHT_MAX_SEC
            state = self._state

            while not state.stop_motion:
                if time.time() >= deadline:
                    timeout_triggered = True
                    break

                pulse_steps = random.randint(min_pulse, max_pulse)

                self._motors.step_dirs(
                    ids,
                    pulse_steps,
                    motor_dirs,
                    delay,
                    stop_check=lambda: (
                        state.stop_motion or time.time() >= deadline
                    ),
                )

                if time.time() >= deadline:
                    timeout_triggered = True
                    break

                for m in ids:
                    motor_dirs[m] = not motor_dirs[m]

        finally:
            self._state.flight_exited.set()
            log.debug("flight_exited set")

        if timeout_triggered:
            log.info("Flight timeout (%ds) — quiet return home", int(cfg.FLIGHT_MAX_SEC))
            self._state.transition_to(State.RETURNING_HOME)
            self._motors.return_home(rpm=cfg.CRASH_PROTECTED_RPM)
            self._state.reset()

    # ── Subset flight ─────────────────────────────────────────────────────────

    def flight_subset(self, motor_ids: list[int]) -> None:
        """
        Jog a subset of motors until subset_flight_stop is set.
        Used during partial-virus path: protected drones keep flying while
        infected drones are thrashed and returned home concurrently.
        """
        self._state.subset_flight_exited.clear()
        try:
            log.info("SUBSET FLIGHT motors=%s @ %d RPM", motor_ids, cfg.FLIGHT_RPM)
            delay = MotorController.calculate_delay(cfg.FLIGHT_RPM)
            max_pulse = int(cfg.STEPS_PER_REV * 0.5)
            min_pulse = int(max_pulse * 0.3)
            init_dirs = {1: True, 2: False, 3: True, 4: False}
            motor_dirs = {m: init_dirs.get(m, True) for m in motor_ids}
            stop_check = self._state.subset_flight_stop.is_set

            while not stop_check():
                pulse_steps = random.randint(min_pulse, max_pulse)
                self._motors.step_dirs(
                    motor_ids,
                    pulse_steps,
                    motor_dirs,
                    delay,
                    stop_check=stop_check,
                )
                for m in motor_ids:
                    motor_dirs[m] = not motor_dirs[m]

        finally:
            self._state.subset_flight_exited.set()
            log.debug("subset_flight_exited set")

    # ── Thrash ────────────────────────────────────────────────────────────────

    def thrash(
        self,
        motor_ids:    list[int],
        duration_sec: float,
        rpm:          int,
    ) -> None:
        """
        Sporadic random-direction bursts on motor_ids for duration_sec.
        Each iteration picks a random direction per motor independently and
        a short burst (5-15% of a full revolution). Yields ~20-60 direction
        reversals per second for a chaotic effect.
        """
        log.info("THRASH motors=%s duration=%.1fs @ %d RPM",
                 motor_ids, duration_sec, rpm)
        delay = MotorController.calculate_delay(rpm)
        start = time.time()
        end_t = start + duration_sec
        min_burst = max(1, int(cfg.STEPS_PER_REV * 0.05))
        max_burst = max(2, int(cfg.STEPS_PER_REV * 0.15))
        state = self._state
        _randint = random.randint
        _getrandbits = random.getrandbits

        while time.time() < end_t and not state.stop_motion:
            dirs  = {m: bool(_getrandbits(1)) for m in motor_ids}
            burst = _randint(min_burst, max_burst)

            self._motors.step_dirs(
                motor_ids,
                burst,
                dirs,
                delay,
                stop_check=lambda: (
                    state.stop_motion or time.time() >= end_t
                ),
            )

    # ── Virus sequence ────────────────────────────────────────────────────────

    def trigger_virus(self) -> None:
        """
        Entry point for touchscreen VIRUS button tap.
        Validates state, stops flight cleanly, then spawns virus() in a
        new daemon thread. Safe to call from the Tk main thread.
        """
        with self._state._lock:
            if self._state.current != State.FLIGHT:
                log.debug("trigger_virus: ignored in state %s", self._state.current)
                return
            if len(self._state.protected_drones) >= 4:
                log.info("trigger_virus: refused — all 4 drones protected")
                return

        clean = self._stop_flight_and_wait()
        if not clean:
            log.warning("trigger_virus: flight did not exit cleanly")

        threading.Thread(
            target=self.virus,
            daemon=True,
            name="virus-seq",
        ).start()

    def virus(self) -> None:
        """
        Generalised virus sequence driven by state.protected_drones.

        Branch A — 0 protected (all infected)
          Thrash all @ VIRUS_NORMAL_RPM → return all home @ CRASH_ALL_RPM.

        Branch B — 1-3 protected
          Spawn flight_subset(safe) concurrently.
          Thrash infected @ VIRUS_SECURITY_RPM.
          Return infected home @ CRASH_M3_RPM (protected still flying).
          Wait SECURITY_WAIT_SEC.
          Stop subset flight → return protected home @ CRASH_PROTECTED_RPM.

        try/except/finally ensures state.reset() ALWAYS runs.
        """
        protected = sorted(self._state.protected_drones)
        infected  = sorted({1, 2, 3, 4} - set(protected))

        log.info("=" * 60)
        log.info("VIRUS  safe=%s  infected=%s", protected, infected)
        log.info("=" * 60)

        try:
            self._state.transition_to(State.VIRUS)
            time.sleep(cfg.VIRUS_PRE_PAUSE)

            if not protected:
                # ── Branch A: all infected ────────────────────────────────
                log.info("All motors infected — thrashing @ %d RPM for %.1fs",
                         cfg.VIRUS_NORMAL_RPM, cfg.NORMAL_VIRUS_DURATION)
                self._lights.set_mode('virus_pulse_all')
                self.thrash(infected, cfg.NORMAL_VIRUS_DURATION, cfg.VIRUS_NORMAL_RPM)

                self._state.transition_to(State.RETURNING_HOME)
                log.info("All motors returning home @ %d RPM", cfg.CRASH_ALL_RPM)
                self._lights.set_mode('crash_fade_all')
                self._motors.return_home(infected, rpm=cfg.CRASH_ALL_RPM)

            else:
                # ── Branch B: mixed ───────────────────────────────────────
                log.info("Infected=%s thrashing; protected=%s keep flying",
                         infected, protected)
                self._lights.set_mode('virus_pulse_z3')

                # Protected drones jog concurrently
                self._state.subset_flight_stop.clear()
                subset_t = threading.Thread(
                    target=self.flight_subset,
                    args=(protected,),
                    daemon=True,
                    name="subset-flight",
                )
                subset_t.start()

                # Infected thrash while protected fly
                self.thrash(infected, cfg.SECURITY_VIRUS_DURATION, cfg.VIRUS_SECURITY_RPM)

                # Infected crash home — protected still jogging
                self._state.transition_to(State.POST_VIRUS_FLIGHT)
                log.info("Infected %s returning home @ %d RPM", infected, cfg.CRASH_M3_RPM)
                self._motors.return_home(infected, rpm=cfg.CRASH_M3_RPM)

                # Protected flight window
                self._lights.set_mode('security_crash_m3')
                log.info("Protected %s continue flight for %ds",
                         protected, cfg.SECURITY_WAIT_SEC)
                for remaining in range(cfg.SECURITY_WAIT_SEC, 0, -1):
                    log.debug("  Protected flight: %ds remaining", remaining)
                    time.sleep(1)

                # Stop subset flight cleanly
                self._stop_subset_and_wait()

                # Protected return home
                self._state.transition_to(State.RETURNING_HOME)
                log.info("Protected %s returning home @ %d RPM",
                         protected, cfg.CRASH_PROTECTED_RPM)
                self._motors.return_home(protected, rpm=cfg.CRASH_PROTECTED_RPM)

        except Exception:
            log.exception("Exception during virus sequence — forcing reset")
            self._state.subset_flight_stop.set()   # stop any running subset flight

        finally:
            self._state.reset()
            self._motors.set_relay(True)  # button LED back to green

    # ── Height adjustment ──────────────────────────────────────────────────────

    def adjust_height(self, target_rot: float) -> None:
        """
        Move all four motors from current_height_rot to target_rot.

        1. If in FLIGHT, stop cleanly (wait for flight_exited).
        2. Enter ADJUSTING_HEIGHT.
        3. Retarget loop: step toward target; update current_height_rot every
           10% of travel so a mid-move retarget starts from the right position.
           Loop again if target changed during travel.
        4. Resume flight.
        """
        self._state.height_adjust_exited.clear()
        try:
            target_rot = self._state.target_height_rot   # use latest committed target

            # Stop flight if running
            if self._state.current == State.FLIGHT:
                clean = self._stop_flight_and_wait()
                if not clean:
                    log.warning("adjust_height: flight did not exit cleanly")

            self._state.transition_to(State.ADJUSTING_HEIGHT)

            # Retarget loop — slider may be moved again while we travel
            while True:
                self._state.height_adjust_stop.clear()
                self._adjust_height_to(target_rot)

                current = self._state.current_height_rot
                new_target = self._state.target_height_rot
                if abs(current - new_target) < 0.01:
                    break
                log.debug("adjust_height: retargeting %.1f → %.1f rot",
                          current, new_target)
                target_rot = new_target

            # Resume flight
            self._state.transition_to(State.FLIGHT)
            threading.Thread(
                target=self.flight,
                daemon=True,
                name="flight-post-adjust",
            ).start()

        finally:
            self._state.height_adjust_exited.set()
            log.debug("height_adjust_exited set")

    def _adjust_height_to(self, target_rot: float) -> None:
        """
        Inner single-move routine for adjust_height().
        Steps all 4 motors from current_height_rot to target_rot.
        Updates current_height_rot live every 10% of travel.
        Respects height_adjust_stop and stop_motion.
        """
        current = self._state.current_height_rot
        delta   = target_rot - current
        if abs(delta) < 0.01:
            return

        direction = delta > 0   # True = up
        steps     = int(abs(delta) * cfg.STEPS_PER_REV)
        delay     = MotorController.calculate_delay(cfg.SLIDER_MOVE_RPM)

        log.info("HEIGHT  %.1f → %.1f rot  (%s  %d steps @ %d RPM)",
                 current, target_rot,
                 "UP" if direction else "DOWN", steps, cfg.SLIDER_MOVE_RPM)

        ids = list(cfg.MOTORS.keys())
        with self._motors._gpio_lock:
            self._motors._batch_set_dirs(ids, {m: direction for m in ids})
        time.sleep(0.001)

        steps_done = 0
        update_interval = max(1, cfg.STEPS_PER_REV // 10)

        for _ in range(steps):
            if self._state.height_adjust_stop.is_set() or self._state.stop_motion:
                break
            with self._motors._gpio_lock:
                self._motors._batch_set('PUL', ids, Value.ACTIVE)
            time.sleep(delay)
            with self._motors._gpio_lock:
                self._motors._batch_set('PUL', ids, Value.INACTIVE)
            time.sleep(delay)
            for m in ids:
                self._motors.motor_positions[m] += 1 if direction else -1
            steps_done += 1

            # Update current_height_rot every 10% of a revolution so a
            # retarget knows the actual position, not the planned end point
            if steps_done % update_interval == 0:
                rot_done = steps_done / cfg.STEPS_PER_REV
                self._state.current_height_rot += rot_done if direction else -rot_done
                steps_done = 0

        # Commit any remaining partial rotation
        if steps_done > 0:
            rot_done = steps_done / cfg.STEPS_PER_REV
            self._state.current_height_rot += rot_done if direction else -rot_done

        # Snap to exact target if we completed without interruption
        if not self._state.height_adjust_stop.is_set() and not self._state.stop_motion:
            self._state.current_height_rot = target_rot

        log.debug("HEIGHT now at %.2f rot", self._state.current_height_rot)

    def request_height_change(self, new_target_rot: float) -> None:
        """
        Called by TouchUI on slider commit (finger lift).
        Gates on current state; spawns adjust_height() in a daemon thread.
        """
        new_target_rot = max(cfg.SLIDER_MIN_ROT, min(cfg.SLIDER_MAX_ROT, new_target_rot))
        log.debug("request_height_change → %.1f rot", new_target_rot)

        with self._state._lock:
            if self._state.current == State.ADJUSTING_HEIGHT:
                # Mid-move retarget: update target and signal current move to stop
                self._state.target_height_rot = new_target_rot
                self._state.height_adjust_stop.set()
                log.debug("Height retarget to %.1f", new_target_rot)
                return

            if self._state.current != State.FLIGHT:
                log.debug("request_height_change ignored — state=%s", self._state.current)
                return

            self._state.target_height_rot = new_target_rot

        threading.Thread(
            target=self.adjust_height,
            args=(new_target_rot,),
            daemon=True,
            name="height-adjust",
        ).start()

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _stop_flight_and_wait(self, timeout: float | None = None) -> bool:
        """Set stop_motion, wait for flight_exited. Returns True on clean exit."""
        timeout = timeout or cfg.FLIGHT_EXIT_TIMEOUT
        self._state.stop_motion = True
        clean = self._state.flight_exited.wait(timeout=timeout)
        if not clean:
            log.warning("_stop_flight_and_wait: timed out after %.1fs", timeout)
        self._state.stop_motion = False
        return clean

    def _stop_subset_and_wait(self, timeout: float = 3.0) -> bool:
        """Set subset_flight_stop, wait for subset_flight_exited. Returns True on clean exit."""
        self._state.subset_flight_stop.set()
        clean = self._state.subset_flight_exited.wait(timeout=timeout)
        if not clean:
            log.warning("_stop_subset_and_wait: timed out after %.1fs", timeout)
        return clean
