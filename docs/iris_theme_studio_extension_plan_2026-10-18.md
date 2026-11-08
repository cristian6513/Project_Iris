# Theme Studio: tipografía, transiciones y temas transportables

Fecha: 2026-10-18. **Fuentes WOFF2: implementadas en fuente/exportación. Temas completos: diseño. Validación
de temas v1 y transiciones CSS/JSON: implementadas en fuente y exportación web,
pendientes del candidato Windows.** El usuario
propuso elegir/subir tipografías, importar animaciones y revisar el error de
importación/exportación señalado por Claude. Su reporte ya fue aportado:
referencias remotas de fondo/perfil sin validar y JSON que no incluye imágenes.
Se reprodujo y corrigió en el corte descrito en el
informe de importación segura (informe interno no distribuido).
No se cambió el tema personal; TS02 probó un WOFF2 incluido en el proyecto sobre un perfil desechable. No se
compilaron instaladores Windows.

**Vista previa solicitada después:** ya implementada en fuente y exportación
web como miniatura pasiva del Inicio guardado, con sus accesos/widgets y tema
actual (informe (informe interno no distribuido)). Sustituye
la actividad ficticia anterior. Esta parte no implementa los importadores ni
las otras ampliaciones; candidato y QA instalada siguen pendientes.

**Transiciones solicitadas después:** importador de CSS de fotogramas acotado,
paquetes `iris.transition/v1`, prueba antes de guardar, biblioteca en el snapshot
de Face y tres ejemplos originales. [Guía](guides/theme-transitions.md) e
informe (informe interno no distribuido). No habilita una
Store, un editor visual ni efectos de partículas. La biblioteca
de transiciones en Core sigue pendiente; se conserva el dueño actual del tema.
TS02 ya añade un almacén propio de fuentes WOFF2 por perfil en Core.

## Base comprobada

- `face/js/os_theme.js`: cuatro presets y, tras TS02, elecciones `textFont`/`titleFont`.
- `face/js/hermes_activity.js`: importación/exportación JSON v1, preferencias en
  `irisOsTheme.v1`, importación de imágenes a Core y publicación al host Desktop.
- `face/css/styles.css`: Outfit para lectura, Syne para títulos; JetBrains Mono
  y referencias heredadas de fuentes monoespaciadas en áreas técnicas.
- Hay **cinco transiciones**: fade, slide, zoom, glitch e iris, además de none.
  Se usan al entrar/cambiar escritorio y al abrir ventanas. No son clips del
  avatar ni fondos animados: estos conservan sus propios formatos y permisos.
- El padre Desktop (`wizard/ui/desktop_theme.js`, `core/desktop_theme.py`)
  conserva solo una proyección visual. No preserva hoy todos los campos del
  tema de Face; no debe asumirse que ampliar el JSON ya sincroniza las superficies.
- Imágenes propias: `/api/os/theme-image`, archivos del perfil en `ui-assets/`.
  Hoy hay un nombre activo por tipo; no es una biblioteca de recursos de temas.
- Fuentes propias (TS02): `/api/os/fonts`, bytes por perfil en el almacén protegido
  `apps/theme-fonts/fonts.sqlite3`. Solo WOFF2, hasta ocho de 2 MB; selección en el
  snapshot de Face y respaldo si el recurso no puede cargarse.

## Hallazgos reproducidos del importador/exportador

Evidencia: `artifacts/theme-studio-oct18-review/probe.mjs` y `findings.json`.
El script extrae y ejecuta los métodos reales del shell contra DOM y almacenamiento
simulados. No usa cuentas, red ni preferencias personales, ni prueba la descarga
nativa de Tauri. Estas reproducciones iniciales preceden al reporte exacto de
Claude. La tabla conserva el diagnóstico anterior; TS01 ya corrige estos casos,
salvo compartir imágenes completas, que pertenece al futuro TS03.

