# Conexiones ampliables y relación con las apps de Iris

Fecha: 2026-10-17. Propuesta del usuario: una base específica para conexiones,
similar al kit de apps, que permita añadir servicios como Notion y apps que los usen.
**Diseño pendiente; no implementa Notion ni un cargador de conectores externos.**
No cambia el orden del escritorio/candidato acordado en el
plan coordinador (informe interno no distribuido).

## Objetivo: aportes de la comunidad

El usuario aclara que no se pretende desarrollar desde Iris todos los servicios.
La entrega debe dejar una base extensible para que otras personas creen y
aporten conectores. **Notion es un ejemplo, no el límite ni un requisito para
dar el kit por preparado.** Tampoco supone prometer compatibilidad con cualquier API.

«Preparado para contribuir» exige más que un manifiesto: API versionada de
adaptadores, plantilla ejecutable, validador, proveedor simulado, pruebas,
documentación y un proceso de revisión que otra persona pueda seguir. La UI
común debe presentar un conector nuevo registrado sin añadir ramas manuales
por nombre de proveedor en Conexiones o modificar el servicio Google.

## Una conexión y varios consumidores

Conexiones sigue siendo la app central para configurar servicios, autorizar
cuentas o espacios de trabajo, consultar permisos y desconectar. No se crea
otro administrador paralelo para cada app.

| Pieza | Responsabilidad | Ejemplo |
| --- | --- | --- |
| Conector de servicio | Autorización, credenciales, operaciones, caché/sincronización y ciclo de conexión en Core. | Google actual; Notion como integración futura. |
| Instancia de conexión | Cuenta o espacio de trabajo concretos y permisos efectivos dentro de un perfil. | Dos cuentas Google independientes. |
| App | Presentar datos y solicitar operaciones admitidas, sin recibir credenciales. | Correo y Calendario usan Google; una futura app de notas podría usar Notion. |
| Widget | Otra vista del estado compartido de la app. | Próximas reuniones; un futuro resumen de tareas. |
| Skill/acción IA opcional | Solicitar operaciones a Core con los permisos y confirmaciones aplicables. | Consultar datos autorizados; no crear otro login. |

Una conexión puede servir a varias apps; una app puede necesitar más de un
servicio. Un conector no necesita tener una app propia. Instalar/abrir una app
o añadir un widget no conecta cuentas, concede capacidades ni autoriza envíos a IA.

## Base existente que se conserva

- `core/google_connections.py` es el propietario único de conexiones Google por
  perfil, del almacén y de la comunicación con su worker. Coalesce lecturas,
  serializa renovación y descarta trabajo de generaciones desconectadas.
- Google ya separa configuración del cliente, autorización, datos leídos y
  consentimientos de IA; permite varias cuentas y distingue desconexión/revocación.
- La [guía de Conexiones](guides/google-connections.md) documenta el JSON personal
  de Google y su protección. No sustituirlo ni ampliar scopes por esta propuesta.
- El catálogo F09 acepta campos conocidos: `connections` u otros campos nuevos
  no son parte del `app.json` actual. Debe evolucionar su contrato y sus generadores
  antes de documentarlos como API disponible.

No renombrar/migrar todos los archivos Google al empezar. Primero un adaptador
del servicio existente detrás del registro común, manteniendo rutas, almacenes,
locks, identidad de cuentas, cancelación y controles ya probados.

## Kit previsto para aportar un conector

Propuesta de organización de fuente revisada, separada de `apps/`:

```text
connectors/<provider-id>/
  connector.json          # datos públicos y contrato versionado
  backend/                # adaptador del servicio, revisado e incluido por el build
  tests/                  # proveedor simulado, errores y lifecycle
  README.md               # configuración, permisos, límites y prueba reproducible
```

Es una estructura prevista, no un directorio que Iris escanee o ejecute hoy.
El manifiesto describe ID estable, nombre/icono, versión, compatibilidad del kit,
métodos de configuración soportados, capacidades/operaciones y referencias a
documentación. No contiene tokens, secretos, credenciales del autor ni URLs o
imports que el cargador ejecute libremente.

