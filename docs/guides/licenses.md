# Licencias de terceros

> Disponibilidad por versión/candidato: [Estado actual](../estado-actual.md).
> Los resultados fechados de esta guía corresponden a aquella comprobación,
> no a una validación nueva de todos los ejecutables.

El código propio de IRIS OS tiene licencia MIT. Eso no anuncia una release
pública nueva. Los candidatos Windows incluyen además
Hermes Agent (Nous Research, MIT), dos runtimes privados de Python con sus
paquetes, bibliotecas nativas de audio y aceleración, y las bibliotecas y fuentes
de la interfaz. Cada componente conserva su propia licencia.

Revisión documental: 2026-10-16. Inventarios y cantidades dependen del candidato;
no reutilices los conteos de A08.6 como inventario del paquete actual.

## Dónde verlas

En el asistente, el botón **Licencias** de la cabecera abre el módulo
**Licencias** del Manual. En Iris Desktop se abre dentro de la misma
ventana. La lista se puede filtrar por paquete o licencia y cada aviso se abre
como texto. Los archivos de código fuente incluidos por cumplimiento (por
ejemplo FFmpeg, Opus o LAME) aparecen en la lista, pero no se abren como texto;
están en la carpeta `licenses/` de la instalación.

## De dónde sale la lista

La lista no está escrita a mano. Se lee de los inventarios que se generan al
preparar el paquete:

| Inventario | Contenido |
| --- | --- |
| `runtime-inventory.json`, junto al descriptor de runtime (A08.4) | Python y paquetes de los runtimes de Iris y Hermes, Hermes Agent, audio y bibliotecas nativas. |
| `face/web-assets.json`, dentro de Iris (A08.3) | Bibliotecas web y fuentes de la exportación verificada. |
| `desktop/licenses.json` | Dependencias enlazadas en la ventana nativa de Iris. |
| `companion/licenses.json` | Componentes del Companion incluido, cuando el manifiesto lo declara. |
| `installer/licenses.json` | Componentes del instalador Windows. |

`wizard/licenses.py` solo sirve un archivo que uno de esos inventarios cita
como licencia o aviso. Antes de mostrarlo comprueba que el tamaño y el SHA-256
coincidan con los registrados, que esté dentro de la instalación y que ningún
symlink o junction lo redirija. No es un lector genérico de archivos: una ruta
que no aparece en el inventario, una ruta absoluta o con `..`, y los archivos
binarios se rechazan. Si falta un inventario o está incompleto, el Manual lo
dice («falta parte del inventario: repara Iris») en lugar de completar la lista.

En desarrollo no existe el inventario del runtime privado: el Manual muestra la
licencia de IRIS OS e indica que la lista completa se genera al preparar el
paquete. Las de las dependencias de desarrollo se consultan en sus paquetes.

## Extras y recursos aportados

- Los extras opcionales de voz (A08.5) se descargan después de instalar y viven
  en el perfil. Sus licencias están en los `dist-info` de su entorno aislado.
- El avatar de IRIS (`face/animations/iris.vrm`), de Cristian Betancourt, **no**
  está bajo la licencia MIT: se incluye solo como avatar predeterminado de IRIS OS,
  sin permiso para redistribuirlo ni modificarlo
  ([LICENSE-IRIS-VRM.txt](../../face/animations/LICENSE-IRIS-VRM.txt) y sus
  metadatos VRM).
- Avatares, voces, música y animaciones que añada el usuario no adquieren la
  licencia de Iris por importarlos. Revisar permisos antes de redistribuirlos.
- El pin actual excluye `fetch-use==0.4.0` por falta de texto verificable de
  licencia. Esto elimina las funciones de proxy cloud de Browser Use, no la
  navegación HTTP normal. La excepción está ligada a versiones revisadas;
  véanse [Actualizaciones de Hermes](hermes-updates.md) y el manifiesto del pin.

[← Índice de documentación](../README.md)
