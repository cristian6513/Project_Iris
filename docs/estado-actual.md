# Estado actual de IRIS OS

Revisión documental: **2026-10-28**. Esta página resume la fuente y la última
construcción; no anuncia una release ni sustituye los criterios del
plan A08/F01–F16 (informe interno no distribuido).

## Corte más reciente: QA18

QA18 es un único candidato Windows x64 **0.9.2**, ID **2d740555**, con
**Companion 0.1.4**, sin firma ni publicación. Incluye las ampliaciones del SDK
posteriores a QA17, el ACK y P3 de «Avisar a Iris», el ajuste del acceso directo
del Companion y **IRIS MToon v11** por defecto, con VRM propios.
Pasaron las comprobaciones del paquete y 12/12 del flujo programático de instalar,
reparar y desinstalar. **La QA manual instalada sigue pendiente.**
Alcance, huellas, repeticiones y guía de QA18 (informe interno no distribuido).

## Corte anterior: QA17

QA17 es un único candidato Windows x64 **0.9.2**, ID **53a167c8**, sin firma.
Incluye la fuente posterior a QA16: certificados de Nous, QA4, cámara/pantalla,
FPS, peticiones visuales de Iris y tareas en segundo plano con sus correcciones.
Pasaron las comprobaciones automáticas del paquete y 12/12 del backend de
instalar, reparar y desinstalar. **La QA manual del candidato sigue pendiente**,
especialmente el selector nativo de pantalla y el modelo real con Hermes 0.21.6.
No se construyó otra 0.9.3 ni se publicó una release.
Alcance, huellas y guía de QA17 (informe interno no distribuido).

QA16, ID ec7291bd, se conserva como candidato anterior; sus bytes no cambian por
los arreglos de fuente descritos a continuación.

Después de congelar QA16 se revisó la web, se prepararon las
[tres copias limpias de publicación](guides/publication-repositories.md), se añadió
la [entrada de compilación personal](guides/build-from-source.md) y se actualizó
la documentación/manual. Esa entrada tiene comprobaciones parciales; **su primera
ejecución completa hasta el EXE sigue pendiente**. Estos cambios de fuente no
reconstruyen QA16 ni equivalen a publicar los repositorios.

Se midió Ligera v10 (informe interno no distribuido) con el visor del
avatar y efectos de Iris; sus números no son un benchmark de toda la habitación ni
del instalador. El 28 de octubre, en fuente, [Ligera v10 pasó a ser el avatar del
producto](audits/iris_vrm_default_avatar_2026-10-28.md). Se quitaron el avatar
geométrico y el de partículas. IRIS viene incluida y cada persona puede usar su
propio VRM. Después se convirtió a MToon v11 para la iluminación de Iris.
QA18 incluye este modelo; falta la QA instalada del candidato.

En QA16 se confirmó después el fallo de Nous en la VM: la sesión funciona,
pero `urllib` no encuentra la cadena de confianza HTTPS con los certificados
por defecto de ese Windows. La corrección TLS (informe interno no distribuido)
está en fuente; el usuario confirmó el catálogo con el lanzador provisional que
usa el bundle incluido. El arranque normal de QA16 conserva sus bytes anteriores.
QA17 sí incorpora el arreglo; falta comprobar en la VM su arranque normal.

Las correcciones de QA 4 (informe interno no distribuido) están en fuente:
Donaciones en el Centro de control, guardado de voz visible, diálogo común de foto,
separación pantalla/cámara, información de actualización automática y avisos de
Calendario acotados al periodo. El cumpleaños ausente era una tarea de Google Tasks,
fuera de la conexión de eventos actual. Estos cambios tampoco reconstruyen QA16.

