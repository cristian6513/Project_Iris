# Crear y compartir transiciones de Iris

Revisión documental: 2026-10-26. Incluido en QA16; su presencia no sustituye
la QA instalada del [candidato exacto](../estado-actual.md).
Las transiciones afectan la entrada al escritorio y la apertura/restauración de
ventanas de Iris OS. Cerrar, minimizar, el avatar y los fondos tienen su propio
comportamiento: este formato no los sustituye.

## Añadir una transición

1. Abre **Theme Studio → Transiciones → Añadir transición**.
2. Selecciona un archivo `.css` individual o un paquete `.json` de Iris. También
   puedes pegar su contenido y pulsar **Revisar contenido**.
3. Si contiene varias animaciones, elige una. Revisa nombre, autor, licencia y
   duración. Los comentarios del CSS se conservan como atribución; Iris no
   adivina su autor o licencia. **Cargar ejemplo** completa esos datos para el
   ejemplo original incluido con Iris.
4. Pulsa **Probar antes de guardar**. Solo se anima una ventana de muestra; tu
   selección sigue igual. Con movimiento reducido, la muestra permanece quieta.
5. **Guardar y usar** añade una copia con identificador interno nuevo y la
   selecciona. **Descartar** conserva el tema anterior.

La lista incluye las cinco transiciones existentes y **Sin animación**. Una
transición añadida puede probarse, exportarse o quitarse con confirmación. Al
quitar la seleccionada se usa **Sin animación**. Las incluidas no se borran.
Restaurar los colores del tema o importar un tema antiguo sin biblioteca no
elimina las transiciones que añadiste.

Se guardan fotogramas y metadatos en el snapshot existente `irisOsTheme.v1` de
Face, junto con la selección. Se recuperan en el mismo almacenamiento del
navegador/WebView y se incluyen en la exportación JSON del tema. **No se ha
implementado aún una biblioteca canónica de recursos por perfil en Core** ni
la migración de todos los temas entre superficies. Para compartir solo el
movimiento, **Exportar transición** produce un archivo autónomo con sus datos,
sin imágenes locales, cuentas, permisos o dependencias externas.

La [exportación del tema](theme-studio.md) también incluye esta biblioteca
validada, pero omite las referencias al fondo y a la foto propios. El JSON no
es un paquete de imágenes ni descarga recursos del autor al importarse.

## Obtener animaciones existentes

