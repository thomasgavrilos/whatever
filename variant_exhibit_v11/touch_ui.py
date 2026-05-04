#!/usr/bin/env python3
"""
touch_ui.py — TouchUI (PyGame)
Fullscreen kiosk for the 7" DSI touchscreen (800 x 480 px).
All rendering is programmatic — no image assets required.
"""

import logging
import math
import random
import threading
import time
from typing import TYPE_CHECKING

import pygame
import pygame.freetype
import pygame.gfxdraw

from config import cfg
from exhibit_state import ExhibitState, State

if TYPE_CHECKING:
    from sequence_runner import SequenceRunner

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------
C_BG           = (0, 0, 0)
C_PANEL        = (26, 35, 50)
C_PANEL_LITE   = (34, 48, 68)
C_BORDER       = (58, 80, 104)
C_BORDER_BLUE  = (74, 122, 170)
C_BORDER_RED   = (139, 0, 0)
C_TEXT          = (200, 200, 200)
C_TEXT_DIM      = (85, 102, 119)
C_GREEN        = (0, 255, 0)
C_GREEN_MID    = (0, 204, 0)
C_CYAN         = (0, 200, 230)
C_RED          = (198, 40, 40)
C_RED_BRIGHT   = (255, 34, 34)
C_VIRUS_BG     = (26, 10, 10)
C_DONE_BLUE    = (33, 150, 243)
C_YELLOW       = (255, 214, 0)
C_SLIDER_TRACK = (42, 58, 74)
C_SLIDER_BG    = (26, 35, 50)

DRONE_COLORS = {
    1: (0, 204, 0),
    2: (33, 150, 243),
    3: (255, 214, 0),
    4: (156, 39, 176),
}
DRONE_LABELS = {1: 'D1', 2: 'D2', 3: 'D3', 4: 'D4'}

W = cfg.DISPLAY_WIDTH
H = cfg.DISPLAY_HEIGHT
FPS = 30
_SLIDER_RANGE = max(1, cfg.SLIDER_MAX_ROT - cfg.SLIDER_MIN_ROT)

# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------

def lerp_color(a: tuple, b: tuple, t: float) -> tuple:
    t = max(0.0, min(1.0, t))
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def draw_panel(surf: pygame.Surface, rect: pygame.Rect,
               fill: tuple = C_PANEL, border: tuple = C_BORDER,
               border_w: int = 2, corner: int = 0) -> None:
    """Draw a filled panel with border.  corner>0 draws corner accents."""
    pygame.draw.rect(surf, fill, rect)
    pygame.draw.rect(surf, border, rect, border_w)
    if corner > 0:
        c = corner
        bw = border_w
        for (cx, cy, dx, dy) in [
            (rect.left, rect.top, 1, 1),
            (rect.right - 1, rect.top, -1, 1),
            (rect.left, rect.bottom - 1, 1, -1),
            (rect.right - 1, rect.bottom - 1, -1, -1),
        ]:
            pygame.draw.line(surf, border, (cx, cy), (cx + dx * c, cy), bw)
            pygame.draw.line(surf, border, (cx, cy), (cx, cy + dy * c), bw)


