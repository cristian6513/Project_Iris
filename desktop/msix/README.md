# Prototipo MSIX de Iris OS

`AppxManifest.xml` contiene la identidad reservada en Partner Center y declara Iris como aplicación de escritorio x64. La primera prueba usa `0.9.2.0`, con los binarios del QA22 ya compilados. `runFullTrust` permite al lanzador nativo iniciar los procesos Python que ejecutan Core y Hermes.

Para ensamblar el prototipo, se toma `Iris.exe`, `runtime.json`, el inventario y el pin del runtime, el `LEEME.txt` y las carpetas `desktop`, `hermes`, `licenses`, `locks`, `runtimes` y `tools` del staging verificado. Se añaden el manifiesto y tres PNG en `Assets` de 50×50, 150×150 y 44×44 generados a partir de `companion/src-tauri/icons/iris-app-icon-master.png`. Se usa `MakeAppx pack` con la validación habilitada.

## Estado al 2026-11-08

El Envío 1 de Iris OS está en borrador en Partner Center. La
[ficha en español](ficha-store-borrador.md) está preparada localmente. El plan
mantiene vacía la sección **Paquetes** hasta disponer de un candidato que haya
pasado la prueba de instalación. La beta de GitHub se distribuye por su propio
canal. [Plan de distribución de Iris](../../docs/audits/iris_a08_implementation_plan_2026-09.md#microsoft-store--preparación-en-borrador).

MakeAppx aceptó el prototipo x64 `0.9.2.0` sin firma. Se verificó la integridad
de 27.831 entradas y la coincidencia de los ejecutables de Iris y Python y la
entrada de Hermes con el staging. Desde ese staging, con un perfil aislado,
el controlador sirvió `/desktop/` y Hermes informó la versión `0.21.6`. La
instalación MSIX y el arranque nativo en WebView2 quedan pendientes.

El prototipo local pesa 466.399.973 bytes y su SHA-256 es
`c65c708067f9900c04989077780d46af3eb5d68f9994bd88dec02f66beea9fc4`.

## Pendientes antes de la certificación

- Incluir el ejecutable de Companion en el paquete y hacer que Iris lo abra desde allí. El instalador NSIS de Companion se excluye del prototipo.
- Probar el paquete firmado en Windows limpio con WebView2, el arranque de Core y Hermes, la persistencia del perfil y la desinstalación. Comprobar también la convivencia con la edición de GitHub.
- Adaptar la interfaz de actualizaciones al canal de la Store. El prototipo arranca como staging autónomo y carece del lanzador por versiones y del canal firmado que usa la edición de GitHub.
- Preparar las notas de certificación, incluida la justificación de `runFullTrust` y la descripción de las apps y extensiones locales que admite Iris. Ejecutar Windows App Certification Kit sobre el candidato final.
- Declarar la generación de contenido con IA en la ficha y en Partner Center, y ofrecer una vía para reportar contenido inapropiado, según la sección 11.16 de las políticas de Microsoft.

[Requisitos de paquetes MSIX](https://learn.microsoft.com/en-us/windows/apps/publish/publish-your-app/package-version-numbering) · [Comportamiento de las apps de escritorio empaquetadas](https://learn.microsoft.com/en-us/windows/msix/desktop/desktop-to-uwp-behind-the-scenes) · [Políticas de Microsoft Store](https://learn.microsoft.com/en-us/windows/apps/publish/store-policies)
