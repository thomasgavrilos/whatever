#!/usr/bin/env python3
"""
config.py — Variant Security Drone Exhibit
===================================================
Single source of truth for every hardware pin, motor constant,
timing value, and application-wide setting.

Rules
-----
  - No logic, no hardware calls, no imports beyond stdlib.
  - All other modules import from this file only.
  - To retune, rewire, or add hardware: change this file only.
"""

import logging
from typing import NamedTuple


# ---------------------------------------------------------------------------
# Hardware descriptor types
# ---------------------------------------------------------------------------

class MotorPins(NamedTuple):
    """GPIO pin numbers for one stepper-motor driver."""
    DIR: int    # direction select (HIGH = forward, LOW = reverse)
    PUL: int    # step pulse (rising edge = one microstep)
    ENA: int    # enable, active-LOW on these drivers (LOW = energised)


# ---------------------------------------------------------------------------
# Config class
# ---------------------------------------------------------------------------

class Config:
    """
    Immutable application configuration.
    Access via the module-level singleton: `from config import cfg`.

    Sections
    --------
      Motor GPIO            — pin assignments for all four drivers
      Auxiliary GPIO        — launch button, relay
      Stepper constants     — microstepping, RPM-to-delay formula
      Launch sequence       — phase 1 / phase 2 speeds and distances
      Flight                — jogging RPM, auto-return timeout
      Height slider         — rotation range, default, travel speed
      Virus sequence        — thrash RPMs, durations, security window
      Crash / return        — per-path return-to-home speeds
      Button debounce       — polling rate, stable-poll count
      Lighting              — serial port candidates, baud rate
      Display               — touchscreen resolution
      Logging               — file path, fallback path, level, format
    """

    # ── Motor GPIO ──────────────────────────────────────────────────────────
    MOTORS: dict = {
        1: MotorPins(DIR=17, PUL=27, ENA=22),
        2: MotorPins(DIR=23, PUL=24, ENA=25),
        3: MotorPins(DIR=5,  PUL=6,  ENA=13),
        4: MotorPins(DIR=19, PUL=26, ENA=21),
        # NOTE: Motor 4 has never moved — suspected GPIO 21 conflict or
        # driver fault. Swap-test recommended before calling this resolved.
    }

    # ── Auxiliary GPIO ──────────────────────────────────────────────────────
    LAUNCH_BUTTON_GPIO: int = 12   # physical pin 32; DPDT momentary; PULL_UP; active-LOW
    RELAY_GPIO:         int = 16   # physical pin 36; drives IN_A + IN_B for bicolor LED swap
    #   NOT YET WIRED — code is correct; relay board needs physical hookup.

    # ── Stepper constants ───────────────────────────────────────────────────
    STEPS_PER_REV:  int = 1600     # microstepping setting on all four DM542T drivers

    # ── Launch sequence ─────────────────────────────────────────────────────
    LAUNCH_SLOW_RPM: int   = 60    # Phase 1 speed
    LAUNCH_SLOW_ROT: int   = 2     # Phase 1 distance (rotations from HOME)
    LAUNCH_FAST_RPM: int   = 150   # Phase 2 speed
    LAUNCH_FAST_ROT: int   = 10    # Phase 2 distance (total = 12 rot from HOME)

    # ── Flight ──────────────────────────────────────────────────────────────
    FLIGHT_RPM:     int   = 30
    FLIGHT_MAX_SEC: float = 300.0  # 5-minute auto-return-to-home timeout

    # ── Height slider ────────────────────────────────────────────────────────
    SLIDER_MIN_ROT:     int   = 8    # lowest allowed height (rotations from HOME)
    SLIDER_MAX_ROT:     int   = 18   # highest allowed height
    SLIDER_DEFAULT_ROT: int   = 12   # matches launch landing height
    SLIDER_MOVE_RPM:    int   = 100  # travel speed during height adjustment

    # ── Virus sequence ───────────────────────────────────────────────────────
    VIRUS_PRE_PAUSE:         float = 0.1   # brief pause before thrash begins
    VIRUS_NORMAL_RPM:        int   = 250   # all-infected thrash speed
    NORMAL_VIRUS_DURATION:   float = 3.0   # all-infected thrash duration (sec)
    VIRUS_SECURITY_RPM:      int   = 200   # partial-infection thrash speed
    SECURITY_VIRUS_DURATION: float = 2.0   # partial-infection thrash duration (sec)
    SECURITY_WAIT_SEC:       int   = 12    # protected-drone flight window after crash

    # ── Crash / return-to-home speeds ───────────────────────────────────────
    CRASH_ALL_RPM:       int = 120   # all motors, all-infected path
    CRASH_M3_RPM:        int = 250   # infected motors, partial-infection path
    CRASH_PROTECTED_RPM: int = 60    # protected motors; also used for timeout return

    # ── Button debounce ─────────────────────────────────────────────────────
    BUTTON_POLL_SEC:     float = 0.02   # 20 ms polling interval
    STABLE_POLLS:        int   = 5      # 5 × 20 ms = 100 ms minimum press time
    FLIGHT_EXIT_TIMEOUT: float = 2.0    # max seconds to wait for flight_mode to exit

    # ── Lighting (XIAO ESP32C3 over USB-C serial) ────────────────────────────
    LIGHTING_PORTS: tuple = ('/dev/ttyACM0', '/dev/ttyUSB0')
    LIGHTING_BAUD:  int   = 9600

    # ── Display ──────────────────────────────────────────────────────────────
    DISPLAY_WIDTH:  int = 800
    DISPLAY_HEIGHT: int = 480

    # ── Logging ─────────────────────────────────────────────────────────────
    LOG_PATH:          str = '/home/newlab/stepper_project/exhibit.log'
    LOG_PATH_FALLBACK: str = '/tmp/exhibit.log'
    LOG_LEVEL:         int = logging.DEBUG
    LOG_FORMAT:        str = '%(asctime)s [%(levelname)-8s] %(name)s: %(message)s'
    LOG_DATE_FORMAT:   str = '%H:%M:%S'


# Module-level singleton — every other module does:  from config import cfg
cfg = Config()
