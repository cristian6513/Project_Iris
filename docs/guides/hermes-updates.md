# Actualizar Hermes desde IRIS

Revisión documental: 2026-10-16. Hay dos flujos distintos:

- **Paquete Windows de Iris:** Hermes `0.21.6` fijado, con Python privado
  `3.14.8`. Se actualiza junto con Iris desde su canal. No se ejecuta
  `hermes update` ni se modifica su entorno incluido por separado.
- **Hermes externo usado en desarrollo:** puede usar su actualizador oficial,
  con revisión de compatibilidad y autorización para esa operación.

La sección de septiembre conserva evidencia histórica, no identifica el motor
del candidato actual. Consulta [Estado actual](../estado-actual.md).

## Flujo de Hermes externo en desarrollo

IRIS OS → Ajustes → Actualizaciones del sistema → Buscar actualizaciones → Actualizar.

La comprobación de Hermes usa `hermes update --check`. La instalación usa
`hermes update --yes` dentro del entorno administrado de Hermes: el actualizador
oficial conserva su política de cambios locales, sincroniza dependencias y emite
un recibo. IRIS también comprueba ese recibo para distinguir éxito, fallo parcial
y actualización rechazada. No se usa un simple `git pull` para actualizar Hermes.

Una actualización se ejecuta en segundo plano en IRIS Core. Varias ventanas
comparten la misma ejecución. Recargar Face o volver a abrir Ajustes recupera su
estado y resultado mientras siga vivo ese proceso de Core. Esto no garantiza
recuperación tras cerrar Core o apagar el equipo. Al terminar, reinicia IRIS Core
para cargar las integraciones con el código nuevo.

En Windows, cierra las consolas de Hermes con `/exit` antes de actualizar. Si un
`hermes.exe` está abierto, el actualizador oficial rechaza reemplazarlo; IRIS
muestra el motivo. No fuerza el cierre de conversaciones. Si IRIS está respondiendo,
espera a que termine antes de pulsar Actualizar.

## Verificación del 21 de septiembre de 2026

**Actualización completada desde Iris y verificada: Hermes 0.21.0 → 0.21.4.**

El botón real terminó con recibo nativo `success`. Las integraciones de Iris
probadas funcionan. Se detectó además un error de la API nativa `/v1/skills`,
que el Skills Hub de Iris no utiliza; se detalla como limitación más abajo.

- Antes: **0.21.0**, fecha `2026.8.31`, commit `f709bd88b6cc62b23f40e878c1d5960604302ee2`.
- Después: **0.21.4**, fecha `2026.9.21`, commit `fca3221df48d4ae1e4db9e8a1a041de21ea4d9cf`.
- El repositorio final de Hermes está limpio; 137 dependencias pasan `uv pip check`.
- La política de compatibilidad de Iris devuelve `ready`.
- Primer intento rechazado: `Another hermes.exe is running`, PID `14912`.
  El usuario confirmó que no abrió Hermes. El proceso estaba huérfano, sin tareas,
  conexiones externas ni conversaciones de hoy. Windows exigió terminar ese árbol
  de procesos para liberar el ejecutable; se conservó el recibo del rechazo.
- El primer rechazo conservó el diff original. Durante la actualización, Windows
  perdió un archivo de contribuyentes cuyo nombre cambió solo de mayúsculas;
  se restauró verificando que su contenido `momomojo` coincide tanto con el cambio
  previo como con la revisión nueva.
- El actualizador antiguo dejó un marcador de reinicio sin inventario. Se respaldó
  y se recuperó usando el formato y validador nativos, después de comprobar que
  no había gateways ni servicio Windows instalado. No se inició ningún servicio.
- Iris ahora permite reintentar una instalación parcial aunque el código ya esté
  al día. Ese reintento se verificó con el botón real y terminó correctamente.
- Respaldo nativo: `20260921-145202-iris-pre-update-2026-09-21` en
  `%LOCALAPPDATA%/hermes/state-snapshots`.
- Referencia Git de recuperación: `codex/iris-before-hermes-update-20260921`.
- No se instalaron plugins adicionales ni se actualizaron los archivos de IRIS mediante Git.

