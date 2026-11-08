# Acceso a proveedores desde Iris

El paso de modelo del wizard permite conectar cuentas sin abrir una terminal.
Los campos de API key siguen dentro de Iris. Para OAuth, Iris muestra el enlace
de autorización, el código cuando corresponde, el estado y la opción de cancelar.
La autorización de la cuenta se realiza en la página del proveedor.

Revisión documental: 2026-10-22. Este OAuth corresponde a **proveedores de IA
de Hermes**, no al cliente JSON de Google para Calendario/Correo. Esa conexión
se configura en [Conexiones](google-connections.md), con almacén separado.

| Proveedor | Flujo disponible |
| --- | --- |
| OpenAI / Codex | Código de dispositivo. La cuenta debe permitir este método. |
| Nous | Código de dispositivo gestionado por Hermes. |
| MiniMax | Código de dispositivo gestionado por Hermes. |
| xAI | Código de dispositivo gestionado por Hermes. |
| OpenRouter | Enlace con PKCE y retorno automático a un servidor local temporal. |
| Anthropic / Claude | Reutilizar una credencial OAuth ya configurada en Hermes, o introducir una API key. |
| Qwen OAuth | Comprobar/importar una sesión existente de Qwen CLI. Qwen Cloud tiene una opción separada de API key. |

La lista efectiva depende de las capacidades de la instalación de Hermes. Iris
no abre una consola como alternativa silenciosa para proveedores que todavía
dependen de una herramienta externa.

En la fuente posterior a `qa11`, conectar un proveedor adicional guarda su
credencial sin reemplazar el principal. Guardar/verificar el principal y
agregar otro son operaciones distintas. Véanse [Configuración](configuration.md)
y [Modelos](models.md), con disponibilidad por candidato en
[Estado actual](../estado-actual.md).

## Versión instalada de Iris

Los flujos son los mismos y los ejecuta el Hermes incluido; no hace falta
instalar Hermes, Python ni otra herramienta para API key u OAuth integrado.
Si el motor incluido falta o está dañado, el mensaje pide reparar Iris en lugar
de instalar Hermes. Las opciones que dependen de una herramienta oficial externa
(sesiones existentes de Claude Code o Qwen CLI) se mantienen. **GitHub Copilot
(ACP)** sigue el contrato de Hermes: `HERMES_COPILOT_ACP_COMMAND` (o
`COPILOT_CLI_PATH`) contiene solo el ejecutable, sin comillas aunque la ruta
tenga espacios, y los argumentos van en `HERMES_COPILOT_ACP_ARGS` (por defecto
`--acp --stdio`). En la versión instalada debe ser la ruta completa: el motor
incluido no busca programas en el PATH. En desarrollo también vale un nombre
que Windows pueda iniciar desde el PATH (un `.exe`; el shim `copilot.cmd` de npm
no basta). Estas tres variables se leen como las lee Hermes: si el perfil tiene
`.env`, manda ese archivo, incluso con un valor vacío, y una variable heredada
del sistema que el archivo no define no se usa. Solo sin `.env` cuentan las
variables del sistema.

## Uso

1. Reiniciar el wizard si estaba abierto antes de actualizar Iris y recargar la página.
2. Elegir el proveedor. Para OAuth, pulsar **Conectar cuenta** y autorizar en la página del proveedor, con el código si aparece. Iris carga los modelos cuando termina la autorización.
3. Para API key, pegar la clave y pulsar **Cargar modelos**. Si ya hay una clave guardada para ese proveedor, se puede consultar sin volver a escribirla.
4. Elegir el modelo y pulsar **Guardar proveedor**. Consultar una lista no genera respuestas ni cambia el proveedor principal; la clave nueva solo se persiste al guardar.

Este orden está en la fuente posterior a QA13; los instaladores QA13 existentes conservan el flujo anterior. Los proveedores locales y **Agregar otro proveedor** mantienen su flujo propio: una app local no necesita login, y el proveedor adicional no sustituye al principal.

La lista pertenece al proveedor, al método de acceso, a la clave y al endpoint elegidos. Cambiar cualquiera invalida la consulta anterior. API y OAuth pueden ofrecer modelos distintos, por ejemplo OpenAI API y la suscripción de ChatGPT/Codex. No se elige automáticamente el primer modelo al cargar la lista.