| Caso | Comportamiento antes de TS01 | Corrección planteada |
| --- | --- | --- |
| Importar `{}` | Sustituye el tema existente por los valores de fábrica. | Rechazar archivos sin contenido de tema antes de modificar nada. |
| Paquete `version: 999` | Aplica su bloque `theme`. | Validar formato/versión; mantener compatibilidad explícita con v1. |
| Color inválido, número negativo o `reducedMotion: "false"` | Copia esos valores sin validarlos. La cadena no equivale al booleano `false`. | Validación común de tipos, rangos, enumeraciones y recursos. |
| Fallo al guardar la preferencia | Aplica el tema y no informa que `_applyOsTheme(true)` devolvió `false`. | Resultado de guardado visible y operación que no anuncie persistencia inexistente. |
| Importar ceros numéricos | Algunas propiedades los sustituyen por defaults mediante `valor || default`, por ejemplo blur 0 → 20 px. | Usar ausencia/validez, no falsedad, para decidir el fallback. |
| Exportar un fondo personalizado | Exporta `/api/os/theme-image/background?...`, sin bytes de imagen. Otro perfil no posee ese recurso o tiene otro fondo bajo la misma ruta. | Paquete autónomo y referencias verificables; no prometer transportabilidad del JSON actual. |

Los fallos de datos/guardado se verificaron por ejecución del código. La
limitación de compartir imágenes se verificó inspeccionando el Blob exportado
y el almacenamiento por perfil de Core. No se reprodujo un fallo de descarga en
un instalador Windows. TS01 comprobó después la descarga real del JSON en el
navegador aislado y el bloqueo de referencias de imagen con un servidor trampa
local, sin usar un tema personal.

## Experiencia propuesta

Mantener Theme Studio y el sistema de temas actual, con tres grupos claros:

1. **Tipografía:** texto general y títulos; una opción avanzada separada para
   código/registros. Elegir fuentes incluidas o **Añadir fuente**, previsualizar
   una frase y aplicar. Restaurar esa elección tipográfica no restablece Inicio,
   cuentas, colores o distribución del escritorio.
2. **Movimiento:** elegir una transición incluida o añadir un preset compatible,
   previsualizar y ajustar duración/intensidad. El propio archivo no elige dónde
   ejecutarse ni puede ocultar controles fuera del recorrido de la transición.
3. **Compartir tema:** exportar una configuración ligera o un tema completo con
   sus recursos. Antes de importar, mostrar nombre, recursos incluidos y cambios;
   un error/cancelación conserva el tema que ya estaba aplicado.

La elección de tipografía debe propagarse a apps, widgets, menús y diálogos de
Iris OS. Revisar también estilos que fijan Outfit/Syne directamente y los que
usan `--font-body` sin una definición central. No alterar por defecto el Command
Center/chat, Wizard o Companion fuera del alcance visual acordado; si se amplía
a esas superficies, sus adaptadores deben consumir el contrato de forma expresa.

## Fuentes propias

- Primera entrega: archivos de fuente web/local compatibles (priorizar WOFF2;
  probar TTF/OTF antes de anunciarlos), con límite de tamaño y número de recursos.
  No instalar fuentes en Windows ni explorar las del equipo.
- Core conserva recursos por perfil con IDs/hashes internos y nombres seguros;
  una petición no escoge una ruta de destino. Comprobar límites, formato y
  contención/enlaces antes de usar un almacén nuevo. No copiar ciegamente el
  escritor histórico de imágenes a un nuevo tipo de archivo.
- El navegador carga el archivo por datos/recurso local con un nombre interno
  generado; no interpolar el nombre externo en CSS. Comprobar decodificación
  antes de aplicar y mantener fallback si el archivo falta o falla.
- Liberar `FontFace`/URLs temporales al reemplazar. Una carga lenta anterior no
  puede pisar una selección nueva. No repetir la carga por cada app/widget.
- Un tipo sin todas las variantes usa fallback de forma definida; no anunciar
  soporte de pesos/acentos/glyphs solo por detectar la extensión.
- Exportar una fuente con un tema la copia al archivo compartible. Mostrarlo
  explícitamente y conservar sus metadatos de origen/licencia si se aportan;
  la importación personal no demuestra derechos para redistribuirla.

## Transiciones aportadas por la comunidad

La extensión implementada es un **preset de datos**, con entrada opcional de
CSS limitado a fotogramas y reglas de tiempo. El lector no monta el CSS: lo
convierte a fotogramas validados de opacidad y transformación (desplazamiento,
escala, giro), duración y curva. Iris elige el elemento y produce la animación
con Web Animations. El contrato y sus límites están en la guía; no se ejecutan
scripts, CSS libre, imports, recursos o ciclos ilimitados.

