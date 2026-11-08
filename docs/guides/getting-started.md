# Instalación y primeros pasos

[← Índice de documentación](../README.md)

IRIS OS utiliza Hermes Agent como motor. El wizard detecta el equipo y el
estado real de Hermes antes de proponer una instalación, reparación o
configuración.

Consulta el [estado actual](../estado-actual.md) para identificar qué candidato
contiene cada función. No hay una descarga pública nueva aprobada por esta guía.

## Probar el paquete de Windows

El candidato de QA incluye Iris, Hermes y sus dos intérpretes privados. Usa el
EXE que identifica el informe del candidato, en Windows 10/11 x64 y con
WebView2 instalado. No requiere Python, Git ni uv globales. Si falta WebView2,
el instalador informa y ofrece la página oficial de Microsoft; no lo instala.

La instalación por usuario usa `%LOCALAPPDATA%\Programs\Iris OS` y versiones
separadas bajo `v/`. Las preferencias y cuentas pertenecen al perfil mutable,
fuera del programa instalado. Sigue LISTA-QA (referencia no distribuida: `desktop/installer/qa/LISTA-QA.md`)
para probar instalación, reparación, actualización y desinstalación. Los EXE
marcados «solo QA» no se publican ni se usan como versión habitual.

## Requisitos para trabajar desde fuente

- Python 3.11 o superior.
- Windows 10/11, Linux/WSL2 o macOS Apple Silicon dentro de la matriz
  compatible de Hermes.
- Conexión a internet para instalar Hermes o utilizar proveedores remotos.
- Git cuando la plataforma o el flujo de instalación lo requiera.
- GPU opcional. Un proveedor remoto sigue siendo válido en equipos modestos.

El flujo externo conserva `0.19.0` como mínimo base y negocia capacidades en
ejecución. El paquete Windows fija Hermes `0.21.6`; sus contratos avanzados no
se dan por compatibles con cualquier motor que supere ese mínimo.

## Instalar dependencias de IRIS

