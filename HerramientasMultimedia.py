"""Herramientas multimedia - programa unificado.

Pestañas:
  1. Zoom en imágenes (Ken Burns -> MP4)
  2. Video vertical a 16:9 (fondo desenfocado)
  3. Extractor de fotogramas (video -> PNG)
  4. Recortador de silencios (audio)
  5. Subtítulos para Canva (divisor de texto)

Dependencias: opencv-python, numpy, Pillow, pydub (y ffmpeg para pydub).
"""
import os
import json
import re
import math
import shutil
import subprocess
import threading
import wave
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, colorchooser

import cv2
import numpy as np
from PIL import Image, ImageTk, ImageFilter
from pydub import AudioSegment
from pydub.silence import detect_nonsilent

from Conversor import UniversalConverter, colores_tema
import Actualizador
from Divisor import SplitterTab
import sv_ttk
from tkinterdnd2 import TkinterDnD, DND_FILES

__version__ = "1.0.0"

CONFIG_PATH = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                           "HerramientasMultimedia", "config.json")


def cargar_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def guardar_config(cfg):
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
    except OSError:
        pass


ASPECT_RATIOS = {
    "9:16 (TikTok / Reels / Shorts)": (1080, 1920),
    "16:9 (YouTube estándar)": (1920, 1080),
    "1:1 (Post cuadrado)": (1080, 1080),
    "4:5 (Instagram vertical)": (1080, 1350)
}

BG_MODES = [
    "Espejo Difuminado (Blur)",
    "Recorte Directo (Sin fondo)",
    "Color: Negro",
    "Color: Blanco",
    "Color Personalizado..."
]

EASING_FUNCTIONS = {
    "Lineal (Velocidad constante)": lambda t: t,
    "Ease Out (Frenado suave)": lambda t: math.sin((t * math.pi) / 2),
    "Ease In (Aceleración progresiva)": lambda t: 1 - math.cos((t * math.pi) / 2),
    "Ease In-Out (Ken Burns cinematográfico)": lambda t: -(math.cos(math.pi * t) - 1) / 2,
    "Exponencial Cúbico (Dramático)": lambda t: 4 * t * t * t if t < 0.5 else 1 - math.pow(-2 * t + 2, 3) / 2
}

EXPORT_MODES = [
    "Solo el clip actual (1 MP4)",
    "Todos en un solo video unificado (MP4)",
    "Todos en clips separados (1 por imagen)"
]

class ImageItem:
    def __init__(self, path):
        self.path = path
        self.start_pt = [0.5, 0.5]
        self.end_pt = [0.5, 0.5]
        self.start_scale = 1.0
        self.end_scale = 2.0
        self.bg_mode = "Espejo Difuminado (Blur)"
        self.custom_color = (25, 25, 30)

