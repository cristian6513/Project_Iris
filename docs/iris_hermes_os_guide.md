# IRIS OS maintenance guide

> **Referencia histórica/de diseño.** Puede describir rutas, versiones o
> funciones del corte original. Para uso actual, consulta [Estado actual](estado-actual.md)
> y las [guías](README.md); no se altera aquí la evidencia histórica.

> **Documento legacy.** Se conserva como registro de mantenimiento y puede
> mencionar rutas anteriores. Para uso actual consulta el
> [índice público](README.md) y las [guías estables](guides/getting-started.md).

Esta guia es para continuar el trabajo desde un chat nuevo sin tener que redescubrir la estructura del proyecto.

## Archivos principales

- `face/js/hermes_activity.js`
  - Controla IRIS OS, Activity Canvas, Mission Board, Routines/Cron, Doctor, Gateway Manager, MCP Tools, Hermes Skills, Marketplace e IRIS Player.
  - Es el primer archivo a revisar cuando algo del PC, OS, Hermes, cron, skills o activity no carga bien.

- `face/css/styles.css`
  - Controla el estilo visual del OS, dock, sidebar, panels, Doctor, Routines, Gateway, Skills y Mission Board.
  - Las clases del OS suelen empezar por `workbench-`, `os-`, `mission-` o `cron-`.

- `face/js/room_interactions.js`
  - Controla hotspots y acciones de habitacion como `Use PC`, `Use PC 2`, sofa, cama, ventana y bookshelf.

- `face/js/cyber_room.js`
  - Controla el cuarto 3D premium: piso, paredes, ventana, escritorio, cama, sofa, luces y materiales.

## Mapa rapido del OS

En `face/js/hermes_activity.js`:

- Constructor: estado interno y caches.
- `_renderPanel()`: estructura HTML del OS y tabs.
- `open()` / `close()`: abre y cierra el OS.
- `_setTab(name)`: cambia entre `desktop`, `activity`, `missions`, `routines`, `doctor`, `skills`, `tools`.
- `loadOsSummary()`: carga estado base del OS. Debe ser ligero y no bloquear tabs.
- `loadGatewayStatus()`: lee estado del gateway.
- `loadGatewayLogs()`: lee logs del gateway.
- `loadHermesDoctor()`: ejecuta Doctor. Debe ser bajo demanda, no automatico al abrir el OS.
- `loadRoutines()`: lista rutinas y entregas cron.
- `loadMissions()`: lee Mission Board/Kanban.
- `loadMcpCatalog()`: lista MCP disponibles/instalados.
- `loadSkills()`: lista skills de Hermes.
- `loadMarketplace()`: combina los catálogos de Skills y MCP solo cuando su ventana está visible.
- `_playerApp.load()`: carga el catálogo local solo cuando la ventana Player está visible.

IRIS Player mantiene su lógica separada en `face/js/iris_player.js`, su estado
puro en `face/js/iris_player_state.js` y sus estilos en
`face/css/iris_player.css`. La persistencia y el control de rutas viven en
`core/iris_player.py`; `core/websocket_server.py` expone únicamente IDs y
streams loopback.

## Reglas de rendimiento

- No ejecutar Doctor automaticamente al abrir el OS.
- No bloquear la UI esperando Doctor, Kanban, Skills o MCP.
- Cargar primero lo esencial: diagnostics + gateway.
- Cargar lo pesado solo cuando la tab este abierta o cuando el usuario pulse `Refresh`.
- Evitar `await` en flujos de UI si no es necesario para pintar la pantalla.
- Mantener caches con timestamps para evitar polling pesado.
- `open()` debe pintar el panel inmediatamente y despues llamar `_loadActiveTab()`.
- `_setTab()` no debe usar `force:true` por defecto; el cache debe respetarse al navegar.
- Mission Board solo debe hacer polling cuando la tab `missions` esta visible.
- Routines debe pintar jobs/outputs primero y cargar el historial seleccionado de forma diferida.
- Gateway status no debe cargar logs grandes salvo en Doctor o bajo accion explicita.

## Gateway Manager

