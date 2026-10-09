"""Pestaña "Dividir": parte audio, video y PDF en varios archivos.

Audio/video: cada X tiempo, en N partes iguales o por rangos de tiempo (ffmpeg).
PDF: una página por archivo, cada N páginas o por rangos de páginas (PyMuPDF).
"""
import json
import os
import re
import shutil
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
AUDIO_EXT = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".opus", ".wma", ".aiff"}
VIDEO_EXT = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv", ".m4v", ".mpg", ".mpeg", ".3gp", ".ts"}
PDF_EXT = {".pdf"}

MODOS_MEDIA = ["Cada X segundos / minutos", "En N partes iguales", "Rangos de tiempo personalizados"]
MODOS_PDF = ["Una página por archivo", "Cada N páginas", "Rangos de páginas personalizados"]


class DivisionError(Exception):
    pass


def tipo_de(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in AUDIO_EXT:
        return "audio"
    if ext in VIDEO_EXT:
        return "video"
    if ext in PDF_EXT:
        return "pdf"
    return None


def parse_tiempo(txt):
    """'90', '1:30', '01:02:03.5' o '1h2m3s' -> segundos (float)."""
    t = txt.strip().lower().replace(",", ".")
    if not t:
        raise ValueError("tiempo vacío")
    m = re.fullmatch(r"(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m)?(?:(\d+(?:\.\d+)?)s?)?", t)
    if m and any(m.groups()) and re.search(r"[hm]", t):
        h, mi, s = (float(g) if g else 0.0 for g in m.groups())
        return h * 3600 + mi * 60 + s
    partes = t.split(":")
    if len(partes) > 3:
        raise ValueError(f"tiempo no válido: {txt}")
    total = 0.0
    for p in partes:
        total = total * 60 + float(p)
    return total


def fmt_tiempo(seg):
    return f"{int(seg // 3600):02d}:{int(seg % 3600 // 60):02d}:{seg % 60:06.3f}"


def segmentos_tiempo(modo, dur, valor, rangos_txt):
    """Devuelve [(inicio, fin), ...] en segundos."""
    if modo == MODOS_MEDIA[0]:
        paso = parse_tiempo(valor)
        if paso <= 0:
            raise ValueError("La duración por parte debe ser mayor que 0.")
        out, i = [], 0.0
        while i < dur - 0.05:
            out.append((i, min(i + paso, dur)))
            i += paso
        return out
    if modo == MODOS_MEDIA[1]:
        n = int(float(valor))
        if n < 2:
            raise ValueError("Indica al menos 2 partes.")
        paso = dur / n
        return [(i * paso, (i + 1) * paso if i < n - 1 else dur) for i in range(n)]
    out = []
    for trozo in re.split(r"[;,\n]+", rangos_txt):
        trozo = trozo.strip()
        if not trozo:
            continue
        if "-" not in trozo:
            raise ValueError(f"Rango no válido: «{trozo}» (usa inicio-fin, p. ej. 0:30-1:45).")
        a, b = trozo.split("-", 1)
        try:
            ini = parse_tiempo(a)
            fin = parse_tiempo(b) if b.strip() else dur
        except ValueError:
            raise ValueError(f"Tiempo no válido en «{trozo}» (usa 90, 1:30 o 1:02:03).")
        fin = min(fin, dur)
        if fin <= ini:
            raise ValueError(f"El rango «{trozo}» termina antes de empezar o fuera del archivo.")
        out.append((ini, fin))
    if not out:
        raise ValueError("Escribe al menos un rango.")
    return out


def grupos_paginas(modo, n_pag, valor, rangos_txt):
    """Devuelve una lista de listas de páginas (índices base 0)."""
    if modo == MODOS_PDF[0]:
        return [[i] for i in range(n_pag)]
    if modo == MODOS_PDF[1]:
        k = int(float(valor))
        if k < 1:
            raise ValueError("N debe ser al menos 1.")
        return [list(range(i, min(i + k, n_pag))) for i in range(0, n_pag, k)]
    grupos = []
    for trozo in re.split(r"[;,\n]+", rangos_txt):
        trozo = trozo.strip()
        if not trozo:
            continue
        m = re.fullmatch(r"(\d+)\s*(?:-\s*(\d*))?", trozo)
        if not m:
            raise ValueError(f"Rango no válido: «{trozo}» (usa 3, 1-5 o 8-).")
        a = int(m.group(1))
        if m.group(2) is None:
            b = a
        else:
            b = int(m.group(2)) if m.group(2) else n_pag
        if a < 1 or b < a or a > n_pag:
            raise ValueError(f"Rango fuera del documento ({n_pag} páginas): «{trozo}».")
        grupos.append(list(range(a - 1, min(b, n_pag))))
    if not grupos:
        raise ValueError("Escribe al menos un rango de páginas.")
    return grupos


def duracion_media(path):
    if shutil.which("ffprobe"):
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                            "json", path], capture_output=True, creationflags=NO_WINDOW)
        try:
            return float(json.loads(r.stdout)["format"]["duration"])
        except (ValueError, KeyError):
            pass
    raise DivisionError("No se pudo leer la duración (¿ffmpeg/ffprobe en el PATH?).")