En fuente del 27 de octubre se añadieron [capturas de pantalla a petición y el
ajuste de FPS](audits/iris_screen_capture_fps_2026-10-27.md). Las capturas tienen
permiso separado, selección de origen y revisión antes de enviar; Rendimiento
ofrece 30, 60 o sin límite para Face y Companion. Falta comprobar el selector de
Windows en QA17; QA16 no contiene estas funciones.
La revisión posterior (informe interno no distribuido) corrige el margen
de FPS en monitores de 60 Hz y las capturas activadas por preguntas generales;
cuando no se comparte una imagen, la pregunta continúa por texto sin fingir visión.
Después, Iris puede pedir ella misma una foto o una captura (informe interno no distribuido)
cuando la necesita. Es una por turno local, con un código de un solo uso, y tiene
permisos propios inicialmente en «Preguntar». Telegram y las automatizaciones no
pueden usarlo. La prueba real desde fuente pasó; falta comprobarlo en el candidato QA17.
Además, Iris puede dejar encargos largos en segundo plano (informe interno no distribuido) y seguir conversando. Muestra «Trabajando en» con Cancelar y presenta el resultado al terminar, o lo deja en la Bandeja si cambiaste de conversación. Probado el 2026-10-27 con Hermes 0.21.4 y gpt-5.6-luna reales, tras corregir cuatro problemas que solo aparecieron con el modelo real. Falta el selector de Windows en el instalador e integrar cron.
En fuente posterior a QA17, el SDK de apps resuelve cuatro límites de la plataforma (informe interno no distribuido):
- operaciones por público, con el origen de cada llamada para el backend;
- avisos que las apps programan y Core muestra a su hora, también en la Bandeja;
- «Avisar a Iris» tras un clic de la persona;
- `openApp(id, datos)` desde ventanas.

Los cinco hallazgos y sus dos variantes quedan [cerrados en fuente tras la revisión
de Codex](audits/iris_sdk_platform_limits_codex_closure_2026-10-28.md): audiencias,
persistencia y retirada de avisos, gesto único para «Avisar a Iris», revisión completa
de los datos y apertura con datos. Pasaron las reproducciones de Core y Edge real.
Las [guías de creación](guides/community-apps-python.md) y la skill de contribución
documentan ese corte. La numeración sigue siendo SDK 2; **QA17 no incluye la ampliación**.
QA18 incluye la ampliación y sus pruebas del runtime aislado pasan. Falta la QA
manual con WebView2 y el modelo real. Cada app debe
adoptar las funciones que necesite; actualizar el SDK no modifica sus paquetes.

Otro ajuste de fuente hace que «Avisar a Iris» espere una confirmación de Core
por identificador antes de mostrar éxito. Los rechazos llegan a la app con su motivo.
La revisión de ese ajuste (informe interno no distribuido)
confirma el envío. El detalle de paridad con decimales pequeños junto al límite de
4 KB quedó corregido y comprobado junto al cambio de avatar (informe interno no distribuido).

## Pendientes vigentes

- Completar la QA manual instalada del candidato exacto, incluidas las
  comprobaciones de cuentas, voz y hardware descritas en su guía. Las pruebas
  automáticas no cierran globalmente A08.9/F09.6/F10.5.
- Ejecutar por completo la entrada nueva `COMPILAR.cmd` en un entorno preparado;
  sus pruebas de receta no acreditan otro instalador terminado.
- Las copias de publicación están preparadas localmente; no se han creado ni
  subido los tres repositorios remotos por prepararlas.
- Catálogo remoto de Store y SDK comunitario de conexiones siguen fuera del
  alcance implementado. Cuentas/suscripciones/créditos Iris y Android son
  [ideas diferidas](iris_future_services_notes_2026-10-26.md).

## Versiones y distribución

