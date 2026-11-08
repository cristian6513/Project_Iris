# Página de IRIS OS

Documentación revisada el 2026-10-25. Los comandos siguientes no publican nada
por leerlos; `deploy.mjs` es una operación de publicación independiente. La
fuente de funciones/versiones es [Estado actual](../docs/estado-actual.md), no
una propuesta de marketing ni una captura antigua del producto.

Sitio de presentación de IRIS OS. La página es en sí misma un escritorio de IRIS OS:
cada sección se abre como una ventana de app (arrastrable, redimensionable, con
minimizar, maximizar y su pestaña en la barra de ventanas), y los cuatro temas del
sistema (Cyber, Sakura, Hacker y Classic) cambian toda la página con su transición propia.

El diseño (revisado el 2026-10-21; el usuario prefirió el diseño publicado al rediseño
del 2026-10-20 y conservó de este la barra superior y los accesos del inicio):

- **Barra superior** como la de IRIS OS: marca, estado («En espera» o la ventana
  abierta), fecha y hora, el `?` del tutorial y Apps.
- **Inicio**: el titular a la izquierda y, a la derecha, la tarjeta de IRIS con sus
  cuatro **accesos** debajo (Estado, Preguntas, Arquitectura, Historia). La tarjeta se
  oculta por debajo de 1181 px; los accesos, cuando la ventana mide menos de 1341 px de
  ancho (el dock centrado llegaría a su columna) o menos de 640 px de alto. Siguen en Apps.
- **Dock** con Apps, la ruta 1 → 2 → 3, Companion, Privacidad y Temas, y **Runtime
  Pulse** con las ventanas abiertas.
- **Barra de ventanas** encima del dock cuando hay ventanas.
- **Vista compacta** cuando el ancho no supera 760 px o la altura no supera
  600 px, también en horizontal: una sola ventana enfocada, con scroll propio,
  separada de la barra de ventanas y del dock.

La sección Funciones describe las capacidades incluidas en la beta, todavía en
QA: Inicio con widgets, Calendario, Correo, Conexiones y Automatizaciones, además
de voz, avatar y herramientas. Comunidad separa esas funciones de la hoja de ruta.
La Store local para paquetes ya está en la beta; su catálogo comunitario en línea
sigue pendiente y no se presenta como disponible.

Para que nadie se pierda:

- **Mini tutorial** de 4 pasos la primera vez; el botón `?` de la barra superior lo repite.
- **Ruta recomendada** 1 → 2 → 3 (Qué es IRIS → Funciones → Instalar), marcada en el dock
  con números que pasan a ✓ cuando se visitan, y un pulso en el siguiente paso.
- **Pie «Qué hago después»** al final de cada ventana, con el siguiente paso.
- **Launcher por grupos**: Empieza aquí, Descubre, Profundiza y Comunidad.

Es HTML, CSS y JS estáticos, sin dependencias. El build de producción solo usa Node.

## Desarrollo

```bash
node pagina-iris/scripts/preview.mjs --source
```

Sirve el código fuente en `http://127.0.0.1:4174/`, sin caché y con las mismas
cabeceras de seguridad que producción: si algo rompe la CSP, lo verás en la consola.

## Producción

```bash
node pagina-iris/scripts/build.mjs      # genera pagina-iris/build/
node pagina-iris/scripts/preview.mjs    # prueba el build tal como lo servirá nginx
node pagina-iris/scripts/deploy.mjs     # lo publica en el VPS
```

La URL pública, el servidor y otras opciones van en `site.config.json`. La guía
completa del servidor (DNS, HTTPS, nginx, despliegue, cambio de dominio) está en
[deploy/README.md](deploy/README.md).

El build:

- pone un hash en el nombre de CSS, JS, imágenes y fuentes, así que se pueden guardar
  en caché un año sin que nadie vea versiones viejas;
- convierte en absolutas las URLs de SEO (canonical, Open Graph, JSON-LD);
- genera `robots.txt`, `sitemap.xml` y, si configuras un contacto, `security.txt`;
- comprueba que no falte ningún archivo y que nada rompa la CSP;
- precomprime todo en gzip y brotli;
- genera la configuración de nginx para tu dominio.

`tests/js/pagina_iris_site.test.mjs` ejecuta el build en CI.

## Mantener textos y metadatos

Al cambiar funciones, precio, versión o disponibilidad, coordinar `<title>`,
description, Open Graph, Twitter, JSON-LD y el texto visible. No confundir el
`1.0.0` del código con un instalador publicado, ni convertir una fecha propuesta
en un lanzamiento confirmado. El número de Hermes del paquete sale del pin;
no hace falta mostrarlo como argumento principal para el usuario.

