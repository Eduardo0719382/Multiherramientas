"""Lanzador: sincroniza el código con GitHub y arranca el programa.

Se compila a .exe una sola vez. Si el código actualizado falla al iniciar, usa el que
viene incluido en el .exe.
"""
import os
import sys
import traceback
import tkinter as tk
from tkinter import messagebox

import Actualizador


def _carpeta_incluida():
    return os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))),
                        "codigo" if getattr(sys, "frozen", False) else "")


def _crear_app(carpeta):
    for m in ("HerramientasMultimedia", "Conversor", "Divisor", "Rafaga", "CamaraAudio"):
        sys.modules.pop(m, None)
    sys.path.insert(0, carpeta)
    try:
        import HerramientasMultimedia
        return HerramientasMultimedia.HerramientasMultimedia()
    finally:
        sys.path.remove(carpeta)


def main():
    incluida = _carpeta_incluida()
    if len(sys.argv) == 3 and sys.argv[1] == "--comprobar-inicio":
        import json
        app = _crear_app(incluida)
        app.withdraw()
        app.update_idletasks()
        tab = app.nametowidget(app.notebook.tabs()[0])
        with open(sys.argv[2], "w", encoding="utf-8") as f:
            json.dump({"titulo": app.title(), "pestanas": len(app.notebook.tabs()),
                       "rafaga": type(tab).__name__, "perspectiva": tab.settings()["perspective"]}, f)
        app.destroy()
        return
    errores = []
    try:
        Actualizador.preparar(incluida)
        Actualizador.actualizar(timeout=4)
    except Exception as e:
        errores.append(f"Actualización omitida: {e}")

    for carpeta in (Actualizador.APP_DIR, incluida):
        try:
            app = _crear_app(carpeta)
        except Exception:
            errores.append(traceback.format_exc())
            continue
        app.mainloop()
        return
    r = tk.Tk()
    r.withdraw()
    messagebox.showerror("Herramientas Multimedia", "No se pudo iniciar el programa:\n\n" + "\n".join(errores)[-1500:])


if __name__ == "__main__":
    main()
