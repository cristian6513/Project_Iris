# Iconos nativos de Iris

`iris-app-icon-master.png` es el maestro de alta resolución con transparencia
exterior. Conserva el retrato y el aro cian/violeta de Iris. El retrato original
de la interfaz, `face/public/assets/os/iris-profile-icon-v1.png`, sigue separado.

Los PNG de 32, 128, 256 y 512 píxeles y `icon.ico` se generan desde este maestro
con el comando `icon` del CLI de Tauri instalado en `companion/node_modules`.
Genera primero en una carpeta temporal y copia solo los cinco archivos usados
por Windows: `32x32.png`, `128x128.png`, `128x128@2x.png`, `icon.png` e `icon.ico`.
No uses el retrato opaco de la interfaz para regenerar estos iconos.

Desktop, Companion y el instalador de Iris comparten este conjunto. `icon.png`
ofrece 512×512 para el icono de ventana y las pantallas con escalado; el ICO
contiene los tamaños que extrae el shell de Windows. La ausencia de un icono
propio del Companion en el escritorio no cambia.

Revisión del 29 de octubre de 2026: retirada la placa cuadrada exterior con
`imagegen`, seguida de conversión nativa con Tauri. Se comprobaron el canal alfa
de los PNG/ICO y vistas sobre fondos claro y oscuro. La caché de iconos y el
resultado en Windows instalado se comprueban con el siguiente candidato.
