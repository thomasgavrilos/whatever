#!/usr/bin/env python3
"""
touch_ui.py — TouchUI
Tkinter fullscreen kiosk for the 7" DSI touchscreen (800 × 480 px).
All rendering is programmatic — no PNG files required.
"""

import logging
import math
import random
import threading
import tkinter as tk
from typing import TYPE_CHECKING

from config import cfg
from exhibit_state import ExhibitState, State

if TYPE_CHECKING:
    from sequence_runner import SequenceRunner

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# UI palette
# ---------------------------------------------------------------------------
UI_BG        = '#000000'
PANEL_BG     = '#1a2332'
PANEL_BORDER = '#3a5068'
DRONE_COLORS = {1: '#00CC00', 2: '#2196F3', 3: '#FFD600', 4: '#9C27B0'}
DRONE_LABELS = {1: 'D1',    2: 'D2',    3: 'D3',    4: 'D4'}
SLIDER_GREEN = '#00FF00'
SLIDER_TRACK = '#2a3a4a'
SLIDER_BG    = '#1a2332'
FRAME_BLUE   = '#4a7aaa'
FRAME_RED    = '#8B0000'
VIRUS_RED    = '#C62828'
DONE_BLUE    = '#2196F3'
UI_REFRESH_MS = 100
_SLIDER_RANGE = max(1, cfg.SLIDER_MAX_ROT - cfg.SLIDER_MIN_ROT)


# ---------------------------------------------------------------------------
# IdleScreen
# ---------------------------------------------------------------------------

class IdleScreen:
    """Breathing brick logo on a subtle radar background. ~30 fps after-loop."""

    PULSE_FRAMES = 150   # 150 × 33 ms ≈ 5 s full cycle (half-cosine)
    PULSE_LO     = 0.40
    PULSE_HI     = 1.00

    def __init__(self, parent: tk.Widget) -> None:
        self._parent  = parent
        self._canvas  = tk.Canvas(parent, bg=UI_BG, highlightthickness=0)
        self._after_id = None
        self._running  = False
        self._t        = 0

    def show(self) -> None:
        self._canvas.place(x=0, y=0, relwidth=1, relheight=1)
        self._canvas.tk.call('raise', self._canvas._w)  # Python 3.13 compat
        self._running = True
        self._t = 0
        self._draw()

    def hide(self) -> None:
        self._running = False
        if self._after_id:
            self._parent.after_cancel(self._after_id)
            self._after_id = None
        self._canvas.place_forget()

    def _draw(self) -> None:
        if not self._running:
            return
        c = self._canvas
        c.delete('all')
        w = self._parent.winfo_width()
        h = self._parent.winfo_height()
        cx, cy = w // 2, h // 2

        # Half-cosine breathing pulse
        phase      = (self._t % self.PULSE_FRAMES) / float(self.PULSE_FRAMES)
        alpha      = self.PULSE_LO + (self.PULSE_HI - self.PULSE_LO) * (
                         0.5 - 0.5 * math.cos(phase * 2 * math.pi))
        v          = int(alpha * 255)
        logo_color = f'#{v:02x}{v:02x}{v:02x}'

        # Panel border pulses in blue alongside logo
        bv           = int(alpha * 0.6 * 255)
        border_color = f'#0a{bv:02x}{min(int(bv * 1.3), 255):02x}'

        # Radar background
        self._draw_radar_bg(c, cx, cy, min(w, h) * 0.45)

        # Metallic panel
        pw = min(w - 80, 500)
        ph = min(h - 60, 360)
        px = cx - pw // 2
        py = cy - ph // 2 + 10
        c.create_rectangle(px, py, px + pw, py + ph,
                           fill=PANEL_BG, outline=border_color, width=2)

        # Title
        c.create_text(cx, py + 40, text="VARIANT SECURITY", fill=logo_color,
                      font=('Helvetica', 30, 'bold'), anchor='center')

        # Brick logo centered in panel
        self._draw_logo_mark(c, cx, cy + 15, min(pw, ph) * 0.42, logo_color)

        # Footer
        c.create_text(cx, py + ph - 25, text="SYSTEM STANDBY",
                      fill='#556677', font=('Courier', 12), anchor='center')

        self._t += 1
        self._after_id = self._parent.after(33, self._draw)

    @staticmethod
    def _draw_radar_bg(canvas: tk.Canvas, cx: int, cy: int, max_r: float) -> None:
        color = '#0a2a0a'
        for i in range(1, 6):
            r = max_r * i / 5
            canvas.create_oval(cx - r, cy - r, cx + r, cy + r,
                               outline=color, fill='', width=1)
        canvas.create_line(cx - max_r, cy, cx + max_r, cy, fill=color, width=1)
        canvas.create_line(cx, cy - max_r, cx, cy + max_r, fill=color, width=1)
        for a_deg in [45, 135]:
            a = math.radians(a_deg)
            dx, dy = math.cos(a) * max_r, math.sin(a) * max_r
            canvas.create_line(cx - dx, cy - dy, cx + dx, cy + dy,
                               fill=color, width=1)

    @staticmethod
    def _draw_logo_mark(
        canvas: tk.Canvas, cx: int, cy: int, size: float, color: str
    ) -> None:
        """
        Staggered brick layout:
            [ ][ ]         row 0 — 2 blocks, +0.5 col offset
          [ ][ ][ ]        row 1 — 3 blocks
          [ ][ ][ ]        row 2 — 3 blocks
            [ ][ ]         row 3 — 2 blocks, +0.5 col offset
        """
        cell  = size * 0.27
        gap   = size * 0.09
        col_w = cell + gap
        row_h = cell + gap
        ox    = cx - (3 * col_w - gap) / 2
        oy    = cy - (4 * row_h - gap) / 2
        layout = [(0, 0.5, 2), (1, 0.0, 3), (2, 0.0, 3), (3, 0.5, 2)]
        for (row, col_off, n) in layout:
            for col in range(n):
                x = ox + (col + col_off) * col_w
                y = oy + row * row_h
                canvas.create_rectangle(x, y, x + cell, y + cell,
                                        fill=color, outline='', width=0)


