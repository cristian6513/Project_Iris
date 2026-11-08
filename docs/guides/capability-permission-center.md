# Capability & Permission Center

[← Índice de documentación](../README.md)

Capability & Permission Center separa dos decisiones que antes podían parecer
la misma: la política de confianza de IRIS y los controles de ejecución de
Hermes. IRIS decide si una acción propia queda bloqueada, pide confirmación o
puede continuar. Hermes conserva toolsets, MCP, autenticación, reglas de
comandos peligrosos y acceso real a cada proveedor.

## Abrir el centro

En IRIS OS abre **Aplicaciones → Permisos**. La aplicación solo consulta su
inventario al abrirse o al pulsar **Sincronizar**, por lo que no ejecuta el CLI
de Hermes durante una conversación normal.

## Modos de política

- **Bloquear:** una acción iniciada por IRIS no debe continuar.
- **Preguntar:** necesita una confirmación explícita en su flujo.
- **Permitir:** puede avanzar al siguiente controlador, pero no evita las
  aprobaciones ni los límites de Hermes.

Los perfiles Privado, Equilibrado y Colaborativo reemplazan la política local
completa después de una confirmación. El valor predeterminado bloquea cámara y
micrófono, y pregunta antes de acciones con archivos, terminal, navegador,
red, MCP, agentes o Player.

**Permitir sección** muestra una sola confirmación con todas las capacidades de
esa sección y las guarda juntas. No cambia otras secciones. El guardado conserva
el desplazamiento y el foco; las opciones individuales siguen disponibles.

## Confirmaciones de apps y permisos recordados

En las acciones que Core ejecuta después de la confirmación, tienes hasta dos
minutos para decidir. Una solicitud caducada se cierra y no ejecuta la acción.
La espera para decidir está separada del tiempo de ejecución de la app.

Cuando el alcance lo permite, el diálogo ofrece **Permitir siempre esta
operación**. En una app comunitaria solo recuerda esa operación y esa
instalación; actualizarla o reinstalarla vuelve a pedir permiso. No autoriza las
demás apps. Para Correo solo se ofrece al seleccionar una cuenta concreta y
recuerda la consulta con sus mismos datos. Otra cuenta o consulta vuelve a
preguntar; las consultas generales de cuentas y Calendario siguen siendo puntuales.

Puedes quitar cada excepción en **Permisos recordados → Volver a preguntar**.
Bloquear una capacidad prevalece sobre lo recordado. Cambiar su política o
aplicar un perfil de confianza elimina las excepciones correspondientes.

## Estado de Hermes

La vista combina datos reales de Hermes 0.21:

- inventario y activación de `hermes tools list` para la plataforma CLI;
- diagnóstico de `hermes egress status`;
- servidores MCP instalados y prueba oficial `hermes mcp test`;
- auditoría manual de dependencias con `hermes security audit --json`;
- laboratorio de comandos con `hermes approvals test --json`.

El laboratorio **no ejecuta el comando**. Solo devuelve `allow`, `ask` o
`deny` según el guard nativo. La auditoría de dependencias consulta OSV.dev
únicamente después de confirmarla.

## Límites de seguridad

Permitir una capacidad en IRIS:

- no enciende cámara o micrófono;
- no instala, autentica ni concede permisos a un MCP;
- no habilita un toolset de Hermes por sí solo;
- no evita approvals, blocklists ni confirmaciones destructivas;
- no concede administración remota.

Todas las mutaciones del centro son locales y requieren confirmación. El
archivo `~/.iris/capability_permissions.json` se escribe de forma atómica,
guarda como máximo 160 recibos y 160 operaciones recordadas, y omite prompts,
comandos, claves y credenciales. Los alcances se identifican mediante huellas,
sin guardar los argumentos. El historial se puede borrar desde la propia aplicación.

## Alcance vigente en fuente

El contrato se aplica a las órdenes estructuradas de IRIS
Player, a la concesión exclusiva de cámara y al envío de contexto perceptivo
derivado. Capturar vídeo local y permitir que una señal emocional resumida
llegue al contexto del proveedor son capacidades independientes, ambas
bloqueadas por defecto. Restringir cualquiera de ellas revoca inmediatamente
la captura o el contexto activo.

F03 añade `vision.snapshot` para la foto a petición; F06 añade
`automations.run` para las reglas y F09 amplía las acciones estructuradas de
apps. La interfaz revalida permisos antes de ejecutar: haber abierto una
confirmación no congela una autorización que después se denegó. Una app o un
manifiesto no puede concederse esos permisos ni cargar Python arbitrario.
Consulta [Cámara](camera.md), [Automatizaciones](automations.md) y
[Contribuir apps](contributing-apps.md). La entrega instalada que los contiene
se identifica en [Estado actual](../estado-actual.md).

El mismo contrato sirve de base para Notification Center, Vision V2,
Orchestration y Browser Surface. La instalación, OAuth o revocación profunda de
plugins/MCP siguen perteneciendo a Hermes; recordar una operación de Iris no las
autoriza.

[← Volver a Funcionalidades](features.md)