| Referencia | Estado comprobable |
| --- | --- |
| Código Python de Iris | `core/version.py` declara `1.0.0`; no significa que se haya publicado ese instalador. |
| Candidato de QA vigente | QA18: un único Iris `0.9.2`, ID `2d740555`, Companion `0.1.4`; comprobaciones automáticas documentadas y QA manual instalada pendiente. No se construyó otra `0.9.3`. |
| Motor fijado para Windows x64 | Hermes `0.21.6`, revisión `818c13be1dc4fd28987e1e881a9408224afd4535`. |
| Python privado | Iris `3.11.16`; Hermes `3.14.8`, separados. Los pins y hashes están en `scripts/runtime_sources.json`. |
| Desktop Companion | Fuente y manifiesto `0.1.4`, Windows x64, incluido en QA18. Una recarga web no actualiza un ejecutable anterior. |
| Instalador y canal | A08.9 sigue abierto. La firma del manifiesto de actualización no equivale a firma Authenticode del EXE. Los candidatos de QA están declarados sin firma. |
| Fecha pública | No se fija por esta revisión. La fecha sugerida en una propuesta de marketing no constituye un lanzamiento aprobado. |

La matriz general de Hermes o la CI de Linux no acredita un instalador de Iris
para ARM64, Linux o macOS. El paquete integral en validación es **Windows x64**;
el desarrollo desde fuente tiene requisitos distintos.

## Entregas y límites

Las funciones de esta matriz están implementadas e incluidas en QA17, salvo
las ausencias indicadas. Cada informe conserva su corte y evidencia; incluir
código en el paquete no acredita todas las interacciones, cuentas o dispositivos
reales. Los cambios de fuente posteriores al congelado se describen arriba.