El registro se genera desde conectores revisados e incluidos por el build;
Core decide adaptadores y destinos permitidos. Las opciones públicas de configuración
pueden llegar a la UI, pero el backend y los secretos no se exportan a Face.
La fuente `connectors/` y el almacén privado `<IRIS_HOME>/connections/<provider-id>/`
son cosas distintas; no mover las credenciales Google existentes por organizar fuente.

La app declara qué capacidades de conexión necesita y si son opcionales; el
nombre concreto del campo se fija al versionar el contrato F09. Core valida
consumidor, operación, perfil, cuenta/espacio y permisos reales: declarar una
dependencia no concede acceso. Elegir la cuenta es explícito; no usar otra por
defecto cuando la seleccionada deja de estar disponible.

El primer alcance sigue limitado a código integrado/revisado. Una lista de
permisos no aísla código externo que comparta proceso u origen con Iris; admitir
conectores de terceros requiere el contrato de ejecución de la futura Store.

## Configuración y experiencia

Conexiones muestra servicios realmente soportados, con búsqueda y tarjetas:
configuración necesaria, conectado, permisos parciales, requiere reconectar,
sin red o error. Abrir el catálogo no autoriza ni sincroniza. Una marca futura
como Notion no debe anunciarse como funcional antes de tener su integración.

En el detalle: cuentas/espacios, permisos efectivos, última sincronización,
operaciones disponibles y apps consumidoras conocidas. Cada app/widget explica
«Conectar en Conexiones» si le falta el servicio y vuelve a su estado real al regresar.
Los estados y errores siguen el tema y los componentes existentes de Iris.

No imponer el JSON de Google a otros servicios. El método de acceso se revisa
para cada proveedor y se describe en su ficha; puede requerir otra configuración.
En Notion, su documentación distingue conexiones internas mediante token y
conexiones públicas mediante OAuth, con acceso a recursos autorizado por el usuario.
La vía concreta para Iris sigue pendiente de elección y validación; no prometer
que su OAuth público se resuelve copiando el flujo Desktop de Google.
Los secretos de un cliente público no se consideran protegidos por incluirlos
en un instalador; resolver ese modelo antes de habilitar el flujo.

## Contratos compartidos

- Credenciales protegidas por perfil y proveedor, accesibles solo al componente
  propietario; no entregarlas a apps, widgets, prompts, logs ni skills. Las skills
  independientes de Hermes no ceden sus sesiones implícitamente a Iris.
- Un propietario de sincronización por conexión; vistas y consumidores comparten
  caché/resultados y peticiones concurrentes. Paginación, cobertura y frescura
  explícitas; un error o un recurso no autorizado no se convierte en una lista vacía.
- Permiso de acceso a un servicio, sincronización automática y envío a IA son
  decisiones separadas. Empezar la futura integración con operaciones de lectura
  acotadas; cualquier escritura exige su propio contrato de efectos y confirmación.
- Cancelación, timeout, backoff y generación por conexión. Desconectar impide
  nuevos trabajos y publicaciones tardías; no repetir una operación remota de
  resultado incierto sin reconciliación. Las apps conservan sus estados sin conexión.
- Desconectar, revocar y borrar datos son acciones distintas. Explicar todos los
  consumidores afectados antes de desconectar una cuenta o espacio compartido.
  No anunciar revocación si el proveedor no la soporta o no confirmó el resultado.
- Ocultar una app solo oculta el icono. Desinstalar una app externa recoge sus
  consumidores/trabajos propios, sin desconectar una cuenta usada por otras apps.
  Retirar un conector requiere revisar sus dependientes, operaciones activas y
  conservación de datos; no borrar la conexión por retirar la última ventana.
- La futura Store reutiliza el gestor de paquetes y registros de propiedad
  previsto para apps, con tipo de paquete y dependencias de conector explícitos.
  No crear un segundo instalador ni permitir carga de código por un JSON.

## Etapas propuestas y validación

1. **Contrato:** registro versionado, límites de Core, declaraciones de consumidores
   y plantilla/guía para colaboradores con pruebas de un proveedor inventado.