- **Nous Portal:** consulta el plan y las recomendaciones actualizadas después de conectar. Muestra los modelos admitidos; no presupone una cantidad fija de gratuitos. Si no se puede comprobar el plan, pide reintentar. Lo vuelve a comprobar antes de guardar.
- **OpenRouter:** consulta su lista para el usuario, respetando preferencias y restricciones de la cuenta. Incluye los modelos gratuitos que el servicio devuelve con salida de texto y soporte de herramientas.
- **OpenCode Zen:** consulta el catálogo público de Zen, separado de Go y de OpenRouter. Los modelos marcados **«Gratis solo en OpenCode»** son exclusivos de la aplicación OpenCode. Para usar Zen desde Iris/Hermes necesitas créditos y elegir un modelo de pago; añadir créditos no desbloquea esos gratuitos. **OpenCode Go** requiere una suscripción activa y uno de sus modelos incluidos. La lista pública no valida la clave ni la cuota. Esta restricción está documentada por [OpenCode](https://opencode.ai/v2/docs/console/api/inference) y se reprodujo el 29 de octubre de 2026: DeepSeek y GLM gratuitos devolvieron `429 FreeUsageLimitError` incluso fuera de Iris.
- Si Hermes solo tiene modelos de referencia para un proveedor, Iris lo indica. Una lista no garantiza saldo, cuota ni acceso de inferencia. Los modelos de generación de imagen o vídeo no se ofrecen como modelos de conversación.

**Actualizar catálogo** actualiza la información general de proveedores. **Actualizar modelos** consulta el acceso del proveedor seleccionado. Las consultas de modelos no guardan claves en un catálogo compartido.

Recargar la misma pestaña conserva la referencia al acceso pendiente; si el
proveedor seleccionado coincide, Iris retoma la comprobación. Un corte de
conexión permite **Volver a comprobar**. Cambiar de proveedor o volver a API key
cancela el acceso anterior. Cancelar no elimina una credencial que el proveedor
ya había guardado antes de recibir esa petición.

## Implementación y límites

### Nous conectado sin modelos en una VM

En QA16 se confirmó un fallo de confianza HTTPS en Windows limpio: el inicio de
sesión terminaba, pero Python no verificaba el certificado de la consulta de cuenta.
La corrección posterior en fuente hace que los procesos privados usen el bundle
de certificados ya incluido y distingue el fallo TLS del error genérico de catálogo.
Mantiene la comprobación de certificado y nombre del servidor. Volver a iniciar
sesión o elegir un plan de pago no corrige una cadena de confianza ausente.
El [estado actual](../estado-actual.md) identifica la corrección y su validación;
el candidato original conserva el comportamiento anterior en su arranque normal.

### Propiedad de autenticación y ejecución

- `wizard/provider_auth.py` administra operaciones con POST, GET y DELETE,
  identificadores aleatorios, vencimiento y controles de origen del wizard.
- `wizard/hermes_auth_worker.py` ejecuta los flujos nativos con el Python de
  Hermes, en un proceso oculto independiente por operación. No inicia el gateway.
- Hermes realiza el intercambio y guarda las credenciales en su formato nativo.
  Iris comprueba que exista la credencial antes de confirmar el acceso.
- El navegador recibe únicamente datos de presentación. `sessionStorage` guarda
  el identificador de operación y el proveedor; no guarda tokens ni códigos.
- Cancelar espera a que termine el proceso propio. El cierre del wizard o de su
  canal con el proceso también detiene los sondeos pendientes.
- El endpoint SSE antiguo responde 410 y pide recargar la página.

El adaptador se verificó contra Hermes 0.21.4, commit
`fca3221df48d4ae1e4db9e8a1a041de21ea4d9cf`, y contra Hermes 0.21.6, commit
`818c13be1dc4fd28987e1e881a9408224afd4535`, con el mismo resultado. Usa interfaces internas de Hermes;
una futura versión necesita volver a pasar la verificación de contratos antes
de considerarse compatible.

## Verificación

Pruebas de backend: `tests/test_provider_auth.py` y `tests/test_provider_oauth.py`.
Pruebas de concurrencia y recuperación de UI: `tests/js/provider_auth.test.mjs`.

`scripts/verify_provider_auth_native.py` importa la instalación real de Hermes
con un directorio de datos temporal, bloquea conexiones externas y simula las
respuestas del proveedor y la escritura de credenciales. Comprueba los cinco
contratos integrados sin usar cuentas reales.

`scripts/verify_provider_login.mjs` usa el HTML y JavaScript del wizard en un
navegador con respuestas HTTP simuladas. Requiere Playwright, que también puede
indicarse mediante `PLAYWRIGHT_MODULE`. Guarda capturas y resultados en
`artifacts/provider-login/`.

Estas pruebas no sustituyen la autorización real de cada cuenta ni demuestran
que un proveedor vaya a aceptar las condiciones o permisos de una cuenta dada.