class ZoomVideoMaker(ttk.Frame):
    def __init__(self, master):
        super().__init__(master)

        self.items = []
        self.current_idx = 0
        self.pil_img = None
        self.composed_cache = None

        self.is_playing = False
        self.play_timer = None
        self.drag_state = None

        self._build_ui()

    def _build_ui(self):
        main_paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        main_paned.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        # ---------------- PANEL IZQUIERDO: CONTROLES ----------------
        ctrl = ttk.Frame(main_paned, width=340, padding=10)
        main_paned.add(ctrl, weight=0)

        ttk.Button(ctrl, text="📁 Cargar Imágenes", command=self.load_images).pack(fill=tk.X, pady=2)
        self.lbl_info = ttk.Label(ctrl, text="Sin imágenes", font=("Segoe UI", 9, "italic"))
        self.lbl_info.pack(anchor=tk.W)

        nav_box = ttk.Frame(ctrl)
        nav_box.pack(fill=tk.X, pady=3)
        ttk.Button(nav_box, text="◀ Anterior", command=self.prev_img).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=1)
        ttk.Button(nav_box, text="Siguiente ▶", command=self.next_img).pack(side=tk.RIGHT, expand=True, fill=tk.X, padx=1)

        ttk.Separator(ctrl, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=5)

        ttk.Label(ctrl, text="Formato de salida:").pack(anchor=tk.W)
        self.combo_ratio = ttk.Combobox(ctrl, values=list(ASPECT_RATIOS.keys()), state="readonly")
        self.combo_ratio.set("9:16 (TikTok / Reels / Shorts)")
        self.combo_ratio.pack(fill=tk.X, pady=2)
        self.combo_ratio.bind("<<ComboboxSelected>>", lambda e: self.invalidate_and_refresh())

        ttk.Label(ctrl, text="Tratamiento de fondo:").pack(anchor=tk.W, pady=(4, 0))
        self.combo_bg = ttk.Combobox(ctrl, values=BG_MODES, state="readonly")
        self.combo_bg.set("Espejo Difuminado (Blur)")
        self.combo_bg.pack(fill=tk.X, pady=2)
        self.combo_bg.bind("<<ComboboxSelected>>", self.on_bg_change)
        self.btn_pick_color = ttk.Button(ctrl, text="Elegir Color", command=self.pick_color)

        ttk.Separator(ctrl, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=5)

        ttk.Label(ctrl, text="Curva de Velocidad (Easing):", font=("Segoe UI", 9, "bold")).pack(anchor=tk.W)
        self.combo_easing = ttk.Combobox(ctrl, values=list(EASING_FUNCTIONS.keys()), state="readonly")
        self.combo_easing.set("Ease In-Out (Ken Burns cinematográfico)")
        self.combo_easing.pack(fill=tk.X, pady=2)

        ttk.Label(ctrl, text="Duración por clip (segundos):").pack(anchor=tk.W, pady=(4, 0))
        self.spin_dur = ttk.Spinbox(ctrl, from_=1.0, to=30.0, increment=0.5)
        self.spin_dur.set(4.0)
        self.spin_dur.pack(fill=tk.X, pady=2)

        ttk.Separator(ctrl, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=6)

        ttk.Label(ctrl, text="Escala Cuadro Inicial (0.4x - 4.0x):").pack(anchor=tk.W)
        self.scale_start_slider = ttk.Scale(ctrl, from_=0.4, to=4.0, orient=tk.HORIZONTAL, command=self.on_start_scale_slider)
        self.scale_start_slider.pack(fill=tk.X)
        self.lbl_start_scale = ttk.Label(ctrl, text="Zoom Inicio: 1.00x")
        self.lbl_start_scale.pack(anchor=tk.W)

        ttk.Label(ctrl, text="Escala Cuadro Final (0.4x - 4.0x):").pack(anchor=tk.W, pady=(4, 0))
        self.scale_end_slider = ttk.Scale(ctrl, from_=0.4, to=4.0, orient=tk.HORIZONTAL, command=self.on_end_scale_slider)
        self.scale_end_slider.pack(fill=tk.X)
        self.lbl_end_scale = ttk.Label(ctrl, text="Zoom Fin: 2.00x")
        self.lbl_end_scale.pack(anchor=tk.W)

        ttk.Separator(ctrl, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8)

        # Configuración de Exportación (Uno solo, Todos o Separados)
        ttk.Label(ctrl, text="Modo de Exportación:", font=("Segoe UI", 9, "bold")).pack(anchor=tk.W)
        self.combo_export_mode = ttk.Combobox(ctrl, values=EXPORT_MODES, state="readonly")
        self.combo_export_mode.set(EXPORT_MODES[0])
        self.combo_export_mode.pack(fill=tk.X, pady=2)

        self.btn_render = ttk.Button(ctrl, text="🎬 Iniciar Exportación", style="Accent.TButton", command=self.start_render)
        self.btn_render.pack(fill=tk.X, pady=4)
        self.progress = ttk.Progressbar(ctrl, mode="determinate")
        self.progress.pack(fill=tk.X, pady=2)
        self.lbl_render_status = ttk.Label(ctrl, text="", font=("Segoe UI", 8))
        self.lbl_render_status.pack(anchor=tk.W)

        # ---------------- PANEL DERECHO: VISORES INTERACTIVOS ----------------
        right = ttk.Frame(main_paned, padding=6)
        main_paned.add(right, weight=1)

        info_header = ttk.Label(
            right,
            text="💡 Tip: Arrastra dentro de 'Frame Inicial' o 'Frame Final' para mover el encuadre. Usa la rueda del ratón para regular el zoom.",
            font=("Segoe UI", 9, "bold"),
            foreground="#1976d2"
        )
        info_header.pack(anchor=tk.W, pady=(0, 4))

        frames_box = ttk.Frame(right)
        frames_box.pack(fill=tk.BOTH, expand=True, pady=2)

        box_start = ttk.LabelFrame(frames_box, text=" Frame Inicial (Arrastra / Scroll Zoom) ")
        box_start.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=3)
        self.cv_start = tk.Canvas(box_start, bg="#121212", cursor="fleur")
        self.cv_start.pack(fill=tk.BOTH, expand=True)

        box_mid = ttk.LabelFrame(frames_box, text=" Vista Previa Transición ")
        box_mid.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=3)
        self.cv_mid = tk.Canvas(box_mid, bg="#181818")
        self.cv_mid.pack(fill=tk.BOTH, expand=True)

        box_end = ttk.LabelFrame(frames_box, text=" Frame Final (Arrastra / Scroll Zoom) ")
        box_end.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=3)
        self.cv_end = tk.Canvas(box_end, bg="#121212", cursor="fleur")
        self.cv_end.pack(fill=tk.BOTH, expand=True)

        self._bind_canvas_events(self.cv_start, is_start=True)
        self._bind_canvas_events(self.cv_end, is_start=False)

        play_ctrl = ttk.Frame(right, padding=4)
        play_ctrl.pack(fill=tk.X, pady=4)

        self.btn_play = ttk.Button(play_ctrl, text="▶ Reproducir Transición", command=self.toggle_playback)
        self.btn_play.pack(side=tk.LEFT, padx=4)

        ttk.Label(play_ctrl, text="Progreso:").pack(side=tk.LEFT, padx=(6, 2))
        self.timeline_slider = ttk.Scale(play_ctrl, from_=0.0, to=1.0, orient=tk.HORIZONTAL, command=self.on_scrub)
        self.timeline_slider.set(0.0)
        self.timeline_slider.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)

        self.lbl_t_val = ttk.Label(play_ctrl, text="0.00s / 4.00s", width=14)
        self.lbl_t_val.pack(side=tk.LEFT, padx=4)

    def _bind_canvas_events(self, canvas, is_start):
        canvas.bind("<Button-1>", lambda e: self.on_box_mouse_down(e, is_start))
        canvas.bind("<B1-Motion>", lambda e: self.on_box_mouse_drag(e, is_start))
        canvas.bind("<MouseWheel>", lambda e: self.on_box_mouse_wheel(e, is_start))
        canvas.bind("<Button-4>", lambda e: self.on_box_mouse_wheel_linux(e, is_start, zoom_in=True))
        canvas.bind("<Button-5>", lambda e: self.on_box_mouse_wheel_linux(e, is_start, zoom_in=False))

    def on_box_mouse_down(self, event, is_start):
        if not self.items:
            return
        item = self.items[self.current_idx]
        pt = item.start_pt if is_start else item.end_pt
        self.drag_state = {
            "x": event.x,
            "y": event.y,
            "orig_pt": list(pt),
            "is_start": is_start
        }

    def on_box_mouse_drag(self, event, is_start):
        if not self.drag_state or not self.items:
            return

        item = self.items[self.current_idx]
        scale = item.start_scale if is_start else item.end_scale
        canvas = self.cv_start if is_start else self.cv_end

        cw = canvas.winfo_width() or 250
        ch = canvas.winfo_height() or 400

        dx = (event.x - self.drag_state["x"]) / (cw * scale)
        dy = (event.y - self.drag_state["y"]) / (ch * scale)

        new_x = max(0.0, min(1.0, self.drag_state["orig_pt"][0] - dx))
        new_y = max(0.0, min(1.0, self.drag_state["orig_pt"][1] - dy))

        if is_start:
            item.start_pt = [new_x, new_y]
        else:
            item.end_pt = [new_x, new_y]

        self.update_all_previews()

    def on_box_mouse_wheel(self, event, is_start):
        delta = 0.08 if event.delta > 0 else -0.08
        self._apply_zoom_step(delta, is_start)

    def on_box_mouse_wheel_linux(self, event, is_start, zoom_in):
        delta = 0.08 if zoom_in else -0.08
        self._apply_zoom_step(delta, is_start)

    def _apply_zoom_step(self, delta, is_start):
        if not self.items:
            return
        item = self.items[self.current_idx]
        if is_start:
            item.start_scale = max(0.4, min(4.0, item.start_scale + delta))
            self.scale_start_slider.set(item.start_scale)
            self.lbl_start_scale.config(text=f"Zoom Inicio: {item.start_scale:.2f}x")
        else:
            item.end_scale = max(0.4, min(4.0, item.end_scale + delta))
            self.scale_end_slider.set(item.end_scale)
            self.lbl_end_scale.config(text=f"Zoom Fin: {item.end_scale:.2f}x")
        self.update_all_previews()

    def on_start_scale_slider(self, val):
        if not self.items:
            return
        v = float(val)
        self.items[self.current_idx].start_scale = v
        self.lbl_start_scale.config(text=f"Zoom Inicio: {v:.2f}x")
        self.update_all_previews()

    def on_end_scale_slider(self, val):
        if not self.items:
            return
        v = float(val)
        self.items[self.current_idx].end_scale = v
        self.lbl_end_scale.config(text=f"Zoom Fin: {v:.2f}x")
        self.update_all_previews()

    def on_bg_change(self, event=None):
        if not self.items:
            return
        item = self.items[self.current_idx]
        item.bg_mode = self.combo_bg.get()
        if item.bg_mode == "Color Personalizado...":
            self.btn_pick_color.pack(fill=tk.X, pady=2)
            self.pick_color()
        else:
            self.btn_pick_color.pack_forget()
        self.invalidate_and_refresh()

    def pick_color(self):
        if not self.items:
            return
        item = self.items[self.current_idx]
        color = colorchooser.askcolor(title="Selecciona Color de Fondo")
        if color[0]:
            item.custom_color = tuple(int(c) for c in color[0])
            self.invalidate_and_refresh()

    def load_images(self):
        files = filedialog.askopenfilenames(filetypes=[("Imágenes", "*.jpg *.jpeg *.png *.webp *.bmp")])
        self.recibir_archivos(files)

    def recibir_archivos(self, files):
        files = [f for f in files if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".bmp"))]
        if files:
            self.items = [ImageItem(p) for p in files]
            self.current_idx = 0
            self.load_current_item()

    def prev_img(self):
        if self.items and self.current_idx > 0:
            self.current_idx -= 1
            self.load_current_item()

    def next_img(self):
        if self.items and self.current_idx < len(self.items) - 1:
            self.current_idx += 1
            self.load_current_item()

    def load_current_item(self):
        item = self.items[self.current_idx]
        self.lbl_info.config(text=f"Foto {self.current_idx + 1}/{len(self.items)}: {os.path.basename(item.path)}")
        self.pil_img = Image.open(item.path).convert("RGB")
        self.combo_bg.set(item.bg_mode)

        self.scale_start_slider.set(item.start_scale)
        self.scale_end_slider.set(item.end_scale)
        self.lbl_start_scale.config(text=f"Zoom Inicio: {item.start_scale:.2f}x")
        self.lbl_end_scale.config(text=f"Zoom Fin: {item.end_scale:.2f}x")

        self.invalidate_and_refresh()

    def invalidate_and_refresh(self):
        self.composed_cache = None
        self.update_all_previews()

    def build_composed_canvas(self, img: Image.Image, target_w: int, target_h: int, bg_mode: str, custom_color) -> Image.Image:
        orig_w, orig_h = img.size
        target_aspect = target_w / target_h

        if bg_mode == "Recorte Directo (Sin fondo)":
            scale = max(target_w / orig_w, target_h / orig_h)
            rw, rh = int(orig_w * scale), int(orig_h * scale)
            resized = img.resize((rw, rh), Image.Resampling.BILINEAR)
            x1 = (rw - target_w) // 2
            y1 = (rh - target_h) // 2
            return resized.crop((x1, y1, x1 + target_w, y1 + target_h))

        if bg_mode == "Espejo Difuminado (Blur)":
            scale_fill = max(target_w / orig_w, target_h / orig_h)
            bg = img.resize((int(orig_w * scale_fill), int(orig_h * scale_fill)), Image.Resampling.BILINEAR)
            bx = (bg.width - target_w) // 2
            by = (bg.height - target_h) // 2
            bg = bg.crop((bx, by, bx + target_w, by + target_h)).filter(ImageFilter.GaussianBlur(radius=25))
        elif bg_mode == "Color: Blanco":
            bg = Image.new("RGB", (target_w, target_h), (255, 255, 255))
        elif bg_mode == "Color: Negro":
            bg = Image.new("RGB", (target_w, target_h), (0, 0, 0))
        else:
            bg = Image.new("RGB", (target_w, target_h), custom_color)

        scale_fit = min(target_w / orig_w, target_h / orig_h)
        fw, fh = int(orig_w * scale_fit), int(orig_h * scale_fit)
        fitted = img.resize((fw, fh), Image.Resampling.BILINEAR)
        bg.paste(fitted, ((target_w - fw) // 2, (target_h - fh) // 2))
        return bg

    def render_frame_item(self, item: ImageItem, base_img: Image.Image, t_linear: float, target_w: int, target_h: int, ease_fn) -> Image.Image:
        bw, bh = base_img.size
        t_curved = ease_fn(max(0.0, min(1.0, t_linear)))

        scale = item.start_scale + (item.end_scale - item.start_scale) * t_curved
        cx_rel = item.start_pt[0] + (item.end_pt[0] - item.start_pt[0]) * t_curved
        cy_rel = item.start_pt[1] + (item.end_pt[1] - item.start_pt[1]) * t_curved

        if scale >= 1.0:
            cw = bw / scale
            ch = bh / scale
            cx = cx_rel * bw
            cy = cy_rel * bh
            x1 = int(np.clip(cx - cw / 2, 0, bw - cw))
            y1 = int(np.clip(cy - ch / 2, 0, bh - ch))
            return base_img.crop((x1, y1, int(x1 + cw), int(y1 + ch))).resize((target_w, target_h), Image.Resampling.BILINEAR)
        else:
            sw, sh = int(bw * scale), int(bh * scale)
            small = base_img.resize((sw, sh), Image.Resampling.BILINEAR)

            if item.bg_mode == "Color: Blanco":
                bg = Image.new("RGB", (target_w, target_h), (255, 255, 255))
            elif item.bg_mode == "Color: Negro":
                bg = Image.new("RGB", (target_w, target_h), (0, 0, 0))
            elif item.bg_mode == "Color Personalizado...":
                bg = Image.new("RGB", (target_w, target_h), item.custom_color)
            else:
                bg = base_img.filter(ImageFilter.GaussianBlur(radius=30))

            px = int((target_w - sw) / 2 + (0.5 - cx_rel) * (target_w - sw))
            py = int((target_h - sh) / 2 + (0.5 - cy_rel) * (target_h - sh))
            bg.paste(small, (px, py))
            return bg

    def render_frame_at_t(self, t_linear: float) -> Image.Image:
        target_w, target_h = ASPECT_RATIOS[self.combo_ratio.get()]
        item = self.items[self.current_idx]

        if self.composed_cache is None:
            self.composed_cache = self.build_composed_canvas(
                self.pil_img, target_w, target_h, item.bg_mode, item.custom_color
            )

        ease_fn = EASING_FUNCTIONS[self.combo_easing.get()]
        return self.render_frame_item(item, self.composed_cache, t_linear, target_w, target_h, ease_fn)

    def draw_to_canvas(self, canvas: tk.Canvas, pil_image: Image.Image, badge_text=""):
        cw = canvas.winfo_width() or 240
        ch = canvas.winfo_height() or 380
        iw, ih = pil_image.size

        scale = min(cw / iw, ch / ih) * 0.95
        dw, dh = int(iw * scale), int(ih * scale)
        ox = (cw - dw) // 2
        oy = (ch - dh) // 2

        tk_img = ImageTk.PhotoImage(pil_image.resize((dw, dh), Image.Resampling.BILINEAR))
        canvas.delete("all")
        canvas.create_image(ox, oy, anchor=tk.NW, image=tk_img)
        canvas.create_rectangle(ox, oy, ox + dw, oy + dh, outline="#444", width=2)
        canvas.image_ref = tk_img

        if badge_text:
            canvas.create_rectangle(ox + 8, oy + 8, ox + 105, oy + 30, fill="#000000", outline="#666")
            canvas.create_text(ox + 56, oy + 19, text=badge_text, fill="#ffffff", font=("Segoe UI", 9, "bold"))

    def update_all_previews(self):
        if not self.items or self.pil_img is None:
            return

        item = self.items[self.current_idx]

        img_start = self.render_frame_at_t(0.0)
        self.draw_to_canvas(self.cv_start, img_start, badge_text=f"Zoom {item.start_scale:.2f}x")

        img_end = self.render_frame_at_t(1.0)
        self.draw_to_canvas(self.cv_end, img_end, badge_text=f"Zoom {item.end_scale:.2f}x")

        t_current = self.timeline_slider.get()
        img_current = self.render_frame_at_t(t_current)
        self.draw_to_canvas(self.cv_mid, img_current)

        dur = float(self.spin_dur.get())
        self.lbl_t_val.config(text=f"{t_current * dur:.2f}s / {dur:.2f}s")

    def on_scrub(self, val):
        self.update_all_previews()

    def toggle_playback(self):
        if self.is_playing:
            self.is_playing = False
            self.btn_play.config(text="▶ Reproducir Transición")
            if self.play_timer:
                self.after_cancel(self.play_timer)
        else:
            self.is_playing = True
            self.btn_play.config(text="⏸ Pausar")
            self._playback_loop()

    def _playback_loop(self):
        if not self.is_playing:
            return

        current_t = self.timeline_slider.get()
        dur = float(self.spin_dur.get())
        step = (1.0 / (dur * 30))

        new_t = current_t + step
        if new_t > 1.0:
            new_t = 0.0

        self.timeline_slider.set(new_t)
        self.update_all_previews()
        self.play_timer = self.after(33, self._playback_loop)

    def start_render(self):
        if not self.items:
            messagebox.showwarning("Atención", "Carga imágenes primero.")
            return

        mode = self.combo_export_mode.get()
        duration = float(self.spin_dur.get())
        ratio_key = self.combo_ratio.get()
        easing_key = self.combo_easing.get()

        if mode == "Solo el clip actual (1 MP4)":
            curr_item = self.items[self.current_idx]
            base_name = os.path.splitext(os.path.basename(curr_item.path))[0]
            out_path = filedialog.asksaveasfilename(
                title="Guardar Clip Actual",
                initialfile=f"clip_{base_name}.mp4",
                defaultextension=".mp4",
                filetypes=[("Video MP4", "*.mp4")]
            )
            if not out_path:
                return

            self.btn_render.config(state=tk.DISABLED)
            threading.Thread(
                target=self._render_single_clip,
                args=(out_path, curr_item, duration, ratio_key, easing_key),
                daemon=True
            ).start()

        elif mode == "Todos en un solo video unificado (MP4)":
            out_path = filedialog.asksaveasfilename(
                title="Guardar Video Unificado",
                initialfile="video_completo.mp4",
                defaultextension=".mp4",
                filetypes=[("Video MP4", "*.mp4")]
            )
            if not out_path:
                return

            self.btn_render.config(state=tk.DISABLED)
            threading.Thread(
                target=self._render_unified,
                args=(out_path, duration, ratio_key, easing_key),
                daemon=True
            ).start()

        else:
            # Todos en clips separados
            out_dir = filedialog.askdirectory(title="Selecciona la carpeta donde guardar los clips")
            if not out_dir:
                return

            self.btn_render.config(state=tk.DISABLED)
            threading.Thread(
                target=self._render_separated,
                args=(out_dir, duration, ratio_key, easing_key),
                daemon=True
            ).start()

    def _render_single_clip(self, output_path, item, duration, ratio_key, easing_key):
        target_w, target_h = ASPECT_RATIOS[ratio_key]
        fps = 30
        frames_per_clip = int(duration * fps)

        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        writer = cv2.VideoWriter(output_path, fourcc, fps, (target_w, target_h))
        ease_fn = EASING_FUNCTIONS[easing_key]

        self.lbl_render_status.config(text=f"Renderizando clip actual: {os.path.basename(item.path)}...")
        raw_img = Image.open(item.path).convert("RGB")
        base = self.build_composed_canvas(raw_img, target_w, target_h, item.bg_mode, item.custom_color)

        for i in range(frames_per_clip):
            t_linear = i / max(1, frames_per_clip - 1)
            frame = self.render_frame_item(item, base, t_linear, target_w, target_h, ease_fn)
            writer.write(cv2.cvtColor(np.array(frame), cv2.COLOR_RGB2BGR))

            self.progress["value"] = ((i + 1) / frames_per_clip) * 100

        writer.release()
        self.btn_render.config(state=tk.NORMAL)
        self.progress["value"] = 0
        self.lbl_render_status.config(text="Clip exportado con éxito.")
        messagebox.showinfo("Exportación Completada", f"Clip actual guardado en:\n{output_path}")

    def _render_unified(self, output_path, duration, ratio_key, easing_key):
        target_w, target_h = ASPECT_RATIOS[ratio_key]
        fps = 30
        frames_per_clip = int(duration * fps)
        total_frames = frames_per_clip * len(self.items)

        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        writer = cv2.VideoWriter(output_path, fourcc, fps, (target_w, target_h))
        ease_fn = EASING_FUNCTIONS[easing_key]
        frame_counter = 0

        for idx, item in enumerate(self.items):
            self.lbl_render_status.config(text=f"Renderizando foto {idx + 1}/{len(self.items)}...")
            raw_img = Image.open(item.path).convert("RGB")
            base = self.build_composed_canvas(raw_img, target_w, target_h, item.bg_mode, item.custom_color)

            for i in range(frames_per_clip):
                t_linear = i / max(1, frames_per_clip - 1)
                frame = self.render_frame_item(item, base, t_linear, target_w, target_h, ease_fn)
                writer.write(cv2.cvtColor(np.array(frame), cv2.COLOR_RGB2BGR))

                frame_counter += 1
                self.progress["value"] = (frame_counter / total_frames) * 100

        writer.release()
        self.btn_render.config(state=tk.NORMAL)
        self.progress["value"] = 0
        self.lbl_render_status.config(text="Completado.")
        messagebox.showinfo("Exportación Completada", f"Video unificado guardado en:\n{output_path}")

    def _render_separated(self, output_dir, duration, ratio_key, easing_key):
        target_w, target_h = ASPECT_RATIOS[ratio_key]
        fps = 30
        frames_per_clip = int(duration * fps)
        total_frames = frames_per_clip * len(self.items)

        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        ease_fn = EASING_FUNCTIONS[easing_key]
        frame_counter = 0

        for idx, item in enumerate(self.items):
            base_name = os.path.splitext(os.path.basename(item.path))[0]
            out_file = os.path.join(output_dir, f"clip_{idx + 1:02d}_{base_name}.mp4")

            self.lbl_render_status.config(text=f"Exportando clip {idx + 1}/{len(self.items)}: {base_name}.mp4")
            writer = cv2.VideoWriter(out_file, fourcc, fps, (target_w, target_h))

            raw_img = Image.open(item.path).convert("RGB")
            base = self.build_composed_canvas(raw_img, target_w, target_h, item.bg_mode, item.custom_color)

            for i in range(frames_per_clip):
                t_linear = i / max(1, frames_per_clip - 1)
                frame = self.render_frame_item(item, base, t_linear, target_w, target_h, ease_fn)
                writer.write(cv2.cvtColor(np.array(frame), cv2.COLOR_RGB2BGR))

                frame_counter += 1
                self.progress["value"] = (frame_counter / total_frames) * 100

            writer.release()

        self.btn_render.config(state=tk.NORMAL)
        self.progress["value"] = 0
        self.lbl_render_status.config(text="Clips exportados.")
        messagebox.showinfo(
            "Exportación Completada",
            f"Se exportaron con éxito {len(self.items)} clips independientes en:\n{output_dir}"
        )


class VideoFrameExtractor(ttk.Frame):
    def __init__(self, master):
        super().__init__(master)

        self.video_path = tk.StringVar()
        self.output_dir = tk.StringVar()
        self.mode = tk.StringVar(value="seconds")  # 'seconds' o 'frames'
        self.interval_val = tk.DoubleVar(value=1.0)
        self.is_processing = False

        self._crear_interfaz()

    def _crear_interfaz(self):
        padding = {'padx': 10, 'pady': 5}

        # 1. Selección de Video
        frame_video = ttk.LabelFrame(self, text="Video de entrada")
        frame_video.pack(fill="x", **padding)

        ttk.Entry(frame_video, textvariable=self.video_path, width=45).pack(side="left", padx=5, pady=5)
        ttk.Button(frame_video, text="Examinar...", command=self._seleccionar_video).pack(side="left", padx=5)

        # 2. Directorio de Salida
        frame_out = ttk.LabelFrame(self, text="Carpeta de destino")
        frame_out.pack(fill="x", **padding)

        ttk.Entry(frame_out, textvariable=self.output_dir, width=45).pack(side="left", padx=5, pady=5)
        ttk.Button(frame_out, text="Examinar...", command=self._seleccionar_salida).pack(side="left", padx=5)

        # 3. Configuración de Intervalo
        frame_config = ttk.LabelFrame(self, text="Criterio de extracción")
        frame_config.pack(fill="x", **padding)

        ttk.Radiobutton(frame_config, text="Cada N segundos (ej. 1.0 = cada segundo)", 
                        variable=self.mode, value="seconds").pack(anchor="w", padx=10, pady=2)
        ttk.Radiobutton(frame_config, text="Cada N fotogramas (ej. 1 = todos los frames)", 
                        variable=self.mode, value="frames").pack(anchor="w", padx=10, pady=2)

        subframe = ttk.Frame(frame_config)
        subframe.pack(fill="x", padx=10, pady=5)
        ttk.Label(subframe, text="Valor del intervalo (N):").pack(side="left")
        ttk.Entry(subframe, textvariable=self.interval_val, width=10).pack(side="left", padx=10)

        # 4. Progreso y Estado
        frame_status = ttk.Frame(self)
        frame_status.pack(fill="x", **padding)

        self.progress = ttk.Progressbar(frame_status, orient="horizontal", mode="determinate")
        self.progress.pack(fill="x", pady=5)

        self.lbl_status = ttk.Label(frame_status, text="Listo para iniciar.", anchor="center")
        self.lbl_status.pack(fill="x")

        # 5. Botón de Ejecución
        self.btn_run = ttk.Button(self, text="Iniciar Extracción", style="Accent.TButton", command=self._iniciar_hilo)
        self.btn_run.pack(pady=10)

    def _seleccionar_video(self):
        archivo = filedialog.askopenfilename(
            filetypes=[("Archivos de video", "*.mp4 *.avi *.mkv *.mov *.flv"), ("Todos", "*.*")]
        )
        if archivo:
            self.video_path.set(archivo)

    def recibir_archivos(self, files):
        if files:
            self.video_path.set(files[0])

    def _seleccionar_salida(self):
        directorio = filedialog.askdirectory()
        if directorio:
            self.output_dir.set(directorio)

    def _iniciar_hilo(self):
        if self.is_processing:
            return

        video = self.video_path.get()
        salida = self.output_dir.get()
        intervalo = self.interval_val.get()

        if not os.path.isfile(video):
            messagebox.showerror("Error", "Seleccione un archivo de video válido.")
            return
        if not os.path.isdir(salida):
            messagebox.showerror("Error", "Seleccione una carpeta de destino válida.")
            return
        if intervalo <= 0:
            messagebox.showerror("Error", "El intervalo debe ser un número mayor a 0.")
            return

        self.is_processing = True
        self.btn_run.config(state="disabled")
        threading.Thread(target=self._procesar_video, daemon=True).start()

    def _procesar_video(self):
        cap = cv2.VideoCapture(self.video_path.get())
        if not cap.isOpened():
            self._finalizar("Error al abrir el video.", error=True)
            return

        fps = cap.get(cv2.CAP_PROP_FPS)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

        if fps <= 0:
            fps = 30.0  # Valor de respaldo si no detecta FPS

        # Determinar salto de frames
        if self.mode.get() == "seconds":
            step_frames = max(1, round(self.interval_val.get() * fps))
        else:
            step_frames = max(1, int(self.interval_val.get()))

        salida = self.output_dir.get()
        current_frame = 0
        saved_count = 0

        while True:
            ret, frame = cap.read()
            if not ret:
                break

            if current_frame % step_frames == 0:
                out_name = os.path.join(salida, f"frame_{current_frame:06d}.png")
                cv2.imwrite(out_name, frame)
                saved_count += 1

            current_frame += 1

            if current_frame % 10 == 0 or current_frame == total_frames:
                porcentaje = (current_frame / total_frames) * 100
                self.progress["value"] = porcentaje
                self.lbl_status.config(
                    text=f"Procesando: {current_frame}/{total_frames} frames ({saved_count} PNGs guardados)"
                )

        cap.release()
        self._finalizar(f"Extracción finalizada. Se guardaron {saved_count} imágenes.")

    def _finalizar(self, mensaje, error=False):
        self.is_processing = False
        self.btn_run.config(state="normal")
        self.lbl_status.config(text=mensaje)
        if error:
            messagebox.showerror("Atención", mensaje)
        else:
            messagebox.showinfo("Completado", mensaje)


class AudioSilencerApp(ttk.Frame):
    def __init__(self, master):
        super().__init__(master)
        self.root = self

        self.file_path = None
        self._build_ui()

    def _build_ui(self):
        # Selección de archivo
        file_frame = ttk.LabelFrame(self.root, text=" Archivo de Audio ", padding=10)
        file_frame.pack(fill="x", padx=15, pady=10)

        self.btn_select = ttk.Button(file_frame, text="Seleccionar Audio...", command=self.select_file)
        self.btn_select.pack(anchor="w")

        self.lbl_filename = ttk.Label(file_frame, text="Ningún archivo seleccionado", foreground="gray")
        self.lbl_filename.pack(anchor="w", pady=(5, 0))

        # Parámetros de detección
        params_frame = ttk.LabelFrame(self.root, text=" Configuración de Silencio ", padding=10)
        params_frame.pack(fill="x", padx=15, pady=5)

        # Umbral dBFS
        ttk.Label(params_frame, text="Umbral de silencio (dBFS bajo la media):").grid(row=0, column=0, sticky="w", pady=4)
        self.spin_thresh = ttk.Spinbox(params_frame, from_=5, to=60, width=8)
        self.spin_thresh.set(16)
        self.spin_thresh.grid(row=0, column=1, sticky="e", pady=4)

        # Duración mínima del silencio
        ttk.Label(params_frame, text="Silencio mínimo detectable (ms):").grid(row=1, column=0, sticky="w", pady=4)
        self.spin_min_silence = ttk.Spinbox(params_frame, from_=100, to=3000, increment=50, width=8)
        self.spin_min_silence.set(500)
        self.spin_min_silence.grid(row=1, column=1, sticky="e", pady=4)

        # Margen / Padding
        ttk.Label(params_frame, text="Margen de respiración / relleno (ms):").grid(row=2, column=0, sticky="w", pady=4)
        self.spin_margin = ttk.Spinbox(params_frame, from_=0, to=1000, increment=25, width=8)
        self.spin_margin.set(150)
        self.spin_margin.grid(row=2, column=1, sticky="e", pady=4)

        # Barra de progreso y estado
        self.lbl_status = ttk.Label(self.root, text="Listo para procesar", anchor="center")
        self.lbl_status.pack(fill="x", padx=15, pady=(15, 5))

        self.progress = ttk.Progressbar(self.root, mode="indeterminate")
        self.progress.pack(fill="x", padx=15, pady=5)

        # Botón de ejecución
        self.btn_process = ttk.Button(self.root, text="Recortar y Guardar Como...", style="Accent.TButton", command=self.start_processing, state="disabled")
        self.btn_process.pack(fill="x", padx=15, pady=15)

    def select_file(self):
        types = [
            ("Archivos de audio", "*.wav *.mp3 *.ogg *.flac *.m4a"),
            ("Todos los archivos", "*.*")
        ]
        chosen = filedialog.askopenfilename(title="Seleccionar pista de audio", filetypes=types)
        if chosen:
            self.recibir_archivos([chosen])

    def recibir_archivos(self, files):
        if files:
            self.file_path = files[0]
            self.lbl_filename.config(text=os.path.basename(files[0]),
                                     foreground=colores_tema()[1])
            self.btn_process.config(state="normal")

    def start_processing(self):
        # Deshabilitar controles mientras trabaja en un hilo separado
        self.btn_select.config(state="disabled")
        self.btn_process.config(state="disabled")
        self.progress.start(10)
        self.lbl_status.config(text="Cargando y analizando audio...")

        thread = threading.Thread(target=self._process_audio, daemon=True)
        thread.start()

    def _process_audio(self):
        try:
            silence_thresh_offset = float(self.spin_thresh.get())
            min_silence_len = int(self.spin_min_silence.get())
            margin = int(self.spin_margin.get())

            # 1. Cargar audio
            audio = AudioSegment.from_file(self.file_path)
            
            # El umbral se calcula relativo al volumen medio del archivo
            silence_threshold = audio.dBFS - silence_thresh_offset

            # 2. Detectar fragmentos con sonido (no silenciosos)
            self.root.after(0, lambda: self.lbl_status.config(text="Buscando zonas de silencio..."))
            nonsilent_ranges = detect_nonsilent(
                audio,
                min_silence_len=min_silence_len,
                silence_thresh=silence_threshold
            )

            if not nonsilent_ranges:
                self.root.after(0, lambda: messagebox.showwarning("Aviso", "No se detectaron zonas de voz o sonido con los parámetros actuales."))
                return

            # 3. Concatenar fragmentos añadiendo el margen
            self.root.after(0, lambda: self.lbl_status.config(text="Ensamblando audio recortado..."))
            output_audio = AudioSegment.empty()
            total_duration = len(audio)

            for start, end in nonsilent_ranges:
                # Aplicar el margen respetando los límites inicial y final
                buffered_start = max(0, start - margin)
                buffered_end = min(total_duration, end + margin)
                output_audio += audio[buffered_start:buffered_end]

            # 4. Solicitar ruta para guardar
            ext = os.path.splitext(self.file_path)[1].lower().replace(".", "")
            if not ext:
                ext = "wav"

            output_file = filedialog.asksaveasfilename(
                title="Guardar audio recortado",
                defaultextension=f".{ext}",
                filetypes=[(f"Archivo {ext.upper()}", f"*.{ext}"), ("Todos los archivos", "*.*")]
            )

            if output_file:
                output_audio.export(output_file, format=ext)
                self.root.after(0, lambda: messagebox.showinfo("Éxito", f"Audio procesado correctamente:\n{output_file}"))
                self.root.after(0, lambda: self.lbl_status.config(text="Proceso completado."))
            else:
                self.root.after(0, lambda: self.lbl_status.config(text="Guardado cancelado."))

        except Exception as err:
            self.root.after(0, lambda: messagebox.showerror("Error", f"Ocurrió un error al procesar:\n{err}"))
            self.root.after(0, lambda: self.lbl_status.config(text="Error al procesar."))

        finally:
            self.root.after(0, self._restore_ui)

    def _restore_ui(self):
        self.progress.stop()
        self.btn_select.config(state="normal")
        self.btn_process.config(state="normal")


class TextSplitterApp(ttk.Frame):
    def __init__(self, master):
        super().__init__(master)
        root = self
        self.root = self

        # --- Panel Superior (Entrada y Ajustes) ---
        top_frame = ttk.Frame(root, padding=12)
        top_frame.pack(fill="x")

        lbl_input = ttk.Label(
            top_frame, 
            text="Pega tu texto aquí (se calculará el ancho real para no pasar de 2 líneas en Canva):", 
            font=("Segoe UI", 10, "bold")
        )
        lbl_input.pack(anchor="w", pady=(0, 5))

        self.txt_input = tk.Text(top_frame, height=5, wrap="word", font=("Segoe UI", 10))
        self.txt_input.pack(fill="x", pady=(0, 10))

        ctrl_frame = ttk.Frame(top_frame)
        ctrl_frame.pack(fill="x")

        ttk.Label(ctrl_frame, text="Capacidad máx. por línea:").pack(side="left", padx=(0, 4))
        self.spin_limite = ttk.Spinbox(ctrl_frame, from_=8, to=30, width=4)
        self.spin_limite.set(13)
        self.spin_limite.pack(side="left", padx=(0, 15))

        btn_procesar = ttk.Button(ctrl_frame, text="Dividir Subtítulos", style="Accent.TButton", command=self.procesar_texto)
        btn_procesar.pack(side="left", padx=(0, 5))

        btn_limpiar = ttk.Button(ctrl_frame, text="Limpiar", command=self.limpiar)
        btn_limpiar.pack(side="left", padx=(0, 5))

        self.btn_copiar_todo = ttk.Button(ctrl_frame, text="Copiar Todo", command=self.copiar_todo, state="disabled")
        self.btn_copiar_todo.pack(side="left")

        self.lbl_info = ttk.Label(ctrl_frame, text="", font=("Segoe UI", 9, "bold"), foreground="#007acc")
        self.lbl_info.pack(side="right")

        # --- Panel Inferior (Lista de Bloques) ---
        lbl_output = ttk.Label(root, text="Bloques listos para pegar en Canva (Máximo 2 líneas garantizadas):", font=("Segoe UI", 10, "bold"))
        lbl_output.pack(anchor="w", padx=15, pady=(5, 5))

        container = ttk.Frame(root)
        container.pack(fill="both", expand=True, padx=15, pady=(0, 15))

        self.canvas = tk.Canvas(container, bg=colores_tema()[0],
                                highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(container, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = ttk.Frame(self.canvas)

        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )

        self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

        self.canvas.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", self._on_mousewheel))
        self.canvas.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))
        self.fragmentos = []

    def _on_mousewheel(self, event):
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def peso_palabra(self, palabra):
        """
        Calcula el espacio visual proporcional aproximado que ocupan
        las letras en mayúsculas dentro de Canva.
        """
        letras_anchas = set("MWOmwQ")
        letras_angostas = set("Iijlrt1!.,:;|")
        peso = 0.0
        for c in palabra:
            if c in letras_anchas:
                peso += 1.4
            elif c in letras_angostas:
                peso += 0.5
            else:
                peso += 1.0
        return peso

    def segmentar_oracion(self, oracion, limite_peso):
        palabras = oracion.split()
        if not palabras:
            return []

        lineas = []
        actual = []
        peso_actual = 0.0

        for p in palabras:
            p_peso = self.peso_palabra(p)
            extra = 0.8 if actual else 0.0

            if peso_actual + extra + p_peso > limite_peso:
                if actual:
                    lineas.append(" ".join(actual))
                    actual = [p]
                    peso_actual = p_peso
                else:
                    lineas.append(p)
                    actual = []
                    peso_actual = 0.0
            else:
                actual.append(p)
                peso_actual += extra + p_peso

        if actual:
            lineas.append(" ".join(actual))

        return lineas

    def procesar_texto(self):
        raw_text = self.txt_input.get("1.0", tk.END).strip()
        if not raw_text:
            messagebox.showwarning("Aviso", "Por favor ingresa o pega algún texto.")
            return

        try:
            limite = float(self.spin_limite.get())
        except ValueError:
            limite = 13.0

        texto_limpio = re.sub(r'\s+', ' ', raw_text).strip()
        oraciones = re.split(r'(?<=[.?!])\s+', texto_limpio)

        bloques = []
        for oracion in oraciones:
            oracion = oracion.strip()
            if not oracion:
                continue

            lineas = self.segmentar_oracion(oracion, limite)

            # Agrupa de máximo 2 en 2 líneas por bloque de subtítulo
            for i in range(0, len(lineas), 2):
                bloques.append("\n".join(lineas[i:i+2]))

        self.fragmentos = bloques

        for widget in self.scrollable_frame.winfo_children():
            widget.destroy()

        if not self.fragmentos:
            self.lbl_info.config(text="No se generaron subtítulos.")
            self.btn_copiar_todo.config(state="disabled")
            return

        for i, bloque in enumerate(self.fragmentos, 1):
            sub_lines = bloque.split('\n')
            cant_lineas = len(sub_lines)

            row_frame = ttk.LabelFrame(
                self.scrollable_frame,
                text=f" Subtítulo {i:02d} ({cant_lineas} {'líneas' if cant_lineas == 2 else 'línea'}) ",
                padding=(8, 4)
            )
            row_frame.pack(fill="x", expand=True, padx=8, pady=3)

            lbl_texto = ttk.Label(
                row_frame,
                text=bloque,
                font=("Consolas", 10, "bold"),
                justify="left"
            )
            lbl_texto.pack(side="left", fill="x", expand=True, padx=(0, 10))

            btn_copiar = ttk.Button(
                row_frame,
                text="Copiar",
                width=9,
                command=lambda b=bloque, idx=i: self.copiar_fragmento(b, idx)
            )
            btn_copiar.pack(side="right")

        self.lbl_info.config(text=f"{len(self.fragmentos)} subtítulos listos")
        self.btn_copiar_todo.config(state="normal")

    def copiar_fragmento(self, texto, idx):
        self.root.clipboard_clear()
        self.root.clipboard_append(texto)
        self.root.update()
        self.lbl_info.config(text=f"Copiado [{idx:02d}] al portapapeles")

    def copiar_todo(self):
        if not self.fragmentos:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append("\n\n".join(self.fragmentos))
        self.root.update()
        self.lbl_info.config(text="Todos los subtítulos copiados")

    def limpiar(self):
        self.txt_input.delete("1.0", tk.END)
        for widget in self.scrollable_frame.winfo_children():
            widget.destroy()
        self.fragmentos = []
        self.lbl_info.config(text="")
        self.btn_copiar_todo.config(state="disabled")


class VideoBlurFill(ttk.Frame):
    """Convierte videos verticales (u otra proporción) a 16:9 rellenando
    los lados con el mismo video ampliado y difuminado. Admite varios
    videos, exportados por separado o unidos en uno solo."""

    RESOLUCIONES = {
        "1920x1080 (Full HD)": (1920, 1080),
        "1280x720 (HD)": (1280, 720),
        "2560x1440 (2K)": (2560, 1440),
        "3840x2160 (4K)": (3840, 2160),
    }
    MODO_SEPARADOS = "Cada video por separado (1 MP4 por video)"
    MODO_JUNTOS = "Todos juntos en un solo video (MP4)"
    EXT_VIDEO = "*.mp4 *.avi *.mkv *.mov *.flv *.webm"

    def __init__(self, master):
        super().__init__(master, padding=10)
        self.videos = []
        self.var_res = tk.StringVar(value="1920x1080 (Full HD)")
        self.var_modo = tk.StringVar(value=self.MODO_SEPARADOS)
        self.var_blur = tk.IntVar(value=40)
        self.var_dark = tk.IntVar(value=25)
        self.var_audio = tk.BooleanVar(value=True)
        self.is_processing = False
        self.cancel = False
        self._build_ui()

    def _build_ui(self):
        pad = {"padx": 10, "pady": 5}

        # --- Lista de videos ---
        fr_list = ttk.LabelFrame(self, text="Videos de entrada")
        fr_list.pack(fill="both", expand=True, **pad)

        lst_box = ttk.Frame(fr_list)
        lst_box.pack(side="left", fill="both", expand=True, padx=5, pady=5)
        self.listbox = tk.Listbox(lst_box, selectmode="extended", height=8, activestyle="none")
        sb = ttk.Scrollbar(lst_box, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=sb.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        btn_col = ttk.Frame(fr_list)
        btn_col.pack(side="left", fill="y", padx=5, pady=5)
        self.list_buttons = [
            ttk.Button(btn_col, text="Añadir videos...", command=self._agregar),
            ttk.Button(btn_col, text="Quitar selección", command=self._quitar),
            ttk.Button(btn_col, text="Subir ▲", command=lambda: self._mover(-1)),
            ttk.Button(btn_col, text="Bajar ▼", command=lambda: self._mover(1)),
            ttk.Button(btn_col, text="Vaciar lista", command=self._vaciar),
        ]
        for b in self.list_buttons:
            b.pack(fill="x", pady=2)

        # --- Ajustes ---
        fr_cfg = ttk.LabelFrame(self, text="Ajustes (salida horizontal 16:9)")
        fr_cfg.pack(fill="x", **pad)
        fr_cfg.columnconfigure(1, weight=1)

        ttk.Label(fr_cfg, text="Exportación:").grid(row=0, column=0, sticky="w", padx=8, pady=4)
        self.combo_modo = ttk.Combobox(fr_cfg, textvariable=self.var_modo, state="readonly",
                                       values=[self.MODO_SEPARADOS, self.MODO_JUNTOS])
        self.combo_modo.grid(row=0, column=1, sticky="ew", padx=8, pady=4)

        ttk.Label(fr_cfg, text="Resolución:").grid(row=1, column=0, sticky="w", padx=8, pady=4)
        self.combo_res = ttk.Combobox(fr_cfg, textvariable=self.var_res, values=list(self.RESOLUCIONES),
                                      state="readonly")
        self.combo_res.grid(row=1, column=1, sticky="ew", padx=8, pady=4)

        ttk.Label(fr_cfg, text="Intensidad del desenfoque:").grid(row=2, column=0, sticky="w", padx=8, pady=4)
        ttk.Scale(fr_cfg, from_=5, to=100, variable=self.var_blur,
                  orient="horizontal").grid(row=2, column=1, sticky="ew", padx=8, pady=4)

        ttk.Label(fr_cfg, text="Oscurecer fondo (%):").grid(row=3, column=0, sticky="w", padx=8, pady=4)
        ttk.Scale(fr_cfg, from_=0, to=80, variable=self.var_dark,
                  orient="horizontal").grid(row=3, column=1, sticky="ew", padx=8, pady=4)

        self.chk_audio = ttk.Checkbutton(fr_cfg, text="Conservar el audio original (requiere ffmpeg)",
                                         variable=self.var_audio)
        self.chk_audio.grid(row=4, column=0, columnspan=2, sticky="w", padx=8, pady=4)

        # --- Progreso y ejecución ---
        self.progress = ttk.Progressbar(self, mode="determinate")
        self.progress.pack(fill="x", **pad)
        self.lbl_status = ttk.Label(self, text="Añade uno o varios videos para empezar.", anchor="center")
        self.lbl_status.pack(fill="x", **pad)

        btns = ttk.Frame(self)
        btns.pack(pady=10)
        self.btn_run = ttk.Button(btns, text="Convertir a 16:9", style="Accent.TButton", command=self._iniciar)
        self.btn_run.pack(side="left", padx=5)
        self.btn_cancel = ttk.Button(btns, text="Cancelar", command=self._cancelar, state="disabled")
        self.btn_cancel.pack(side="left", padx=5)

    # ---------- gestión de la lista ----------
    def _refrescar_lista(self):
        self.listbox.delete(0, tk.END)
        for i, v in enumerate(self.videos, 1):
            self.listbox.insert(tk.END, f"{i:02d}.  {os.path.basename(v)}")
        self.lbl_status.config(text=f"{len(self.videos)} video(s) en la lista.")

    def _agregar(self):
        files = filedialog.askopenfilenames(
            filetypes=[("Archivos de video", self.EXT_VIDEO), ("Todos", "*.*")])
        self.recibir_archivos(files)

    def recibir_archivos(self, files):
        for f in files:
            if f not in self.videos:
                self.videos.append(f)
        self._refrescar_lista()

    def _quitar(self):
        for i in sorted(self.listbox.curselection(), reverse=True):
            del self.videos[i]
        self._refrescar_lista()

    def _vaciar(self):
        self.videos.clear()
        self._refrescar_lista()

    def _mover(self, d):
        sel = list(self.listbox.curselection())
        if not sel:
            return
        if d < 0 and sel[0] == 0 or d > 0 and sel[-1] == len(self.videos) - 1:
            return
        for i in (sel if d < 0 else reversed(sel)):
            self.videos[i], self.videos[i + d] = self.videos[i + d], self.videos[i]
        self._refrescar_lista()
        for i in sel:
            self.listbox.selection_set(i + d)

    def _bloquear(self, bloquear):
        estado = "disabled" if bloquear else "normal"
        for w in self.list_buttons + [self.btn_run, self.chk_audio]:
            w.config(state=estado)
        self.combo_modo.config(state="disabled" if bloquear else "readonly")
        self.combo_res.config(state="disabled" if bloquear else "readonly")
        self.btn_cancel.config(state="normal" if bloquear else "disabled")

    # ---------- control ----------
    def _iniciar(self):
        if self.is_processing:
            return
        if not self.videos:
            messagebox.showerror("Error", "Añade al menos un video a la lista.")
            return
        juntos = self.var_modo.get() == self.MODO_JUNTOS
        if juntos:
            destino = filedialog.asksaveasfilename(
                title="Guardar video unido", initialfile="video_unido_16x9.mp4",
                defaultextension=".mp4", filetypes=[("Video MP4", "*.mp4")])
        else:
            destino = filedialog.askdirectory(title="Carpeta donde guardar los videos convertidos")
        if not destino:
            return
        if juntos and os.path.abspath(destino) in {os.path.abspath(v) for v in self.videos}:
            messagebox.showerror("Error", "El archivo de salida no puede ser uno de los videos de entrada.")
            return

        self.is_processing = True
        self.cancel = False
        self._bloquear(True)
        args = (list(self.videos), destino, juntos,
                self.RESOLUCIONES[self.var_res.get()],
                int(self.var_blur.get()), int(self.var_dark.get()), self.var_audio.get())
        threading.Thread(target=self._trabajo, args=args, daemon=True).start()

    def _cancelar(self):
        self.cancel = True

    def _ui(self, progress=None, status=None):
        """Actualiza la interfaz desde el hilo de trabajo."""
        def apply():
            if progress is not None:
                self.progress["value"] = progress
            if status is not None:
                self.lbl_status.config(text=status)
        self.after(0, apply)

    def _fin(self, msg, error=False):
        def apply():
            self.is_processing = False
            self._bloquear(False)
            self.progress["value"] = 0
            self.lbl_status.config(text=msg.splitlines()[0])
            if error:
                messagebox.showerror("Atención", msg)
            else:
                messagebox.showinfo("Completado", msg)
        self.after(0, apply)

    # ---------- procesamiento de imagen ----------
    @staticmethod
    def componer_frame(frame, tw, th, blur, dark):
        h, w = frame.shape[:2]

        # Fondo: recorte central del frame con la proporción de salida,
        # ampliado y desenfocado. El desenfoque se hace a tamaño reducido
        # (mucho más rápido y visualmente equivalente).
        if w / h > tw / th:
            cw, ch = int(round(h * tw / th)), h
        else:
            cw, ch = w, int(round(w * th / tw))
        x0, y0 = (w - cw) // 2, (h - ch) // 2
        crop = frame[y0:y0 + ch, x0:x0 + cw]

        sw, sh = max(2, tw // 8), max(2, th // 8)
        bg = cv2.resize(crop, (sw, sh), interpolation=cv2.INTER_AREA)
        k = max(3, int(blur * sw / 100) | 1)
        bg = cv2.GaussianBlur(bg, (k, k), 0)
        bg = cv2.resize(bg, (tw, th), interpolation=cv2.INTER_LINEAR)
        if dark:
            bg = cv2.convertScaleAbs(bg, alpha=1 - dark / 100.0)

        # Primer plano: el frame completo, ajustado y centrado.
        s = min(tw / w, th / h)
        ow, oh = max(1, int(round(w * s))), max(1, int(round(h * s)))
        fg = cv2.resize(frame, (ow, oh), interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)
        x, y = (tw - ow) // 2, (th - oh) // 2
        bg[y:y + oh, x:x + ow] = fg
        return bg

    @staticmethod
    def _borrar(*rutas):
        for r in set(rutas):
            try:
                os.remove(r)
            except OSError:
                pass

    @staticmethod
    def _ffmpeg(cmd):
        try:
            r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error"] + cmd, capture_output=True,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return r.returncode == 0
        except OSError:
            return False

    def _volcar_video(self, src, writer, tw, th, blur, dark, fps_out, hecho, total):
        """Escribe los frames de `src` en `writer` a `fps_out`. Devuelve
        (frames_escritos, frames_leidos) o None si se canceló."""
        cap = cv2.VideoCapture(src)
        if not cap.isOpened():
            raise IOError(f"No se pudo abrir: {os.path.basename(src)}")
        fps_in = cap.get(cv2.CAP_PROP_FPS) or fps_out
        acumulador, escritos, leidos = 0.0, 0, 0
        try:
            while not self.cancel:
                ok, frame = cap.read()
                if not ok:
                    break
                leidos += 1
                acumulador += fps_out / fps_in
                if acumulador >= 1.0:
                    comp = self.componer_frame(frame, tw, th, blur, dark)
                    while acumulador >= 1.0:
                        writer.write(comp)
                        escritos += 1
                        acumulador -= 1.0
                if leidos % 5 == 0:
                    self._ui(progress=min(100, (hecho + leidos) / total * 100),
                             status=f"{os.path.basename(src)}: {leidos} frames")
        finally:
            cap.release()
        return None if self.cancel else (escritos, leidos)

    @staticmethod
    def _info(src):
        cap = cv2.VideoCapture(src)
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
        cap.release()
        return fps, n

    def _wav_de(self, src, dur, ruta):
        """Audio de `src` como WAV de exactamente `dur` s (silencio si no tiene)."""
        ok = self._ffmpeg(["-i", src, "-vn", "-map", "0:a:0", "-ac", "2", "-ar", "44100",
                           "-af", f"apad=whole_dur={dur:.4f}", "-t", f"{dur:.4f}", ruta])
        if not ok:
            n = int(round(dur * 44100))
            with wave.open(ruta, "wb") as w:
                w.setnchannels(2)
                w.setsampwidth(2)
                w.setframerate(44100)
                w.writeframes(b"\x00" * 4 * n)

    # ---------- trabajo principal ----------
    def _trabajo(self, videos, destino, juntos, size, blur, dark, keep_audio):
        tw, th = size
        usar_audio = keep_audio and shutil.which("ffmpeg") is not None
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        infos = [self._info(v) for v in videos]
        total = sum(n for _, n in infos) or 1
        hecho = 0
        errores, creados, temporales = [], [], []

        try:
            if juntos:
                fps_out = infos[0][0]
                tmp = destino + ".tmp_video.mp4"
                temporales.append(tmp)
                writer = cv2.VideoWriter(tmp if usar_audio else destino, fourcc, fps_out, (tw, th))
                if not writer.isOpened():
                    raise IOError("No se pudo crear el archivo de salida.")
                wavs = []
                for idx, (src, (_, n)) in enumerate(zip(videos, infos), 1):
                    self._ui(status=f"Video {idx}/{len(videos)}: {os.path.basename(src)}")
                    try:
                        r = self._volcar_video(src, writer, tw, th, blur, dark, fps_out, hecho, total)
                    except IOError as e:
                        errores.append(str(e))
                        r = (0, 0)
                    if r is None:
                        break
                    hecho += n
                    if usar_audio and r[0]:
                        wav = f"{destino}.tmp_{idx}.wav"
                        temporales.append(wav)
                        self._ui(status=f"Extrayendo audio {idx}/{len(videos)}...")
                        self._wav_de(src, r[0] / fps_out, wav)
                        wavs.append(wav)
                writer.release()

                if self.cancel:
                    self._borrar(destino, *temporales)
                    return self._fin("Proceso cancelado.")
                if usar_audio:
                    self._ui(status="Uniendo audio y video...")
                    unido = destino + ".tmp_audio.wav"
                    temporales.append(unido)
                    with wave.open(unido, "wb") as out:
                        for i, w in enumerate(wavs):
                            with wave.open(w, "rb") as entrada:
                                if i == 0:
                                    out.setparams(entrada.getparams())
                                out.writeframes(entrada.readframes(entrada.getnframes()))
                    if self._ffmpeg(["-i", tmp, "-i", unido, "-map", "0:v:0", "-map", "1:a:0",
                                     "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18",
                                     "-c:a", "aac", "-shortest", destino]):
                        pass
                    else:
                        os.replace(tmp, destino)
                        errores.append("ffmpeg falló: el video unido se guardó sin audio.")
                creados.append(destino)
            else:
                for idx, (src, (fps, n)) in enumerate(zip(videos, infos), 1):
                    base = os.path.splitext(os.path.basename(src))[0]
                    out = os.path.join(destino, f"{base}_16x9.mp4")
                    if os.path.abspath(out) == os.path.abspath(src):
                        errores.append(f"{base}: la salida coincide con la entrada, omitido.")
                        hecho += n
                        continue
                    self._ui(status=f"Video {idx}/{len(videos)}: {base}")
                    tmp = out + ".tmp_video.mp4"
                    temporales.append(tmp)
                    writer = cv2.VideoWriter(tmp if usar_audio else out, fourcc, fps, (tw, th))
                    if not writer.isOpened():
                        errores.append(f"{base}: no se pudo crear la salida.")
                        hecho += n
                        continue
                    try:
                        r = self._volcar_video(src, writer, tw, th, blur, dark, fps, hecho, total)
                    except IOError as e:
                        errores.append(str(e))
                        r = (0, 0)
                    writer.release()
                    if r is None:
                        self._borrar(out, *temporales)
                        return self._fin("Proceso cancelado.")
                    hecho += n
                    if usar_audio:
                        self._ui(status=f"Añadiendo audio: {base}")
                        if self._ffmpeg(["-i", tmp, "-i", src, "-map", "0:v:0", "-map", "1:a:0?",
                                         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18",
                                         "-c:a", "aac", "-shortest", out]):
                            self._borrar(tmp)
                        else:
                            os.replace(tmp, out)
                            errores.append(f"{base}: guardado sin audio (ffmpeg falló).")
                    creados.append(out)
        except Exception as err:
            self._borrar(*temporales)
            return self._fin(f"Error durante el procesamiento:\n{err}", error=True)
        finally:
            self._borrar(*[t for t in temporales if os.path.exists(t)])

        msg = (f"Video unido guardado en:\n{creados[0]}" if juntos and creados
               else f"Se convirtieron {len(creados)} video(s) en:\n{destino}")
        if errores:
            msg += "\n\nAvisos:\n- " + "\n- ".join(errores)
        self._fin(msg)


class HerramientasMultimedia(TkinterDnD.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"Herramientas Multimedia v{__version__}")
        self.cfg = cargar_config()
        sv_ttk.set_theme(self.cfg.get("tema", "light"))
        ttk.Style().configure("TNotebook.Tab", padding=(8, 8), font=("Segoe UI", 9, "bold"))
        ttk.Style().configure("Accent.TButton", font=("Segoe UI", 10, "bold"))
        self.option_add("*Font", ("Segoe UI", 10))
        self.geometry("1340x880")
        self.minsize(700, 600)

        menu = tk.Menu(self)
        ver = tk.Menu(menu, tearoff=0)
        ver.add_command(label="Alternar tema claro / oscuro", command=self.alternar_tema, accelerator="Ctrl+T")
        menu.add_cascade(label="Ver", menu=ver)
        self.bind("<Control-t>", lambda e: self.alternar_tema())
        ayuda = tk.Menu(menu, tearoff=0)
        ayuda.add_command(label="Buscar actualizaciones...",
                          command=lambda: Actualizador.comprobar(self, silencioso=False))
        ayuda.add_command(label=f"Versión {__version__} (código {Actualizador.sha_local()[:7] or 'incluido'})",
                          state="disabled")
        menu.add_cascade(label="Ayuda", menu=ayuda)
        self.config(menu=menu)

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True)

        pestanas = [
            (" 🔍  Zoom de imágenes ", ZoomVideoMaker),
            (" 📱 Video vertical a 16:9 ", VideoBlurFill),
            (" 🎞 Extractor de fotogramas ", VideoFrameExtractor),
            (" ✂ Recortar silencios ", AudioSilencerApp),
            (" 💬 Subtítulos Canva ", TextSplitterApp),
            (" 🔄 Convertidor universal ", UniversalConverter),
            (" ✂ Dividir archivos ", SplitterTab),
        ]
        for titulo, clase in pestanas:
            notebook.add(clase(notebook), text=titulo)
        self.notebook = notebook
        self.after(200, self._activar_drop)

    def alternar_tema(self):
        sv_ttk.set_theme("light" if sv_ttk.get_theme() == "dark" else "dark")
        self.cfg["tema"] = sv_ttk.get_theme()
        guardar_config(self.cfg)

    def _activar_drop(self):
        """Soltar archivos sobre la ventana: los recibe la pestaña activa."""
        def registrar(w):
            try:
                w.drop_target_register(DND_FILES)
                w.dnd_bind("<<Drop>>", self._al_soltar)
            except Exception:
                pass
            for h in w.winfo_children():
                registrar(h)
        registrar(self.notebook)

    def _al_soltar(self, event):
        archivos = [f for f in self.tk.splitlist(event.data) if os.path.isfile(f)]
        tab = self.nametowidget(self.notebook.select())
        if archivos and hasattr(tab, "recibir_archivos"):
            tab.recibir_archivos(archivos)
        return event.action


if __name__ == "__main__":
    HerramientasMultimedia().mainloop()
