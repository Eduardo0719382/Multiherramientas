"""Actualización del código del programa desde GitHub.

El .exe es solo un lanzador; el código (.py) se guarda en %APPDATA%\\HerramientasMultimedia\\app
y se sincroniza con la rama principal del repositorio. Para publicar una actualización basta
con hacer push a esa rama: no hace falta recompilar el .exe (salvo que se agregue una librería).
"""
import io
import json
import os
import shutil
import subprocess
import sys
import threading
import urllib.request
import zipfile
import tkinter as tk
from tkinter import messagebox

REPO = "Eduardo0719382/Multiherramientas"
BRANCH = "main"
DATA_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "HerramientasMultimedia")
APP_DIR = os.path.join(DATA_DIR, "app")
STATE = os.path.join(DATA_DIR, "estado.json")
MAIN_FILE = "HerramientasMultimedia.py"
NO_UPDATE = {"Lanzador.py", "Actualizador.py"}   # viven dentro del .exe
UA = {"User-Agent": "HerramientasMultimedia-Updater"}


def _estado():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _guardar_estado(d):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(d, f)


def sha_local():
    return _estado().get("sha", "")


def sha_remoto(timeout=8):
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/commits/{BRANCH}",
                                 headers={**UA, "Accept": "application/vnd.github.sha"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode().strip()


def preparar(carpeta_incluida):
    """Primera ejecución: copia el código incluido en el .exe a APP_DIR."""
    if os.path.exists(os.path.join(APP_DIR, MAIN_FILE)):
        return
    os.makedirs(APP_DIR, exist_ok=True)
    for n in os.listdir(carpeta_incluida):
        if n.endswith(".py"):
            shutil.copy2(os.path.join(carpeta_incluida, n), os.path.join(APP_DIR, n))


def actualizar(timeout=8):
    """Sincroniza APP_DIR con GitHub. Devuelve True si se descargó código nuevo."""
    try:
        remoto = sha_remoto(timeout)
    except urllib.error.HTTPError as e:
        if e.code in (404, 409):      # repositorio vacío / sin la rama todavía
            return False
        raise
    if not remoto or remoto == sha_local():
        return False
    req = urllib.request.Request(f"https://codeload.github.com/{REPO}/zip/{remoto}", headers=UA)
    with urllib.request.urlopen(req, timeout=max(timeout, 30)) as r:
        datos = r.read()
    nuevos = {}
    with zipfile.ZipFile(io.BytesIO(datos)) as z:
        for info in z.infolist():
            partes = info.filename.split("/")
            if len(partes) == 2 and partes[1].endswith(".py") and partes[1] not in NO_UPDATE:
                nuevos[partes[1]] = z.read(info)
    if MAIN_FILE not in nuevos:
        raise RuntimeError(f"El repositorio no contiene {MAIN_FILE} en la raíz.")
    for nombre, src in nuevos.items():          # comprobar que compila antes de reemplazar nada
        compile(src, nombre, "exec")
    os.makedirs(APP_DIR, exist_ok=True)
    for nombre, src in nuevos.items():
        with open(os.path.join(APP_DIR, nombre), "wb") as f:
            f.write(src)
    _guardar_estado({"sha": remoto})
    return True


def reiniciar():
    if getattr(sys, "frozen", False):
        subprocess.Popen([sys.executable], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    else:
        subprocess.Popen([sys.executable] + sys.argv)


def comprobar(root, silencioso=False):
    """Desde el menú Ayuda: busca, descarga y ofrece reiniciar."""
    def tarea():
        try:
            cambio = actualizar()
        except Exception as e:
            if not silencioso:
                root.after(0, lambda: messagebox.showerror("Actualizaciones", f"No se pudo actualizar:\n{e}"))
            return
        if not cambio:
            if not silencioso:
                root.after(0, lambda: messagebox.showinfo("Actualizaciones", "Ya tienes la última versión."))
            return

        def avisar():
            if messagebox.askyesno("Actualización instalada",
                                   "Se descargó una versión nueva del programa.\n¿Reiniciar ahora para usarla?"):
                reiniciar()
                root.destroy()
        root.after(0, avisar)
    threading.Thread(target=tarea, daemon=True).start()
