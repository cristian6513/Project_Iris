# Sistema visual compartido de Iris

Base inspeccionada: 2026-09-29. Los valores siguientes describen el código actual; releer las fuentes al modificar el sistema de temas. No son un rediseño ni una certificación de accesibilidad.

## Fuentes de verdad

| Tema | Fuente del proyecto |
| --- | --- |
| Defaults e IDs de presets | `face/js/os_theme.js`: `OS_THEME_DEFAULT`, `OS_THEME_PRESETS` |
| Aplicación al escritorio | `face/js/hermes_activity.js`: `_applyOsTheme()` |
| Estilos/componentes y overrides | `face/css/styles.css`, CSS específico de cada app |
| Adaptación a ventanas estrechas/móvil | `face/css/iris_os_responsive.css` |
| Nombres visibles y puente Desktop | `wizard/ui/desktop_theme.js` |
| Intención visual/editorial | `docs/iris_os_master.md`, sección Theme Studio |

`styles.css` conserva capas antiguas y overrides. El oro `--iris-gold: #D4A017` del inicio pertenece al estilo heredado de Face; no usarlo como paleta canónica del escritorio actual. Resolver la cascada de la superficie que se cambia, incluyendo CSS modular y responsive. La documentación usa rangos y nombres visibles; el contrato JS determina los valores e IDs efectivos.

## Paleta y presets existentes

| ID técnico | Nombre visible | Primario | Secundario | Ready | Fondo |
| --- | --- | --- | --- | --- | --- |
| `iris` | Cyber | `#73f7ff` | `#ff74d4` | `#5cffad` | `city` |
| `sunrise` | Classic | `#9ed8ff` | `#73f7ff` | `#9de7ff` | `classic` |
| `verdant` | Hacker | `#5cffad` | `#0eff8f` | `#5cffad` | `hacker` |
| `rose` | Sakura | `#ff8ed7` | `#c78bff` | `#ff8ed7` | `sakura` |

Los nombres visibles no sustituyen los IDs guardados. El usuario puede personalizar colores, fondo y perfil.

| ID | Glow | Borde | Opacidad de tarjeta | Blur | Neón | Fondo oscurecido | Transición |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `iris` | 80 | 60 | 88 | 24px | 80 | 80 | `none` |
| `sunrise` | 40 | 35 | 92 | 16px | 35 | 70 | `slide` |
| `verdant` | 70 | 45 | 90 | 18px | 70 | 80 | `glitch` |
| `rose` | 65 | 50 | 88 | 20px | 60 | 70 | `iris` |

Las intensidades se guardan como valores de 0–100 y el aplicador convierte las correspondientes variables CSS a fracciones. El blur se aplica en píxeles. Defaults adicionales: `liveIntensity=70`, `compactMode=false`, `animatedGlow=true`, `reducedMotion=false`, `transparentCards=true`. Ninguno obliga a activar efectos en una pantalla nueva: consumir la preferencia vigente.

## Variables que ya existen

| Uso | Variables |
| --- | --- |
| Acentos | `--iris-os-accent`, `--iris-os-accent-2`, `--workbench-accent` |
| Listo/disponible | `--iris-ready-color` |
| Imágenes | `--iris-os-bg-image`, `--iris-profile-bg-image` |
| Material | `--iris-card-opacity`, `--iris-blur-intensity` |
| Efectos | `--iris-neon-intensity`, `--iris-accent-glow`, `--iris-border-glow`, `--iris-live-intensity`, `--iris-background-dim` |

Flags sobre el root: `data-compact-mode`, `data-animated-glow`, `data-reduced-motion`, `data-transparent-cards` y `data-desktop-transition`. Reutilizar el aplicador, no escribir otro almacén de preferencias por app. El snapshot existente es `irisOsTheme.v1`; no renombrarlo por una mejora estética.

Neutrales base definidos en CSS:

| Token | Valor base |
| --- | --- |
| `--os-panel` | `rgba(4, 8, 18, 0.42)` |
| `--os-panel-strong` | `rgba(5, 9, 20, 0.62)` |
| `--os-line` | `rgba(141, 239, 255, 0.10)` |
| `--os-line-soft` | `rgba(255, 255, 255, 0.045)` |
| `--os-text` | `rgba(242, 248, 255, 0.88)` |
| `--os-muted` | `rgba(222, 237, 246, 0.50)` |

Estos valores translucen el fondo y pueden recibir overrides. Un texto importante no debe quedar ilegible por copiar un muted con opacidad baja: comprobar contraste efectivo, especialmente con fondos personalizados y tarjetas transparentes.

## Tipografía, iconos y assets

- `--font-main`: `'Outfit', system-ui, sans-serif`, para lectura y controles.
- `--font-display`: `'Syne', system-ui, sans-serif`, para títulos/identidad.
- Face carga Syne 600/700/800 y Outfit 300/400/500/600/700 en `face/index.html`; Companion dispone de `@font-face` local en `face/companion/companion.css`. Desktop incluye Syne local.
- Algunos componentes usan `--font-body`/`--font-mono`, pero no se encontró una declaración central garantizada. No tratarlos como aliases canónicos: usar tokens definidos o fallback explícito, y leer el componente concreto.
- El proyecto no tiene una escala central completa de tamaños/espaciados. Reutilizar el módulo más cercano; no presentar una escala nueva como si ya existiera.

`osIcon()` en `face/js/hermes_activity.js` define los SVG del shell: 24×24, `fill="none"`, `stroke="currentColor"`, grosor 1.7 y extremos/uniones redondos. Reutilizar catálogo y estilo; añadir solo el icono que falte. SVG decorativo puede llevar `aria-hidden`; el botón necesita nombre accesible. No sustituir etiquetas útiles por emoji o iconos sin explicación.

Assets fuente del producto:

- `face/public/assets/os/iris-profile-icon-v1.png`: retrato de Iris oscuro con acentos cian/violeta; perfil/favicons actuales y el mismo retrato en todos los temas (la variante Sakura se retiró el 2026-10-22).
- Fondos: `iris-os-bg-v1.png`, `theme-sakura.png`, `theme-hacker.png`, `theme-classic.png` en esa misma carpeta.

El skill incluye copias sin modificación de los dos iconos de perfil en `../assets/`. Usar esos originales cuando sean pertinentes; no inventar un logotipo nuevo por defecto. Son imágenes existentes, no capturas que prueben una función ni una declaración de derechos de otros assets. Revalidar archivos/licencias para distribución y renovar las copias si cambia la identidad original.

## Materiales, geometría y movimiento

La ventana `.os-window` actual ilustra el lenguaje: radio 18px, titlebar 44px, borde de 1px, fondo oscuro en capas, blur configurable y sombra amplia (`0 28px 76px rgba(0,0,0,.46)`). Maximizada/acoplada usa radio 16px. Los controles tienen escala propia. Son ejemplos del shell, no valores obligatorios para tarjetas, formularios o una web.

Hay variantes enfocada, minimizada, maximizada y acoplada. Foco de teclado visible y jerarquía de capas son parte de la interacción. No aumentar glow, blur o z-index de un componente para compensar un problema estructural.

Transiciones existentes: `none`, `fade`, `slide`, `zoom`, `glitch`, `iris`. El preset Iris usa `none`. No convertir glitch, animación de entrada o glow continuo en requisito de marca. Respetar preferencia de movimiento reducido y sistema cuando la superficie lo soporte; comprobar la ruta nueva aunque los estilos antiguos no la cubran.

## Lenguaje editorial

Ayudas, instrucciones y acciones en español. Los títulos existentes de apps y estados breves pueden permanecer en inglés. Conservar términos técnicos reales (Hermes, MCP, Skills, Gateway, VRM, WebRTC) y sus identificadores; no convertir un cambio visible de marca en un reemplazo global de protocolos.

Colores e iconos refuerzan el estado, pero no lo explican solos. Combinar etiqueta y contexto: pendiente, en curso, completado, error o sin actualizar son estados distintos. Para apps, continuar con [iris-os.md](iris-os.md); para la web, con [website.md](website.md).