2. **Google como adaptador:** el comportamiento actual debe seguir funcionando;
   dos apps/una conexión no duplican OAuth ni lecturas. Probar dos perfiles,
   varias cuentas, permisos parciales, errores, desconexión y resultados tardíos.
3. **Segundo proveedor sintético:** copiar/renombrar la plantilla y añadir un
   servicio ficticio mediante el registro, sin modificar Google ni la UI común.
   Esa prueba acredita la extensibilidad del kit. Un proveedor real posterior,
   sea Notion u otro aporte, requiere investigación vigente y QA autorizada propias.
4. **App/widget consumidor:** demostrar selección de conexión, permisos limitados,
   estado compartido, cambios de cuenta, error y uso sin conexión. IA, si se añade,
   mediante operaciones revisadas de Core, sin otro almacén de tokens.
5. **Integración:** exportación, paquete, regresiones Google y QA del candidato;
   actualización/rollback conservan configuraciones compatibles sin ampliar permisos.

El siguiente trabajo acordado sigue siendo el escritorio. Estas etapas no se
ejecutan automáticamente ni habilitan nuevas cuentas por dejar el plan escrito.
La futura guía debe enseñar manifiesto, adaptador, pruebas, permisos, ejemplo
simulado y cómo aportar; no presentar una plantilla como conexión ya operativa.

## Entregables y recorrido para colaboradores

La implementación del kit debe entregar:

- **Contrato público versionado:** interfaces de configuración, autorización,
  operaciones y cierre; identidad de cuenta/espacio, errores y límites. Declarar
  compatibilidad del SDK y versión de API del proveedor, sin mezclar sus cambios.
- **Plantilla mínima:** manifiesto, adaptador, pruebas y README con nombres
  sustituibles. No exigir una app, widget o skill para aportar solo una conexión;
  incluir una muestra consumidora opcional para quien también quiera crearlos.
- **Validación y pruebas locales:** comandos documentados y comprobados al
  implementar, con credenciales inventadas y servidor simulado. Cubrir permisos
  parciales, paginación, caducidad, límites, cancelación, desconexión y respuestas
  tardías, sin requerir la cuenta personal del autor ni secretos en el repositorio.
- **Guía y skill de contribución:** redactadas contra la API que efectivamente
  funcione; explicar estructura, configuración, operaciones, pruebas y envío del
  aporte. Una skill ayuda a desarrollar usando el contrato, no crea permisos ni
  convierte un paquete sin revisión en un conector instalable.
- **Ficha del aporte:** autor/mantenedor, licencia, fuente oficial consultada,
  versión compatible, datos leídos/escritos, permisos, dependencias y límites.
  Debe distinguir código probado con un simulador de una QA real del servicio.

Recorrido inicial: copiar la plantilla → implementar el adaptador → validar y
probar → documentar → enviar una contribución para revisión. El catálogo se genera
tras admitir el conector revisado en el build. Esto permite contribuciones desde
el principio sin esperar una Store, pero no es instalación libre de Python.
La futura distribución por Store reutilizará los contratos de paquetes,
compatibilidad y propiedad ya previstos.

El criterio de cierre del kit es demostrar el recorrido desde una copia nueva
de la plantilla, con otro ID/proveedor y sin correcciones especiales del shell.
El registro explícito y la revisión de confianza se conservan; lo que se evita
es reescribir infraestructura común para cada aporte. No declarar el kit listo
solo por publicar esta propuesta o un JSON de ejemplo.

## Fuentes y frescura

- Código inspeccionado: `core/google_connections.py`, `face/js/os_app_manifest.js`;
  [guía Google](guides/google-connections.md), [apps/widgets](iris_apps_widgets_plan.md)
  y [personalización/paquetes externos](iris_desktop_personalization_plan_2026-10-17.md).
- Notion, [autorización](https://developers.notion.com/guides/get-started/authorization)
  y [capacidades](https://developers.notion.com/reference/capabilities), consultadas
  el 2026-10-17. Son referencias para revisar el proveedor, no prueba de compatibilidad
  ni de una implementación Notion en Iris.