Para el usuario: **Añadir transición** importa CSS compatible o ese preset JSON,
con revisión y prueba antes de **Guardar y usar**. Un editor
sencillo puede producirlo más adelante sin que la persona escriba JSON. GIF,
vídeo y animaciones corporales VRMA no son sustitutos de ese formato; añadirlos
como fondo/avatar sería una opción aparte, sin concederla desde un tema.

Requisitos de ejecución:

- una transición nueva cancela/libera la anterior; selección vigente gana;
- controles y navegación no dependen de que se dispare `animationend`;
- al terminar/cancelar desaparece todo estado visual temporal;
- respetar `reducedMotion` del tema y `prefers-reduced-motion` del sistema;
- previsualización solo por acción visible; importar un tema no activa efectos
  persistentes, audio, sensores ni trabajo de IA;
- mantener las cinco transiciones y `none` como fallback y por compatibilidad.

## Un tema completo transportable

Un formato nuevo debe incluir manifiesto con versión, preferencias visuales y
recursos opcionales identificados por hash (imágenes, fuentes y presets de
transición). No contiene cuentas, tokens, conversaciones, rutas absolutas,
permisos, layout de Inicio ni código ejecutable. Una futura Store puede catalogar
estos temas sin convertirlos en apps/skills o conectores.

Definir antes de implementar: formato contenedor y presupuesto total, tamaño
descomprimido, número de entradas y recursos, duplicados, integridad y versiones.
Si se usa ZIP, no extraer nombres externos directamente al perfil: rechazar
traversal, enlaces y colisiones, procesar entradas acotadas y escribir por IDs.

Importación por fases: leer/validar todo → previsualizar → preparar recursos →
guardar selección y recursos coherentes → aplicar. Si falla, conservar el tema
anterior y no anunciar éxito. Los temas JSON v1 siguen legibles, con aviso si
solo contienen referencias a imágenes que faltan; no apuntarlas silenciosamente
a un recurso diferente del perfil receptor ni descargar URLs externas.

La persistencia canónica debe ser visual y por perfil; `localStorage` puede ser
caché/migración, no el único dueño de fuentes/imágenes compartibles. Desktop,
Face y otros consumidores conservan adaptadores explícitos para sus subconjuntos.

## Orden recomendado

| Tramo propuesto | Resultado comprobable |
| --- | --- |
| TS01 | Implementado en fuente/exportación: reporte de Claude reproducido, JSON v1/legado validado, ceros conservados y persistencia antes de aplicar. Importación/exportación omiten referencias de imagen con aviso; las imágenes propias se suben localmente. Candidato/QA Windows pendientes. |
| TS02 | Implementado en fuente/exportación: selección de texto/títulos, WOFF2 hasta 2 MB, biblioteca de ocho por perfil, preview, respaldo y cargas tardías descartadas. Candidato/QA Windows pendientes. |
| TS03 | Paquete completo que viaja a otro perfil sin depender de rutas/recursos del emisor; compatibilidad v1 explícita. |
| TS04 | Implementado en fuente/exportación: importación CSS acotada/JSON, biblioteca, prueba, exportación, tres ejemplos y guía/CLI. Candidato/QA WebView2 pendientes. |

Estos son tramos de trabajo, no nuevas Fxx aceptadas. TS04 tiene la entrega
identificada arriba; TS01 también tiene su informe. TS02 está implementado en fuente/exportación para WOFF2, con biblioteca por perfil
en Core, previsualización y respaldo (informe (informe interno no distribuido)).
TS03 sigue pendiente: no hay paquete de tema con los bytes de las imágenes/fuentes.
El empaquetado Windows permanece conjunto según el acuerdo previo.

Pruebas previstas: importación malformada/futura/vacía/sobredimensionada, booleanos,
rangos y ceros, fallo de escritura, respuesta tardía, recurso ausente/ajeno,
portabilidad entre dos perfiles y reinicio, byte identity del paquete, límites
de recursos/ZIP, solo texto seguro, ninguna red implícita, teclado/zoom/móvil,
temas alternativos, cancelación y cleanup de animaciones/fuentes. Mantener pruebas
separadas de navegador aislado y QA WebView2/candidato real.

Referencias técnicas primarias: [CSS Font Loading](https://www.w3.org/TR/css-font-loading-3/)
permite fuentes desde datos binarios; [Web Animations](https://www.w3.org/TR/web-animations-1/)
aporta efectos y control de cancelación. Son capacidades de plataforma, no una
confirmación de que Iris ya tenga estos importadores o de su QA en WebView2.
