# Contribuir a IRIS OS

Puedes aportar código, pruebas, documentación o mejoras de diseño. Empieza por la
[guía de desarrollo](docs/guides/development.md) y describe el problema y el alcance
antes de una ampliación grande.

Lee el [estado actual](docs/estado-actual.md) para distinguir código, candidato
y QA; no presupongas que una función de la fuente ya se publicó. Las guías de
uso viven en `docs/guides/`, los criterios de entrega en el plan coordinador y
las pruebas históricas en los informes fechados. Si cambias comportamiento,
actualiza su guía y deja la evidencia de esa revisión sin reescribir la anterior.

- Para apps del escritorio, servicios Python y acciones que pueda usar Iris,
  sigue [Contribuir aplicaciones](docs/guides/contributing-apps.md): copia una de
  las plantillas de [`apps/`](apps/README.md). El kit F09 está implementado en el
  código fuente y pendiente de integrarse en una versión distribuida.
- Para orientarte, consulta [arquitectura](docs/guides/architecture.md), el
  [índice de documentos](docs/README.md) y el plan de la entrega correspondiente.
- Para preparar un PR, incluye comportamiento resultante, pruebas realizadas y
  límites u omisiones. Conserva los datos y recursos personales fuera del aporte.
- El proyecto tiene [licencia MIT](LICENSE). Los recursos y dependencias de
  terceros conservan sus propias licencias y avisos.

Las contribuciones se incorporan mediante revisión del repositorio y la
distribución conjunta de Iris. Esta guía no habilita plugins externos ni una
publicación automática.
