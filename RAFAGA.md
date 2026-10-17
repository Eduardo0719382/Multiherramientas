# Ráfaga de fotos

Abre `dist/HerramientasMultimedia.exe` (versión 1.3.0) para usar las mejoras. Al iniciar instala el código incluido si la copia guardada es anterior. También puedes ejecutar el código con `Abrir herramientas.bat`.

1. En la pestaña **Ráfaga de fotos**, elige el video de fondo.
2. Añade imágenes y ordénalas con las flechas. También puedes arrastrar archivos a la pestaña.
3. Indica el inicio en segundos o `mm:ss`, el intervalo entre apariciones y cuánto permanece la última composición.
4. Elige **Collage acumulado** (como la referencia, desde abajo a la derecha) o **Una por una** (fotos centradas).
5. Ajusta volumen, conservación del audio original y destello opcional. El collage usa siempre el encuadre completo; el tamaño solo afecta al modo Una por una.
6. Mueve el control de tiempo y pulsa **Actualizar vista previa** para inspeccionar un fotograma. Esta vista es estática y no reproduce audio.
7. Pulsa **Exportar MP4**. El video de fondo conserva su duración; las fotos se muestran solo durante el intervalo configurado.

Ejemplo: 6 fotos, inicio 0, intervalo 0,15 y permanencia 2 muestran la última foto en 0,75 s; todas desaparecen en 2,75 s.

El sonido proporcionado viene integrado en `CamaraAudio.py` y seleccionado por defecto. También se conserva una copia en `assets/camara.mp3`. El botón **Usar tu sonido incluido** restaura el predeterminado. Se elimina el silencio inicial y se utiliza un fragmento de hasta 350 ms por aparición. Puedes elegir otro archivo de cámara.

Requiere las dependencias existentes del proyecto y `ffmpeg`/`ffprobe` en PATH. Para trasladar esta versión, incluye `Rafaga.py` y `CamaraAudio.py`, además de los archivos habituales. El audio integrado también se distribuye mediante el actualizador de archivos Python.

Comprobación: `python -m unittest test_rafaga -v`.

El collage ajusta el tamaño de las fotos en un mosaico con perspectiva suave y bordes compartidos. Cada píxel pertenece a una sola foto: no se superponen ni quedan huecos cuando aparece la última foto. La inclinación se limita para que las piezas encajen. Durante la entrada progresiva todavía se ven las zonas sin cubrir. Las imágenes se recortan para llenar cada zona; sus transparencias se rellenan de blanco.

Los bordes se calculan a 4 veces la resolución y se reducen con suavizado. Las uniones mezclan contribuciones complementarias para evitar dientes de sierra y filtraciones del fondo. La perspectiva incluye una convergencia trapezoidal ligera, como fotografías recostadas.

La casilla **Perspectiva 3D del collage** activa o desactiva el efecto. El ejecutable comprueba actualizaciones en el repositorio configurado, pero no instala versiones inferiores a la que ya tienes. Para actualizar otras copias antiguas mediante Internet hay que publicar los archivos Python en ese repositorio; recompilar el ejecutable solo actualiza esta copia local.

**Zoom de entrada suave** viene activado: al empezar la ráfaga, acerca progresivamente el conjunto un 6 % y mantiene el acercamiento hasta que termina el efecto. Puede desactivarse. El acercamiento recorta ligeramente los bordes externos del encuadre.

En **Activar zoom** puedes habilitar o quitar el efecto. Elige **Zoom in (acercar)** o **Zoom out (alejar)** y una **Intensidad de 0 a 100 %** (6 % por defecto; 0 % no cambia la escala). Zoom out empieza acercado y vuelve al encuadre completo, sin crear bordes vacíos. Estos controles se aplican a la vista previa y al MP4.
