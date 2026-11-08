# Consola privada de Windows

QA 2 detectó que la herramienta terminal necesitaba un Bash externo. El runtime nuevo incluye una selección sin modificaciones de Bash y utilidades de Git for Windows 2.56.0.2, con sus DLL de MSYS2. No instala Git global, no modifica PATH del usuario y no usa el lanzador WSL.

Fuentes y hashes revisados: `scripts/shell_runtime_sources.json`. El productor verifica el archivo completo, cada miembro seleccionado, las DLL importadas y los avisos antes de crear dos ZIP deterministas.

```powershell
.venv\Scripts\python.exe -B -m scripts.build_shell_runtime --output <carpeta-nueva> --cache-root <cache-binarios> --sources-cache <cache-fuentes> --offline
```

El caché debe contener el archivo oficial y las fuentes enumeradas con sus hashes. Copiar al caché del runtime los dos resultados, `iris-private-shell.zip` e `iris-private-shell-sources.zip`. `prepare_private_runtime.py` exige ambos y sus hashes; la selección del shell y todos sus avisos entran en el inventario. El ZIP de fuentes se conserva como archivo separado para distribuirlo junto al instalador; no hace falta meter cientos de MB de fuentes en la instalación.

**Antes de distribuir un instalador que incluya este shell, publicar también `iris-private-shell-sources.zip` en la misma entrega.** Incluye las fuentes originales, recetas PKGBUILD y parches de los componentes. Los archivos de fuente de MSYS2 pueden contener repositorios Git además de los tarballs; se reconstruyen en el SDK de MSYS2 siguiendo sus recetas. No ejecutar recetas descargadas durante la preparación de Iris.

En ejecución, Iris fija HERMES_GIT_BASH_PATH a su propia instalación y añade solo sus utilidades al PATH privado. Si faltan Bash, sh, MSYS2 o los montajes, se rechaza el entorno con una indicación de reparar el instalador; no se cambia silenciosamente a un Git ajeno. Los permisos y aprobaciones de la herramienta terminal siguen siendo los de Hermes.

Este shell está incluido en QA15 y QA16. QA14 permanece intacto; las fuentes
correspondientes acompañan a los candidatos nuevos según sus informes.