| Área | Prueba y resultado |
| --- | --- |
| Python de IRIS | 891 pasan, 3 omitidas; incluye actualizador y regresiones anteriores |
| JavaScript de Face/Companion | 240 pasan |
| Dependencias de Hermes | `uv pip check`: 137 paquetes compatibles |
| Botón Actualizar | Interfaz de producción + API de producción; rechazo, fallo parcial y reintento con éxito comprobados |
| Recarga de página | Éxito y fallo simulados; una sola petición de actualización; estado y resultado recuperados |
| Chat | Iris contra HTTP real de Hermes: autenticación, sesión, respuesta estructurada y streaming pasan |
| Visión | El payload de imagen y la opción de razonamiento son aceptados; respuesta del agente simulada |
| Continuity | Listado, mensajes, texto visible sin envoltorio JSON, cambio de título y bifurcación pasan |
| Async Agents | Crear ejecución, consultar estado, completar y extraer resultado pasan |
| Cron | Crear, listar, pausar, reanudar, editar y eliminar una tarea sintética pasan |
| Kanban | Crear tarea sintética, consultar detalles, tablero y workers pasan |
| Skills, MCP y plugins | Skills Hub de Iris y CLI nativas pasan; catálogo de plugins y `plugins compat` pasan; sin instalar extensiones |
| Comandos y perfiles | Registro de comandos y listado de perfiles consumidos por IRIS pasan |
| Voz / wake | Servidor nativo, autenticación WebSocket y consulta de estado pasan; sin grabación |

Las integraciones se repitieron con la **versión final 0.21.4**, almacenamiento
aislado y un agente simulado. No equivalen a una conversación con un proveedor real,
una transcripción de voz, una prueba física de cámara, una entrega externa de cron
o una ejecución de herramientas de terceros. La prueba de avatar/animaciones es
automática, no una inspección visual completa de todos los VRM.

Observaciones de Hermes, separadas de las rutas comprobadas de Iris:

- En 0.21.4, `/v1/skills` responde 500: el handler pasa `include_editorial` a una
  función que no acepta ese argumento. No se modificó el código de Hermes para
  ocultarlo. El Skills Hub de Iris lee su catálogo local y pasa su prueba con una
  skill sintética; la CLI nativa de Skills también funciona.
- Antes de actualizar, `/v1/toolsets` superó 30 segundos en un perfil vacío; el
  registro mostró sondeos auxiliares sin autenticación. No se cuenta como prueba
  aprobada de esa API ni de proveedores externos.
- La migración de configuración nativa pasó de v39 a v45. Hermes advirtió sobre
  toolsets de Teams y Google Chat; esas plataformas no forman parte de las rutas
  de Iris verificadas y no se activaron durante las pruebas.

La suite inicial tenía tres aserciones estáticas desactualizadas de trabajos
anteriores de cámara/avatar y versión de assets. Se ajustaron a las implementaciones
actuales; se conservaron las pruebas de comportamiento.

## Motor incluido en IRIS OS: Hermes 0.21.6

La versión instalada de IRIS OS trae **Hermes 0.21.6** (commit
`818c13be1dc4fd28987e1e881a9408224afd4535`, publicado el 2026-10-08). Hermes usa su
propio Python 3.14.8, la única versión que admite 0.21.6; IRIS sigue en Python 3.11.

- **Actualizaciones.** En la versión instalada, Hermes no se actualiza solo: su sello de
  instalación lo declara administrado por IRIS y `hermes update` se niega. El motor nuevo
  llega con las actualizaciones de IRIS.
- **Seguridad.** 0.21.6 corrige cuatro fallos de autenticación del panel web, la
  ejecución de filtros de Git en repositorios no confiables y la suplantación del
  remitente en el canal de correo.
- **Novedades de Hermes que IRIS aún no muestra:** transcripción de voz en directo,
  conversación de voz con un modelo auxiliar, aislamiento de plugins, modelos locales,
  paquetes de idioma, diagnóstico de rutinas y Kanban unificado. Se evalúan en el plan
  de IRIS; no se activan solo por cambiar el motor.
