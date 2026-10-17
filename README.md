# Multiherramientas

Programa de escritorio (Windows) con herramientas multimedia: zoom en imágenes, video vertical a 16:9,
extractor de fotogramas, recorte de silencios, subtítulos para Canva y un convertidor universal
(video, audio, imagen, PDF, Word, Excel/CSV, PowerPoint, texto).

## Actualizaciones
El .exe es un lanzador. Al abrir, descarga desde esta rama `main` los archivos `.py` más recientes
(`HerramientasMultimedia.py`, `Conversor.py`), por lo que publicar una actualización es hacer `git push`.
`Lanzador.py` y `Actualizador.py` van dentro del .exe y requieren recompilar si cambian.

Requisitos: ffmpeg en el PATH (video/audio) y Microsoft Office (Word/Excel/PowerPoint a PDF).

## Ráfaga de fotos (v1.2.0)
Imágenes sobre un video con el sonido de cámara incluido, inicio e intervalo configurables,
collage sin huecos, bordes suavizados, perspectiva 3D opcional y zoom de entrada suave opcional.
Consulta [RAFAGA.md](RAFAGA.md). Los archivos `Rafaga.py`, `CamaraAudio.py` y `Divisor.py`
se distribuyen junto al programa mediante el actualizador. El audio está integrado en Python.

Pruebas: `python -m unittest discover -s tests -v`.

### v1.3.0
Zoom configurable: activar/desactivar, Zoom in o Zoom out e intensidad de 0 a 100 %.
