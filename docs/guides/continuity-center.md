# Continuity Center

> Disponibilidad por versión/candidato: [Estado actual](../estado-actual.md).
> Los resultados fechados de esta guía corresponden a aquella comprobación,
> no a una validación nueva de todos los ejecutables.

[← Índice de documentación](../README.md)

Continuity Center reúne perfiles y sesiones de Hermes dentro de una ventana de
IRIS OS. No crea una segunda memoria ni una base de sesiones paralela: Hermes
sigue siendo la fuente de verdad y IRIS aporta navegación, filtros y el tema
visual.

## Qué permite hoy

- listar los perfiles que devuelve `hermes profile list`;
- ver cuál es el perfil activo, su modelo, Gateway y distribución cuando
  Hermes publica esos datos;
- activar un perfil mediante `hermes profile use <nombre>`;
- crear perfiles vacíos o clonados mediante `hermes profile create`;
- listar sesiones cuando el Gateway anuncia `session_resources` y el endpoint
  `/api/sessions`;
- buscar, renombrar y fijar sesiones cuando Hermes anuncia `session_update`;
- reanudar una sesión dentro de IRIS cuando Hermes anuncia historial y
  continuidad autenticada;
- crear una conversación limpia mediante `session_create`;
- crear y abrir una rama completa mediante `session_fork`;
- copiar el ID de una sesión para usarla también desde CLI/TUI.

La aplicación carga el catálogo únicamente al abrirla o pulsar
**Sincronizar**. El transcript se solicita solo al reanudar, crear o ramificar,
y se proyectan como máximo 120 mensajes visibles. No mantiene polling mientras
el usuario conversa con IRIS.

## Fuente de verdad y privacidad

IRIS usa tres contratos públicos de Hermes:

1. La CLI `hermes profile` para listar, crear y activar perfiles.
2. `/v1/capabilities` y `/api/sessions` para detectar y administrar sesiones.
3. `X-Hermes-Session-Id` para continuar el transcript elegido y
   `X-Hermes-Session-Key` para conservar el ámbito de memoria duradera de IRIS
   aunque se cambie de conversación.

Continuity Center no importa `SessionDB`, no abre `state.db` y no copia
manualmente `SOUL.md`, memoria, skills o credenciales. Los modos de clonación
se delegan a Hermes; `--clone-from` puede copiar configuración, `.env`,
identidad, skills y memoria. La interfaz lo advierte antes de crear el perfil.
`--clone-all` conserva todavía más estado, pero Hermes excluye historial de
sesiones, `state.db`, backups, snapshots y checkpoints.

Al abrir una conversación, Core lee el historial mediante el endpoint oficial,
descarta prompts de sistema, razonamiento, llamadas y resultados de tools, y
entrega al navegador únicamente los turnos `user`/`assistant`. Después cambia
en una sola sección crítica el transcript de Hermes, el ámbito de memoria local
de IRIS y la conversación visible. El ID activo se persiste, pero el transcript
no se duplica en otra base de sesiones.

Las rutas completas del equipo tampoco se envían a la UI. Si una versión
futura de la API publica `cwd` o `git_repo_root`, Core entrega solo el nombre de
la carpeta y un identificador opaco. Hermes 0.20.1 no incluye esas rutas en la
proyección segura del Gateway, por lo que Workspace Explorer permanece
limitado.

## Modos de funcionamiento

| Modo | Significado |
| --- | --- |
| Hermes nativo | CLI de perfiles y Session API disponibles. |
| Cobertura parcial | Solo la CLI o solo la Session API está disponible. |
| Hermes desconectado | No se pudo consultar ninguna de las dos superficies. |

En modo parcial u offline la aplicación conserva funciones de lectura que sí
sean verificables y desactiva el resto. No usa archivos internos como fallback.

## Límites actuales

- El Gateway sin prefijo muestra las sesiones del perfil que está ejecutando;
  no se mezclan sesiones de otros perfiles con claves distintas.
- El perfil persistente elegido por `hermes profile use` y el perfil que sirve
  un Gateway ya iniciado pueden ser distintos. IRIS los muestra por separado y
  atribuye las sesiones al Gateway en ejecución cuando puede identificarlo.
- Si `hermes_url` apunta a un Gateway remoto, los perfiles del CLI local se
  ocultan y sus mutaciones se rechazan para no combinar dos instalaciones.
- Cambiar el perfil persistente no sustituye un Gateway que ya está corriendo.
  La UI solicita confirmación y avisa que debe reiniciarse.
- **Continuar dentro de IRIS** solo se habilita cuando el Gateway anuncia
  `session_messages`, el encabezado `X-Hermes-Session-Id` y existe una
  `API_SERVER_KEY`. Si IRIS está respondiendo, el cambio se rechaza hasta que
  termine el turno.
- Crear una rama usa el linaje nativo de Hermes y abre la rama resultante; no
  copia archivos ni crea una conversación paralela propia de IRIS.
- Workspace Explorer todavía no puede listar archivos porque el contrato
  seguro de sesiones no expone una ruta de proyecto.
- Hermes 0.20.1 permite marcar una sesión como archivada, pero su listado
  estable no devuelve las archivadas. IRIS no ofrece esa acción hasta que pueda
  garantizar también su restauración.
- Se cargan hasta 200 sesiones recientes, además de las fijadas que Hermes
  reincorpora. La UI avisa si el servidor indica que existen más.

## Referencias de Hermes

- [Profiles: Running Multiple Agents](https://hermes-agent.nousresearch.com/docs/user-guide/profiles/)
- [Profile Commands Reference](https://hermes-agent.nousresearch.com/docs/reference/profile-commands)
- [Sessions](https://hermes-agent.nousresearch.com/docs/user-guide/sessions/)
- [API Server](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server)
