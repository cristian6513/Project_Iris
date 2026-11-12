---
name: iris-os-design
description: "Diseña e implementa interfaces con la identidad visual de Iris: apps y ventanas de Iris OS, Face, Desktop/Companion, Wizard y la futura web pública de Iris. Úsalo al crear, ampliar o revisar esas interfaces y sus componentes; no para diseño de marcas ajenas ni tareas de backend sin interfaz."
---

# Iris OS Design

Construye interfaces que parezcan parte de Iris y aprovechen su sistema visual. El usuario quiere conservar esa identidad al añadir apps y al crear su futura página web. Este skill guía diseño e integración; no convierte una petición visual en autorización para implementar otras funciones del producto.

## Elegir la referencia pertinente

- Lee [sistema visual](references/visual-system.md) para colores, temas, tipografía, materiales y assets verificados.
- Para interfaces dentro del producto, lee [apps y superficies de Iris OS](references/iris-os.md). Incluye ventanas, Theme Studio, Face, Desktop/Companion y Wizard.
- Para la web pública, landing, descargas o páginas de documentación de Iris, lee [web de Iris](references/website.md). Comparte identidad con el producto, adaptando composición y densidad al navegador.

Carga solo el modo necesario. Las rutas del código indicadas en las referencias son relativas al checkout de Iris, no a la carpeta instalada de este skill. Localiza ese checkout por sus archivos; no dependas de la ruta del ordenador original. Si el proyecto web no incluye el checkout, usa la referencia visual y los assets suministrados aquí como base, sin fingir acceso a APIs o componentes ausentes.

## Identidad y prioridad de fuentes

El lenguaje actual combina superficies oscuras, profundidad translúcida, bordes finos, acentos cian/magenta y una interfaz de sistema operativo con paneles compactos. El acento y la intensidad son personalizables: conservar identidad no significa fijar todos los componentes a un único preset.

El código vigente es la autoridad sobre tokens y capacidades. `face/js/os_theme.js` define el tema; los componentes deben consumir sus variables. La documentación explica intención y puede conservar nombres antiguos. La referencia visual distingue valores existentes de recomendaciones para trabajo nuevo.

Reutiliza componentes, tipografías y recursos de Iris antes de inventar otros. Si también se usa un skill genérico de frontend, aplica sus técnicas dentro de la identidad elegida por el usuario; sus sugerencias de variar estética o fuentes no sustituyen el diseño existente de Iris. Respeta cualquier cambio concreto de diseño que el usuario pida.

## Trabajo sobre una interfaz

1. Identifica superficie, función y estados reales; lee el componente más cercano y su tema antes de modificarlo.
2. Diseña con los tokens y patrones de esa superficie. Encapsula estilos de una app nueva; no cambies globalmente el escritorio para resolver un panel.
3. Implementa el flujo completo dentro del alcance: carga, vacío, contenido, error recuperable y disponibilidad de acciones. Una tarjeta bonita no sustituye la conexión o estado funcional que se pidió.
4. Comprueba tema alternativo, tamaño compacto/móvil, teclado, foco y movimiento reducido. Verifica visualmente lo que las pruebas de contratos no muestran, cuando haya herramientas disponibles.

Si la tarea es solo un boceto, hazlo identificable como tal y no lo conectes silenciosamente a cuentas o servicios. Las vistas no anuncian éxito, conexión, progreso ni resultados que el backend no haya confirmado.

## Copy, estados y comportamiento

Escribe ayudas y acciones en español. Los nombres de aplicaciones y etiquetas breves existentes pueden permanecer en inglés; conserva identificadores técnicos y contratos. Usa estados comprensibles y controles que expliquen su efecto, sin incorporar JSON interno, comandos o detalles de implementación a la experiencia del usuario.

Prioriza una acción principal por contexto y presenta las acciones secundarias con menos énfasis. Conserva pausa, cancelación, reintento y preferencias donde el flujo las necesite. El contenido del resultado y el estado de la ejecución se presentan por separado.

La interfaz refleja los permisos existentes: conectar Google no activa por sí mismo revisión IA; abrir una ventana no activa cámara, micrófono o trabajo en segundo plano. Las funciones futuras se presentan como futuras, según el estado actual del proyecto.

## Validación proporcional y mantenimiento

Para cambios de estilo reversibles, verifica la vista y las superficies afectadas sin crear tests que repitan reglas CSS. Para cambios de registro, navegación, eventos o contratos, ejecuta pruebas relacionadas y añade regresiones de comportamiento cuando sean necesarias. No declares QA física de dispositivos o de un instalador sin haberla realizado.

Cuando cambie una fuente canónica o aparezca un patrón reutilizable real, actualiza la referencia correspondiente con su archivo de origen. No conviertas decisiones puntuales de una pantalla en restricciones universales.