def cortar_media(src, ini, fin, destino, preciso):
    if not shutil.which("ffmpeg"):
        raise DivisionError("No se encontró ffmpeg en el PATH.")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{ini:.3f}", "-i", src, "-t", f"{fin - ini:.3f}"]
    if not preciso:
        cmd += ["-c", "copy", "-avoid_negative_ts", "make_zero"]
    cmd.append(destino)
    r = subprocess.run(cmd, capture_output=True, creationflags=NO_WINDOW)
    if r.returncode != 0:
        raise DivisionError("ffmpeg: " + r.stderr.decode("utf-8", "replace").strip()[-300:])


def dividir_pdf(src, grupos, carpeta, base, juntar, cancel=lambda: False, progreso=lambda i, n, p: None):
    import fitz
    creados = []
    origen = fitz.open(src)
    try:
        if juntar:
            nuevo = fitz.open()
            for g in grupos:
                for p in g:
                    nuevo.insert_pdf(origen, from_page=p, to_page=p)
            out = os.path.join(carpeta, f"{base}_seleccion.pdf")
            nuevo.save(out)
            nuevo.close()
            return [out]
        ancho = max(2, len(str(len(grupos))))
        for i, g in enumerate(grupos, 1):
            if cancel():
                break
            nuevo = fitz.open()
            for p in g:
                nuevo.insert_pdf(origen, from_page=p, to_page=p)
            sufijo = f"p{g[0] + 1}" if len(g) == 1 else f"p{g[0] + 1}-{g[-1] + 1}"
            out = os.path.join(carpeta, f"{base}_{i:0{ancho}d}_{sufijo}.pdf")
            nuevo.save(out)
            nuevo.close()
            creados.append(out)
            progreso(i, len(grupos), out)
    finally:
        origen.close()
    return creados