| Área | Qué existe | Qué sigue pendiente |
| --- | --- | --- |
| A08.1–A08.8 | Descriptor, perfil mutable, web local, runtimes, extras aislados, asistente, staging y actualizaciones conjuntas. | Su aceptación previa no cubre automáticamente cambios posteriores. |
| A08.9 / QA16 | Un candidato 0.9.2, hashes y contenido verificados; backend de instalar/reparar/desinstalar 12/12. | QA manual instalada según el informe QA16; las comprobaciones del backend no cubren la UI NSIS. No se repitió un ciclo con dos candidatos nuevos. |
| F01 | Modelo por conversación y cambio separado del predeterminado de Hermes. | QA instalada y proveedor real según el candidato; la selección visible no acredita una respuesta del modelo. |
| F02/F03 | Modelo de visión, controles de cámara y foto a petición con destino autorizado. | QA con cámara física/proveedores. No confundir señales locales con análisis remoto de una foto. |
| F04–F07 | Reglas por inactividad de Iris, ejecutor, historial, Bandeja y aviso visual al volver. | C01/C02 están corregidos en el código empaquetado; comprobar su integración instalada según QA16. F08 añade una fuente Windows separada y opcional; no se confunde con ausencia física. |
| F08a/F08b | Puente Win32 en Core con consentimiento por perfil; resumen de resultados por voz, global y por tarea, con recibos durables. Implementados en fuente y exportación web. | QA instalada de minimizado, bloqueo, suspensión y audio real. Focus de Iris silencia la voz; la señal de Windows no garantiza detectar todas las variantes de Focus/No molestar. No está en `qa11`. |
| F09.1–F09.5 | Registro, lifecycle, plantillas, acciones y guía de contribuciones en fuente; revisión de conjunto y exportación realizadas. | Integración instalada F09.6 sobre QA16. Sin catálogo remoto; Store/SDK 2 instala paquetes locales por su contrato externo. La allowlist de servicios Python importados en Core permanece vacía. |
| F10 | Conexiones Google de lectura, varias cuentas, caché, renovación y revocación; QA aislada previa con dos cuentas autorizadas. | F10.5: QA del paquete instalado. El JSON personal se importa o pega, se valida y se protege con DPAPI por perfil; esta UI nueva no está en `qa11`. Cliente compartido oficial desactivado. |
| F11 | Calendario Día/Semana/Mes/Agenda y recordatorios visuales opcionales a −60 minutos/inicio, sobre la caché F10. Revisión de conjunto y exportación realizadas. | Integración y QA instalada. No está en `qa11`; no escribe eventos de Google. F15 prepara notas por consentimiento separado. |
| F12a/F12b | Personalización del escritorio por perfil y widget Calendario declarado en el JSON de la app. Comparten estado y lecturas con F11; fuente y exportación web implementadas. | QA instalada de QA16; no está en `qa11`. Tamaño compacto; Player amplía el catálogo y el inicio nuevo amplía el orden lógico sin borrar F12. |
| F13 | App Correo en fuente, basada en el mockup: varias cuentas, carpetas, búsqueda local, filtros, paginación y lectura del cuerpo en texto al seleccionar. HTML convertido a texto, sin imágenes remotas ni marcado leído. | QA instalada de QA16. El cuerpo necesita conexión; no hay apertura de adjuntos ni escritura en Gmail. Seleccionar el cuerpo no llama a IA; F14 tiene otro consentimiento. No está en `qa11`. |
| F14 | Relevancia opcional de correos nuevos con consentimiento propio, modelo/criterios, presupuesto, resultados, corrección local y fuente Gmail en Bandeja. | QA instalada de QA16; validación automatizada del código y la web completada. No procesa historial ni usa herramientas; abrir Correo no la activa. |
| F15 | Preparación manual de reuniones en fuente: fuentes revisadas, consentimiento por petición, modelo propio, notas persistentes y origen separado en Bandeja. | QA instalada de QA16; cierre de validación en el informe (informe interno no distribuido). Sin correo relacionado automático, herramientas ni escritura Google. |
| F16 | Transcripción provisional de voz manual y Wake en fuente, sobre el `stt.streaming` nativo. Opción explícita por perfil y un único envío final al chat. | QA instalada de QA16 con micrófono/proveedor reales. El directo depende del STT; sin soporte o ante fallo se conserva el recorrido final de Hermes. Informe (informe interno no distribuido). |
| Inicio y aplicaciones | Cajón con búsqueda, accesos removibles y ordenados junto a widgets, preferencias por perfil y administrador para mostrar/ocultar iconos. Fuente y exportación por cortes; informe (informe interno no distribuido). | QA instalada de QA16. Sin desactivación/restablecimiento; F17 añade paquetes de interfaz y widgets; SDK 2 amplía el contrato con Python/skills desde QA15, también en QA16. |
| F17 Iris Store sencilla | Instalar apps de interfaz (y sus widgets) desde un .zip o una carpeta, con las reglas del build, revisión heurística y aviso de acceso completo. Catálogo en vivo para instalar, actualizar y desinstalar, con correcciones L01/L02/L03. [Guía](guides/iris-store.md) · informe (informe interno no distribuido). | Incluida en QA16; QA instalada según su informe; el caso L03 de reinstalación durante un guardado antiguo está corregido y validado en fuente. Sin sandbox de Windows, revisión por IA ni catálogo remoto. SDK 2 añade Python/skills desde QA15, también en QA16. |
| Store SDK 2, incluido en QA15/QA16 | Python externo, acciones y skill opcional, lifecycle por instalación/perfil y kit de creación ampliado. Informe (informe interno no distribuido). | Contrato/lifecycle y worker empaquetado comprobados según los informes de QA15/QA16. QA instalada/conversación real pendientes. Stdlib y módulos propios, sin dependencias descargadas ni sandbox de Windows. |
| Menús de Inicio y galería | Sin tres puntos por app: clic derecho, pulsación prolongada y teclado; menú del espacio vacío, Personalizar y Listo. Previews pasivas opcionales desde app.json; informe (informe interno no distribuido). | Fuente/exportación verificadas por cortes; QA instalada de QA16 y táctil real pendientes, sección 26. |
| Chat y Theme Studio | Chat explícito en el dock, misma conversación y borrador; Theme Studio responde al ancho de su ventana. Se evita reducir ventanas al pasar al chat; informe (informe interno no distribuido). | Fuente/exportación comprobadas; QA Windows de QA16 pendiente, sección 27. |
| Habitación y accesibilidad | Centro de control, vistas y estados en español; los controles ocultos de la habitación quedan inertes y fuera del árbol accesible al abrir OS, con restauración de foco y borrador al volver. Informe (informe interno no distribuido). | Fuente/exportación comprobadas; lector de pantalla y QA Windows de QA16 pendientes, sección 28. |
| Comodidad y tipografía | Calendario compacto, arrastre, navegación y fuentes (informe interno no distribuido) implementados en fuente y exportación web. WOFF2 locales por perfil con preview y respaldo. | QA instalada de QA16. Sin coordenadas libres, fuentes TTF/OTF ni temas con archivos portables. |
| Claridad del escritorio | Cabecera de 64 px con estado real en español, nombres visibles coherentes, Theme Studio traducido y Panel con dock accesible; informe (informe interno no distribuido). | Fuente/exportación. QA instalada de QA16. Conserva preferencias e Inicio existente; sin widget «Hoy» ni rediseño del Command Center. |
| Player: widget y ejemplo comunitario | Widget de controles, pista y progreso sobre el mismo Player de la app/skill, sin cuentas externas. [Ejemplo](../apps/player/README.md) e informe (informe interno no distribuido). | QA instalada de QA16. F17 incorpora la Store sencilla en fuente; el SDK comunitario de conexiones sigue pendiente. |
| Theme Studio: ampliación | Vista previa pasiva del Inicio (informe interno no distribuido), [transiciones CSS/JSON](guides/theme-transitions.md) e [importación segura de temas v1](guides/theme-studio.md) en fuente/exportación. El JSON no carga ni transporta imágenes externas. | QA Windows de QA16. Las fuentes WOFF2 ya tienen biblioteca por perfil en Core; temas completos e imágenes portables siguen pendientes en el [plan](iris_theme_studio_extension_plan_2026-10-18.md); no son movimientos del avatar. |

