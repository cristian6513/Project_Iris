# Plan para subir la puntuación de la auditoría · Claude · 2026-11-17

**Base:** las dos auditorías del 17 de noviembre, la de Claude y la de Codex. Ambas coinciden en que IRIS OS es una **beta pública temprana para Windows x64** y en que no conviene ampliar la difusión hasta publicar el arreglo de arranque. Codex le da 7,5/10 en conjunto.

**Regla del plan:** las notas suben con **evidencia**, no con funciones nuevas. Casi todo lo que sigue es arreglar, medir, observar y registrar. No hay fechas: el orden es por impacto.

## Dónde estamos y qué movería cada nota

| Área | Claude | Codex | Meta | Qué la sube |
|---|---|---|---|---|
| Estabilidad y preparación | 5 | 7 | 8 | CI en verde sobre el commit exacto de cada candidato, QA manual registrada por caso y arreglo de arranque probado en un PC modesto |
| Instaladores y distribución | 6 | 7 | 8 | Firma Authenticode (o la Store como canal firmado), una ficha por versión y el MSIX probado en Windows limpio |
| Interfaz, UX y accesibilidad | 6 | 7 | 8 | Tres personas no técnicas llegan solas a su primera respuesta, un recorrido completo con Narrador y teclado, y un inicio pensado para el usuario |
| Rendimiento | 6 | 7 | 8 | Medición en tres equipos, incluida una gráfica integrada; requisitos mínimos y valores predeterminados basados en esos datos |
| Calidad y mantenibilidad | 6 | 7 | 8 | Partir los archivos gigantes de forma gradual y automatizar las versiones `?v=N` |
| Apps, Store y SDK | 7 | 7 | 8 | Aviso inequívoco para las apps que ejecutan Python y el recorrido de alguien que recibe una app de un tercero |
| Hermes e IA | 7 | 8 | 8–9 | Conversación con proveedores y cuentas reales, incluidos un inicio de sesión fallido y el cambio de proveedor |
| Seguridad y privacidad | 7 | 7 | 8 | Firma, aviso de Python en la Store y modelo de amenazas de la LAN por escrito |
| Arquitectura | 7 | 8 | 8 | Un identificador de turno que se pueda seguir entre Core, Hermes y la interfaz |
| Avatar y Vida propia | 8 | 9 | 9 | Mantenerlo: pruebas con VRM de terceros y tiempos normales; no añadir más por ahora |

## Fase 0 · Hecho hoy, en fuente y sin compilar

- **Audio bajo demanda:** `core/tts.py` ya no abre el audio al importarse. Era la causa del fallo de la CI de Linux.
- **Detener durante la preparación:** pulsar Detener mientras se abre el audio o se decodifica la frase ya no deja que la frase suene después. Lo encontró Codex. Tiene prueba de regresión, comprobada contra el `stop()` anterior.
- **Dos pruebas de arranque desactualizadas,** puestas al día con `face_bootstrap.js`.
- Detalle en El audio se abre al hablar (informe interno no distribuido).
- **Después, el mismo día, con el visto bueno de Codex:**
  - **Documentación al día:** `estado-actual.md`, `features.md`, los README y siete guías, con QA25 y 95 clips.
  - **Una sola versión por módulo:** había nueve módulos con dos o tres versiones; ahora hay una prueba que lo vigila.
  - **CI en ramas de versión y etiquetas de release.**
  - **Sugerencia de Ahorro a 30 FPS** cuando la habitación se dibuja por software. Solo guarda si la persona acepta.
  - **Aviso de Python en la Store** y **guion para las pruebas con personas** (pasos 5 y 6). Informe (informe interno no distribuido).
  - **UX:** el cajón empieza por lo de uso diario, la barra lateral queda sin Panel y se corrigieron textos en inglés y tildes. Se corrigió además el catálogo de apps que el navegador servía viejo. Informe (informe interno no distribuido).
  - Suites: Python 5.425 y JavaScript 1.589 aprobadas, sin fallos.
  - Documentación y versiones (informe interno no distribuido) · CI y Ahorro (informe interno no distribuido).

## Fase 1 · Próximo candidato: que lo publicado sea fiable

1. **Arreglo de arranque en el EXE.** *(Codex compila; el usuario prueba)*
   - Abrir en frío en un PC modesto.
   - Ocultar Iris en la bandeja mientras carga y volver.
   - Forzar un fallo y usar «Volver a comprobar».
   - Es el único fallo publicado que puede impedir entrar.