class SplitterTab(ttk.Frame):
    def __init__(self, master):
        super().__init__(master)
        self.archivo = None
        self.tipo = None
        self.cancel = False
        self.trabajando = False
        self._build_ui()

    def _build_ui(self):
        top = ttk.Frame(self, padding=12)
        top.pack(fill=tk.BOTH, expand=True)
        ttk.Label(top, text="Dividir audio, video o PDF en partes", font=("Segoe UI", 11, "bold")).pack(anchor=tk.W)

        f1 = ttk.Frame(top)
        f1.pack(fill=tk.X, pady=(8, 2))
        ttk.Button(f1, text="📁 Elegir archivo…", command=self.elegir).pack(side=tk.LEFT)
        self.lbl_arch = ttk.Label(f1, text="Arrastra un archivo aquí o usa el botón (audio, video o PDF)",
                                  font=("Segoe UI", 9, "italic"))
        self.lbl_arch.pack(side=tk.LEFT, padx=10)
        self.lbl_info = ttk.Label(top, text="")
        self.lbl_info.pack(anchor=tk.W)

        caja = ttk.LabelFrame(top, text=" Cómo dividir ", padding=10)
        caja.pack(fill=tk.X, pady=8)
        ttk.Label(caja, text="Modo:").grid(row=0, column=0, sticky=tk.W)
        self.combo_modo = ttk.Combobox(caja, state="readonly", width=34)
        self.combo_modo.grid(row=0, column=1, sticky=tk.W, padx=6, pady=2)
        self.combo_modo.bind("<<ComboboxSelected>>", lambda e: self._modo_cambiado())

        self.lbl_valor = ttk.Label(caja, text="Valor:")
        self.lbl_valor.grid(row=1, column=0, sticky=tk.W)
        self.var_valor = tk.StringVar(value="60")
        self.ent_valor = ttk.Entry(caja, textvariable=self.var_valor, width=18)
        self.ent_valor.grid(row=1, column=1, sticky=tk.W, padx=6, pady=2)
        self.lbl_ayuda = ttk.Label(caja, text="", foreground="#1976d2")
        self.lbl_ayuda.grid(row=1, column=2, sticky=tk.W)

        self.lbl_rangos = ttk.Label(caja, text="Rangos:")
        self.txt_rangos = tk.Text(caja, height=4, width=48, wrap=tk.WORD, relief=tk.SOLID, borderwidth=1)
        self.var_preciso = tk.BooleanVar(value=False)
        self.chk_preciso = ttk.Checkbutton(caja, text="Corte preciso (re-codifica; más lento, sin errores de ±segundos)",
                                           variable=self.var_preciso)
        self.var_juntar = tk.BooleanVar(value=False)
        self.chk_juntar = ttk.Checkbutton(caja, text="Unir las páginas elegidas en un solo PDF",
                                          variable=self.var_juntar)

        f2 = ttk.Frame(top)
        f2.pack(fill=tk.X, pady=2)
        ttk.Label(f2, text="Carpeta de salida:").pack(side=tk.LEFT)
        self.var_salida = tk.StringVar()
        ttk.Entry(f2, textvariable=self.var_salida).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        ttk.Button(f2, text="Elegir…", command=self.elegir_salida).pack(side=tk.LEFT)

        f3 = ttk.Frame(top)
        f3.pack(fill=tk.X, pady=8)
        self.btn_ir = ttk.Button(f3, text="✂ Dividir", style="Accent.TButton", command=self.iniciar)
        self.btn_ir.pack(side=tk.LEFT)
        self.btn_cancel = ttk.Button(f3, text="Cancelar", command=lambda: setattr(self, "cancel", True),
                                     state=tk.DISABLED)
        self.btn_cancel.pack(side=tk.LEFT, padx=6)
        self.progress = ttk.Progressbar(f3, mode="determinate")
        self.progress.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)

        self.log = tk.Text(top, height=9, state=tk.DISABLED, wrap=tk.WORD, relief=tk.FLAT)
        try:
            from Conversor import colores_tema
            fondo, texto = colores_tema()
            self.log.config(bg=fondo, fg=texto, highlightthickness=0)
            self.txt_rangos.config(bg=fondo, fg=texto, insertbackground=texto)
        except Exception:
            pass
        self.log.pack(fill=tk.BOTH, expand=True)
        self._modo_cambiado()

    # ---------- selección ----------
    def elegir(self):
        exts = sorted(AUDIO_EXT | VIDEO_EXT | PDF_EXT)
        f = filedialog.askopenfilename(filetypes=[("Audio, video y PDF", " ".join("*" + e for e in exts)),
                                                  ("Todos", "*.*")])
        if f:
            self.recibir_archivos([f])

    def recibir_archivos(self, files):
        for f in files:
            t = tipo_de(f)
            if t:
                self.archivo, self.tipo = f, t
                self.lbl_arch.config(text=os.path.basename(f), font=("Segoe UI", 9, "bold"))
                self.var_salida.set(os.path.join(os.path.dirname(f),
                                                 os.path.splitext(os.path.basename(f))[0] + "_partes"))
                self._info_archivo()
                self._configurar_modos()
                return
        self._log("Formato no soportado (usa audio, video o PDF).")

    def _info_archivo(self):
        try:
            if self.tipo == "pdf":
                import fitz
                with fitz.open(self.archivo) as d:
                    self.lbl_info.config(text=f"PDF · {d.page_count} páginas")
            else:
                self.lbl_info.config(text=f"{self.tipo.capitalize()} · duración {fmt_tiempo(duracion_media(self.archivo))[:8]}")
        except Exception as e:
            self.lbl_info.config(text=f"No se pudo leer el archivo: {e}")

    def _configurar_modos(self):
        modos = MODOS_PDF if self.tipo == "pdf" else MODOS_MEDIA
        self.combo_modo.config(values=modos)
        self.combo_modo.set(modos[0])
        self._modo_cambiado()

    def _modo_cambiado(self):
        modo = self.combo_modo.get()
        for w in (self.lbl_rangos, self.txt_rangos, self.chk_preciso, self.chk_juntar):
            w.grid_remove()
        pdf = self.tipo == "pdf"
        if modo in (MODOS_MEDIA[0], MODOS_PDF[1]) and modo:
            self.lbl_valor.grid()
            self.ent_valor.grid()
            self.lbl_valor.config(text="Cada:" if not pdf else "Páginas por archivo:")
            self.lbl_ayuda.config(text="p. ej. 60, 1:30, 5m" if not pdf else "p. ej. 10")
            if not self.var_valor.get().strip():
                self.var_valor.set("10" if pdf else "60")
        elif modo == MODOS_MEDIA[1]:
            self.lbl_valor.grid()
            self.ent_valor.grid()
            self.lbl_valor.config(text="Número de partes:")
            self.lbl_ayuda.config(text="p. ej. 4")
        else:
            self.lbl_valor.grid_remove()
            self.ent_valor.grid_remove()
            self.lbl_ayuda.config(text="")
        if modo in (MODOS_MEDIA[2], MODOS_PDF[2]):
            self.lbl_rangos.grid(row=2, column=0, sticky=tk.NW, pady=4)
            self.txt_rangos.grid(row=2, column=1, columnspan=2, sticky=tk.W, padx=6, pady=4)
            self.lbl_ayuda.config(text="")
            if not self.txt_rangos.get("1.0", tk.END).strip():
                self.txt_rangos.insert("1.0", "1-3, 5, 8-" if pdf else "0:00-1:30, 1:30-3:00, 3:00-")
            self.lbl_rangos.config(text="Rangos (separa con comas):")
        if pdf and modo:
            self.chk_juntar.grid(row=3, column=1, columnspan=2, sticky=tk.W, padx=6)
        elif modo:
            self.chk_preciso.grid(row=3, column=1, columnspan=2, sticky=tk.W, padx=6)

    def elegir_salida(self):
        d = filedialog.askdirectory()
        if d:
            self.var_salida.set(d)

    # ---------- ejecución ----------
    def _log(self, t):
        self.log.config(state=tk.NORMAL)
        self.log.insert(tk.END, t + "\n")
        self.log.see(tk.END)
        self.log.config(state=tk.DISABLED)

    def iniciar(self):
        if self.trabajando:
            return
        if not self.archivo:
            messagebox.showwarning("Dividir", "Elige primero un archivo.")
            return
        carpeta = self.var_salida.get().strip()
        if not carpeta:
            messagebox.showwarning("Dividir", "Elige la carpeta de salida.")
            return
        try:
            if self.tipo == "pdf":
                import fitz
                with fitz.open(self.archivo) as d:
                    n = d.page_count
                plan = grupos_paginas(self.combo_modo.get(), n, self.var_valor.get(),
                                      self.txt_rangos.get("1.0", tk.END))
            else:
                dur = duracion_media(self.archivo)
                plan = segmentos_tiempo(self.combo_modo.get(), dur, self.var_valor.get(),
                                        self.txt_rangos.get("1.0", tk.END))
        except (ValueError, DivisionError) as e:
            messagebox.showwarning("Dividir", str(e))
            return
        except Exception as e:
            messagebox.showerror("Dividir", f"No se pudo leer el archivo:\n{e}")
            return
        if len(plan) > 200 and not messagebox.askyesno("Dividir", f"Se crearán {len(plan)} archivos. ¿Continuar?"):
            return
        os.makedirs(carpeta, exist_ok=True)
        self.cancel = False
        self.trabajando = True
        self.btn_ir.config(state=tk.DISABLED)
        self.btn_cancel.config(state=tk.NORMAL)
        self.progress.config(value=0)
        self._log(f"Dividiendo en {len(plan) if not self.var_juntar.get() or self.tipo != 'pdf' else 1} parte(s)…")
        threading.Thread(target=self._trabajo, args=(self.archivo, self.tipo, plan, carpeta,
                                                     self.var_preciso.get(), self.var_juntar.get()),
                         daemon=True).start()

    def _trabajo(self, src, tipo, plan, carpeta, preciso, juntar):
        ui = lambda fn: self.after(0, fn)
        base, ext = os.path.splitext(os.path.basename(src))
        creados, error = [], None
        try:
            if tipo == "pdf":
                def prog(i, n, p):
                    ui(lambda: (self.progress.config(value=i / n * 100), self._log("   ✔ " + os.path.basename(p))))
                creados = dividir_pdf(src, plan, carpeta, base, juntar, lambda: self.cancel, prog)
                if juntar:
                    ui(lambda: self._log("   ✔ " + os.path.basename(creados[0])))
            else:
                ancho = max(2, len(str(len(plan))))
                for i, (a, b) in enumerate(plan, 1):
                    if self.cancel:
                        break
                    out = os.path.join(carpeta, f"{base}_parte{i:0{ancho}d}{ext}")
                    cortar_media(src, a, b, out, preciso)
                    creados.append(out)
                    ui(lambda i=i, a=a, b=b, o=out: (self.progress.config(value=i / len(plan) * 100),
                                                    self._log(f"   ✔ {os.path.basename(o)}  "
                                                              f"({fmt_tiempo(a)[:8]} → {fmt_tiempo(b)[:8]})")))
        except Exception as e:
            error = e

        def fin():
            self.trabajando = False
            self.btn_ir.config(state=tk.NORMAL)
            self.btn_cancel.config(state=tk.DISABLED)
            if error:
                self._log(f"✘ Error: {error}")
                messagebox.showerror("Dividir", str(error))
            elif self.cancel:
                self._log(f"Cancelado ({len(creados)} archivo(s) creados).")
            else:
                self.progress.config(value=100)
                self._log(f"Listo: {len(creados)} archivo(s) en {carpeta}")
                if messagebox.askyesno("Dividir", f"Se crearon {len(creados)} archivo(s).\n¿Abrir la carpeta?"):
                    try:
                        os.startfile(carpeta)
                    except OSError:
                        pass
        ui(fin)
