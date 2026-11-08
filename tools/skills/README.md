# Skills compartidos de desarrollo de Iris

Esta carpeta conserva las fuentes de los skills para Codex y Claude. Se pueden leer desde el checkout; su presencia aquí no garantiza que una aplicación los registre automáticamente. Los skills del asistente Hermes que usa el producto viven en otra carpeta (`.hermes/skills/iris-os`).

| Skill | Cuándo usarlo |
|---|---|
| iris-project-context (referencia no distribuida: `tools/skills/iris-project-context/SKILL.md`) | Retomar trabajo, recuperar contexto o investigar conexiones entre componentes; planes/fuentes actuales y NeuroMapa cuando esté disponible. |
| iris-regression-review (referencia no distribuida: `tools/skills/iris-regression-review/SKILL.md`) | Revisar un arreglo, contrato o candidato; falsos positivos, reproducciones y evidencia de aceptación. |
| [iris-os-design](iris-os-design/SKILL.md) | Crear o revisar interfaces con la identidad visual de Iris. |
| [iris-app-contributor](iris-app-contributor/SKILL.md) | Desarrollar o ampliar una app integrada, con Python y uso por IA opcionales; preparar el aporte de la comunidad. |

Los estados de las entregas siguen en los planes/reportes, no en las instrucciones de los skills. Las referencias se leen según la tarea; no hace falta cargar todos los skills a la vez.

La entrada documental vigente es [Estado actual](../../docs/estado-actual.md),
seguida del [índice](../../docs/README.md). Distingue fuente, candidato y QA;
los skills y las fichas no deben mantener otra matriz de aprobaciones.

Para usar una fuente compartida explícitamente en Claude, indicar el archivo del skill y la tarea, por ejemplo:

```text
Usa tools/skills/iris-project-context/SKILL.md para retomar F07. Contrasta el plan,
el reporte más reciente y el código actual; diferencia implementación y aceptación.
```

La instalación automática en Claude Desktop requiere su integración propia; no equivale a la de Claude Code. La copia instalada en Codex debe mantenerse coherente con estas fuentes cuando se revise un skill.
