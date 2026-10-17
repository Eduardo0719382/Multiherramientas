"""Ráfaga de fotografías sobre un video, con exportación MP4 y obturador."""
import math
import io
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import cv2
import numpy as np
from PIL import Image, ImageOps, ImageTk
from pydub import AudioSegment
from pydub.silence import detect_nonsilent

from Divisor import parse_tiempo
from CamaraAudio import AUDIO

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DEFAULT_SOUND = os.path.join(os.path.dirname(__file__), "assets", "camara.mp3")


def video_info(path):
    cap = cv2.VideoCapture(path)
    try:
        fps = cap.get(cv2.CAP_PROP_FPS)
        count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        ok, frame = cap.read()
        if not ok or not math.isfinite(fps) or fps <= 0 or count <= 0:
            raise ValueError("No se puede leer el video seleccionado.")
        h, w = frame.shape[:2]
        return fps, int(count), w, h
    finally:
        cap.release()


def validate(settings, duration, count):
    if not count:
        raise ValueError("Añade al menos una imagen.")
    if not all(math.isfinite(settings[k]) for k in ("start", "interval", "hold", "size", "volume")):
        raise ValueError("Introduce números finitos.")
    if settings["start"] < 0 or settings["interval"] < .04 or settings["hold"] < .04:
        raise ValueError("El inicio debe ser positivo o cero; intervalo y permanencia, al menos 0,04 s.")
    if settings["start"] + (count - 1) * settings["interval"] + settings["hold"] > duration + .001:
        raise ValueError("La ráfaga no cabe en el video. Reduce inicio, intervalo o permanencia.")
    if not 10 <= settings["size"] <= 100 or not 0 <= settings["volume"] <= 200:
        raise ValueError("Tamaño: 10–100 %. Volumen: 0–200 %.")


