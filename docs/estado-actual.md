# Estado actual de IRIS OS

Revisión documental: **2026-11-12**. Esta matriz distingue fuente, candidato de
QA y publicación. No sustituye los criterios del
plan A08/F01–F16 (informe interno no distribuido) ni da por
aprobada una instalación manual por haber pasado pruebas automáticas.

## Iris OS 0.9.3 beta · QA24

Fuente del candidato **02168be7** para Windows x64, con **Companion 0.1.9** y
**Hermes 0.21.6**. Ajusta el encuadre de Iris en el borde del escritorio,
corrige el giro del pie al descansar boca abajo y el pelo PMX al acostarse boca
arriba. También corrige el cierre de ventanas de apps y la identificación de
procesos guardados que ya pertenecen a otro programa.

Se verificaron el paquete y la descarga desde 0.9.2 en un canal firmado local.
La prueba del instalador reconoció QA23 como origen de la actualización.
La activación y la revisión visual en WebView2 instalado siguen en QA.
[SOURCE.json](../SOURCE.json) registra la procedencia y las huellas de QA24.

## Iris OS 0.9.3 beta · QA23

Fuente del candidato **734ff308** para Windows x64, con **Companion 0.1.9** y
**Hermes 0.21.6**. Incluye 95 clips de fábrica, los giros corregidos de la cama,
la importación de avatares PMX y el cambio de proveedor durante una respuesta.

Pasaron 5.400 pruebas de Python (11 omitidas), 1.407 de JavaScript, 176 de apps y
12 del flujo de instalación, reparación y desinstalación en una copia aislada.
La QA manual sigue pendiente con cuentas reales, voz y PMX en WebView2 instalado.
[SOURCE.json](../SOURCE.json) registra la procedencia y las huellas del candidato.

## Iris OS 0.9.2 beta · QA22