[Animista](https://animista.net/) permite configurar y exportar CSS de
animaciones individuales. [Animate.css](https://animate.style/) publica
fotogramas reutilizables. Iris admite el subconjunto indicado abajo: una
animación de esas bibliotecas puede necesitar adaptación. No importa una hoja
completa de estilos, un proyecto JavaScript ni un paquete de KDE/GNOME.

Conserva la atribución y licencia del recurso que compartas. Los ejemplos de
este repositorio son originales y llevan el aviso MIT completo dentro del CSS
y del JSON. No se descargan bibliotecas ni recursos automáticamente al importar.

## Ejemplos completos

| Ejemplo | CSS editable | Paquete listo para importar |
| --- | --- | --- |
| Entrada suave | [CSS](../../examples/transitions/entrada-suave.css) | [JSON](../../examples/transitions/entrada-suave.json) |
| Zoom sereno | [CSS](../../examples/transitions/zoom-sereno.css) | [JSON](../../examples/transitions/zoom-sereno.json) |
| Giro sutil | [CSS](../../examples/transitions/giro-sutil.css) | [JSON](../../examples/transitions/giro-sutil.json) |

Una entrada mínima:

```css
.mi-entrada { animation: mi-entrada 420ms ease-out both; }
@keyframes mi-entrada {
  from { opacity: 0; transform: translateY(20px) scale(0.96); }
  to   { opacity: 1; transform: translateY(0) scale(1); }
}
```

## Contrato CSS admitido

- Archivo UTF-8 de hasta **64 KiB**, hasta ocho animaciones distintas. Se
  aceptan `@keyframes` y copias `@-webkit-keyframes` equivalentes. Los bloques de
  clase solo declaran nombre/tiempos de animación; no se aplican como estilos.
- Entre **2 y 32 fotogramas** por animación; `from`, `to`, porcentajes y
  porcentajes agrupados. Incluye 0% y 100%. Se ordenan por posición; bloques
  repetidos en una posición se combinan siguiendo su orden.
- Propiedades: `opacity`, `transform`, `transform-origin` y
  `animation-timing-function`. Se reconocen sus formas `-webkit-` equivalentes.
- Transformaciones: `translate`, `translateX/Y`, `scale`, `scaleX/Y`, `rotate`
  y `rotateZ`. `translate3d` admite Z=0 y `scale3d` Z=1 para CSS habitual que
  expresa un movimiento plano. Argumentos múltiples separados por comas.
- Desplazamientos: hasta ±2000 px o ±120%; cero sin unidad también es válido.
  Escala: de −2 a 2. Giro: hasta ±360 grados. Máximo ocho funciones por fotograma.
- Origen: posiciones `left/right/top/bottom/center` y porcentajes 0–100%.
- Duración: **80–2000 ms**; una ejecución, sin retraso, dirección normal.
  Curvas `linear`, `ease`, `ease-in`, `ease-out`, `ease-in-out` o
  `cubic-bezier` con X en 0–1 e Y en −2–2. Duración predeterminada sin regla:
  420 ms. El relleno CSS se lee, pero Iris siempre libera el efecto al terminar.
- Se rechazan propiedades no soportadas (`filter`, sombras, `clip-path`, etc.),
  variables/expresiones CSS, imports, recursos, selectores globales, bucles y
  código. Un archivo parcialmente compatible se rechaza con el motivo; no se
  eliminan declaraciones silenciosamente para hacerlo parecer compatible.

Los fotogramas importados usan las propiedades de
[Web Animations](https://www.w3.org/TR/web-animations-1/). El paquete añade
metadatos de Iris y límites; no pretende ser un formato universal de escritorio.
Fuego, partículas y desintegración requieren otro motor y no están incluidos.

## Paquete y validación para contribuir

[Esquema JSON v1](../schemas/iris-transition-v1.schema.json). El validador de
ejecución añade las comprobaciones semánticas que JSON Schema no expresa:
orden/extremos de los offsets, transformaciones y curvas permitidas. El lector
canónico está en [os_transition_format.js](../../face/js/os_transition_format.js).

Desde la raíz del proyecto, con Node:

```text
node scripts/transition_tool.mjs check examples/transitions/entrada-suave.css
node scripts/transition_tool.mjs convert mi-entrada.css mi-entrada.json --name "Mi entrada" --author "Tu nombre" --license "MIT"
```

Si el CSS tiene varias animaciones, añade `--effect nombre-del-keyframes`.
La conversión **no sobrescribe** un archivo existente. Comprueba después el
JSON con `check`, impórtalo y pruébalo en Theme Studio. La herramienta utiliza
exactamente el mismo lector que la interfaz; convertir no ejecuta el CSS.

Al proponer un ejemplo mediante una contribución al repositorio, incluye el
CSS o JSON, atribución completa, una captura y cómo se comporta con teclado,
tema alternativo y movimiento reducido. No requiere una app, función Python o
skill de Hermes. Una futura Store podrá distribuir estos archivos; hoy no hay
catálogo remoto ni instalación automática.

Iris limita la biblioteca a **20 transiciones**, valida también los datos
restaurados y respeta tanto el ajuste del tema como la reducción de movimiento
del sistema. Cada animación libera sus estilos al finalizar/cancelarse. Cambiar
de tema o navegación cancela los efectos anteriores; el cierre y los controles
de las ventanas nunca esperan a que termine una animación.