def prepare_photos(paths, width, height, settings):
    if settings["mode"] == "Collage acumulado":
        return prepare_collage(paths, width, height, settings.get("perspective", True))
    columns = max(1, math.ceil(math.sqrt(len(paths) * width / height)))
    rows = math.ceil(len(paths) / columns)
    if settings["mode"] == "Una por una":
        columns = rows = 1
    area_w, area_h = int(width * settings["size"] / 100), int(height * settings["size"] / 100)
    cell_w, cell_h = max(2, area_w // columns), max(2, area_h // rows)
    result = []
    for i, path in enumerate(paths):
        with Image.open(path) as source:
            photo = ImageOps.exif_transpose(source).convert("RGBA")
            photo.thumbnail((cell_w, cell_h), Image.Resampling.LANCZOS)
        # Primero abajo a la derecha, como en la referencia.
        slot = 0 if settings["mode"] == "Una por una" else i
        col, row = columns - 1 - slot % columns, rows - 1 - slot // columns
        x = (width - area_w) // 2 + col * cell_w + (cell_w - photo.width) // 2
        y = (height - area_h) // 2 + row * cell_h + (cell_h - photo.height) // 2
        result.append((photo, (x, y)))
    return result


def prepare_collage(paths, width, height, perspective=True):
    """Mosaico con límites compartidos y un único propietario por píxel."""
    scale = 4
    width, height = width * scale, height * scale
    rows = min(len(paths), max(1, round(math.sqrt(len(paths) * height / width))))
    cells = []

    def boundary(row, x):
        # Los bordes externos quedan rectos. Cada separación interior es una
        # sola recta, compartida incluso por filas con distinta cantidad de fotos.
        slope = 0 if not perspective or row in (0, rows) else (.16 if row % 2 else -.16)
        return height * row / rows + slope * height / rows * (x / width - .5)

    def column_x(col, columns, row):
        u = col / columns
        # Convergencia suave en profundidad: lados ligeramente trapezoidales.
        offset = .055 * math.sin(math.pi * u) * (1 if row % 2 else -1) if perspective else 0
        return width * (u + offset)

    for row in range(rows - 1, -1, -1):
        columns = len(paths) // rows + ((rows - 1 - row) < len(paths) % rows)
        for col in range(columns - 1, -1, -1):
            x0, x1 = column_x(col, columns, row), column_x(col + 1, columns, row)
            x2, x3 = column_x(col + 1, columns, row + 1), column_x(col, columns, row + 1)
            cells.append(np.float32([
                [x0, boundary(row, x0)], [x1, boundary(row, x1)],
                [x2, boundary(row + 1, x2)], [x3, boundary(row + 1, x3)]
            ]))

    # Clasificación por centros de píxel: evita huecos de redondeo en uniones
    # en T y evita que dos fotografías pinten el mismo borde.
    xx = (np.arange(width, dtype=np.float32) + .5) / width
    yy = np.arange(height, dtype=np.float32)[:, None] + .5
    owner = np.full((height, width), -1, dtype=np.int32)
    index = 0
    for row in range(rows - 1, -1, -1):
        columns = len(paths) // rows + ((rows - 1 - row) < len(paths) % rows)
        top = boundary(row, xx * width)
        bottom = boundary(row + 1, xx * width)
        in_row = (yy >= top) & (yy < bottom)
        for col in range(columns - 1, -1, -1):
            quad = cells[index]
            def side_x(a, b):
                return a[0] + (yy - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
            left = side_x(quad[0], quad[3])
            right = side_x(quad[1], quad[2])
            mask = in_row & (xx * width >= left) & (xx * width < right)
            owner[mask] = index
            index += 1

    result = []
    for index, (path, corners) in enumerate(zip(paths, cells)):
        origin = np.maximum(0, np.floor(corners.min(axis=0) / scale).astype(int) * scale)
        end = np.minimum((width, height), np.ceil(corners.max(axis=0) / scale).astype(int) * scale)
        bw, bh = (int(v) for v in end - origin)
        if bw < 1 or bh < 1 or not np.any(owner[origin[1]:end[1], origin[0]:end[0]] == index):
            raise ValueError("Hay demasiadas fotos para la resolución del video.")
        with Image.open(path) as source:
            photo = ImageOps.exif_transpose(source).convert("RGBA")
            opaque = Image.new("RGBA", photo.size, "white")
            opaque.alpha_composite(photo)
            photo = ImageOps.fit(opaque.convert("RGB"), (max(2, bw), max(2, bh)), Image.Resampling.LANCZOS)
        src = np.float32([[0, 0], [photo.width - 1, 0], [photo.width - 1, photo.height - 1], [0, photo.height - 1]])
        # Muestrear en centros de píxel, igual que la asignación de celdas.
        matrix = cv2.getPerspectiveTransform(src, corners - origin.astype(np.float32) - .5)
        warped = cv2.warpPerspective(np.asarray(photo), matrix, (bw, bh), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        alpha = (owner[origin[1]:end[1], origin[0]:end[0]] == index).astype(np.uint8) * 255
        tile = Image.fromarray(np.dstack((warped, alpha)))
        # Filtro de área con alfa premultiplicado: suaviza contorno y contenido
        # sin halos oscuros ni bordes que invadan la foto vecina.
        tile = tile.resize((bw // scale, bh // scale), Image.Resampling.BOX)
        result.append((tile, tuple(int(v // scale) for v in origin)))
    return result


def compose(frame, time, photos, settings):
    relative = time - settings["start"]
    end = (len(photos) - 1) * settings["interval"] + settings["hold"]
    if relative < 0 or relative >= end:
        return frame
    index = min(len(photos) - 1, int((relative + 1e-8) / settings["interval"]))
    canvas = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    visible = photos[:index + 1] if settings["mode"] == "Collage acumulado" else photos[index:index + 1]
    if settings["mode"] == "Collage acumulado":
        colors = np.zeros((frame.shape[0], frame.shape[1], 3), dtype=np.float32)
        weights = np.zeros(frame.shape[:2], dtype=np.float32)
        for photo, (x, y) in visible:
            rgba = np.asarray(photo, dtype=np.float32)
            alpha = rgba[:, :, 3] / 255
            colors[y:y + photo.height, x:x + photo.width] += rgba[:, :, :3] * alpha[:, :, None]
            weights[y:y + photo.height, x:x + photo.width] += alpha
        # Las contribuciones de un borde compartido se suman, no se pintan una
        # encima de otra (eso dejaría entrever el fondo por la doble mezcla).
        full = index == len(photos) - 1
        divisor = np.maximum(weights, 1e-8) if full else np.maximum(weights, 1)
        rgb = colors / divisor[:, :, None]
        if not full:
            rgb += np.asarray(canvas) * (1 - np.minimum(weights, 1))[:, :, None]
        canvas = Image.fromarray(np.clip(np.rint(rgb), 0, 255).astype(np.uint8))
    else:
        for photo, position in visible:
            canvas.paste(photo, position, photo)
    if settings.get("zoom", True):
        # Acercamiento del conjunto durante las entradas, con frenado suave.
        # Se conserva la escala final mientras permanece el collage.
        duration = max(.45, (len(photos) - 1) * settings["interval"])
        progress = min(1.0, relative / duration)
        progress = progress * progress * (3 - 2 * progress)
        factor = 1 + .06 * progress
        w, h = canvas.size
        dx, dy = w * (1 - 1 / factor) / 2, h * (1 - 1 / factor) / 2
        canvas = canvas.transform((w, h), Image.Transform.EXTENT,
                                  (dx, dy, w - dx, h - dy), Image.Resampling.BICUBIC)
    if settings["flash"] and relative - index * settings["interval"] < .05:
        canvas = Image.blend(canvas, Image.new("RGB", canvas.size, "white"), .25)
    return cv2.cvtColor(np.asarray(canvas), cv2.COLOR_RGB2BGR)


def run_process(args, cancel):
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=log, creationflags=NO_WINDOW)
        try:
            while True:
                if cancel.is_set():
                    raise InterruptedError("Exportación cancelada.")
                try:
                    code = process.wait(timeout=.2)
                    break
                except subprocess.TimeoutExpired:
                    pass
            if code:
                log.seek(0)
                raise RuntimeError(log.read().decode("utf-8", errors="replace")[-2000:])
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()


def export_burst(video, paths, sound, output, settings, cancel, progress):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise ValueError("Se necesitan FFmpeg y FFprobe en PATH para exportar el video con sonido.")
    inputs = [video, sound] + list(paths)
    if any(os.path.normcase(os.path.realpath(output)) == os.path.normcase(os.path.realpath(p)) for p in inputs if p):
        raise ValueError("La salida no puede reemplazar un archivo de entrada.")
    fps, count, width, height = video_info(video)
    validate(settings, count / fps, len(paths))
    photos = prepare_photos(paths, width, height, settings)
    with tempfile.TemporaryDirectory(prefix="rafaga_", dir=os.path.dirname(os.path.abspath(output))) as tmp:
        silent = os.path.join(tmp, "video.mp4")
        cap = cv2.VideoCapture(video)
        writer = cv2.VideoWriter(silent, cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
        frames = 0
        try:
            if not writer.isOpened():
                raise ValueError("No se pudo crear el video temporal.")
            while True:
                if cancel.is_set():
                    raise InterruptedError("Exportación cancelada.")
                ok, frame = cap.read()
                if not ok:
                    break
                writer.write(compose(frame, frames / fps, photos, settings))
                frames += 1
                if frames % 12 == 0:
                    progress(min(85, frames / count * 85), "Procesando imágenes…")
        finally:
            writer.release()
            cap.release()
        if frames < count - 1:
            raise ValueError("El video no se pudo leer completo; no se guardó una salida incompleta.")
        progress(88, "Mezclando el sonido y comprimiendo…")
        duration = frames / fps
        # Un solo clic por foto: quitar silencio inicial y usar hasta 350 ms del audio elegido.
        source = io.BytesIO(AUDIO) if sound == DEFAULT_SOUND else sound
        click = AudioSegment.from_file(source, format="mp3" if sound == DEFAULT_SOUND else None)[:10000]
        regions = detect_nonsilent(click, min_silence_len=20, silence_thresh=-45)
        if not regions:
            raise ValueError("El archivo de cámara no contiene sonido audible.")
        click = click[regions[0][0]:regions[0][0] + 350].fade_out(25)
        if settings["volume"] == 0:
            click = click - 120
        else:
            click += 20 * math.log10(settings["volume"] / 100)
        burst_ms = math.ceil(((len(paths) - 1) * settings["interval"] + .4) * 1000)
        track = AudioSegment.silent(duration=burst_ms, frame_rate=44100).set_channels(2)
        for i in range(len(paths)):
            track = track.overlay(click, position=round(i * settings["interval"] * 1000))
        wav = os.path.join(tmp, "camara.wav")
        track.export(wav, format="wav").close()
        encoded = os.path.join(tmp, "final.mp4")
        args = ["ffmpeg", "-y", "-v", "error", "-i", silent, "-i", video, "-i", wav]
        delay = round(settings["start"] * 1000)
        filters = f"[2:a]adelay={delay}:all=1,apad,atrim=duration={duration}[click]"
        # amovie no es necesario: ffprobe identifica si existe audio original.
        probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=index", "-of", "csv=p=0", video], capture_output=True, creationflags=NO_WINDOW, check=True)
        if settings["keep_audio"] and probe.stdout.strip():
            filters += ";[1:a:0]asetpts=PTS-STARTPTS[original];[original][click]amix=inputs=2:duration=longest:normalize=0,alimiter=limit=0.95:latency=1[a]"
        else:
            filters += ";[click]anull[a]"
        args += ["-filter_complex", filters, "-map", "0:v:0", "-map", "[a]", "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-t", str(duration), "-movflags", "+faststart", encoded]
        run_process(args, cancel)
        if cancel.is_set():
            raise InterruptedError("Exportación cancelada.")
        os.replace(encoded, output)
    progress(100, "Video guardado: " + output)


class RafagaTab(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=12)
        self.paths = []
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.busy = False
        self.video = tk.StringVar()
        self.sound = tk.StringVar(value=DEFAULT_SOUND)
        self.values = {k: tk.StringVar(value=v) for k, v in {"start": "0", "interval": "0.15", "hold": "2", "size": "100", "volume": "100", "mode": "Collage acumulado"}.items()}
        self.keep_audio = tk.BooleanVar(value=True)
        self.flash = tk.BooleanVar(value=False)
        self.perspective = tk.BooleanVar(value=True)
        self.zoom = tk.BooleanVar(value=True)
        left = ttk.Frame(self)
        left.pack(side="left", fill="y", padx=(0, 16))
        right = ttk.Frame(self)
        right.pack(side="left", fill="both", expand=True)
        for label, variable, kind in [("Video de fondo", self.video, "video"), ("Sonido de cámara", self.sound, "audio")]:
            ttk.Label(left, text=label).pack(anchor="w")
            ttk.Entry(left, textvariable=variable, width=40).pack(fill="x")
            ttk.Button(left, text="Elegir " + label.lower(), command=lambda v=variable, k=kind: self.pick(v, k)).pack(fill="x", pady=(2, 8))
        ttk.Button(left, text="Usar tu sonido incluido", command=lambda: self.sound.set(DEFAULT_SOUND)).pack(fill="x", pady=(0, 5))
        ttk.Button(left, text="Añadir imágenes…", command=self.add_images).pack(fill="x")
        self.listbox = tk.Listbox(left, height=7, exportselection=False)
        self.listbox.pack(fill="x", pady=5)
        actions = ttk.Frame(left)
        actions.pack(fill="x")
        for text, command in [("↑", lambda: self.move(-1)), ("↓", lambda: self.move(1)), ("Quitar", self.remove)]:
            ttk.Button(actions, text=text, command=command, width=8).pack(side="left", expand=True, fill="x")
        for key, label in [("start", "Inicio (segundos o mm:ss)"), ("interval", "Intervalo entre fotos (s)"), ("hold", "Permanencia después de la última foto (s)"), ("size", "Tamaño en modo Una por una (%)"), ("volume", "Volumen de cámara (%)")]:
            row = ttk.Frame(left)
            row.pack(fill="x", pady=3)
            ttk.Label(row, text=label).pack(side="left")
            ttk.Entry(row, textvariable=self.values[key], width=7).pack(side="right")
        ttk.Combobox(left, textvariable=self.values["mode"], values=["Collage acumulado", "Una por una"], state="readonly").pack(fill="x", pady=5)
        ttk.Checkbutton(left, text="Conservar audio del video", variable=self.keep_audio).pack(anchor="w")
        ttk.Checkbutton(left, text="Perspectiva 3D del collage", variable=self.perspective).pack(anchor="w")
        ttk.Checkbutton(left, text="Zoom de entrada suave", variable=self.zoom).pack(anchor="w")
        ttk.Checkbutton(left, text="Destello al aparecer cada foto", variable=self.flash).pack(anchor="w")
        self.export_button = ttk.Button(left, text="Exportar MP4…", command=self.start_export, style="Accent.TButton")
        self.export_button.pack(fill="x", pady=8)
        self.cancel_button = ttk.Button(left, text="Cancelar exportación", command=self.cancel.set, state="disabled")
        self.cancel_button.pack(fill="x")
        ttk.Label(right, text="Vista previa: mueve el control y pulsa Actualizar.\nCollage: fotos ajustadas con perspectiva suave, sin superponerse ni dejar huecos.\nTu sonido de cámara ya está incluido y seleccionado.", wraplength=550).pack(anchor="w")
        self.preview = ttk.Label(right, anchor="center")
        self.preview.pack(fill="both", expand=True)
        self.position = tk.DoubleVar(value=0)
        self.timeline = ttk.Scale(right, from_=0, to=10, variable=self.position)
        self.timeline.pack(fill="x", pady=8)
        ttk.Button(right, text="Actualizar vista previa (sin sonido)", command=self.show_preview).pack()
        self.progress = ttk.Progressbar(right, maximum=100)
        self.progress.pack(fill="x", pady=8)
        self.status = ttk.Label(right, text="Carga el video y las imágenes para comenzar.", wraplength=550)
        self.status.pack(fill="x")
        self.after(100, self.poll)

    def pick(self, variable, kind):
        types = [("Videos", "*.mp4 *.mov *.mkv *.avi *.webm")] if kind == "video" else [("Audio", "*.mp3 *.wav *.ogg *.m4a *.flac")]
        path = filedialog.askopenfilename(filetypes=types + [("Todos", "*.*")])
        if path:
            variable.set(path)

    def add_images(self):
        self.paths.extend(filedialog.askopenfilenames(filetypes=[("Imágenes", "*.jpg *.jpeg *.png *.webp *.bmp")]))
        self.refresh_list()

    def recibir_archivos(self, paths):
        for path in paths:
            ext = os.path.splitext(path)[1].lower()
            if ext in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
                self.paths.append(path)
            elif ext in {".mp3", ".wav", ".ogg", ".m4a", ".flac"}:
                self.sound.set(path)
            elif ext in {".mp4", ".mov", ".mkv", ".avi", ".webm"}:
                self.video.set(path)
        self.refresh_list()

    def refresh_list(self):
        self.listbox.delete(0, "end")
        for i, path in enumerate(self.paths, 1):
            self.listbox.insert("end", f"{i}. {os.path.basename(path)}")

    def move(self, direction):
        selection = self.listbox.curselection()
        if selection and 0 <= selection[0] + direction < len(self.paths):
            i, j = selection[0], selection[0] + direction
            self.paths[i], self.paths[j] = self.paths[j], self.paths[i]
            self.refresh_list()
            self.listbox.selection_set(j)

    def remove(self):
        for i in reversed(self.listbox.curselection()):
            del self.paths[i]
        self.refresh_list()

    def settings(self):
        result = {k: (v.get() if k == "mode" else parse_tiempo(v.get())) for k, v in self.values.items()}
        result.update(keep_audio=self.keep_audio.get(), flash=self.flash.get(), perspective=self.perspective.get(), zoom=self.zoom.get())
        return result

    def show_preview(self):
        cap = None
        try:
            settings = self.settings()
            fps, count, w, h = video_info(self.video.get())
            validate(settings, count / fps, len(self.paths))
            self.timeline.configure(to=max(0, (count - 1) / fps))
            t = min(self.position.get(), (count - 1) / fps)
            cap = cv2.VideoCapture(self.video.get())
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, frame = cap.read()
            if not ok:
                raise ValueError("No se pudo leer ese fotograma.")
            scale = min(640 / w, 520 / h, 1)
            frame = cv2.resize(frame, (max(2, int(w * scale)), max(2, int(h * scale))))
            photos = prepare_photos(self.paths, frame.shape[1], frame.shape[0], settings)
            result = compose(frame, t, photos, settings)
            self.preview_image = ImageTk.PhotoImage(Image.fromarray(cv2.cvtColor(result, cv2.COLOR_BGR2RGB)))
            self.preview.configure(image=self.preview_image)
            self.status.configure(text=f"Vista previa: {t:.2f} s / {count / fps:.2f} s")
        except Exception as error:
            messagebox.showerror("Ráfaga", str(error))
        finally:
            if cap is not None:
                cap.release()

    def start_export(self):
        if self.busy:
            return
        try:
            settings = self.settings()
            video, sound, paths = self.video.get(), self.sound.get(), list(self.paths)
            if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
                raise ValueError("Instala FFmpeg y FFprobe y añádelos al PATH.")
            if sound != DEFAULT_SOUND and not os.path.isfile(sound):
                raise ValueError("Elige un archivo de sonido de cámara.")
            fps, count, _, _ = video_info(video)
            validate(settings, count / fps, len(paths))
        except Exception as error:
            messagebox.showerror("Ráfaga", str(error))
            return
        output = filedialog.asksaveasfilename(defaultextension=".mp4", initialfile="video_rafaga.mp4", filetypes=[("Video MP4", "*.mp4")])
        if not output:
            return
        self.busy = True
        self.cancel.clear()
        self.export_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.progress["value"] = 0
        self.status.configure(text="Preparando exportación…")
        def work():
            try:
                export_burst(video, paths, sound, output, settings, self.cancel, lambda n, text: self.events.put(("progress", n, text)))
                self.events.put(("done", 100, "Video guardado: " + output))
            except InterruptedError as error:
                self.events.put(("done", 0, str(error)))
            except Exception as error:
                self.events.put(("error", 0, str(error)))
        threading.Thread(target=work, daemon=True).start()

    def poll(self):
        try:
            while True:
                kind, value, text = self.events.get_nowait()
                self.progress["value"] = value
                self.status.configure(text=text)
                if kind != "progress":
                    self.busy = False
                    self.export_button.configure(state="normal")
                    self.cancel_button.configure(state="disabled")
                    if kind == "error":
                        messagebox.showerror("Ráfaga", text)
        except queue.Empty:
            pass
        self.after(100, self.poll)