**QA22** es el corte Windows x64 **Iris 0.9.2**, ID **ddd205bd**, con
**Companion 0.1.8**. Su instalador sin firma Authenticode está disponible en
la [beta pública 0.9.2 de GitHub](https://github.com/cristian6513/Project_Iris/releases/tag/v0.9.2-beta).
Informe de QA22 (informe interno no distribuido) ·
Guía de QA (informe interno no distribuido).

Incluye las versiones corregidas de los 18 gestos/reposos, sus expresiones
faciales, la salida suave al detener un clip y las revisiones de lectura/tablet
de pie y ventana posteriores a QA21. Conserva **59 clips de fábrica** y el
avatar **IRIS MToon V12**. La habitación, Vida propia y el teletransporte se
adaptan al estado de conversación y a las preferencias de movimiento.

**Guardar voz** ya se aplica a la próxima respuesta sin cerrar Iris ni detener
Core. Una configuración de voz dañada conserva la voz actual; al sustituir un
modelo local se libera primero el anterior y cancelar no deja el motor retirado
sin cerrar. La corrección importa porque cerrar la ventana no reinicia Core.
Revisión y cierre (informe interno no distribuido).

Pasaron **5.355 pruebas Python** (11 omitidas), **1.355 JavaScript**, **176 de
apps**, los nueve grupos de controles empaquetados y **12/12** del flujo aislado
de instalar/reparar/desinstalar. También se verificaron los iconos, los hashes
y la copia entregada. La web tiene 423 recursos y pasó sin conexión.
**La QA manual de QA22 sigue pendiente de registrar**, especialmente voz real,
WebView2 instalado, Vida propia con tiempos normales, cambios de VRM y Companion.
Las pruebas de motores locales usan trabajadores simulados; no acreditan su
memoria o velocidad reales, ni una clave válida de Fish.

La beta publicada conserva la huella del instalador de QA22. El
[registro de procedencia](https://github.com/cristian6513/Project_Iris/blob/v0.9.2-beta/SOURCE.json)
documenta la fuente y los artefactos.

## Fuente posterior a QA22

La corrección de delegación del 2026-11-06 prepara las herramientas efectivas
del perfil que recibirá cada turno local de Iris. Cubre perfiles secundarios y
agregados all/*, conserva las otras plataformas y verifica de nuevo si cambia
la configuración. Si no puede comprobarlo, el turno no se envía y muestra el
motivo. Implementación y pruebas (informe interno no distribuido).
Esta corrección aún no está en los bytes de QA22; necesita otro instalador y
una prueba con el proveedor real. No cambia el nivel de razonamiento ni la voz.

## Candidatos anteriores

Todos los informes siguientes describen su propio corte. Sus expresiones
«en fuente», «próximo instalador» y «pendiente» no son el estado de QA22.

| Candidato | Corte y referencia |
| --- | --- |
| QA21 (informe interno no distribuido) | Iris 0.9.2, ID 63df6b3d, Companion 0.1.7. Añadió los tres clips de ventana y correcciones de voz/bloom. El cambio de voz en vivo y los gestos revisados de QA22 no están en sus bytes. |
| QA20 (informe interno no distribuido) | Iris 0.9.2, Companion 0.1.6. Vida propia, sala/Companion, teletransporte y 56 clips de fábrica; anterior a la entrega de ventana. |
| QA19 (informe interno no distribuido) | Iris 0.9.2, ID b5d19b16, Companion 0.1.5. Acercamiento del avatar, iconos y presentación de respuestas. Anterior a los muebles y rutinas nuevos. |
| QA18 (informe interno no distribuido) | Iris 0.9.2, ID 2d740555, Companion 0.1.4. Ampliaciones SDK 2 y avatar anterior MToon v11. |
| QA17 (informe interno no distribuido) | Iris 0.9.2, ID 53a167c8. Certificados de Nous, capturas/FPS, peticiones visuales y tareas en segundo plano. |
| QA16 (informe interno no distribuido) | Iris 0.9.2, ID ec7291bd. Sus errores y confirmaciones de VM conservan la evidencia de ese candidato, no del nuevo. |

## Pendientes vigentes

- Registrar la QA manual del candidato exacto. Los resultados automáticos no
  cierran por sí solos A08.9, F09.6 ni F10.5.
- Comprobar voz/proveedor reales, WebView2, sensores y superficies nativas según
  la guía. La revisión de QA22 no usó claves reales ni descargó motores locales.
- Ejecutar de principio a fin la entrada pública **COMPILAR.cmd** en un entorno
  preparado: QA22 se construyó con la cadena de candidatos revisada, no con esa
  entrada. No se acredita reconstrucción byte por byte de un EXE firmado.
- Actualizar y verificar las [copias limpias de publicación](guides/publication-repositories.md)
  antes de subirlas. Prepararlas localmente no crea remotos ni publica código.
- El catálogo remoto de Store y el SDK comunitario de conexiones siguen fuera
  del alcance implementado. Cuentas/suscripciones/créditos Iris y Android son
  [ideas diferidas](iris_future_services_notes_2026-10-26.md).

## Versiones y distribución

| Referencia | Estado comprobable |
| --- | --- |
| Código Python del checkout | core/version.py declara 1.0.0. No significa que exista un instalador publicado de esa versión. |
| Beta de GitHub | QA22, Iris 0.9.2, ID ddd205bd y Companion 0.1.8. Instalador x64 sin firma Authenticode. |
| Motor fijado Windows x64 | Hermes 0.21.6, revisión 818c13be1dc4fd28987e1e881a9408224afd4535. |
| Python privado | Iris 3.11.16 y Hermes 3.14.8, separados; pins y hashes en scripts/runtime_sources.json. |
| Desktop Companion | Fuente, manifiesto y par nativo 0.1.8, incluido en QA22. Recargar Face no actualiza un ejecutable anterior. |
| Instalador y canal | A08.9 permanece abierto. Firmar el manifiesto de actualización no equivale a firma Authenticode del EXE. |
| Microsoft Store | Envío en borrador, ficha en español y prototipo MSIX x64 `0.9.2.0` comprobado con MakeAppx. Pendientes Companion integrado, canal de actualización y prueba del MSIX firmado en Windows limpio. [Plan y evidencia](../desktop/msix/README.md). |

El paquete integral validado es **Windows x64**. Las suites de fuente o Linux no
acreditan instaladores de Iris para ARM64, Linux, macOS o Android.

## Entregas y límites

Las funciones de esta matriz están implementadas e incluidas en QA22, salvo
las ausencias indicadas. Cada informe conserva su corte y evidencia; incluir
código en el paquete no acredita todas las interacciones, cuentas o dispositivos
reales. Los informes históricos conservan su fecha; esta matriz no acredita pruebas manuales nuevas.

| Área | Qué existe | Qué sigue pendiente |
| --- | --- | --- |
| A08.1–A08.8 | Descriptor, perfil mutable, web local, runtimes, extras aislados, asistente, staging y actualizaciones conjuntas. | Su aceptación previa no cubre automáticamente cambios posteriores. |
| A08.9 / QA22 | Candidato 0.9.2, ID ddd205bd, Companion 0.1.8; hashes y contenido verificados; backend de instalar/reparar/desinstalar 12/12. | QA manual instalada según el informe QA22; las comprobaciones del backend no cubren la UI NSIS. No se repitió un ciclo con dos candidatos nuevos. |
| F01 | Modelo por conversación y cambio separado del predeterminado de Hermes. | QA instalada y proveedor real según el candidato; la selección visible no acredita una respuesta del modelo. |
| F02/F03 | Modelo de visión, controles de cámara y foto a petición con destino autorizado. | QA con cámara física/proveedores. No confundir señales locales con análisis remoto de una foto. |
| F04–F07 | Reglas por inactividad de Iris, ejecutor, historial, Bandeja y aviso visual al volver. | C01/C02 están corregidos en el código empaquetado; comprobar su integración instalada según QA22. F08 añade una fuente Windows separada y opcional; no se confunde con ausencia física. |
| F08a/F08b | Puente Win32 en Core con consentimiento por perfil; resumen de resultados por voz, global y por tarea, con recibos durables. Implementados en fuente y exportación web. | QA instalada de minimizado, bloqueo, suspensión y audio real. Focus de Iris silencia la voz; la señal de Windows no garantiza detectar todas las variantes de Focus/No molestar. No está en `qa11`. |
| F09.1–F09.5 | Registro, lifecycle, plantillas, acciones y guía de contribuciones en fuente; revisión de conjunto y exportación realizadas. | Integración instalada F09.6 sobre QA22. Sin catálogo remoto; Store/SDK 2 instala paquetes locales por su contrato externo. La allowlist de servicios Python importados en Core permanece vacía. |
| F10 | Conexiones Google de lectura, varias cuentas, caché, renovación y revocación; QA aislada previa con dos cuentas autorizadas. | F10.5: QA del paquete instalado. El JSON personal se importa o pega, se valida y se protege con DPAPI por perfil; esta UI nueva no está en `qa11`. Cliente compartido oficial desactivado. |
| F11 | Calendario Día/Semana/Mes/Agenda y recordatorios visuales opcionales a −60 minutos/inicio, sobre la caché F10. Revisión de conjunto y exportación realizadas. | Integración y QA instalada. No está en `qa11`; no escribe eventos de Google. F15 prepara notas por consentimiento separado. |
| F12a/F12b | Personalización del escritorio por perfil y widget Calendario declarado en el JSON de la app. Comparten estado y lecturas con F11; fuente y exportación web implementadas. | QA instalada de QA22; no está en `qa11`. Tamaño compacto; Player amplía el catálogo y el inicio nuevo amplía el orden lógico sin borrar F12. |
| F13 | App Correo en fuente, basada en el mockup: varias cuentas, carpetas, búsqueda local, filtros, paginación y lectura del cuerpo en texto al seleccionar. HTML convertido a texto, sin imágenes remotas ni marcado leído. | QA instalada de QA22. El cuerpo necesita conexión; no hay apertura de adjuntos ni escritura en Gmail. Seleccionar el cuerpo no llama a IA; F14 tiene otro consentimiento. No está en `qa11`. |
| F14 | Relevancia opcional de correos nuevos con consentimiento propio, modelo/criterios, presupuesto, resultados, corrección local y fuente Gmail en Bandeja. | QA instalada de QA22; validación automatizada del código y la web completada. No procesa historial ni usa herramientas; abrir Correo no la activa. |
| F15 | Preparación manual de reuniones en fuente: fuentes revisadas, consentimiento por petición, modelo propio, notas persistentes y origen separado en Bandeja. | QA instalada de QA22; cierre de validación en el informe (informe interno no distribuido). Sin correo relacionado automático, herramientas ni escritura Google. |
| F16 | Transcripción provisional de voz manual y Wake en fuente, sobre el `stt.streaming` nativo. Opción explícita por perfil y un único envío final al chat. | QA instalada de QA22 con micrófono/proveedor reales. El directo depende del STT; sin soporte o ante fallo se conserva el recorrido final de Hermes. Informe (informe interno no distribuido). |
| Inicio y aplicaciones | Cajón con búsqueda, accesos removibles y ordenados junto a widgets, preferencias por perfil y administrador para mostrar/ocultar iconos. Fuente y exportación por cortes; informe (informe interno no distribuido). | QA instalada de QA22. Sin desactivación/restablecimiento; F17 añade paquetes de interfaz y widgets; SDK 2 amplía el contrato con Python/skills desde QA15, también en QA22. |
| F17 Iris Store sencilla | Instalar apps de interfaz (y sus widgets) desde un .zip o una carpeta, con las reglas del build, revisión heurística y aviso de acceso completo. Catálogo en vivo para instalar, actualizar y desinstalar, con correcciones L01/L02/L03. [Guía](guides/iris-store.md) · informe (informe interno no distribuido). | Incluida en QA22; QA instalada según su informe; el caso L03 de reinstalación durante un guardado antiguo está corregido y validado en fuente. Sin sandbox de Windows, revisión por IA ni catálogo remoto. SDK 2 añade Python/skills desde QA15, también en QA22. |
| Store SDK 2, incluido en QA22 | Python externo, acciones y skill opcional, lifecycle por instalación/perfil y kit de creación ampliado. Informe (informe interno no distribuido). | Contrato/lifecycle y worker empaquetado comprobados según los informes de SDK 2 y QA22. QA instalada/conversación real pendientes. Stdlib y módulos propios, sin dependencias descargadas ni sandbox de Windows. |
| Menús de Inicio y galería | Sin tres puntos por app: clic derecho, pulsación prolongada y teclado; menú del espacio vacío, Personalizar y Listo. Previews pasivas opcionales desde app.json; informe (informe interno no distribuido). | Fuente/exportación verificadas por cortes; QA instalada de QA22 y táctil real pendientes, sección 26. |
| Chat y Theme Studio | Chat explícito en el dock, misma conversación y borrador; Theme Studio responde al ancho de su ventana. Se evita reducir ventanas al pasar al chat; informe (informe interno no distribuido). | Fuente/exportación comprobadas; QA Windows de QA22 pendiente, sección 27. |
| Habitación y accesibilidad | Centro de control, vistas y estados en español; los controles ocultos de la habitación quedan inertes y fuera del árbol accesible al abrir OS, con restauración de foco y borrador al volver. Informe (informe interno no distribuido). | Fuente/exportación comprobadas; lector de pantalla y QA Windows de QA22 pendientes, sección 28. |
| Comodidad y tipografía | Calendario compacto, arrastre, navegación y fuentes (informe interno no distribuido) implementados en fuente y exportación web. WOFF2 locales por perfil con preview y respaldo. | QA instalada de QA22. Sin coordenadas libres, fuentes TTF/OTF ni temas con archivos portables. |
| Claridad del escritorio | Cabecera de 64 px con estado real en español, nombres visibles coherentes, Theme Studio traducido y Panel con dock accesible; informe (informe interno no distribuido). | Fuente/exportación. QA instalada de QA22. Conserva preferencias e Inicio existente; sin widget «Hoy» ni rediseño del Command Center. |
| Player: widget y ejemplo comunitario | Widget de controles, pista y progreso sobre el mismo Player de la app/skill, sin cuentas externas. [Ejemplo](../apps/player/README.md) e informe (informe interno no distribuido). | QA instalada de QA22. F17 incorpora la Store sencilla en fuente; el SDK comunitario de conexiones sigue pendiente. |
| Theme Studio: ampliación | Vista previa pasiva del Inicio (informe interno no distribuido), [transiciones CSS/JSON](guides/theme-transitions.md) e [importación segura de temas v1](guides/theme-studio.md) en fuente/exportación. El JSON no carga ni transporta imágenes externas. | QA Windows de QA22. Las fuentes WOFF2 ya tienen biblioteca por perfil en Core; temas completos e imágenes portables siguen pendientes en el [plan](iris_theme_studio_extension_plan_2026-10-18.md); no son movimientos del avatar. |
| Voz guardada en vivo | El proveedor, la voz y la clave guardados se aplican a la siguiente frase sin reiniciar Core. Configuración de voz dañada conserva el motor actual; se libera el anterior antes de cargar otro y se limpia al cancelar. | QA22 contiene el arreglo y sus controles empaquetados; faltan voz y proveedor reales. [Configuración](guides/configuration.md#guardar-y-cambiar-la-voz). |
| Habitación, animaciones y Vida propia | Avatar MToon V12 de fábrica; 59 clips aprobados, muebles nuevos, acciones de libro/tablet/PC/ventana, rutinas y teletransporte condicionado. | QA22 comprobado con recursos empaquetados y con IRIS/Cantarella; voz real, tiempos normales y otros VRM requieren QA. [Habitación](guides/room.md) y [animaciones](guides/iris-animations.md). |
| Rendimiento | FPS 30/60/sin límite y calidad Ahorro/Equilibrado/Alta. Ahorro limita las texturas del avatar al cargarlo. | Los ajustes no garantizan FPS ni certifican el equipo mínimo. [Configuración](guides/configuration.md#rendimiento-del-avatar-y-la-habitación). |

## Qué usar para cada tarea

- Usuario: [primeros pasos](guides/getting-started.md),
  [manual integrado](../wizard/ui/help.html),
  [funciones](guides/features.md), [modelos](guides/models.md),
  [automatizaciones](guides/automations.md), [Google](guides/google-connections.md)
  y [Calendario](guides/calendar.md), [Correo](guides/mail.md).
- Desarrollo: [entorno y pruebas](guides/development.md),
  [exportación web](guides/local-web-build.md), [compilación personal](guides/build-from-source.md),
  [repositorios de publicación](guides/publication-repositories.md) y [SDK 2](guides/community-apps-python.md).
- QA: informe y guía QA22 (informe interno no distribuido) y
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
  QA22 conserva la apariencia antes de la bienvenida y cinco pasos de configuración;
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
a esa fecha; para el estado vigente usa las secciones anteriores y el informe QA22.

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