La opción recomendada usa [`uv`](https://docs.astral.sh/uv/):

```bash
uv sync
uv run python -m wizard.app
```

El wizard se abre en:

```text
http://127.0.0.1:8000/ui/
```

También puedes usar un entorno virtual:

```bash
python -m venv .venv
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
python -m wizard.app
```

Linux o macOS:

```bash
source .venv/bin/activate
python -m pip install -e .
python -m wizard.app
```

## Qué ruta toma el wizard

En la fuente actual (ajuste del 2026-10-22), un perfil sin configurar abre primero
**Apariencia**, antes de la bienvenida: Cyber, Sakura, Hacker o Classic. La elección
cambia los colores al momento y se guarda. Una configuración existente conserva
su edición. Después, **Configurar IRIS** recorre cinco pasos:

1. **Tu asistente:** nombre y base; personalidad e importación son opcionales.
2. **Inteligencia artificial:** proveedor, sesión o clave y modelo. El equipo
   se comprueba automáticamente; mensajería y diagnóstico se despliegan si hacen falta.
3. **Voz:** motor y voz, con su credencial en el mismo lugar. Escuchar una prueba,
   instalar un extra o clonar requieren pulsar su acción; no se ejecutan al elegir.
4. **Avatar:** IRIS (incluida) o tu propio modelo VRM. Recursos y animaciones quedan como opciones.
5. **Resumen y abrir:** confirma el guardado real y el estado de la IA; ofrece
   corregir cada sección y, por separado, Desktop Companion opcional.

**Cambiar una configuración** abre directamente Apariencia, Asistente, IA, Voz o Avatar.
**Diagnóstico y reparación** abre IA con sus detalles; entrar no inicia ni
reinicia el motor. La activación por voz se configura desde IRIS OS y una
configuración existente se conserva. Este flujo está incluido en QA16; la QA
instalada sigue el informe de ese candidato. Los EXE antiguos conservan sus pantallas.

| Estado detectado | Comportamiento esperado |
| --- | --- |
| Hermes no existe | Ofrece el instalador oficial y verifica el launcher al terminar. |
| Hermes funciona y está configurado | Permite un recorrido rápido sin reescribir credenciales. |
| Hermes funciona pero falta proveedor/modelo | Conserva la instalación y completa la configuración. |
| Launcher o runtime incompletos | Ofrece diagnóstico y reparación con el instalador oficial. |
| Plataforma no compatible | Explica el bloqueo; no promete una instalación que no puede completar. |

La reparación debe conservar la información administrada de Hermes. Aun así,
conviene respaldar configuraciones personalizadas antes de reparar un runtime
que ya fue modificado manualmente.

### Versión instalada de Iris

La tabla anterior describe la vista previa de desarrollo, que trabaja con un
Hermes instalado aparte. La versión instalada ya incluye Hermes Agent y sus
dependencias esenciales, así que el asistente no instala ni actualiza otro
Hermes, no comprueba Python, Git ni uv y no crea un arranque automático externo:

| Estado detectado | Comportamiento esperado |
| --- | --- |
| Motor incluido sin proveedor | **Configurar proveedor de IA**; no se instala nada. |
| Motor incluido configurado y detenido | **Iniciar IRIS** con la configuración guardada, o cambiar de proveedor. |
| Motor incluido activo | Continuar a voz y avatar, o cambiar de proveedor. |
| Motor incluido ausente o dañado | Indica que hay que reparar o reinstalar Iris. Mientras tanto se puede configurar voz y avatar. |

En la fuente posterior a `qa11`, el proveedor se guarda antes de ofrecer el
arranque del Gateway. Mensajería está marcada como opcional y el Gateway va
después de ella. El proveedor configurado se pliega y permite cambiar el
principal o agregar otra credencial. El resumen final refleja la comprobación
real del proveedor, no un simple «archivo guardado».

`qa11` conserva un fallo conocido del resumen final que deja «Guardando tu
configuración…» y bloquea Lanzar/Companion de ese paso. Es un fallo histórico;
QA16 incluye la corrección. Usa el informe y la LISTA-QA del candidato que
ejecutes: una versión antigua no incorpora cambios posteriores del checkout.

Antes de iniciar o reiniciar el motor por primera vez (desde Inteligencia artificial,
Canales o «Abrir IRIS OS»), el asistente pide aceptar el aviso de seguridad de
IRIS (que integra Hermes Agent); la respuesta se guarda en el perfil y no se vuelve a preguntar. Sin
ella, Iris Desktop no inicia el motor y abre Configuración. Las
licencias de los componentes incluidos están en **Licencias**, en la cabecera
del asistente (ver [Licencias de terceros](licenses.md)).

## Iniciar el asistente y abrir el escritorio

En la fuente del 20 de octubre, **Iniciar IRIS** activa el servicio de IA con el
proveedor guardado. Cuando está activo, el control pasa a **Reiniciar IRIS**:
aplica cambios del proveedor o de la mensajería sin cerrar tus ventanas, pero
puede interrumpir una respuesta en curso. **Abrir IRIS OS** abre el escritorio;
no es el nombre del reinicio del servicio.

La atribución del motor se consulta en **Ajustes → Sistema → Acerca de IRIS OS**
y las licencias completas en la cabecera del asistente. Los diagnósticos,
comandos y rutas conservan el nombre técnico Hermes. La frase nativa
**Hey Hermes** tampoco cambia. Estos textos están incluidos en QA16; no cambian
las pantallas de los instaladores anteriores.

## Elegir proveedor

El wizard distingue tres familias:

1. **Proveedor remoto con API key.** Recomendado cuando el equipo tiene poca
   RAM o no dispone de aceleración local.
2. **OAuth gestionado por Hermes.** Solo aparece como utilizable cuando Hermes
   dispone del adaptador correspondiente. La sesión pertenece a Hermes.
3. **Proveedor local.** Ollama o LM Studio solo se recomiendan si el endpoint
   responde y hay modelos disponibles. IRIS no instala estas aplicaciones ni
   descarga sus modelos.

Consulta [Configuración](configuration.md) para entender credenciales, modelos
y proveedores locales.

## Completar la experiencia

El recorrido empieza por apariencia e identidad y luego ofrece IA, voz y avatar.
Puedes personalizar:

- tema visual;
- nombre y perfil;
- personalidad, rasgos y backstory;
- voz y motores opcionales;
- Desktop Companion opcional en Windows.

Después, desde Ajustes de IRIS OS, puedes preparar la activación por voz nativa
de Hermes. Está apagada inicialmente; el asistente conserva la preferencia de
un perfil existente y ya no añade una pantalla obligatoria para ello.

Guardar una frase de activación no abre el micrófono. La escucha solo empieza
después de habilitarla expresamente y de que Hermes confirme que el motor y el
dispositivo están listos.

## Iniciar IRIS

El botón final del wizard puede lanzar el runtime. También puedes iniciarlo
manualmente:

```bash
uv run python -m core.agent
```

Abre:

```text
http://127.0.0.1:8765/face/
```

El Core puede usar HTTPS cuando habilitas acceso LAN, porque cámara y micrófono
requieren un contexto seguro fuera de loopback.

## Diagnóstico rápido

Si IRIS no responde:

1. Abre **Diagnóstico y reparación** y despliega **Equipo, conexión y diagnóstico**.
2. Comprueba por separado launcher, configuración, credenciales, Gateway
   `8642` e IRIS Core `8765`.
3. Un Gateway detenido no significa que Hermes esté mal instalado.
4. Una sesión OAuth no equivale a una API key; el diagnóstico debe validar el
   método elegido.
5. Si el catálogo parece antiguo, utiliza **Actualizar catálogo** y revisa el
   origen/fecha mostrados antes de guardar un modelo.

No compartas la salida completa si contiene rutas personales. El diagnóstico
de IRIS redacta secretos, pero conviene revisar cualquier captura antes de
publicarla.

## Siguientes lecturas

- [Manual integrado de IRIS OS](../../wizard/ui/help.html): apariencia inicial, cinco pasos y uso del
  escritorio, cuentas, voz, cámara, tareas y Store. En Iris se abre con **Manual**
  desde la cabecera de Configuración.
- [Configuración](configuration.md)
- [Privacidad y seguridad](privacy-security.md)
- [Funcionalidades](features.md)