- **Canales.** Se retiró el canal de Home Assistant.
- **Navegador de Hermes.** Desde la versión siguiente a `qa9`, IRIS no incluye el
  componente `fetch-use`, que no publica el texto de su licencia. Sigue funcionando la
  descarga HTTP normal de páginas. Lo que deja de estar disponible, aunque se configure
  `BROWSER_USE_API_KEY`, es el proxy en la nube de Browser Use: detección de bots,
  proxies residenciales y reintentos gestionados.
- [Código de 0.21.6](https://github.com/NousResearch/hermes-agent/tree/818c13be1dc4fd28987e1e881a9408224afd4535).

La sección siguiente conserva las novedades de la actualización de desarrollo a
0.21.4; no describe la versión empaquetada actual.

## Novedades frente a 0.21.0

La versión final de **aquella prueba** fue **0.21.4 / 2026.9.21**. Esa instalación seguía la
rama Git `main`; se registra el commit exacto probado arriba, sin asumir que sea
idéntico al commit de una etiqueta de publicación.

- **0.21.2:** correcciones de bloqueo y lectura de `state.db`, aislamiento de perfiles,
  mejoras de cron, selección de proveedores y renovación OAuth de MCP. Añade un
  catálogo curado de plugins fijados por commit y gestión unificada en Hermes Desktop.
  [Notas oficiales de 0.21.2](https://github.com/NousResearch/hermes-agent/releases/tag/v2026.9.11).
- **0.21.3:** corrige renovaciones simultáneas de sesión en conexiones remotas y
  reduce aperturas duplicadas de la base de sesiones en procesos persistentes.
  [Notas oficiales de 0.21.3](https://github.com/NousResearch/hermes-agent/releases/tag/v2026.9.14).
- **0.21.4:** incorpora el bloqueo de gateway único por equipo, conexión de Desktop
  al backend existente, salida CLI `stream-json`, precarga `skills.auto_load`,
  mejoras de búsqueda de sesiones y descubrimiento MCP. La web añade páginas por
  plugin y autor, README fijado por commit y orden por fecha. Incluye más plugins
  comunitarios y correcciones de cron, Kanban, perfiles y base de sesiones.
  [Notas oficiales de 0.21.4](https://github.com/NousResearch/hermes-agent/releases/tag/v2026.9.21).
- **Plugins:** Hermes ya tenía soporte de plugins en la instalación anterior; la novedad
  documentada es el catálogo curado, sus metadatos y la integración del administrador.
  El índice puede refrescarse independientemente de actualizar todo Hermes.
  Se comprobó `plugins browse` y `plugins compat`; el segundo no encontró plugins
  habilitados que importen rutas retiradas por la reorganización de Hermes.
  [Catálogo oficial](https://hermes-agent.nousresearch.com/docs/user-guide/features/plugin-catalog).

El Marketplace de **IRIS** hoy integra **Skills y MCP**. La página de plugins de
Hermes Desktop no aparece automáticamente dentro de Face. Añadir esa sección a IRIS
requiere una integración propia; además, los plugins que aportan paneles al Desktop
de Hermes no pueden darse por compatibles con la interfaz de IRIS sin adaptarlos.
Los plugins que aportan herramientas al agente deben verificarse individualmente.

## Repetir las comprobaciones

Desde la raíz del proyecto, con los respectivos entornos instalados:

```text
.venv/Scripts/python.exe -m pytest -q
node --test tests/js/*.test.mjs
.venv/Scripts/python.exe scripts/verify_hermes_contract.py
.venv/Scripts/python.exe scripts/verify_hermes_local.py
node scripts/verify_system_updates.mjs --playwright-module <ruta-playwright>
node scripts/verify_system_updates.mjs --simulate-failure --playwright-module <ruta-playwright>
```

La prueba de navegador por defecto usa un actualizador simulado. Para una
actualización real autorizada, el mismo script admite
`--apply-real-hermes-update`; verifica que Hermes sea el único componente elegible.
Nunca usa `--force` ni `--force-venv`.

Evidencia de esta comprobación en `artifacts/hermes-update/`: metadatos de versión,
recibo del intento, respaldo, reportes de navegador, JUnit, pruebas de integración
y comprobación de dependencias. No contiene copias de credenciales de Hermes.