## Qué usar para cada tarea

- Usuario: [primeros pasos](guides/getting-started.md),
  [manual integrado](../wizard/ui/help.html),
  [funciones](guides/features.md), [modelos](guides/models.md),
  [automatizaciones](guides/automations.md), [Google](guides/google-connections.md)
  y [Calendario](guides/calendar.md), [Correo](guides/mail.md).
- Desarrollo: [entorno y pruebas](guides/development.md),
  [exportación web](guides/local-web-build.md), [compilación personal](guides/build-from-source.md),
  [repositorios de publicación](guides/publication-repositories.md) y [SDK 2](guides/community-apps-python.md).
- QA: informe y guía QA16 (informe interno no distribuido) y
  LISTA-QA (referencia no distribuida: `desktop/installer/qa/LISTA-QA.md`). Anotar candidato, versión y
  resultado; una omisión no cuenta como aprobada.
- Retomar con contexto: NeuroMapa indexa `docs/` y las fichas compartidas. Leer
  la fuente de cada resultado; las fichas y sus hashes no certifican releases.
- Historial: las auditorías y los diseños fechados conservan su corte original.
  Sus conteos de pruebas y frases como «siguiente entrega» no describen por sí
  solos el checkout de hoy.

## Evidencia por área y cortes históricos

- Plan coordinador (informe interno no distribuido).
- Wizard: proveedor, resumen final y cambios posteriores a `qa11` (informe interno no distribuido).
- Reestructuración del asistente: histórico (informe interno no distribuido).
  QA16 conserva la apariencia antes de la bienvenida y cinco pasos de configuración;
  las rondas anteriores mantienen sus recorridos y evidencias propios.
- KittenTTS: propuesta futura diferida por el usuario (informe interno no distribuido), evaluado después sin integrarlo; ver las mediciones del 2026-10-21 en ese informe.
- F09: revisión y correcciones (informe interno no distribuido).
- F10: revisión (informe interno no distribuido),
  QA real aislada (informe interno no distribuido) e
  importación del cliente personal (informe interno no distribuido).