2. **CI del candidato exacto.** *(Codex)*
   - Añadir las ramas de versión al disparador `push` de `.github/workflows/ci.yml`, o abrir un PR por candidato.
   - No publicar un candidato sin una ejecución verde de su mismo commit, y enlazarla en las notas de la release.
   - Confirmar en GitHub que el job de Linux ya pasa con el cambio de audio.
   - Regenerar `scripts/release_web_assets.json` con la fuente final del candidato.
     - Hoy la compilación web no coincide con ese contrato, así que la CI fallaría antes de las pruebas Python.
     - Las tres pruebas de distribución dependen de él: la suite solo queda en verde con el contrato al día y `IRIS_WEB_ASSETS_ROOT` apuntando a la web compilada.
3. **Documentación verdadera.** *(Codex o Claude)*
   - `docs/estado-actual.md` de Alice todavía dice QA24, mientras la copia publicada ya dice QA25.
   - `docs/guides/features.md:163` dice 59 clips y el paquete trae 95.
   - Corregirlo en Alice, no solo en la exportación.
4. **Ficha por versión.** *(Codex)*
   - Una página corta en cada release: qué incluye el EXE, qué se probó con hardware y cuentas reales, qué está solo en fuente, qué incidencias siguen abiertas y el enlace a la CI.
   - Así cualquiera sabe qué recibió.
5. **Suites completas antes de compilar:** Python, JavaScript y apps, como siempre. Sin editar archivos mientras corren.

## Fase 2 · Evidencia real, casi sin código

6. **Tres personas no técnicas.** *(Usuario; Claude prepara el guion y la hoja de registro)*
   - **Ya hecha: una persona.** El usuario lo contó el 2026-11-17, sin registro escrito. De esa prueba salieron dos cambios, que ya están en QA25:
     - en el asistente, el inicio de sesión con ChatGPT, Nous Portal y Grok aparece primero, y los demás proveedores quedan en opciones adicionales;
     - en las voces, Edge TTS aparece primero, y las demás quedan como opciones avanzadas.
   - **Faltan dos, con la versión que ya tiene esos cambios,** para comprobar que la primera respuesta ahora llega sin ayuda. Conviene anotar lo que pase, aunque sea en pocas líneas.
   - **Guion y hoja de registro:** PRUEBA-PERSONAS.md (referencia no distribuida: `desktop/installer/qa/PRUEBA-PERSONAS.md`). Incluye siete tareas (entre ellas el error de inicio de sesión a propósito y «Abre el correo y vuelve a Inicio»), cómo observar sin ayudar y las preguntas finales.
   - Una con gráfica integrada, otra con micrófono y altavoces reales, y otra que se equivoque al iniciar sesión.
   - Medir el tiempo hasta la primera respuesta, dónde pidieron ayuda y qué no entendieron.
   - Sin explicarles nada.
   - Es la prueba que más mueve UX y estabilidad.
7. **QA manual registrada.** *(Usuario)*
   - Anotar en `desktop/installer/qa/LISTA-QA.md` cada caso del candidato: correcto, falla u omitida y por qué.
   - Hoy la lista es solo una plantilla.
8. **Rendimiento en tres equipos.** *(Claude mide; el usuario cambia de equipo o de gráfica)*
   - El Ryzen 5 5600G del usuario tiene una Vega 7 integrada, que sirve para aproximar el mínimo.
   - **Evidencia que ya hay:** la VM de VirtualBox del usuario (8 GB de RAM, 4 núcleos, 128 MB de vídeo virtual) la ejecutó, aunque no muy fluida.
     - Sin aceleración real, WebGL probablemente se dibuja por software, así que esa VM es el piso: demuestra que arranca y funciona con 8 GB.
     - Medirla con cifras: anotar el renderizador WebGL que reporta WebView2, los FPS en Ahorro con límite de 30 y la RAM en uso.
   - **Mejora de bajo costo a evaluar:** Iris no detecta el dibujo por software, porque `face/js` no lee el renderizador.
     - Cuando WebGL informe SwiftShader, WARP, «Microsoft Basic Render Driver» o llvmpipe, proponer Ahorro y 30 FPS en el primer arranque.
     - El usuario lo podría cambiar después.
   - Medir el tiempo hasta ver la habitación, los FPS en reposo y hablando, la CPU, la RAM y la memoria de GPU, con IRIS y con un PMX pesado, en Ahorro, Equilibrado y Alta.
   - Con eso, fijar requisitos reales y decidir si Ahorro debe elegirse solo en gráficas integradas.
   - WebGPU queda para después de esta comparación.