«Local primero» debe conservar la explicación de proveedores remotos. La
percepción local de presencia no implica que una foto a petición se analice en
el PC. Evitar prometer un resultado enriquecido de FAQ: Google retiró esa
función en mayo de 2026 ([anuncio](https://developers.google.com/search/updates#may-2026)).

No prometer «todo incluido»: Python y el motor vienen en el paquete, pero WebView2
es un requisito que el instalador comprueba. Los modelos opcionales se descargan
después de elegirlos. Iris es gratuita; los proveedores externos de IA y voz
pueden tener cuotas y costes propios. Mientras no haya descarga pública,
«Seguir novedades» abre Comunidad y sus perfiles configurados.

Integrar propuestas como cambios sobre la fuente vigente. Un HTML antiguo puede
perder marcadores funcionales como `data-support="kofi"` y romper el build.
Una imagen en `brand/` no se publica: los recursos de runtime van en `assets/`
con sus estilos responsive, dimensiones y texto alternativo fiel. Regenerar la
imagen social si se cambia el lema que el alt dice que aparece en ella.

## Verificar la interfaz

```bash
node --test tests/js/pagina_iris_site.test.mjs tests/js/pagina_iris_interactions.test.mjs
```

Las regresiones de interfaz usan Playwright ya disponible en el equipo, un
servidor temporal en localhost y perfiles de navegador aislados. Comprueban
los cambios rápidos de tema, restauración de ventanas tras cambiar el tamaño,
la separación de contenido, pestañas y dock en pantallas de poca altura,
foco del teclado, título móvil, recuperación ante un script bloqueado o tardío, y
el escritorio (abrir por enlace sin desplazarlo, el estado de la barra superior y los
accesos bajo la tarjeta de IRIS).
No instalan dependencias ni publican la web.

Si Playwright no está en los módulos del proyecto, configura
`IRIS_SITE_PLAYWRIGHT` con la ruta de un paquete local existente. Para usar Edge
instalado, configura `IRIS_SITE_BROWSER_CHANNEL=msedge`; sin ese valor se usa
Chromium. Sin Playwright las pruebas de interfaz aparecen como omitidas, por lo
que no cuentan como una validación de la interfaz. Un runtime configurado que
falla sí hace fallar la prueba.

## Seguridad y privacidad

- CSP estricta, sin `unsafe-inline`: no puede haber `<script>` en línea, atributos
  `style="…"` ni `onclick="…"`. Los estilos van en el CSS y el código en `js/`.
- Trusted Types activado: no se puede usar `innerHTML`; crea nodos con
  `createElement`/`textContent`.
- Sin peticiones a terceros: las fuentes están en `fonts/` (licencia OFL) y no hay
  analítica ni cookies.

## Estructura

| Ruta | Contenido |
| --- | --- |
| `index.html` | Escritorio, dock, launcher, tutorial y el contenido de cada app (`<article data-app>`). |
| `css/iris-site.css` | Estilos basados en `face/css/styles.css` y los presets de `face/js/os_theme.js`. |
| `js/iris-site.js` | Gestor de ventanas, ruta, launcher, tutorial, temas, reloj y arranque. |
| `js/boot.js` | Tema guardado antes de pintar (archivo aparte para que la CSP no necesite `unsafe-inline`). |
| `assets/` | Fondos, perfiles, iconos de la app e imagen para compartir. |
| `fonts/` | Syne, Outfit y JetBrains Mono autoalojadas, con sus licencias. |
| `404.html`, `site.webmanifest`, `favicon.*`, `apple-touch-icon.png` | Página de error, manifiesto e iconos. |
| `brand/` | Plantillas de la imagen para compartir, del icono enmascarable y de las imágenes de redes. No se publica. |
| `redes/` | Fotos de perfil, portadas de YouTube y X, y la guía para abrir las cuentas ([redes/README.md](redes/README.md)). No se publica. |
| `scripts/` | `build`, `preview`, `deploy` y `render-images` (regenera las imágenes con Chrome y ffmpeg; `--redes` hace las de redes). |
| `deploy/` | Plantilla de nginx, cabeceras de seguridad y guía del servidor. |
| `site.config.json` | URL pública, indexación, contacto de seguridad, perfiles de redes (`social`), página de apoyo (`support.kofi`, que enlaza la tarjeta «Donar») y datos del servidor. |

## Añadir una app

Agrega un `<article>` dentro de `#apps` en `index.html`:

```html
<article class="app" data-app="mi-app" data-title="Mi app" data-subtitle="Descripción corta"
         data-icon="core" data-accent="cyan" data-size="m"
         data-group="discover" data-next="comunidad">
    <p class="kicker">IRIS OS / Mi app</p>
    <h2 class="app-title">Título</h2>
    <p class="lede">Contenido…</p>
</article>
```

| Atributo | Uso |
| --- | --- |
| `data-app` | Identificador y enlace directo (`index.html#mi-app`). |
| `data-title`, `data-subtitle` | Nombre de la ventana y descripción corta del launcher y el pie. |
| `data-icon` | Un símbolo del sprite (`#i-…`) definido al principio de `index.html`. |
| `data-accent` | `cyan`, `magenta` o `amber`. |
| `data-size` | `s`, `m` o `l`. |
| `data-group` | Grupo del launcher: `start`, `discover`, `deep` o `community`. |
| `data-next` | App que sugiere el pie «Qué hago después». |
| `data-route` | Paso de la ruta recomendada (`1`–`3`). Solo para las apps esenciales. |
| `data-tag` | Etiqueta opcional en la titlebar y el launcher, por ejemplo `Técnico`. |

Aparece sola en el launcher. Para ponerla también en el dock o en los accesos bajo la
tarjeta de IRIS, añade un botón con `data-open="mi-app"` dentro de `.dock` o
`.home-tiles`. Cualquier elemento con `data-open` abre su app.

No hace falta versionar a mano los enlaces de CSS o JS: el build les pone el hash.

## Datos que guarda el navegador

Todo es local al visitante (`localStorage`) y la página funciona igual sin él:

- `irisSite.theme`: el tema elegido.
- `irisSite.tourDone`: si ya vio el tutorial.
- `irisSite.visited`: apps abiertas, para marcar la ruta.

## Atajos

- `Ctrl` + `K` o `/`: abre el launcher.
- `Esc`: cierra el tutorial, el launcher o la ventana enfocada.
- Flechas izquierda/derecha: avanzan o retroceden en el tutorial.
- Doble clic en la titlebar: maximiza o restaura.
- Doble clic en el fondo del escritorio: minimiza todas las ventanas.

Sin JavaScript, el contenido de todas las apps se muestra como una página normal.
Si el controlador no carga, falla al inicializar o tarda más de cinco segundos,
el arranque también vuelve a esa vista de lectura. Una descarga que termine
después de la recuperación mantiene la vista estática hasta recargar la página.