# ---------------------------------------------------------------------------
# MatrixOverlay
# ---------------------------------------------------------------------------

class MatrixOverlay:
    """Full-screen Matrix rain + COMPROMISED text + red scan lines. 15 fps."""

    CHARS = "abcdefghijklmnopqrstuvwxyz0123456789@#$%&*!?<>{}[]=/\\|~^"
    COLS  = 30
    FPS   = 15
    COMPROMISED_DELAY = 15   # frames before COMPROMISED text appears

    _COLOR_HEAD   = '#FFFFFF'
    _COLOR_NEAR   = '#00FF00'
    _COLOR_MID    = '#00CC00'
    _COLOR_TAIL   = '#005500'
    _COMPROMISED_FONT = ('Courier', 42, 'bold')

    def __init__(self, parent: tk.Widget) -> None:
        self._parent  = parent
        self._canvas  = tk.Canvas(parent, bg='black', highlightthickness=0)
        self._running  = False
        self._after_id = None
        self._frame    = 0
        self._streams: list[dict] = []
        self._w = self._h = 0
        self._font_size = 14
        self._font = ('Courier', 14, 'bold')

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> None:
        self._canvas.place(x=0, y=0, relwidth=1, relheight=1)
        self._canvas.tk.call('raise', self._canvas._w)
        self._running = True
        self._frame   = 0
        self._parent.update_idletasks()
        self._w = self._parent.winfo_width()
        self._h = self._parent.winfo_height()
        col_w = max(1, self._w // self.COLS)
        self._font_size = max(8, col_w - 2)
        self._font = ('Courier', self._font_size, 'bold')
        rows = self._h // self._font_size + 2
        self._streams = []
        for c in range(self.COLS):
            self._streams.append({
                'x':      c * col_w + col_w // 2,
                'y':      random.randint(-rows, 0),
                'speed':  random.randint(1, 3),
                'length': random.randint(8, rows),
            })
        self._tick()

    def stop(self) -> None:
        self._running = False
        if self._after_id:
            self._parent.after_cancel(self._after_id)
            self._after_id = None
        self._canvas.place_forget()

    def _tick(self) -> None:
        if not self._running:
            return
        c = self._canvas
        c.delete('all')
        self._frame += 1

        font = self._font
        font_size = self._font_size
        h = self._h
        _choice = random.choice
        chars = self.CHARS
        create_text = c.create_text

        for s in self._streams:
            s_y = s['y']
            s_len = s['length']
            half_len = s_len >> 1

            top_row = s_y - s_len + 1
            if top_row * font_size > h:
                s['y'] = random.randint(-s_len, 0)
                s['speed'] = random.randint(1, 3)
                continue

            for i in range(s_len):
                py = (s_y - i) * font_size
                if py < -font_size or py > h:
                    continue
                if i == 0:
                    color = self._COLOR_HEAD
                elif i <= 2:
                    color = self._COLOR_NEAR
                elif i <= half_len:
                    color = self._COLOR_MID
                else:
                    color = self._COLOR_TAIL
                create_text(s['x'], py, text=_choice(chars), fill=color,
                            font=font, anchor='center')
            s['y'] = s_y + s['speed']

        w = self._w
        for _ in range(random.randint(1, 4)):
            y   = random.randint(0, h)
            x2  = random.randint(w // 3, w)
            c.create_line(0, y, x2, y, fill='#CC0000',
                          width=random.randint(1, 2))

        if self._frame > self.COMPROMISED_DELAY:
            jx = w // 2 + random.randint(-3, 3)
            jy = h // 2 + random.randint(-2, 2)
            comp_font = self._COMPROMISED_FONT
            create_text(jx + 2, jy + 2, text="COMPROMISED",
                        fill='#660000', font=comp_font, anchor='center')
            main_color = '#FF4444' if random.random() < 0.08 else '#FF0000'
            create_text(jx, jy, text="COMPROMISED",
                        fill=main_color, font=comp_font, anchor='center')

        self._after_id = self._parent.after(1000 // self.FPS, self._tick)


# ---------------------------------------------------------------------------
# TouchUI
# ---------------------------------------------------------------------------

class TouchUI:
    """
    Root UI controller.
    Reads ExhibitState for display, dispatches commands to SequenceRunner.
    Never touches GPIO directly.
    """

    def __init__(
        self,
        root:  tk.Tk,
        state: ExhibitState,
        seq:   "SequenceRunner",
    ) -> None:
        self._root  = root
        self._state = state
        self._seq   = seq

        self._last_ui_state:   str | None = None
        self._variant_expanded: bool      = False
        self._slider_active:    bool      = False
        self._slider_frac:      float     = self._rot_to_frac(cfg.SLIDER_DEFAULT_ROT)
        self._last_protected:   frozenset = frozenset()

        # Canvas / widget references populated by build methods
        self._slider_canvas:  tk.Canvas | None = None
        self._alt_value:      tk.Label  | None = None
        self._variant_canvas: tk.Canvas | None = None
        self._variant_outer:  tk.Frame  | None = None
        self._drone_frame:    tk.Frame  | None = None
        self._drone_canvases: dict[int, tk.Canvas] = {}
        self._drone_frames:   dict[int, tk.Frame]  = {}
        self._virus_outer:    tk.Frame  | None = None
        self._virus_canvas:   tk.Canvas | None = None
        self._toast_label:    tk.Label  | None = None
        self._toast_after:    str | None = None

        self._configure_root()
        self._idle_screen  = IdleScreen(root)
        self._flight_frame = self._build_flight_frame()
        self._matrix       = MatrixOverlay(root)
        self._toast_label  = self._build_toast()

        self._idle_screen.show()
        self._refresh()
        log.info("TouchUI ready")

    # ── Root window ───────────────────────────────────────────────────────────

    def _configure_root(self) -> None:
        self._root.title("Variant Security Exhibit")
        self._root.configure(bg=UI_BG)
        self._root.overrideredirect(True)
        self._root.geometry(f'{cfg.DISPLAY_WIDTH}x{cfg.DISPLAY_HEIGHT}+0+0')
        self._root.config(cursor="none")
        self._root.bind('<Escape>', lambda e: self._quit())

    # ── Flight frame ──────────────────────────────────────────────────────────

    def _build_flight_frame(self) -> tk.Frame:
        frame = tk.Frame(self._root, bg=UI_BG)
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=15)
        frame.grid_columnconfigure(1, weight=50)
        frame.grid_columnconfigure(2, weight=35)
        self._build_slider_zone(frame)
        self._build_variant_zone(frame)
        self._build_virus_zone(frame)
        return frame

    def _build_slider_zone(self, parent: tk.Frame) -> None:
        f = tk.Frame(parent, bg=SLIDER_BG)
        f.grid(row=0, column=0, sticky='nsew', padx=2, pady=2)

        tk.Label(f, text='HEIGHT', bg=SLIDER_BG, fg='#666666',
                 font=('Courier', 10, 'bold')).pack(pady=(8, 0))

        self._slider_canvas = tk.Canvas(f, bg=SLIDER_BG, highlightthickness=0)
        self._slider_canvas.pack(fill='both', expand=True, padx=6, pady=2)

        tk.Label(f, text='ALTITUDE:', bg=SLIDER_BG,
                 fg=SLIDER_GREEN, font=('Courier', 9, 'bold')).pack()
        self._alt_value = tk.Label(f, text=str(cfg.SLIDER_DEFAULT_ROT),
                                   bg=SLIDER_BG, fg=SLIDER_GREEN,
                                   font=('Courier', 28, 'bold'))
        self._alt_value.pack(pady=(0, 8))

        self._slider_canvas.bind('<Button-1>',        self._on_slider_tap)
        self._slider_canvas.bind('<B1-Motion>',        self._on_slider_drag)
        self._slider_canvas.bind('<ButtonRelease-1>',  self._on_slider_release)
        self._slider_canvas.bind('<Configure>',        lambda e: self._draw_slider())

    def _build_variant_zone(self, parent: tk.Frame) -> None:
        self._variant_outer = tk.Frame(parent, bg=PANEL_BG,
                                       highlightbackground=FRAME_BLUE,
                                       highlightthickness=2)
        self._variant_outer.grid(row=0, column=1, sticky='nsew', padx=4, pady=4)

        # Collapsed: logo canvas
        self._variant_canvas = tk.Canvas(self._variant_outer, bg=PANEL_BG,
                                         highlightthickness=0)
        self._variant_canvas.pack(fill='both', expand=True)
        self._variant_canvas.bind('<Button-1>',  lambda e: self._on_variant_tapped())
        self._variant_canvas.bind('<Configure>',  lambda e: self._draw_variant_logo())

        # Expanded: 2×2 drone grid + DONE
        self._drone_frame = tk.Frame(self._variant_outer, bg=PANEL_BG)
        self._drone_frame.grid_rowconfigure(0, weight=1)
        self._drone_frame.grid_rowconfigure(1, weight=1)
        self._drone_frame.grid_rowconfigure(2, weight=0)
        self._drone_frame.grid_columnconfigure(0, weight=1)
        self._drone_frame.grid_columnconfigure(1, weight=1)

        positions = {1: (0, 0), 2: (0, 1), 3: (1, 0), 4: (1, 1)}
        for d_id, (r, c) in positions.items():
            fr = tk.Frame(self._drone_frame, bg=PANEL_BG,
                          highlightbackground=DRONE_COLORS[d_id],
                          highlightthickness=2)
            fr.grid(row=r, column=c, sticky='nsew', padx=3, pady=3)
            cv = tk.Canvas(fr, bg=PANEL_BG, highlightthickness=0)
            cv.pack(fill='both', expand=True)
            cv.bind('<Button-1>', lambda e, did=d_id: self._on_drone_toggle(did))
            self._drone_canvases[d_id] = cv
            self._drone_frames[d_id]   = fr

        done_btn = tk.Button(
            self._drone_frame, text='DONE',
            font=('Courier', 14, 'bold'),
            bg=DONE_BLUE, fg='white',
            activebackground='#1976D2',
            relief='flat', bd=0, pady=6,
            command=self._on_variant_collapse,
        )
        done_btn.grid(row=2, column=0, columnspan=2, sticky='ew', padx=6, pady=4)

    def _build_virus_zone(self, parent: tk.Frame) -> None:
        self._virus_outer = tk.Frame(parent, bg='#1a0a0a',
                                     highlightbackground=FRAME_RED,
                                     highlightthickness=3)
        self._virus_outer.grid(row=0, column=2, sticky='nsew', padx=4, pady=4)

        self._virus_canvas = tk.Canvas(self._virus_outer, bg='#1a0a0a',
                                       highlightthickness=0)
        self._virus_canvas.pack(fill='both', expand=True)
        self._virus_canvas.bind('<Button-1>',  lambda e: self._on_virus_tapped())
        self._virus_canvas.bind('<Configure>',  lambda e: self._draw_virus_button())

    def _build_toast(self) -> tk.Label:
        return tk.Label(self._root, text='', font=('Courier', 14, 'bold'),
                        fg='#FFD600', bg='#1a1a1a', padx=16, pady=8)

    # ── Draw: slider ─────────────────────────────────────────────────────────

    def _draw_slider(self) -> None:
        c = self._slider_canvas
        if c is None:
            return
        c.delete('all')
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 5 or h < 5:
            return

        pad      = 20
        track_x  = w // 2
        track_top = pad
        track_bot = h - pad
        track_h   = track_bot - track_top

        # Track
        c.create_rectangle(track_x - 10, track_top, track_x + 10, track_bot,
                           fill=SLIDER_TRACK, outline=PANEL_BORDER, width=1)

        # Tick marks with numbers
        for rot in range(cfg.SLIDER_MIN_ROT, cfg.SLIDER_MAX_ROT + 1, 2):
            frac = self._rot_to_frac(rot)
            y    = track_bot - frac * track_h
            c.create_line(track_x - 14, y, track_x + 14, y, fill='#3a5068', width=1)
            c.create_text(track_x + 22, y, text=str(rot), fill='#556677',
                         font=('Courier', 8), anchor='w')

        # Green fill from bottom to thumb
        frac   = self._slider_frac
        thumb_y = track_bot - frac * track_h
        c.create_rectangle(track_x - 9, thumb_y, track_x + 9, track_bot,
                           fill=SLIDER_GREEN, outline='')

        # Thumb bar
        c.create_rectangle(track_x - 20, thumb_y - 7,
                           track_x + 20, thumb_y + 7,
                           fill=SLIDER_GREEN, outline='#00CC00', width=1)

        # Altitude label — show "from→to" during ADJUSTING_HEIGHT
        if self._alt_value:
            st = self._state.current
            if st == State.ADJUSTING_HEIGHT:
                fr_v = round(self._state.current_height_rot)
                to_v = round(self._state.target_height_rot)
                self._alt_value.config(text=f'{fr_v}→{to_v}', fg=SLIDER_GREEN)
            else:
                rot_val = self._frac_to_rot(frac)
                self._alt_value.config(text=f'{rot_val:.0f}', fg=SLIDER_GREEN)

    # ── Draw: variant logo ─────────────────────────────────────────────────────

    def _draw_variant_logo(self) -> None:
        c = self._variant_canvas
        if c is None:
            return
        c.delete('all')
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 10 or h < 10:
            return
        cx, cy = w // 2, h // 2

        c.create_text(cx, 25, text="VARIANT CONTROL", fill='#cccccc',
                      font=('Helvetica', 14, 'bold'), anchor='center')
        IdleScreen._draw_logo_mark(c, cx, cy, min(w, h) * 0.40, 'white')
        c.create_text(cx, h - 16, text="TAP TO CONFIGURE", fill='#556677',
                      font=('Courier', 10), anchor='center')

    # ── Draw: drone toggle card ────────────────────────────────────────────────

    def _draw_drone_toggle(self, d_id: int) -> None:
        cv = self._drone_canvases.get(d_id)
        fr = self._drone_frames.get(d_id)
        if cv is None or fr is None:
            return
        cv.delete('all')
        w = cv.winfo_width()
        h = cv.winfo_height()
        if w < 10 or h < 10:
            return
        cx, cy = w // 2, h // 2

        is_protected = d_id in self._state.protected_drones
        color = DRONE_COLORS[d_id]

        if is_protected:
            cv.configure(bg=color)
            fr.configure(bg=color, highlightbackground=color)
            cv.create_text(cx, 18, text=DRONE_LABELS[d_id],
                           fill='white', font=('Helvetica', 16, 'bold'), anchor='center')
            self._draw_lock_icon(cv, cx, cy - 5, 20, 'white')
            cv.create_text(cx, h - 18, text="SELECTED",
                           fill='white', font=('Courier', 11, 'bold'), anchor='center')
        else:
            cv.configure(bg=PANEL_BG)
            fr.configure(bg=PANEL_BG, highlightbackground=PANEL_BORDER)
            cv.create_text(cx, 18, text=DRONE_LABELS[d_id],
                           fill='#888888', font=('Helvetica', 16, 'bold'), anchor='center')
            self._draw_drone_silhouette(cv, cx, cy, min(w, h) * 0.3, '#556677')

    # ── Draw: virus button ─────────────────────────────────────────────────────

    def _draw_virus_button(self, enabled: bool = True) -> None:
        c = self._virus_canvas
        if c is None:
            return
        c.delete('all')
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 10 or h < 10:
            return
        cx, cy = w // 2, h // 2

        if enabled:
            bg, icon_color, text_color, hdr_color = '#1a0a0a','#CC0000','#FF2222','#cccccc'
        else:
            bg, icon_color, text_color, hdr_color = '#0a0a0a','#333333','#444444','#444444'

        c.configure(bg=bg)
        c.create_text(cx, 22, text="VIRUS TRIGGER", fill=hdr_color,
                      font=('Helvetica', 12, 'bold'), anchor='center')
        self._draw_biohazard(c, cx, cy, min(w, h) * 0.35, icon_color)
        c.create_text(cx, h - 25, text="VIRUS",
                      fill=text_color, font=('Helvetica', 20, 'bold'), anchor='center')

    # ── Static draw primitives ─────────────────────────────────────────────────

    @staticmethod
    def _draw_drone_silhouette(
        canvas: tk.Canvas, cx: int, cy: int, size: float, color: str,
    ) -> None:
        s = size / 30
        canvas.create_rectangle(cx - 6*s, cy - 3*s, cx + 6*s, cy + 3*s,
                                fill=color, outline='')
        for dx, dy in [(-1, -1), (1, -1), (-1, 1), (1, 1)]:
            ax, ay = cx + dx * 14*s, cy + dy * 10*s
            canvas.create_line(cx + dx*5*s, cy + dy*2*s, ax, ay,
                               fill=color, width=max(1, int(1.5*s)))
            canvas.create_oval(ax - 6*s, ay - 6*s, ax + 6*s, ay + 6*s,
                               outline=color, fill='', width=max(1, int(s)))

    @staticmethod
    def _draw_lock_icon(
        canvas: tk.Canvas, cx: int, cy: int, size: float, color: str,
    ) -> None:
        s = size / 12
        canvas.create_rectangle(cx - 5*s, cy - 2*s, cx + 5*s, cy + 6*s,
                                fill=color, outline='')
        canvas.create_arc(cx - 4*s, cy - 8*s, cx + 4*s, cy,
                         start=0, extent=180, style='arc',
                         outline=color, width=max(1, int(2*s)))

    @staticmethod
    def _draw_biohazard(
        canvas: tk.Canvas, cx: int, cy: int, size: float, color: str,
    ) -> None:
        s = size / 50
        for angle_deg in [90, 210, 330]:
            a  = math.radians(angle_deg)
            ox = cx + math.cos(a) * 16*s
            oy = cy - math.sin(a) * 16*s
            r  = 14*s
            canvas.create_oval(ox - r, oy - r, ox + r, oy + r,
                               outline=color, fill='', width=max(1, int(2*s)))
        canvas.create_oval(cx - 5*s, cy - 5*s, cx + 5*s, cy + 5*s,
                          outline=color, fill='', width=max(1, int(2*s)))
        canvas.create_oval(cx - 2*s, cy - 2*s, cx + 2*s, cy + 2*s,
                          fill=color, outline='')

    # ── Touch event handlers ───────────────────────────────────────────────────

    def _on_slider_tap(self, event: tk.Event) -> None:
        if self._state.current not in (State.FLIGHT, State.ADJUSTING_HEIGHT):
            return
        self._slider_active = True
        self._slider_frac   = self._y_to_frac(event.y)
        self._draw_slider()

    def _on_slider_drag(self, event: tk.Event) -> None:
        if not self._slider_active:
            return
        self._slider_frac = self._y_to_frac(event.y)
        self._draw_slider()

    def _on_slider_release(self, event: tk.Event) -> None:
        if not self._slider_active:
            return
        self._slider_active = False
        target = round(self._frac_to_rot(self._slider_frac))
        self._slider_frac = self._rot_to_frac(target)
        self._draw_slider()
        if target != round(self._state.current_height_rot):
            self._seq.request_height_change(float(target))

    def _on_variant_tapped(self) -> None:
        if self._state.current != State.FLIGHT:
            return
        self._variant_expanded = True
        self._variant_canvas.pack_forget()
        self._drone_frame.pack(fill='both', expand=True)
        self._update_all_drone_toggles()

    def _on_variant_collapse(self) -> None:
        self._variant_expanded = False
        self._drone_frame.pack_forget()
        self._variant_canvas.pack(fill='both', expand=True)
        self._root.after(50, self._draw_variant_logo)

    def _on_drone_toggle(self, drone_id: int) -> None:
        self._state.toggle_drone(drone_id)
        self._update_all_drone_toggles()

    def _update_all_drone_toggles(self) -> None:
        for d_id in self._drone_canvases:
            self._root.after(10, lambda did=d_id: self._draw_drone_toggle(did))

    def _on_virus_tapped(self) -> None:
        if self._state.current != State.FLIGHT:
            return
        if len(self._state.protected_drones) >= 4:
            self._show_toast("Cannot infect — all drones protected")
            return
        self._matrix.start()
        threading.Thread(
            target=self._seq.trigger_virus,
            daemon=True,
            name="virus-ui",
        ).start()

    # ── Refresh loop ──────────────────────────────────────────────────────────

    def _refresh(self) -> None:
        """100ms polling loop — syncs visible screen to ExhibitState.current."""
        try:
            self._refresh_inner()
        except Exception:
            log.exception("_refresh_inner error — UI loop continuing")
        finally:
            self._root.after(UI_REFRESH_MS, self._refresh)

    def _refresh_inner(self) -> None:
        st           = self._state.current
        is_idle      = st == State.IDLE
        in_flight    = st == State.FLIGHT
        adjusting    = st == State.ADJUSTING_HEIGHT
        is_virus     = st in (State.VIRUS, State.POST_VIRUS_FLIGHT, State.RETURNING_HOME)
        flight_or_adj = in_flight or adjusting

        # ── IDLE ─────────────────────────────────────────────────────────────
        if is_idle and self._last_ui_state != 'idle':
            self._flight_frame.place_forget()
            if self._matrix.running:
                self._matrix.stop()
            if self._variant_expanded:
                self._on_variant_collapse()
            self._idle_screen.show()
            self._last_ui_state = 'idle'

        # ── FLIGHT / ADJUSTING ────────────────────────────────────────────────
        if flight_or_adj and self._last_ui_state not in ('flight', 'adjusting'):
            self._idle_screen.hide()
            self._flight_frame.place(x=0, y=0, relwidth=1, relheight=1)
            self._flight_frame.tk.call('raise', self._flight_frame._w)
            self._root.after(50, self._safe_initial_draw)
            self._last_ui_state = 'adjusting' if adjusting else 'flight'

        # ── VIRUS (matrix started by _on_virus_tapped) ───────────────────────
        if is_virus and self._last_ui_state not in ('virus', 'returning'):
            self._last_ui_state = 'virus'

        # Stop matrix when returning to IDLE
        if is_idle and self._matrix.running:
            self._matrix.stop()

        # ── Per-frame FLIGHT updates ───────────────────────────────────────────
        if flight_or_adj:
            if not self._slider_active:
                self._slider_frac = self._rot_to_frac(self._state.current_height_rot)
                self._draw_slider()

            border_blue = FRAME_BLUE  if in_flight else '#222222'
            border_red  = FRAME_RED   if in_flight else '#222222'
            if self._variant_outer:
                self._variant_outer.config(highlightbackground=border_blue)
            if self._virus_outer:
                self._virus_outer.config(highlightbackground=border_red)

            if self._variant_expanded:
                current_protected = frozenset(self._state.protected_drones)
                if current_protected != self._last_protected:
                    self._last_protected = current_protected
                    self._update_all_drone_toggles()

            self._last_ui_state = 'adjusting' if adjusting else 'flight'

    def _safe_initial_draw(self) -> None:
        """Called once after flight_frame is placed to draw initial content."""
        try:
            self._draw_variant_logo()
            self._draw_slider()
            self._draw_virus_button(self._state.current == State.FLIGHT)
        except Exception as exc:
            log.warning("Initial flight draw error (will retry): %s", exc)

    # ── Toast ─────────────────────────────────────────────────────────────────

    def _show_toast(self, msg: str, duration_ms: int = 2000) -> None:
        if self._toast_label is None:
            return
        self._toast_label.config(text=msg)
        self._toast_label.place(relx=0.5, rely=0.5, anchor='center')
        self._toast_label.lift()
        if self._toast_after:
            self._root.after_cancel(self._toast_after)
        self._toast_after = self._root.after(
            duration_ms,
            lambda: self._toast_label.place_forget() if self._toast_label else None,
        )

    # ── Quit ─────────────────────────────────────────────────────────────────

    def _quit(self) -> None:
        log.info("TouchUI quit requested")
        self._root.destroy()

    # ── Coordinate helpers ────────────────────────────────────────────────────

    def _y_to_frac(self, y: int) -> float:
        if self._slider_canvas is None:
            return 0.5
        h   = self._slider_canvas.winfo_height()
        pad = 20
        frac = ((h - pad) - y) / max(1, h - 2 * pad)
        return max(0.0, min(1.0, frac))

    @staticmethod
    def _rot_to_frac(rot: float) -> float:
        return (rot - cfg.SLIDER_MIN_ROT) / _SLIDER_RANGE

    @staticmethod
    def _frac_to_rot(frac: float) -> float:
        frac = max(0.0, min(1.0, frac))
        return cfg.SLIDER_MIN_ROT + frac * _SLIDER_RANGE