9. **Proveedores reales.** *(Usuario, con ayuda de Claude)*
   - Iniciar sesión con ChatGPT, Nous o Grok, mantener una conversación de voz completa, cambiar de proveedor a mitad de una respuesta, y forzar un error de cuenta o de saldo.
   - Comprobar que cada error dice qué hacer.

## Fase 3 · Confianza para ampliar la difusión

10. **Aviso de Python en la Store.** *(Codex o Claude)*
    - Antes de confirmar, distinguir visualmente «solo interfaz» de «ejecuta Python con tus permisos de Windows».
    - Mostrar qué declara la app y dejar claro que no hay aislamiento.
    - Después, probar el recorrido de alguien que recibe un .zip de un tercero.
11. **Canal firmado: Microsoft Store.** *(Decisión del usuario, 2026-11-17: sin certificado de pago)*
    - La Store firma con el certificado de Microsoft el MSIX que certifica, así que no hace falta comprar uno.
    - Un instalador EXE o MSI enviado a la Store sí tendría que llegar firmado con un certificado propio, así que la vía es el MSIX.
    - El EXE de GitHub sigue sin firma, para quien quiera probar antes; la Store pasa a ser el canal para quien no es desarrollador.
    - **Pendientes del MSIX**, según `desktop/msix/README.md`:
      - Companion dentro del paquete.
      - En la edición de la Store, las actualizaciones las hace la Store, así que el canal propio queda desactivado.
      - Probarlo en Windows limpio, firmado con un certificado de prueba autofirmado, que no cuesta nada.
      - Notas de certificación que justifiquen `runFullTrust`.
      - Pasar el Windows App Certification Kit.
    - **Políticas que tocan a Iris** (versión 7.20):
      - **10.2.2, código dinámico:** describir en la ficha que Iris instala apps locales elegidas por el usuario y que el agente usa herramientas, para que eso sea funcionalidad descrita.
      - **10.2.4, dependencias:** decir al principio de la descripción que hace falta una cuenta con un proveedor de IA o un modelo local.
      - **11.16, IA generativa:** declararla y ofrecer una vía para reportar contenido. El borrador ya lo prevé.
12. **Accesibilidad de punta a punta.** *(Usuario o Claude)*
    - Recorrer con Narrador y teclado el asistente, la primera respuesta, abrir y cerrar una app, y la configuración de voz.
    - Registrar lo que no se anuncia o no se alcanza.
13. **Inicio pensado para el usuario.** *(Codex o Claude)*
    - Chat, Calendario y Correo a mano; las apps técnicas (Gateway Manager, Kanban, Continuity, Capability) agrupadas en «Avanzado»; sin duplicados.
    - Validarlo con las personas del punto 6.

## Fase 4 · Mantenibilidad, poco a poco

14. **Archivos gigantes.**
    - Los cuatro más grandes son `face/js/hermes_activity.js`, `wizard/ui/wizard.js`, `core/websocket_server.py` y `wizard/app.py`.
    - Cada vez que se toque una parte, sacarla a su propio módulo con sus pruebas, por ejemplo las rutas de permisos, medios o apps del servidor.
    - Nada de reescrituras de golpe.
15. **Versiones `?v=N`.**
    - Hoy se suben a mano y una olvidada sirve código viejo en caché.
    - Una comprobación automática que falle si un módulo cambió y su versión no, o un sufijo calculado al compilar.
16. **Seguimiento de un turno.**
    - Un identificador por turno en los registros de Core, del puente con Hermes y de la interfaz.
    - Así un fallo se diagnostica sin reconstruirlo proceso por proceso.
17. **Historial de cambios más fino.**
    - Hoy hay un commit por candidato.
    - Commits por cambio, o al menos por función, permitirían encontrar con `git bisect` qué rompió algo. Cómo hacerlo es decisión del usuario.

## Cómo saber que funcionó

Repetir la misma auditoría, de solo lectura y por Claude y Codex por separado, después del próximo candidato que cumpla las fases 1 y 2. La meta es que ninguna área quede por debajo de 7 en ninguna de las dos, y que estabilidad, instaladores y UX lleguen a 8 con evidencia registrada, no solo con pruebas automáticas.