En `hermes_activity.js`:

- `_gatewayRuntimeState()` decide si Gateway esta `Ready`, `Stopped`, `Port only`, `Error` o `Linked`.
- `_renderGatewayManager()` pinta el panel Doctor/Gateway.
- `_runGatewayAction(action)` ejecuta `start`, `stop`, `restart`, `logs`, `refresh`.

En `styles.css`:

- `.os-service-gateway-status`
- `.os-gateway-manager`
- `.os-gateway-actions`
- `.os-gateway-log`

El estado visual debe ser:

- `is-ready`: verde.
- `is-warning`: amber.
- `is-stopped` / `is-error`: rojo.
- `is-linked`: cyan suave.

## Doctor

Doctor es el Setup Center. Debe revisar sin exponer secretos.

Buenas practicas:

- El boton `Refresh` ejecuta `loadHermesDoctor(true)`.
- El boton `Iniciar Gateway` usa `_runGatewayAction('start')`.
- Si Gateway ya esta health 200, no mostrar warnings viejos de Gateway/Cron como activos.
- Mantener el panel compacto: hero, metricas, Gateway Manager, Checks, Recommendations.

## Routines / Cron

En `hermes_activity.js`:

- `loadRoutines(force)`
- `_renderRoutines()`
- `_runCronAction(action, jobId)`
- `_selectCronJob(jobId)`

Controles esperados:

- `New Routine`
- `Refresh`
- `Run now`
- `Pause/Resume`
- `Edit`
- `Delete`
- `Speak` para entregas con texto.

Si no hay una rutina seleccionada, mostrar botones deshabilitados en vez de romper la UI.

## Skills, MCP y Marketplace

Hermes decide cuándo usar skills y cómo instalar o autenticar capacidades. IRIS
separa administración y descubrimiento:

- **Skills Hub** muestra skills ya instaladas o conocidas por Hermes.
- **MCP Tools** muestra MCP ya instalados.
- **Marketplace** combina ambos catálogos disponibles sin prometer una
  instalación que IRIS no pueda completar.
- El descubrimiento de skills pagina el catálogo real de Hermes; una búsqueda
  consulta `hermes skills search --json` y conserva el identificador instalable.

En `hermes_activity.js`:

- `loadSkills()`
- `_renderSkills()`
- `loadMarketplace()`
- `_renderSkillsMarketplace()`

Regla importante:

- No presentar skills como toggles manuales de IRIS si Hermes las usa automáticamente.
- Mostrar fuente, categoría, requisitos, estado real y comando exacto cuando Hermes lo aporte.
- No confundir las 60 entradas de una página con el total publicado por Hermes.
- La acción segura actual copia el comando para ejecutarlo en Hermes CLI; no
  afirmar que la instalación terminó ni solicitar credenciales dentro del catálogo.

## Mission Board

En `hermes_activity.js`:

- `loadMissions()`
- `_renderMissionBoard()`
- `_createMission()`
- `_fetchMissionWorkers()`

Debe mostrar:

- misiones activas,
- subtareas cuando Hermes las exponga,
- workers/subagentes,
- errores de Kanban de forma clara.

## Validacion rapida

Comandos utiles:

```powershell
node --check face\js\hermes_activity.js
```

Buscar secciones:

```powershell
rg -n "loadHermesDoctor|loadRoutines|loadGatewayStatus|_renderGatewayManager|_renderRoutines" face/js/hermes_activity.js
rg -n "os-gateway|os-doctor|os-cron|os-service-gateway" face/css/styles.css
```

## No tocar sin necesidad

- VRM loading.
- Chat/voice/brain/memory core.
- Avatar expressions.
- Room interactions si el cambio es solo OS/Hermes.
- `cyber_room.js` si el cambio es solo UI del OS.

## Checklist antes de cerrar

- El OS abre rapido.
- Doctor no corre automaticamente al abrir.
- Gateway muestra verde si health 200.
- Refresh usa estilo OS, no boton nativo.
- Routines muestran entregas y botones sin romper si no hay seleccion.
- `node --check face\js\hermes_activity.js` pasa.
