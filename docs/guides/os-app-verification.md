# Verificación de aplicaciones de IRIS OS

> Disponibilidad por versión/candidato: [Estado actual](../estado-actual.md).
> Los resultados fechados de esta guía corresponden a aquella comprobación,
> no a una validación nueva de todos los ejecutables.

[← Funcionalidades](features.md)

## Revisión del 8 de septiembre de 2026

Pasada focal sobre el ciclo de vida de ventanas y las consultas de Mission
Board, Async Agents, Routines, Gateway Doctor y la bandeja de notificaciones.
No cambia el diseño ni activa cámara, micrófono o trabajos de Hermes.

### Fallos corregidos

- **Prioridad media — selección cruzada:** cambiar de agente o rutina mientras
  se estaba leyendo el detalle descartaba la nueva consulta y podía mostrar
  datos del elemento anterior. Ahora se cancela la lectura anterior y se
  comprueba la identidad de la petición antes de aceptar datos o errores.
- **Prioridad media — Mission Board desactualizado:** el refresco periódico
  solo reconocía la pestaña, no la aplicación en ventana. Se sincroniza al
  abrir, minimizar, restaurar, cerrar y cambiar de vista.
- **Prioridad media — cargas incompletas:** Routines y Gateway Doctor también
  reconocen sus ventanas para cargar historial y logs, incluso si la lista
  principal o el estado ya estaban en caché.
- **Prioridad baja — consultas innecesarias:** Mission Board y Async Agents
  detienen su sondeo con el OS cerrado, la página oculta, la ventana minimizada
  o el escritorio oculto por otra vista. Restaurar ventanas minimizadas desde
  la configuración guardada no carga sus aplicaciones hasta mostrarlas.

El historial conserva el identificador de la rutina a la que pertenece su
caché. Cambiar a una entrega histórica o desaparecer la rutina seleccionada
invalida la lectura anterior. Las consultas simultáneas del mismo detalle y
de logs se deduplican. Al volver a una página visible, Notification Center
puede recuperar sus cambios pendientes sin un nuevo temporizador periódico.

### Evidencia reproducible

- `node --test tests/js/*.test.mjs`: **162 aprobadas**, incluidas 19 regresiones
  nuevas de ciclo de vida, caché, errores tardíos y respuestas fuera de orden.
- `uv run --no-sync pytest -q`: **778 aprobadas, 3 omitidas**.
- `npm --prefix companion run build`: correcto; conserva el aviso conocido
  por el tamaño del módulo de Three.js, no un error de compilación.
- `node scripts/verify_os_apps.mjs`: QA opcional con Playwright y Three.js
  locales. Admite `--playwright-module <ruta-al-modulo>` si Playwright está
  disponible fuera del proyecto. Usa Edge headless en Windows.

La prueba de navegador pasó diez transiciones de ventana con la clase y el
CSS reales, además de las cargas de logs e historial. Todos los endpoints se
simulan en un servidor efímero de loopback: **cero escrituras al backend y cero
errores JavaScript de página**. No usa el perfil del navegador del usuario.

### Límites

Esta pasada no certifica todas las aplicaciones ni es un benchmark global de
FPS/CPU. No ejecuta ni modifica rutinas o agentes reales, no instala MCP/Skills
y no valida cuentas, dispositivos físicos o datos de producción. Esas pruebas
de extremo a extremo siguen siendo parte de la validación de release.
