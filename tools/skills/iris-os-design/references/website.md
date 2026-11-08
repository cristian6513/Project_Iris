# Web pública de Iris

Modo para landing, página de producto, descargas, documentación o sitio público solicitado por el usuario. La web está prevista; este skill no acredita que ya exista un sitio, instalador público ni canal de releases.

## Identidad compartida, composición propia

Leer [sistema visual](visual-system.md). Usar la paleta Iris, Outfit para lectura, Syne para títulos y assets reales. Para un sitio nuevo sin preferencias adicionales, el preset Iris es la base. Si el usuario elige otra paleta, conservar el resto de la identidad y el contraste.

La web puede tener más espacio, títulos mayores y recorridos de lectura que las ventanas del producto. Mantener superficies oscuras, bordes sutiles y acentos controlados. Un marco de ventana puede servir para una captura o demostración, pero no obliga a convertir cada sección del sitio en un escritorio simulado.

No importar `face/css/styles.css` completo: contiene resets globales, elementos fijos, canvas, capas del avatar y `overflow: hidden` del escritorio. Crear estilos propios con un conjunto pequeño de tokens de marca. No importar el arranque Face, el bridge de Companion, service workers ni endpoints locales al sitio público para mostrar una maqueta.

## Componer según el objetivo de la página

- Producto/beta: propuesta clara, imagen o captura real y una acción disponible. Elegir secciones por el contenido solicitado, no por una plantilla de tarjetas repetidas.
- Descargas: plataforma y estado reales, versión y requisitos cuando se conozcan; enlaces comprobados. Linux futuro no aparece como descarga disponible.
- Documentación: lectura prioritaria, navegación y ejemplos accesibles. El decorado no compite con código, advertencias concretas o pasos.
- Galería/demostración: assets autorizados y contexto de lo que muestran; distinguir captura del producto de un concepto visual.

Si no existe una URL de descarga o formulario operativo, no inventarla. En un prototipo, identificar el estado de esos controles. No publicar el sitio ni conectar un formulario externo por crear su interfaz.

El avatar y la habitación son parte de Iris, pero no es obligatorio cargar Three.js/VRM en una landing. Una imagen o video propio puede comunicar el producto con menos coste. Una demostración 3D se incorpora cuando aporta al objetivo y tiene fallback para móvil, carga fallida y movimiento reducido.

## Assets y tipografía

El skill incluye el icono de perfil actual como asset de referencia reutilizable: `../assets/iris-profile-icon-v1.png`. Todos los temas usan ese retrato (el de Sakura se retiró el 2026-10-22); es la imagen de perfil/favicon existente, no un nuevo logotipo oficial.

Para capturas, fondos, avatar o fuentes adicionales, localizar archivos reales y revisar su permiso de distribución. Preferir fuentes locales con sus avisos de licencia cuando se prepare una web distribuible. Una URL de fuente usada actualmente por Face no prueba que la web tenga sus archivos o avisos incluidos. Conservar fallback legible si la fuente no carga.

La marca textual existente es IRIS / IRIS OS. Reutilizarla sin atribuir al proyecto certificaciones, partners o capacidades no comprobadas. No retirar avisos legales de componentes externos para adaptar los nombres visibles del producto.

## Contenido y validación

Contrastar las afirmaciones del sitio con el código, la documentación actual y los artefactos disponibles. `docs/iris_os_master.md` orienta sobre funciones; `docs/audits/iris_a08_scope_2026-09.md` explica lo pendiente del empaquetado; `docs/iris_automations_google_plan.md` describe funciones acordadas todavía no implementadas. La aprobación de un diseño no demuestra disponibilidad del producto.

Distinguir percepción local de cámara y procesamiento remoto cuando afecte una decisión del usuario. No afirmar que todo Iris funciona offline porque su interfaz pueda arrancar sin red.

Validar lectura en móvil y escritorio, scroll natural, títulos/landmarks, navegación por teclado, foco visible, contraste, zoom, enlaces reales y movimiento reducido. Los colores de marca son acentos: comprobar su contraste en texto, superficies translúcidas y botones. Evitar que animaciones continuas, blur excesivo o un fondo multimedia impidan leer o usar la página.

Si las herramientas lo permiten, revisar el resultado renderizado y errores de consola/red relevantes. Informar las limitaciones verificadas, sin afirmar una publicación o integración que solo se diseñó.