- F11: implementación y límites (informe interno no distribuido).
- Revisión conjunta y paquetes F09–F11 (informe interno no distribuido).
- F08: inactividad Windows y entrega por voz (informe interno no distribuido).
- F12: escritorio y widgets (informe interno no distribuido) y
  [guía para usuarios y colaboradores](guides/widgets.md).
- F13: Correo de lectura (informe interno no distribuido) y
  [guía de la bandeja y sus límites](guides/mail.md).

No se ejecutan pruebas de hardware o cuentas por actualizar esta documentación.
Los resultados válidos son los de cada informe y sus artefactos concretos.

## Historial de candidatos y registros anteriores

Los bloques siguientes conservan el texto registrado para cada corte. Sus
pendientes y expresiones «último», «en fuente» o «próximo candidato» corresponden
a esa fecha; para el estado vigente usa las secciones anteriores y el informe QA16.

## Candidato anterior QA15 del 2026-10-24

Instaladores **0.9.2 principal (f09dba9d)** y **0.9.3 solo QA (f005044a)** compilados y entregados en carpetas nuevas, con hashes recalculados. Incluyen SDK 2, Bash privado, las cuatro operaciones Google de lectura con permiso separado y los arreglos de diagnóstico/voz de QA2. Companion 0.1.3 reconstruido; Edge-TTS 7.3.1 en ambos Python. Las fuentes correspondientes del Bash acompañan a cada instalador.

Pasan **4.954 Python (11 omitidas)**, **1.035 JS**, **173 de apps**, el escritorio offline, **12/12** comprobaciones del backend de instalación y la verificación independiente de ambos EXE/payloads. Los dos candidatos pasan también el smoke empaquetado y el lifecycle/worker SDK 2 con emojis cerca del límite. **QA manual instalada pendiente; W01 no bloqueante. Sin publicación ni commit.**

Instaladores, hashes y alcance (informe interno no distribuido) · Guía de QA15 (informe interno no distribuido). Si la VM quedó en 0.9.3 de QA14, volver primero a 0.9.2 desde Iris o usar una VM limpia. Ambos candidatos nuevos incluyen SDK 2; los anteriores no aceptan su almacén formato 2.

## Candidato anterior QA14 del 2026-10-23

Instaladores **0.9.2 principal (`ede42581`)** y **0.9.3 solo QA (`c28d8da7`)** compilados y entregados en carpetas nuevas, con hashes recalculados. Incluyen los cambios posteriores a QA13: catálogo después de autenticar, selección de IA en Correo/Calendario, ajustes del asistente, cajón, transiciones y Marketplace. Companion 0.1.3 reconstruido; Edge-TTS 7.3.1 en ambos Python.

Pasan **4.901 Python (11 omitidas)**, **1.026 JS**, **171 de apps**, el escritorio offline, **12/12** comprobaciones del backend instalado y las verificaciones independientes de ambos EXE/payloads. El sondeo nuevo también funciona con los Python privados empaquetados y red bloqueada. **QA manual recibida del usuario; los hallazgos posteriores de QA2 se corrigieron e incluyeron en QA15, pendiente de repetir la QA instalada. Sin publicación ni commit.**

Las cinco capturas de «qa 2» motivaron una revisión: el terminal de Hermes depende
de Bash externo y el catálogo de acciones aún no enlaza el chat con Correo,
Calendario o Conexiones. Se recogen también el diagnóstico contradictorio, mejoras
de voz/guardado y la búsqueda de Windows pendiente de reproducir.
Revisión, evidencia y prioridades (informe interno no distribuido).
No se asigna aprobación a casos individuales de cuentas/hardware o del instalador
que no estén acreditados por su evidencia correspondiente.

Instaladores, hashes y alcance (informe interno no distribuido) · Guía de QA14 (informe interno no distribuido). Si la VM quedó en 0.9.3 de QA13, usar Volver a 0.9.2 desde Iris o una VM limpia antes de instalar el principal nuevo.

## Iris Store SDK 2 — incluido en QA15