def draw_text_centered(surf: pygame.Surface, font: pygame.freetype.Font,
                       text: str, cx: int, cy: int, color: tuple) -> pygame.Rect:
    r = font.get_rect(text)
    pos = (cx - r.width // 2, cy - r.height // 2)
    font.render_to(surf, pos, text, color)
    return pygame.Rect(pos, (r.width, r.height))


def draw_glow_text(surf: pygame.Surface, font: pygame.freetype.Font,
                   text: str, cx: int, cy: int, color: tuple,
                   glow_radius: int = 3) -> None:
    """Text with a soft glow halo behind it."""
    glow_color = tuple(max(0, c // 3) for c in color)
    for dx in range(-glow_radius, glow_radius + 1):
        for dy in range(-glow_radius, glow_radius + 1):
            if dx * dx + dy * dy <= glow_radius * glow_radius:
                draw_text_centered(surf, font, text, cx + dx, cy + dy, glow_color)
    draw_text_centered(surf, font, text, cx, cy, color)


def rot_to_frac(rot: float) -> float:
    return (rot - cfg.SLIDER_MIN_ROT) / _SLIDER_RANGE


def frac_to_rot(frac: float) -> float:
    frac = max(0.0, min(1.0, frac))
    return cfg.SLIDER_MIN_ROT + frac * _SLIDER_RANGE


# ---------------------------------------------------------------------------
# Brick logo drawing
# ---------------------------------------------------------------------------

def draw_brick_logo(surf: pygame.Surface, cx: int, cy: int,
                    size: float, color: tuple) -> None:
    cell = int(size * 0.27)
    gap = int(size * 0.09)
    col_w = cell + gap
    row_h = cell + gap
    ox = cx - (3 * col_w - gap) // 2
    oy = cy - (4 * row_h - gap) // 2
    layout = [(0, 0.5, 2), (1, 0.0, 3), (2, 0.0, 3), (3, 0.5, 2)]
    for (row, col_off, n) in layout:
        for col in range(n):
            x = int(ox + (col + col_off) * col_w)
            y = oy + row * row_h
            pygame.draw.rect(surf, color, (x, y, cell, cell))


# ---------------------------------------------------------------------------
# Biohazard icon
# ---------------------------------------------------------------------------

def draw_biohazard(surf: pygame.Surface, cx: int, cy: int,
                   size: float, color: tuple) -> None:
    s = size / 50
    for angle_deg in [90, 210, 330]:
        a = math.radians(angle_deg)
        ox = int(cx + math.cos(a) * 16 * s)
        oy = int(cy - math.sin(a) * 16 * s)
        r = int(14 * s)
        pygame.draw.circle(surf, color, (ox, oy), r, max(1, int(2 * s)))
    pygame.draw.circle(surf, color, (cx, cy), int(5 * s), max(1, int(2 * s)))
    pygame.draw.circle(surf, color, (cx, cy), int(2 * s))


# ---------------------------------------------------------------------------
# Shield icon
# ---------------------------------------------------------------------------

def draw_shield(surf: pygame.Surface, cx: int, cy: int,
                size: int, color: tuple) -> None:
    s = size
    points = [
        (cx, cy - s),
        (cx + s, cy - s // 2),
        (cx + s, cy + s // 4),
        (cx, cy + s),
        (cx - s, cy + s // 4),
        (cx - s, cy - s // 2),
    ]
    pygame.draw.polygon(surf, color, points, 2)
    pygame.draw.line(surf, color, (cx, cy - s // 2), (cx, cy + s // 3), 2)
    pygame.draw.line(surf, color, (cx - s // 3, cy), (cx + s // 3, cy), 2)


# ---------------------------------------------------------------------------
# Radar background
# ---------------------------------------------------------------------------

def draw_radar_bg(surf: pygame.Surface, cx: int, cy: int,
                  max_r: float) -> None:
    color = (10, 42, 10)
    for i in range(1, 6):
        r = int(max_r * i / 5)
        pygame.draw.circle(surf, color, (cx, cy), r, 1)
    pygame.draw.line(surf, color, (int(cx - max_r), cy), (int(cx + max_r), cy), 1)
    pygame.draw.line(surf, color, (cx, int(cy - max_r)), (cx, int(cy + max_r)), 1)
    for a_deg in [45, 135]:
        a = math.radians(a_deg)
        dx, dy = math.cos(a) * max_r, math.sin(a) * max_r
        pygame.draw.line(surf, color,
                         (int(cx - dx), int(cy - dy)),
                         (int(cx + dx), int(cy + dy)), 1)


# ---------------------------------------------------------------------------
# MatrixRain
# ---------------------------------------------------------------------------

class MatrixRain:
    CHARS = "abcdefghijklmnopqrstuvwxyz0123456789@#$%&*!?<>{}[]=/\\|~^"
    COLS = 40

    def __init__(self, w: int, h: int, font: pygame.freetype.Font) -> None:
        self._w = w
        self._h = h
        self._font = font
        self._frame = 0
        self._col_w = max(1, w // self.COLS)
        self._font_size = max(8, self._col_w - 2)
        rows = h // self._font_size + 2
        self._streams: list[dict] = []
        for c in range(self.COLS):
            self._streams.append({
                'x': c * self._col_w + self._col_w // 2,
                'y': random.randint(-rows, 0),
                'speed': random.randint(1, 3),
                'length': random.randint(8, rows),
            })
        self._virus_start = 0.0

    def start(self) -> None:
        self._frame = 0
        self._virus_start = time.time()

    def render(self, surf: pygame.Surface) -> None:
        self._frame += 1
        fs = self._font_size
        h = self._h
        w = self._w
        font = self._font

        for s in self._streams:
            s_y = s['y']
            s_len = s['length']
            half = s_len >> 1

            for i in range(s_len):
                py = (s_y - i) * fs
                if py < -fs or py > h:
                    continue
                ch = random.choice(self.CHARS)
                if i == 0:
                    color = (255, 255, 255)
                elif i <= 2:
                    color = (0, 255, 0)
                elif i <= half:
                    color = (0, 180, 0)
                else:
                    color = (0, 80, 0)
                font.render_to(surf, (s['x'], py), ch, color)

            s['y'] += s['speed']
            if s['y'] * fs > h + s['length'] * fs:
                s['y'] = random.randint(-s['length'], 0)
                s['speed'] = random.randint(1, 3)

        # Red scan lines
        for _ in range(random.randint(2, 6)):
            y = random.randint(0, h)
            x2 = random.randint(w // 3, w)
            pygame.draw.line(surf, (180, 0, 0), (0, y), (x2, y),
                             random.randint(1, 2))

        # WARNING box
        if self._frame > 20:
            elapsed = time.time() - self._virus_start
            mins = int(elapsed) // 60
            secs = int(elapsed) % 60

            # Header
            draw_text_centered(surf, font, "COMPROMISED STATUS",
                               w // 2, 28, (200, 0, 0))

            # Warning box
            bx, by, bw, bh = w // 8, h // 3, w * 3 // 4, h // 4
            pygame.draw.rect(surf, (40, 0, 0), (bx, by, bw, bh))
            pygame.draw.rect(surf, (200, 200, 0), (bx, by, bw, bh), 2)

            font.render_to(surf, (bx + 10, by + 8), "WARNING", (255, 200, 0))

            warn_lines = [
                "SYSTEM STATUS: MALICIOUS NETWORK INTRUSION DETECTED...",
                "ANALYZING NETWORK PACKETS..",
                "CRITICAL FAILURES & ABUSE OF COMMAND, FAILURES.",
                "SYSTEM.. SYSFAIL FAILURES",
            ]
            for i, line in enumerate(warn_lines):
                font.render_to(surf, (bx + 10, by + 30 + i * 18),
                               line, (200, 0, 0))

            # Bottom banner
            jx = random.randint(-2, 2)
            draw_glow_text(
                surf, font,
                "VIRUS ATTACK DETECTED - ALL UNITS COMPROMISED",
                w // 2 + jx, h - 60, (255, 0, 0), glow_radius=2,
            )
            draw_text_centered(surf, font,
                               f"Attack Duration: {mins:02d}:{secs:02d}",
                               w // 2, h - 30, (200, 200, 200))


# ---------------------------------------------------------------------------
# TouchUI — main controller
# ---------------------------------------------------------------------------

class TouchUI:
    """
    PyGame-based fullscreen kiosk UI.
    Reads ExhibitState for display, dispatches commands to SequenceRunner.
    Never touches GPIO directly.

    Call run() from main — it owns the event loop and blocks until quit.
    """

    # Screen IDs
    _SCR_IDLE   = 'idle'
    _SCR_FLIGHT = 'flight'
    _SCR_FLEET  = 'fleet'
    _SCR_VIRUS  = 'virus'

    def __init__(self, state: ExhibitState, seq: "SequenceRunner") -> None:
        self._state = state
        self._seq = seq
        self._running = True
        self._screen = self._SCR_IDLE
        self._frame = 0

        # Slider state
        self._slider_active = False
        self._slider_frac = rot_to_frac(cfg.SLIDER_DEFAULT_ROT)

        # Fleet panel expanded
        self._fleet_expanded = False
        self._last_protected: frozenset = frozenset()

        # Toast
        self._toast_text = ''
        self._toast_until = 0.0

        pygame.init()
        pygame.mouse.set_visible(False)

        try:
            self._display = pygame.display.set_mode((W, H), pygame.FULLSCREEN)
            log.info("Display opened — %dx%d FULLSCREEN", W, H)
        except Exception:
            log.warning("FULLSCREEN failed, falling back to NOFRAME")
            import os
            os.environ.setdefault('SDL_VIDEO_WINDOW_POS', '0,0')
            self._display = pygame.display.set_mode((W, H), pygame.NOFRAME)
            log.info("Display opened — %dx%d NOFRAME", W, H)
        pygame.display.set_caption("Variant Security Exhibit")
        self._clock = pygame.time.Clock()

        # Fonts
        pygame.freetype.init()
        self._fn_lg = pygame.freetype.SysFont('couriernew,courier,monospace', 32)
        self._fn_md = pygame.freetype.SysFont('couriernew,courier,monospace', 20)
        self._fn_sm = pygame.freetype.SysFont('couriernew,courier,monospace', 14)
        self._fn_xs = pygame.freetype.SysFont('couriernew,courier,monospace', 11)
        self._fn_title = pygame.freetype.SysFont('helvetica,arial,sans-serif', 28)
        self._fn_hdr = pygame.freetype.SysFont('helvetica,arial,sans-serif', 16)
        self._fn_big = pygame.freetype.SysFont('couriernew,courier,monospace', 42)

        # Matrix rain (lazy init on first virus screen)
        self._matrix: MatrixRain | None = None

        # Hit zones (populated during render)
        self._zone_slider = pygame.Rect(0, 0, 0, 0)
        self._zone_variant = pygame.Rect(0, 0, 0, 0)
        self._zone_virus = pygame.Rect(0, 0, 0, 0)
        self._zone_done = pygame.Rect(0, 0, 0, 0)
        self._zone_drones: dict[int, pygame.Rect] = {}

        log.info("TouchUI (PyGame) ready — %dx%d @ %d fps", W, H, FPS)

    # ── Main loop ─────────────────────────────────────────────────────────

    def run(self) -> None:
        """Blocking event loop. Returns on quit/Escape/shutdown."""
        try:
            while self._running:
                self._handle_events()
                self._sync_screen()
                self._render()
                self._clock.tick(FPS)
                self._frame += 1
        except KeyboardInterrupt:
            log.info("KeyboardInterrupt in UI loop")
        finally:
            pygame.quit()

    def request_quit(self) -> None:
        self._running = False

    # ── Event handling ────────────────────────────────────────────────────

    def _handle_events(self) -> None:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                self._running = False
            elif ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
                self._running = False

            elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
                self._on_tap(ev.pos)
            elif ev.type == pygame.MOUSEMOTION and pygame.mouse.get_pressed()[0]:
                self._on_drag(ev.pos)
            elif ev.type == pygame.MOUSEBUTTONUP and ev.button == 1:
                self._on_release(ev.pos)

    def _on_tap(self, pos: tuple) -> None:
        x, y = pos

        if self._screen in (self._SCR_FLIGHT, self._SCR_FLEET):
            if self._zone_slider.collidepoint(x, y):
                st = self._state.current
                if st in (State.FLIGHT, State.ADJUSTING_HEIGHT):
                    self._slider_active = True
                    self._slider_frac = self._y_to_frac(y, self._zone_slider)
                return

        if self._screen == self._SCR_FLIGHT:
            if self._zone_variant.collidepoint(x, y):
                if self._state.current == State.FLIGHT:
                    self._fleet_expanded = True
                    self._screen = self._SCR_FLEET
                return
            if self._zone_virus.collidepoint(x, y):
                self._trigger_virus()
                return

        if self._screen == self._SCR_FLEET:
            for d_id, zone in self._zone_drones.items():
                if zone.collidepoint(x, y):
                    self._state.toggle_drone(d_id)
                    return
            if self._zone_done.collidepoint(x, y):
                self._fleet_expanded = False
                self._screen = self._SCR_FLIGHT
                return

    def _on_drag(self, pos: tuple) -> None:
        if self._slider_active:
            self._slider_frac = self._y_to_frac(pos[1], self._zone_slider)

    def _on_release(self, pos: tuple) -> None:
        if self._slider_active:
            self._slider_active = False
            target = round(frac_to_rot(self._slider_frac))
            self._slider_frac = rot_to_frac(target)
            if target != round(self._state.current_height_rot):
                self._seq.request_height_change(float(target))

    def _trigger_virus(self) -> None:
        if self._state.current != State.FLIGHT:
            return
        if len(self._state.protected_drones) >= 4:
            self._show_toast("Cannot infect — all drones protected")
            return
        self._screen = self._SCR_VIRUS
        if self._matrix is None:
            self._matrix = MatrixRain(W, H, self._fn_sm)
        self._matrix.start()
        threading.Thread(
            target=self._seq.trigger_virus,
            daemon=True,
            name="virus-ui",
        ).start()

    def _show_toast(self, msg: str, duration: float = 2.0) -> None:
        self._toast_text = msg
        self._toast_until = time.time() + duration

    # ── State sync ────────────────────────────────────────────────────────

    def _sync_screen(self) -> None:
        st = self._state.current
        if st == State.IDLE:
            if self._screen != self._SCR_IDLE:
                self._fleet_expanded = False
            self._screen = self._SCR_IDLE
        elif st in (State.FLIGHT, State.ADJUSTING_HEIGHT):
            if self._screen == self._SCR_IDLE:
                self._screen = self._SCR_FLIGHT
            elif self._screen == self._SCR_VIRUS:
                self._screen = self._SCR_FLIGHT
        elif st in (State.LAUNCH_SLOW, State.LAUNCH_FAST):
            self._screen = self._SCR_IDLE
        elif st in (State.VIRUS, State.POST_VIRUS_FLIGHT, State.RETURNING_HOME):
            if self._screen != self._SCR_VIRUS:
                if self._matrix is None:
                    self._matrix = MatrixRain(W, H, self._fn_sm)
                self._matrix.start()
                self._screen = self._SCR_VIRUS

        if not self._slider_active and self._screen in (self._SCR_FLIGHT, self._SCR_FLEET):
            self._slider_frac = rot_to_frac(self._state.current_height_rot)

    # ── Render dispatch ───────────────────────────────────────────────────

    def _render(self) -> None:
        surf = self._display
        surf.fill(C_BG)

        if self._screen == self._SCR_IDLE:
            self._render_idle(surf)
        elif self._screen == self._SCR_FLIGHT:
            self._render_flight(surf)
        elif self._screen == self._SCR_FLEET:
            self._render_fleet(surf)
        elif self._screen == self._SCR_VIRUS:
            self._render_virus(surf)

        # Toast overlay
        if self._toast_text and time.time() < self._toast_until:
            self._render_toast(surf)

        pygame.display.flip()

    # ── IDLE screen ───────────────────────────────────────────────────────

    def _render_idle(self, surf: pygame.Surface) -> None:
        cx, cy = W // 2, H // 2

        # Radar background
        draw_radar_bg(surf, cx, cy, min(W, H) * 0.45)

        # Breathing pulse
        t = self._frame / (FPS * 5.0)
        alpha = 0.4 + 0.6 * (0.5 - 0.5 * math.cos(t * 2 * math.pi))
        v = int(alpha * 255)

        # Panel
        pw, ph = min(W - 80, 520), min(H - 60, 380)
        px, py = cx - pw // 2, cy - ph // 2 + 10
        panel_r = pygame.Rect(px, py, pw, ph)

        border_c = lerp_color((10, 40, 60), C_BORDER_BLUE, alpha)
        draw_panel(surf, panel_r, C_PANEL, border_c, 2, corner=20)

        # Red indicators top corners
        pygame.draw.circle(surf, lerp_color((60, 0, 0), (255, 0, 0), alpha),
                           (px + 15, py + 15), 5)
        pygame.draw.circle(surf, lerp_color((60, 0, 0), (255, 0, 0), alpha),
                           (px + pw - 15, py + 15), 5)

        # Title
        title_c = (v, v, v)
        draw_glow_text(surf, self._fn_title, "VARIANT SECURITY",
                       cx, py + 50, title_c, glow_radius=2)

        # Brick logo
        logo_c = lerp_color((30, 80, 50), C_GREEN, alpha)
        draw_brick_logo(surf, cx, cy + 20, min(pw, ph) * 0.38, logo_c)

        # Footer
        st = self._state.current
        if st in (State.LAUNCH_SLOW, State.LAUNCH_FAST):
            footer = "LAUNCH SEQUENCE IN PROGRESS"
            footer_c = C_GREEN_MID
        else:
            footer = "SYSTEM STANDBY | LAUNCH INITIATION REQUIRED"
            footer_c = C_TEXT_DIM
        draw_text_centered(surf, self._fn_xs, footer,
                           cx, py + ph - 20, footer_c)

    # ── FLIGHT OPERATIONS screen ──────────────────────────────────────────

    def _render_flight(self, surf: pygame.Surface) -> None:
        # Header
        draw_text_centered(surf, self._fn_hdr, "FLIGHT OPERATIONS",
                           W // 2, 16, C_TEXT)
        pygame.draw.line(surf, C_BORDER, (10, 32), (W - 10, 32), 1)

        # Three-column layout
        col_y = 40
        col_h = H - col_y - 8
        slider_w = int(W * 0.15)
        variant_w = int(W * 0.50)
        virus_w = W - slider_w - variant_w - 24

        # --- ALTITUDE CONTROL (left) ---
        sr = pygame.Rect(6, col_y, slider_w, col_h)
        self._zone_slider = sr
        draw_panel(surf, sr, C_SLIDER_BG, C_BORDER_BLUE, 2, corner=10)
        draw_text_centered(surf, self._fn_xs, "ALTITUDE CONTROL",
                           sr.centerx, sr.top + 16, C_TEXT_DIM)
        self._draw_slider(surf, sr)

        # --- DRONE SECURITY (center) ---
        vr = pygame.Rect(slider_w + 12, col_y, variant_w, col_h)
        self._zone_variant = vr
        draw_panel(surf, vr, C_PANEL, C_BORDER_BLUE, 2, corner=12)
        draw_text_centered(surf, self._fn_hdr, "DRONE SECURITY",
                           vr.centerx, vr.top + 22, C_TEXT)

        # Brick logo in center
        logo_alpha = 0.5 + 0.5 * math.sin(self._frame / 30.0)
        logo_c = lerp_color((30, 60, 40), C_GREEN, logo_alpha)
        draw_brick_logo(surf, vr.centerx, vr.centery - 10,
                        min(vr.width, vr.height) * 0.35, logo_c)

        # Status text
        n_prot = len(self._state.protected_drones)
        if n_prot > 0:
            status = f"{n_prot}/4 DRONES PROTECTED"
            status_c = C_CYAN
        else:
            status = "TAP TO CONFIGURE DRONE FLEET"
            status_c = C_TEXT_DIM
        draw_text_centered(surf, self._fn_xs, status,
                           vr.centerx, vr.bottom - 22, status_c)

        # --- VIRUS ISOLATION (right) ---
        xr = pygame.Rect(slider_w + variant_w + 18, col_y, virus_w, col_h)
        self._zone_virus = xr
        enabled = self._state.current == State.FLIGHT
        virus_border = C_BORDER_RED if enabled else (40, 40, 40)
        draw_panel(surf, xr, C_VIRUS_BG, virus_border, 3, corner=10)
        draw_text_centered(surf, self._fn_xs, "VIRUS ISOLATION",
                           xr.centerx, xr.top + 18, C_TEXT if enabled else (60, 60, 60))

        # Warning triangles
        if enabled:
            for tx in [xr.left + 16, xr.right - 16]:
                pts = [(tx, xr.top + 10), (tx - 6, xr.top + 22), (tx + 6, xr.top + 22)]
                pygame.draw.polygon(surf, C_YELLOW, pts)
                pygame.draw.polygon(surf, C_BG, pts, 1)

        bio_c = C_RED if enabled else (50, 50, 50)
        draw_biohazard(surf, xr.centerx, xr.centery,
                       min(xr.width, xr.height) * 0.35, bio_c)

        label_c = C_RED_BRIGHT if enabled else (60, 60, 60)
        draw_text_centered(surf, self._fn_md, "VIRUS",
                           xr.centerx, xr.bottom - 35, label_c)
        draw_text_centered(surf, self._fn_xs, "TAP TO ISOLATE",
                           xr.centerx, xr.bottom - 16, C_TEXT_DIM if enabled else (40, 40, 40))

    # ── FLEET OVERVIEW (expanded drone selection) ─────────────────────────

    def _render_fleet(self, surf: pygame.Surface) -> None:
        # Header
        draw_text_centered(surf, self._fn_hdr, "FLEET OVERVIEW",
                           W // 2, 16, C_TEXT)
        pygame.draw.line(surf, C_BORDER, (10, 32), (W - 10, 32), 1)

        col_y = 40
        col_h = H - col_y - 8
        slider_w = int(W * 0.15)

        # Slider (left)
        sr = pygame.Rect(6, col_y, slider_w, col_h)
        self._zone_slider = sr
        draw_panel(surf, sr, C_SLIDER_BG, C_BORDER_BLUE, 2, corner=10)
        draw_text_centered(surf, self._fn_xs, "HEIGHT",
                           sr.centerx, sr.top + 16, C_TEXT_DIM)
        self._draw_slider(surf, sr)

        # Drone grid (center + right)
        grid_x = slider_w + 16
        grid_w = W - grid_x - 8
        grid_y = col_y + 4
        grid_h = col_h - 50
        cell_w = (grid_w - 12) // 2
        cell_h = (grid_h - 12) // 2

        positions = {1: (0, 0), 2: (0, 1), 3: (1, 0), 4: (1, 1)}
        for d_id, (row, col) in positions.items():
            cx = grid_x + col * (cell_w + 8)
            cy_pos = grid_y + row * (cell_h + 8)
            rect = pygame.Rect(cx, cy_pos, cell_w, cell_h)
            self._zone_drones[d_id] = rect
            self._draw_drone_card(surf, rect, d_id)

        # DONE button
        done_r = pygame.Rect(grid_x, grid_y + grid_h + 4, grid_w, 38)
        self._zone_done = done_r
        pygame.draw.rect(surf, C_PANEL_LITE, done_r)
        pygame.draw.rect(surf, C_BORDER, done_r, 2)
        draw_text_centered(surf, self._fn_md, "DONE",
                           done_r.centerx, done_r.centery, C_TEXT)

        # Footer
        draw_text_centered(surf, self._fn_xs,
                           "SYSTEM STANDBY | LAUNCH INITIATION REQUIRED",
                           W // 2, H - 12, C_TEXT_DIM)

    def _draw_drone_card(self, surf: pygame.Surface, rect: pygame.Rect,
                         d_id: int) -> None:
        color = DRONE_COLORS[d_id]
        is_prot = d_id in self._state.protected_drones
        cx, cy = rect.centerx, rect.centery

        if is_prot:
            pygame.draw.rect(surf, color, rect)
            pygame.draw.rect(surf, (255, 255, 255), rect, 2)
            draw_text_centered(surf, self._fn_md, DRONE_LABELS[d_id],
                               cx, rect.top + 22, (255, 255, 255))
            draw_shield(surf, cx, cy, 18, (255, 255, 255))
            draw_text_centered(surf, self._fn_sm, "READY",
                               cx, rect.bottom - 20, (255, 255, 255))
        else:
            pygame.draw.rect(surf, C_PANEL, rect)
            pygame.draw.rect(surf, color, rect, 2)
            draw_text_centered(surf, self._fn_md, DRONE_LABELS[d_id],
                               cx, rect.top + 22, color)
            draw_shield(surf, cx, cy, 16, (80, 80, 80))
            draw_text_centered(surf, self._fn_sm, "PENDING",
                               cx, rect.bottom - 20, C_TEXT_DIM)

    # ── VIRUS / COMPROMISED screen ────────────────────────────────────────

    def _render_virus(self, surf: pygame.Surface) -> None:
        if self._matrix is not None:
            self._matrix.render(surf)

    # ── Slider rendering ──────────────────────────────────────────────────

    def _draw_slider(self, surf: pygame.Surface, area: pygame.Rect) -> None:
        pad = 35
        track_x = area.centerx
        track_top = area.top + pad
        track_bot = area.bottom - 60
        track_h = track_bot - track_top
        if track_h < 10:
            return

        # Track background
        pygame.draw.rect(surf, C_SLIDER_TRACK,
                         (track_x - 8, track_top, 16, track_h))
        pygame.draw.rect(surf, C_BORDER,
                         (track_x - 8, track_top, 16, track_h), 1)

        # Tick marks
        for rot in range(cfg.SLIDER_MIN_ROT, cfg.SLIDER_MAX_ROT + 1, 2):
            frac = rot_to_frac(rot)
            y = int(track_bot - frac * track_h)
            pygame.draw.line(surf, C_BORDER,
                             (track_x - 12, y), (track_x + 12, y), 1)
            self._fn_xs.render_to(surf, (track_x + 16, y - 5),
                                  str(rot), C_TEXT_DIM)

        # Green fill
        frac = self._slider_frac
        thumb_y = int(track_bot - frac * track_h)
        pygame.draw.rect(surf, C_GREEN,
                         (track_x - 7, thumb_y, 14, track_bot - thumb_y))

        # Thumb
        pygame.draw.rect(surf, C_GREEN,
                         (track_x - 16, thumb_y - 5, 32, 10))
        pygame.draw.rect(surf, C_GREEN_MID,
                         (track_x - 16, thumb_y - 5, 32, 10), 1)

        # Value display
        st = self._state.current
        if st == State.ADJUSTING_HEIGHT:
            fr_v = round(self._state.current_height_rot)
            to_v = round(self._state.target_height_rot)
            val_text = f"{fr_v}>{to_v}"
        else:
            val_text = f"{frac_to_rot(frac):.0f}"

        draw_text_centered(surf, self._fn_lg, val_text,
                           area.centerx, area.bottom - 30, C_GREEN)

    # ── Toast ─────────────────────────────────────────────────────────────

    def _render_toast(self, surf: pygame.Surface) -> None:
        r = self._fn_md.get_rect(self._toast_text)
        bw, bh = r.width + 32, r.height + 20
        bx, by = (W - bw) // 2, (H - bh) // 2
        pygame.draw.rect(surf, (26, 26, 26), (bx, by, bw, bh))
        pygame.draw.rect(surf, C_YELLOW, (bx, by, bw, bh), 2)
        draw_text_centered(surf, self._fn_md, self._toast_text,
                           W // 2, H // 2, C_YELLOW)

    # ── Coordinate helpers ────────────────────────────────────────────────

    @staticmethod
    def _y_to_frac(y: int, slider_rect: pygame.Rect) -> float:
        pad = 35
        track_top = slider_rect.top + pad
        track_bot = slider_rect.bottom - 60
        track_h = max(1, track_bot - track_top)
        frac = (track_bot - y) / track_h
        return max(0.0, min(1.0, frac))