Autorizado e implementado el **2026-10-23**: paquetes con Python en proceso propio, acciones y skills opcionales para Hermes; guía/skill de creación y Contador externo completo. Contrato, lifecycle, navegador y Python privados comprobados. La suite final del candidato pasa sin fallos; la validación con el worker real de la app externa Pendientes pasa en ambos payloads de QA15.
Implementación, evidencia y límites (informe interno no distribuido) · [Guía del SDK 2](guides/community-apps-python.md).
**No está en los EXE de QA14.** La conversación real/QA instalada del candidato nuevo sigue pendiente. El almacén es formato 2 y QA14 lo rechaza sin modificarlo; usar perfiles de QA separados al comprobar las rutas de actualización/reversión.

## Candidato anterior QA13 del 2026-10-22

Se construyeron y verificaron los instaladores 0.9.2 y 0.9.3 (solo QA), con la web
corregida y Edge-TTS 7.3.1 en los dos Python privados. Pasan la comprobación
independiente de ambos y 12/12 pruebas del backend de instalar/reparar/desinstalar.
**QA manual parcial recibida de la VM; quedan comprobaciones instaladas y Nous real; no publicado.**
Instaladores, hashes y alcance (informe interno no distribuido).

Las diez capturas del usuario motivaron cambios posteriores en fuente: catálogo
de Nous más claro, selección de IA en Correo/Calendario, textos del asistente,
cajón, importación de transiciones y Marketplace. **No están en los EXE de QA13.**
Hallazgos, correcciones y límites (informe interno no distribuido).

También en fuente posterior a QA13: proveedor → acceso → modelos → guardar. Consultas separadas para OpenRouter y OpenCode, y verificación fresca del plan de Nous. 1.026 pruebas JS y 238 Python relacionadas aprobadas; consulta real de metadatos de OpenCode, demás cuentas simuladas. **No incluido en los EXE QA13.** Detalle y límites (informe interno no distribuido).

## Candidato anterior QA12 del 2026-10-21

Se construyeron los instaladores 0.9.2 y 0.9.3 (esta última solo para QA),
con Companion 0.1.3 reconstruido y exportación web-11. Pasan las suites
completas del producto, la verificación independiente de ambos paquetes y
12 comprobaciones del backend empaquetado de instalación/reparación/desinstalación.
**La QA manual instalada y la actualización/reversión siguen pendientes; no se publicó nada.**
Artefactos, hashes, alcance y resultados (informe interno no distribuido).

La QA manual en la VM (2026-10-22) encontró el dock ausente, Edge-TTS rechazado (HTTP 403),
modelos recortados o fuera del plan y varias mejoras de flujo. Estas correcciones están incluidas en **QA13**; el contrato web se regeneró y sigue pendiente repetir la QA instalada.
Correcciones, pruebas y pendientes (informe interno no distribuido).

## Correcciones posteriores a QA12 — 2026-10-22

Edge-TTS de Hermes se actualizó y probó de forma aislada; contrato web y manual
regenerados, suites sin los errores anteriores de inventario y smoke del escritorio
con preferencias reales en un perfil desechable. Cierre de pendientes (informe interno no distribuido).
Estas correcciones ya están incluidas en el candidato QA13; la QA instalada sigue pendiente.
Nous con cuenta real y QA del nuevo instalador continúan pendientes.

### Correcciones de QA 2 — 2026-10-23

En fuente: consola privada para Windows (sin Git global), lecturas de Conexiones/Correo/Calendario por el chat con permiso google.read, retirada de la tarjeta contradictoria y guardado parcial/controles de voz. La web local está exportada. La notificación del acceso de Inicio se añadió; la búsqueda sigue sin confirmar. El usuario la considera un detalle menor y no bloqueante porque Iris sí aparece en Inicio, Todas las aplicaciones y Configuración. No se generó otro instalador. Detalle y límites: informe de QA 2 (informe interno no distribuido).

[← Índice](README.md)
