//! Descriptor resolution is independent of Tauri, cwd and runtime discovery.
use serde::{Deserialize, Deserializer};
use std::{
    fs::{self, File},
    io::Read,
    path::{Path, PathBuf},
    process::Command,
};

const MAX_DESCRIPTOR_BYTES: u64 = 64 * 1024;
// Windows MAX_PATH (260 UTF-16 units) minus the terminating NUL. A packaged
// staging declares its longest relative path so a too-deep installation folder
// is reported before any file inside it fails with an obscure error (A08.7).
const WINDOWS_MAX_PATH: usize = 259;
const MAX_RELATIVE_PATH: u32 = 4096;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RuntimeMode {
    Development,
    Packaged,
}

#[derive(Debug)]
pub struct ResolvedHermes {
    pub root: PathBuf,
    pub python: PathBuf,
    pub cli: PathBuf,
    pub version: String,
    pub revision: String,
    pub metadata: PathBuf,
}

#[derive(Debug)]
pub struct ResolvedRuntime {
    pub base: PathBuf,
    pub descriptor_path: Option<PathBuf>,
    pub mode: RuntimeMode,
    pub iris_root: PathBuf,
    pub iris_python: PathBuf,
    pub assets: PathBuf,
    pub iris_revision: Option<String>,
    pub hermes: Option<ResolvedHermes>,
    pub max_relative_path: Option<u32>,
}

#[derive(Deserialize)]
struct Header {
    schema_version: u32,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct LegacySpec {
    schema_version: u32,
    mode: String,
    project_root: PathBuf,
    python: PathBuf,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct IrisSpec {
    root: String,
    python: String,
    assets: String,
    revision: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct HermesSpec {
    root: String,
    python: String,
    cli: String,
    version: String,
    revision: String,
    metadata: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct InstallSpec {
    max_relative_path: u32,
}

// Missing is allowed in development; explicit null is not an object.
fn present_hermes<'de, D: Deserializer<'de>>(value: D) -> Result<Option<HermesSpec>, D::Error> {
    HermesSpec::deserialize(value).map(Some)
}

fn present_install<'de, D: Deserializer<'de>>(value: D) -> Result<Option<InstallSpec>, D::Error> {
    InstallSpec::deserialize(value).map(Some)
}

/// Length as Windows counts it for MAX_PATH, without the verbatim prefix that
/// `canonicalize` adds.
pub fn windows_path_length(path: &Path) -> usize {
    let text = path.to_string_lossy();
    if let Some(rest) = text.strip_prefix(r"\\?\UNC\") {
        return 2 + rest.encode_utf16().count();
    }
    text.strip_prefix(r"\\?\").unwrap_or(&text).encode_utf16().count()
}

/// The path without the verbatim prefix `canonicalize` adds on Windows. Child
/// processes compare their paths with what the system reports for running
/// processes, which never carries it (A08.7). The MAX_PATH budget keeps the
/// plain form valid.
pub fn plain_path(path: &Path) -> PathBuf {
    let text = path.to_string_lossy();
    if let Some(rest) = text.strip_prefix(r"\\?\UNC\") {
        return PathBuf::from(format!(r"\\{rest}"));
    }
    match text.strip_prefix(r"\\?\") {
        Some(rest) if rest.as_bytes().get(1) == Some(&b':') => PathBuf::from(rest),
        _ => path.to_path_buf(),
    }
}

pub fn install_root_limit(max_relative_path: u32) -> usize {
    WINDOWS_MAX_PATH.saturating_sub(1).saturating_sub(max_relative_path as usize)
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Spec {
    schema_version: u32,
    mode: String,
    iris: IrisSpec,
    #[serde(default, deserialize_with = "present_hermes")]
    hermes: Option<HermesSpec>,
    #[serde(default, deserialize_with = "present_install")]
    install: Option<InstallSpec>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct HermesMetadata {
    schema_version: u32,
    version: String,
    revision: String,
}

fn bounded_json(path: &Path) -> Result<Vec<u8>, String> {
    if !fs::metadata(path).map_err(|_| "No se encuentra un archivo de runtime.")?.is_file() {
        return Err("El descriptor o metadata de runtime no es un archivo regular.".into());
    }
    let file = File::open(path).map_err(|_| "No se pudo abrir un archivo de runtime.")?;
    let mut bytes = Vec::new();
    file.take(MAX_DESCRIPTOR_BYTES + 1).read_to_end(&mut bytes)
        .map_err(|_| "No se pudo leer un archivo de runtime.")?;
    if bytes.len() as u64 > MAX_DESCRIPTOR_BYTES {
        return Err("El descriptor o metadata excede 64 KiB.".into());
    }
    Ok(bytes)
}

fn parse<T: serde::de::DeserializeOwned>(bytes: &[u8]) -> Result<T, String> {
    // Concrete structs reject unknown fields, duplicate fields and wrong types.
    serde_json::from_slice(bytes).map_err(|_| "El descriptor o metadata de runtime no es válido.".into())
}

fn revision_valid(value: &str) -> bool {
    value.len() == 40 && value.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

fn version_valid(value: &str) -> bool {
    !value.trim().is_empty() && value.chars().count() <= 128 && !value.chars().any(char::is_control)
}

fn packaged_path_valid(value: &str) -> bool {
    if value.is_empty() || value.starts_with('/') || value.contains(['\\', ':', '%'])
        || value.chars().any(|c| c.is_control() || matches!(c, '<' | '>' | '"' | '|' | '?' | '*')) {
        return false;
    }
    value.split('/').all(|part| {
        if part.is_empty() || matches!(part, "." | "..") || part.ends_with(['.', ' ']) {
            return false;
        }
        let stem = part.split('.').next().unwrap_or("").trim_end_matches(' ').to_uppercase();
        !matches!(stem.as_str(), "CON" | "PRN" | "AUX" | "NUL" | "CLOCK$")
            && !(stem.starts_with("COM") || stem.starts_with("LPT"))
                .then(|| stem.chars().nth(3))
                .flatten()
                .is_some_and(|c| "123456789¹²³".contains(c) && stem.chars().count() == 4)
    })
}

fn resolved_path(base: &Path, value: &Path, packaged: bool, directory: bool) -> Result<PathBuf, String> {
    if packaged && !value.to_str().is_some_and(packaged_path_valid) {
        return Err("El runtime empaquetado requiere rutas relativas seguras con /.".into());
    }
    let target = base.join(value).canonicalize().map_err(|_| "Falta una ruta del runtime.")?;
    if packaged && !target.starts_with(base) {
        return Err("Una ruta del runtime sale de la instalación.".into());
    }
    if if directory { !target.is_dir() } else { !target.is_file() } {
        return Err("Una ruta del runtime tiene un tipo incorrecto.".into());
    }
    Ok(target)
}

fn packaged_descriptor_has_base(descriptor: &Path, base: &Path) -> bool {
    // Core receives the canonical file and derives its base from its parent.
    // An alias into a subdirectory would make Native and Python resolve differently.
    descriptor.parent() == Some(base)
}

fn resolve_legacy(base: PathBuf, spec: LegacySpec, descriptor: Option<PathBuf>) -> Result<ResolvedRuntime, String> {
    if spec.schema_version != 1 || spec.mode != "development" {
        return Err("Schema 1 únicamente admite el runtime de desarrollo.".into());
    }
    let root = resolved_path(&base, &spec.project_root, false, true)?;
    let python = resolved_path(&base, &spec.python, false, false)?;
    if !root.join("core/desktop.py").is_file() {
        return Err("El runtime de Iris está incompleto.".into());
    }
    Ok(ResolvedRuntime {
        base, descriptor_path: descriptor, mode: RuntimeMode::Development,
        assets: root.join("face"), iris_root: root, iris_python: python,
        iris_revision: None, hermes: None, max_relative_path: None,
    })
}

pub fn resolve_runtime(base: &Path, debug_project: Option<&Path>) -> Result<ResolvedRuntime, String> {
    let base = base.canonicalize().map_err(|_| "No se encuentra la carpeta de Iris.")?;
    let path = base.join("runtime.json");
    match fs::symlink_metadata(&path) {
        Ok(_) => {},
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            let root = debug_project.ok_or("Falta runtime.json junto al ejecutable.")?;
            let python = root.join(if cfg!(windows) { ".venv/Scripts/python.exe" } else { ".venv/bin/python" });
            return resolve_legacy(base, LegacySpec {
                schema_version: 1, mode: "development".into(),
                project_root: root.into(), python,
            }, None);
        },
        Err(_) => return Err("No se pudo comprobar runtime.json.".into()),
    }
    let bytes = bounded_json(&path)?;
    let header: Header = parse(&bytes)?;
    let descriptor = path.canonicalize().map_err(|_| "No se pudo resolver runtime.json.")?;
    if header.schema_version == 1 {
        return resolve_legacy(base, parse(&bytes)?, Some(descriptor));
    }
    if header.schema_version != 2 {
        return Err("La versión del descriptor no está soportada.".into());
    }
    let spec: Spec = parse(&bytes)?;
    if spec.schema_version != 2 {
        return Err("La versión del descriptor no está soportada.".into());
    }
    let mode = match spec.mode.as_str() {
        "development" => RuntimeMode::Development,
        "packaged" => RuntimeMode::Packaged,
        _ => return Err("El modo de runtime no está soportado.".into()),
    };
    let packaged = mode == RuntimeMode::Packaged;
    if packaged && (!packaged_descriptor_has_base(&descriptor, &base) || spec.hermes.is_none()) {
        return Err("El runtime empaquetado requiere su descriptor y Hermes incluidos.".into());
    }
    let max_relative_path = match &spec.install {
        Some(install) => {
            if install.max_relative_path == 0 || install.max_relative_path > MAX_RELATIVE_PATH {
                return Err("El límite de ruta de instalación no es válido.".into());
            }
            let (length, limit) = (windows_path_length(&base), install_root_limit(install.max_relative_path));
            if packaged && cfg!(windows) && length > limit {
                return Err(format!(
                    "Iris está en una carpeta con una ruta demasiado larga para Windows ({length} caracteres; \
                     máximo {limit}). Mueve la carpeta de Iris a una ruta más corta, por ejemplo C:\\Iris."
                ));
            }
            Some(install.max_relative_path)
        },
        None => None,
    };
    if !revision_valid(&spec.iris.revision) {
        return Err("La revisión de Iris no es válida.".into());
    }
    if [&spec.iris.root, &spec.iris.python, &spec.iris.assets].iter().any(|value| value.is_empty()) {
        return Err("Las rutas del runtime no pueden estar vacías.".into());
    }
    let iris_root = resolved_path(&base, Path::new(&spec.iris.root), packaged, true)?;
    let iris_python = resolved_path(&base, Path::new(&spec.iris.python), packaged, false)?;
    let assets = resolved_path(&base, Path::new(&spec.iris.assets), packaged, true)?;
    // Validate the entry point as well: a symlinked core/ directory cannot escape.
    let entry = iris_root.join("core/desktop.py").canonicalize()
        .map_err(|_| "El runtime de Iris está incompleto.")?;
    if !entry.is_file() || (packaged && !entry.starts_with(&iris_root)) {
        return Err("El runtime de Iris está incompleto o sale de la instalación.".into());
    }
    let hermes = if let Some(item) = spec.hermes {
        if !revision_valid(&item.revision) || !version_valid(&item.version) {
            return Err("La versión o revisión de Hermes no es válida.".into());
        }
        if [&item.root, &item.python, &item.cli, &item.metadata].iter().any(|value| value.is_empty()) {
            return Err("Las rutas del runtime no pueden estar vacías.".into());
        }
        let root = resolved_path(&base, Path::new(&item.root), packaged, true)?;
        let python = resolved_path(&base, Path::new(&item.python), packaged, false)?;
        let cli = resolved_path(&base, Path::new(&item.cli), packaged, false)?;
        let metadata = resolved_path(&base, Path::new(&item.metadata), packaged, false)?;
        let entry = root.join("hermes_cli/__init__.py").canonicalize()
            .map_err(|_| "El runtime de Hermes está incompleto.")?;
        if !entry.is_file() || (packaged && !entry.starts_with(&root)) {
            return Err("El runtime de Hermes está incompleto o sale de la instalación.".into());
        }
        if !metadata.starts_with(&root) {
            return Err("El metadata no pertenece al runtime de Hermes.".into());
        }
        let pin: HermesMetadata = parse(&bounded_json(&metadata)?)?;
        if pin.schema_version != 1 || pin.version != item.version || pin.revision != item.revision {
            return Err("El runtime de Hermes no coincide con el pin declarado.".into());
        }
        Some(ResolvedHermes { root, python, cli, metadata, version: item.version, revision: item.revision })
    } else { None };
    Ok(ResolvedRuntime {
        base, descriptor_path: Some(descriptor), mode, iris_root, iris_python, assets,
        iris_revision: Some(spec.iris.revision), hermes, max_relative_path,
    })
}

pub fn controller_command(runtime: &ResolvedRuntime) -> Command {
    let mut command = Command::new(&runtime.iris_python);
    if runtime.mode == RuntimeMode::Packaged {
        command.args(["-E", "-s", "-B", "-X", "utf8"]);
    }
    command.args(["-u", "-m", "core.desktop"]).current_dir(plain_path(&runtime.iris_root))
        .env("PYTHONIOENCODING", "utf-8").env("PYTHONUNBUFFERED", "1");
    for name in [
        "PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "VIRTUAL_ENV", "__PYVENV_LAUNCHER__",
        "UV_PROJECT_ENVIRONMENT", "IRIS_RUNTIME_DESCRIPTOR",
    ] {
        command.env_remove(name);
    }
    if runtime.mode == RuntimeMode::Packaged {
        command.env_remove("HERMES_REPO").env_remove("HERMES_AGENT_ROOT");
    }
    if let Some(path) = &runtime.descriptor_path {
        command.env("IRIS_RUNTIME_DESCRIPTOR", plain_path(path));
    }
    command
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::{json, Value};
    use std::{ffi::OsStr, sync::atomic::{AtomicU64, Ordering}, time::{SystemTime, UNIX_EPOCH}};

    const IRIS_REV: &str = "1111111111111111111111111111111111111111";
    const HERMES_REV: &str = "fca3221df48d4ae1e4db9e8a1a041de21ea4d9cf";
    static SERIAL: AtomicU64 = AtomicU64::new(0);

    struct Fixture {
        temporary: PathBuf,
        base: PathBuf,
    }

    impl Fixture {
        fn new() -> Self {
            let stamp = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos();
            let serial = SERIAL.fetch_add(1, Ordering::Relaxed);
            let temporary = std::env::temp_dir().join(format!("iris-runtime-rust-{}-{stamp}-{serial}", std::process::id()));
            let base = temporary.join("Iris distribución con espacios");
            fs::create_dir_all(base.join("runtime/iris/core")).unwrap();
            fs::create_dir_all(base.join("runtime/hermes/hermes_cli")).unwrap();
            fs::create_dir_all(base.join("recursos/visión local")).unwrap();
            for name in ["runtime/iris/core/desktop.py", "runtime/iris/python.exe", "runtime/hermes/hermes_cli/__init__.py", "runtime/hermes/python.exe", "runtime/hermes/hermes.exe"] {
                fs::write(base.join(name), "fixture").unwrap();
            }
            fs::write(base.join("runtime/hermes/pin.json"), serde_json::to_vec(&Self::metadata()).unwrap()).unwrap();
            Self { temporary, base }
        }

        fn metadata() -> Value {
            json!({"schema_version": 1, "version": "0.21.4", "revision": HERMES_REV})
        }

        fn spec() -> Value {
            json!({
                "schema_version": 2, "mode": "packaged",
                "iris": {"root": "runtime/iris", "python": "runtime/iris/python.exe", "assets": "recursos/visión local", "revision": IRIS_REV},
                "hermes": {"root": "runtime/hermes", "python": "runtime/hermes/python.exe", "cli": "runtime/hermes/hermes.exe", "version": "0.21.4", "revision": HERMES_REV, "metadata": "runtime/hermes/pin.json"}
            })
        }

        fn write(&self, doc: &Value) {
            fs::write(self.base.join("runtime.json"), serde_json::to_vec(doc).unwrap()).unwrap();
        }

        fn resolve(&self, doc: &Value) -> Result<ResolvedRuntime, String> {
            self.write(doc);
            resolve_runtime(&self.base, None)
        }
    }

    impl Drop for Fixture {
        fn drop(&mut self) {
            // Only the private directory created above, never a user installation.
            if self.temporary.starts_with(std::env::temp_dir())
                && self.temporary.file_name().unwrap().to_string_lossy().starts_with("iris-runtime-rust-") {
                let _ = fs::remove_dir_all(&self.temporary);
            }
        }
    }

    #[test]
    fn packaged_resolves_unicode_spaces_and_explicit_pin() {
        let fixture = Fixture::new();
        let runtime = fixture.resolve(&Fixture::spec()).unwrap();
        assert_eq!(runtime.mode, RuntimeMode::Packaged);
        assert_eq!(runtime.base, fixture.base.canonicalize().unwrap());
        assert_eq!(runtime.descriptor_path.as_deref(), Some(fixture.base.join("runtime.json").canonicalize().unwrap().as_path()));
        assert_eq!(runtime.iris_revision.as_deref(), Some(IRIS_REV));
        assert_eq!(runtime.assets, fixture.base.join("recursos/visión local").canonicalize().unwrap());
        let hermes = runtime.hermes.unwrap();
        assert_eq!(hermes.root, fixture.base.join("runtime/hermes").canonicalize().unwrap());
        assert_eq!(hermes.python, fixture.base.join("runtime/hermes/python.exe").canonicalize().unwrap());
        assert_eq!(hermes.cli, fixture.base.join("runtime/hermes/hermes.exe").canonicalize().unwrap());
        assert_eq!(hermes.metadata, fixture.base.join("runtime/hermes/pin.json").canonicalize().unwrap());
        assert_eq!(hermes.version, "0.21.4");
        assert_eq!(hermes.revision, HERMES_REV);
    }

    #[test]
    fn install_limit_rejects_deep_packaged_folders_and_invalid_values() {
        let fixture = Fixture::new();
        let mut spec = Fixture::spec();
        spec["install"] = json!({"max_relative_path": 40});
        assert_eq!(fixture.resolve(&spec).unwrap().max_relative_path, Some(40));
        assert_eq!(fixture.resolve(&Fixture::spec()).unwrap().max_relative_path, None);
        // A staging whose deepest file would not fit under MAX_PATH here fails
        // before the controller starts, with a message the user can act on.
        spec["install"] = json!({"max_relative_path": 250});
        if cfg!(windows) {
            let error = fixture.resolve(&spec).unwrap_err();
            assert!(error.contains("ruta demasiado larga") && error.contains("máximo 8"), "{error}");
        }
        for invalid in [json!(null), json!({}), json!({"max_relative_path": 0}), json!({"max_relative_path": 5000}),
                        json!({"max_relative_path": 40, "extra": 1}), json!({"max_relative_path": "40"}),
                        json!({"max_relative_path": -1})] {
            spec["install"] = invalid.clone();
            assert!(fixture.resolve(&spec).is_err(), "{invalid}");
        }
        // Development descriptors are never rejected for their location.
        spec["mode"] = json!("development");
        spec["install"] = json!({"max_relative_path": 250});
        assert!(fixture.resolve(&spec).is_ok());
    }

    #[test]
    fn windows_length_ignores_the_verbatim_prefix() {
        assert_eq!(windows_path_length(Path::new(r"\\?\C:\Iris")), 7);
        assert_eq!(windows_path_length(Path::new(r"\\?\UNC\server\share\Iris")), r"\\server\share\Iris".len());
        assert_eq!(windows_path_length(Path::new("C:\\Iris ñ")), 9);
        assert_eq!(windows_path_length(Path::new("C:\\😀")), 5);
        assert_eq!(install_root_limit(174), 84);
        assert_eq!(install_root_limit(400), 0);
        assert_eq!(plain_path(Path::new(r"\\?\C:\Iris ñ\runtime.json")), PathBuf::from(r"C:\Iris ñ\runtime.json"));
        assert_eq!(plain_path(Path::new(r"\\?\UNC\server\share\Iris")), PathBuf::from(r"\\server\share\Iris"));
        assert_eq!(plain_path(Path::new(r"\\?\Volume{x}\Iris")), PathBuf::from(r"\\?\Volume{x}\Iris"));
        assert_eq!(plain_path(Path::new(r"C:\Iris")), PathBuf::from(r"C:\Iris"));
    }

    #[test]
    fn legacy_schema_preserves_relative_parent_and_absolute_paths() {
        let fixture = Fixture::new();
        let runtime = fixture.resolve(&json!({
            "schema_version": 1, "mode": "development",
            "project_root": "../Iris distribución con espacios/runtime/iris",
            "python": fixture.base.join("runtime/iris/python.exe")
        })).unwrap();
        assert_eq!(runtime.mode, RuntimeMode::Development);
        assert!(runtime.hermes.is_none());
        assert!(runtime.iris_revision.is_none());
        #[cfg(windows)] {
            fixture.resolve(&json!({
                "schema_version": 1, "mode": "development",
                "project_root": "..\\Iris distribución con espacios\\runtime\\iris",
                "python": "runtime\\iris\\python.exe"
            })).unwrap();
        }
        for extra in ["iris", "hermes", "capabilities"] {
            let mut invalid = json!({"schema_version":1,"mode":"development","project_root":"runtime/iris","python":"runtime/iris/python.exe"});
            invalid[extra] = json!({});
            assert!(fixture.resolve(&invalid).is_err(), "legacy unknown {extra}");
        }
    }

    #[test]
    fn missing_descriptor_only_uses_explicit_debug_fallback() {
        let fixture = Fixture::new();
        assert!(resolve_runtime(&fixture.base, None).is_err());
        let project = fixture.base.join("runtime/iris");
        let python = project.join(if cfg!(windows) { ".venv/Scripts/python.exe" } else { ".venv/bin/python" });
        fs::create_dir_all(python.parent().unwrap()).unwrap();
        fs::write(&python, "fixture").unwrap();
        let runtime = resolve_runtime(&fixture.base, Some(&project)).unwrap();
        assert_eq!(runtime.mode, RuntimeMode::Development);
        assert!(runtime.descriptor_path.is_none());
        // An invalid descriptor is never hidden by the debug fallback.
        fs::write(fixture.base.join("runtime.json"), "{}").unwrap();
        assert!(resolve_runtime(&fixture.base, Some(&project)).is_err());
    }

    #[test]
    fn schema_two_development_allows_external_runtime_and_optional_hermes() {
        let fixture = Fixture::new();
        let mut spec = Fixture::spec();
        spec["mode"] = json!("development");
        spec["iris"]["root"] = json!(fixture.base.join("runtime/iris"));
        spec.as_object_mut().unwrap().remove("hermes");
        assert!(fixture.resolve(&spec).is_ok());
        spec["mode"] = json!("packaged");
        assert!(fixture.resolve(&spec).is_err());
        spec["mode"] = json!("development");
        spec["hermes"] = Value::Null;
        assert!(fixture.resolve(&spec).is_err());
    }

    #[test]
    fn invalid_schema_mode_fields_types_and_revisions_are_rejected() {
        let fixture = Fixture::new();
        for value in [json!(0), json!(3), json!(true), json!("2"), json!(2.0)] {
            let mut spec = Fixture::spec();
            spec["schema_version"] = value;
            assert!(fixture.resolve(&spec).is_err());
        }
        for value in [json!("production"), json!(true), Value::Null] {
            let mut spec = Fixture::spec();
            spec["mode"] = value;
            assert!(fixture.resolve(&spec).is_err());
        }
        for revision in ["", "A123456789012345678901234567890123456789", "fca3221", "fffffffffffffffffffffffffffffffffffffffff"] {
            for section in ["iris", "hermes"] {
                let mut spec = Fixture::spec();
                spec[section]["revision"] = json!(revision);
                assert!(fixture.resolve(&spec).is_err(), "{section} revision");
            }
        }
        for version in ["", "  ", "0.21.4\n", &"v".repeat(129)] {
            let mut spec = Fixture::spec();
            spec["hermes"]["version"] = json!(version);
            assert!(fixture.resolve(&spec).is_err());
        }
        let mut spec = Fixture::spec();
        spec["capabilities"] = json!({"install_hermes":true});
        assert!(fixture.resolve(&spec).is_err());
        let mut spec = Fixture::spec();
        spec["iris"]["unknown"] = json!(true);
        assert!(fixture.resolve(&spec).is_err());
        for section in ["iris", "hermes"] {
            for key in Fixture::spec()[section].as_object().unwrap().keys() {
                let mut spec = Fixture::spec();
                spec[section].as_object_mut().unwrap().remove(key);
                assert!(fixture.resolve(&spec).is_err(), "missing {section}.{key}");
            }
        }
    }

    #[test]
    fn duplicate_json_bom_invalid_utf8_and_oversized_documents_are_rejected() {
        let fixture = Fixture::new();
        let text = serde_json::to_string(&Fixture::spec()).unwrap();
        for duplicate in [text.replacen("\"schema_version\":2", "\"schema_version\":2,\"schema_version\":2", 1),
            text.replacen("\"revision\":", "\"revision\":\"1111111111111111111111111111111111111111\",\"revision\":", 1)] {
            fs::write(fixture.base.join("runtime.json"), duplicate).unwrap();
            assert!(resolve_runtime(&fixture.base, None).is_err());
        }
        for bytes in [vec![0xef, 0xbb, 0xbf, b'{', b'}'], vec![0xff], vec![b' '; (MAX_DESCRIPTOR_BYTES + 1) as usize]] {
            fs::write(fixture.base.join("runtime.json"), bytes).unwrap();
            assert!(resolve_runtime(&fixture.base, None).is_err());
        }
        fs::write(fixture.base.join("runtime/hermes/pin.json"), vec![b' '; (MAX_DESCRIPTOR_BYTES + 1) as usize]).unwrap();
        assert!(fixture.resolve(&Fixture::spec()).is_err());
    }

    #[test]
    fn all_packaged_paths_reject_absolute_traversal_windows_and_ambiguous_names() {
        let fixture = Fixture::new();
        let bad = ["", "/tmp/outside", "C:/outside", "C:relative", "../outside", "runtime/../iris", "runtime\\iris", "\\\\server\\share", "//server/share", "runtime/python.exe:stream", "runtime//iris", "./runtime/iris", "runtime/iris.", "runtime/iris ", "runtime/CON", "runtime/CON .txt", "runtime/nul.txt", "runtime/COM1.txt", "runtime/LPT²", "runtime/%2e%2e", "runtime/a\0b", "runtime/a?b"];
        for (section, keys) in [("iris", &["root", "python", "assets"][..]), ("hermes", &["root", "python", "cli", "metadata"][..])] {
            for key in keys {
                for value in bad {
                    let mut spec = Fixture::spec();
                    spec[section][*key] = json!(value);
                    assert!(fixture.resolve(&spec).is_err(), "{section}.{key}: {value:?}");
                }
            }
        }
    }

    #[test]
    fn paths_are_checked_for_missing_files_and_wrong_types() {
        let fixture = Fixture::new();
        for (section, keys) in [("iris", &["root", "python", "assets"][..]), ("hermes", &["root", "python", "cli", "metadata"][..])] {
            for key in keys {
                let mut spec = Fixture::spec();
                spec[section][*key] = json!("missing/path");
                assert!(fixture.resolve(&spec).is_err(), "missing {section}.{key}");
                spec[section][*key] = json!(if matches!(*key, "root" | "assets") { "runtime/iris/python.exe" } else { "runtime/iris" });
                assert!(fixture.resolve(&spec).is_err(), "type {section}.{key}");
            }
        }
        fs::remove_file(fixture.base.join("runtime/iris/core/desktop.py")).unwrap();
        assert!(fixture.resolve(&Fixture::spec()).is_err());
    }

    #[test]
    fn metadata_requires_matching_pin_schema_and_hermes_root() {
        let fixture = Fixture::new();
        for invalid in [json!({"schema_version":2,"version":"0.21.4","revision":HERMES_REV}),
            json!({"schema_version":1,"version":"0.21.5","revision":HERMES_REV}),
            json!({"schema_version":1,"version":"0.21.4","revision":IRIS_REV}),
            json!({"schema_version":1,"version":"0.21.4","revision":HERMES_REV,"extra":true})] {
            fs::write(fixture.base.join("runtime/hermes/pin.json"), serde_json::to_vec(&invalid).unwrap()).unwrap();
            assert!(fixture.resolve(&Fixture::spec()).is_err());
        }
        fs::write(fixture.base.join("pin.json"), serde_json::to_vec(&Fixture::metadata()).unwrap()).unwrap();
        let mut spec = Fixture::spec();
        spec["hermes"]["metadata"] = json!("pin.json");
        assert!(fixture.resolve(&spec).is_err());
        fs::write(fixture.base.join("runtime/hermes/pin.json"), format!("{{\"schema_version\":1,\"version\":\"0.21.4\",\"version\":\"0.21.4\",\"revision\":\"{HERMES_REV}\"}}")).unwrap();
        assert!(fixture.resolve(&Fixture::spec()).is_err());
    }

    fn link_dir(target: &Path, link: &Path) -> bool {
        #[cfg(unix)] {
            std::os::unix::fs::symlink(target, link).unwrap();
            true
        }
        #[cfg(windows)] {
            let mut command = Command::new("cmd.exe");
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000);
            let result = command.args(["/D", "/C", "mklink", "/J"])
                .arg(link.to_string_lossy().replace('/', "\\"))
                .arg(target.to_string_lossy().replace('/', "\\")).output().unwrap();
            if !result.status.success() {
                panic!("No se pudo crear junction de prueba: {}", String::from_utf8_lossy(&result.stderr));
            }
            result.status.success()
        }
    }

    fn link_file(target: &Path, link: &Path) -> bool {
        #[cfg(unix)] {
            std::os::unix::fs::symlink(target, link).unwrap();
            true
        }
        #[cfg(windows)] {
            match std::os::windows::fs::symlink_file(target, link) {
                Ok(()) => true,
                Err(error) => { eprintln!("SKIPPED: symlink de archivo requiere permiso Windows: {error}"); false },
            }
        }
    }

    #[test]
    fn directory_symlink_or_junction_cannot_escape_runtime_root() {
        for (directory, filename) in [("runtime/iris/core", "desktop.py"), ("runtime/hermes/hermes_cli", "__init__.py")] {
            let fixture = Fixture::new();
            // Alias target is inside installation but outside the declared root.
            let outside = fixture.base.join("other component");
            fs::create_dir_all(&outside).unwrap();
            fs::write(outside.join(filename), "outside runtime").unwrap();
            let anchor = fixture.base.join(directory);
            fs::remove_dir_all(&anchor).unwrap();
            if !link_dir(&outside, &anchor) { return; }
            assert!(fixture.resolve(&Fixture::spec()).is_err(), "{directory}");
        }
    }

    #[test]
    fn root_symlink_or_junction_cannot_escape_installation() {
        let fixture = Fixture::new();
        let outside = fixture.temporary.join("external iris");
        fs::create_dir_all(&outside).unwrap();
        let link = fixture.base.join("external");
        if !link_dir(&outside, &link) { return; }
        let mut spec = Fixture::spec();
        spec["iris"]["root"] = json!("external");
        assert!(fixture.resolve(&spec).is_err());
    }

    #[test]
    fn directory_alias_cannot_redirect_executable_cli_metadata_or_assets() {
        let fixture = Fixture::new();
        // A textual-prefix comparison would incorrectly accept this sibling.
        let outside = fixture.temporary.join("Iris distribución con espacios exterior");
        fs::create_dir_all(&outside).unwrap();
        fs::write(outside.join("python.exe"), "outside python").unwrap();
        fs::write(outside.join("hermes.exe"), "outside cli").unwrap();
        fs::write(outside.join("pin.json"), serde_json::to_vec(&Fixture::metadata()).unwrap()).unwrap();
        if !link_dir(&outside, &fixture.base.join("redirected")) { return; }
        for (section, key, value) in [
            ("iris", "python", "redirected/python.exe"),
            ("iris", "assets", "redirected"),
            ("hermes", "python", "redirected/python.exe"),
            ("hermes", "cli", "redirected/hermes.exe"),
            ("hermes", "metadata", "redirected/pin.json"),
        ] {
            let mut spec = Fixture::spec();
            spec[section][key] = json!(value);
            assert!(fixture.resolve(&spec).is_err(), "{section}.{key}");
        }
    }

    #[test]
    fn file_symlink_rejects_external_executable_metadata_and_descriptor_when_supported() {
        let fixture = Fixture::new();
        let outside = fixture.temporary.join("external file");
        fs::write(&outside, "outside").unwrap();
        let link = fixture.base.join("linked.exe");
        if !link_file(&outside, &link) { return; }
        let mut spec = Fixture::spec();
        spec["iris"]["python"] = json!("linked.exe");
        assert!(fixture.resolve(&spec).is_err());
        fs::write(&outside, serde_json::to_vec(&Fixture::metadata()).unwrap()).unwrap();
        spec = Fixture::spec();
        spec["hermes"]["metadata"] = json!("linked.exe");
        assert!(fixture.resolve(&spec).is_err());
        fs::write(&outside, serde_json::to_vec(&Fixture::spec()).unwrap()).unwrap();
        fs::remove_file(fixture.base.join("runtime.json")).unwrap();
        assert!(link_file(&outside, &fixture.base.join("runtime.json")));
        assert!(resolve_runtime(&fixture.base, None).is_err());
    }

    #[test]
    fn canonical_descriptor_parent_must_preserve_packaged_base() {
        let base = Path::new("Iris distribución");
        assert!(packaged_descriptor_has_base(&base.join("runtime.json"), base));
        assert!(packaged_descriptor_has_base(&base.join("runtime-pin.json"), base));
        assert!(!packaged_descriptor_has_base(&base.join("config/runtime.json"), base));
        assert!(!packaged_descriptor_has_base(Path::new("Iris distribución exterior/runtime.json"), base));
    }

    #[test]
    fn file_descriptor_alias_preserves_base_when_supported() {
        let fixture = Fixture::new();
        let alias_target = fixture.base.join("runtime-pin.json");
        fs::write(&alias_target, serde_json::to_vec(&Fixture::spec()).unwrap()).unwrap();
        let descriptor = fixture.base.join("runtime.json");
        if !link_file(&alias_target, &descriptor) { return; }
        let runtime = resolve_runtime(&fixture.base, None).unwrap();
        assert_eq!(runtime.descriptor_path, Some(alias_target.canonicalize().unwrap()));
        fs::remove_file(&descriptor).unwrap();
        fs::create_dir_all(fixture.base.join("config")).unwrap();
        let nested = fixture.base.join("config/runtime.json");
        fs::write(&nested, serde_json::to_vec(&Fixture::spec()).unwrap()).unwrap();
        assert!(link_file(&nested, &descriptor));
        assert!(resolve_runtime(&fixture.base, None).is_err());
    }

    #[test]
    fn controller_command_propagates_descriptor_and_sanitizes_environment() {
        let fixture = Fixture::new();
        let runtime = fixture.resolve(&Fixture::spec()).unwrap();
        let command = controller_command(&runtime);
        assert_eq!(command.get_program(), runtime.iris_python.as_os_str());
        // Children receive plain paths, comparable with what Windows reports for processes.
        let plain_root = plain_path(&runtime.iris_root);
        assert_eq!(command.get_current_dir(), Some(plain_root.as_path()));
        assert!(!plain_root.to_string_lossy().starts_with(r"\\?\"));
        assert_eq!(command.get_args().collect::<Vec<_>>(), ["-E", "-s", "-B", "-X", "utf8", "-u", "-m", "core.desktop"].map(OsStr::new));
        let env = command.get_envs().collect::<std::collections::BTreeMap<_, _>>();
        let descriptor = plain_path(runtime.descriptor_path.as_deref().unwrap());
        assert_eq!(env[OsStr::new("IRIS_RUNTIME_DESCRIPTOR")], Some(descriptor.as_os_str()));
        assert!(!descriptor.to_string_lossy().starts_with(r"\\?\") && descriptor.is_file());
        for name in ["PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "VIRTUAL_ENV", "__PYVENV_LAUNCHER__", "UV_PROJECT_ENVIRONMENT", "HERMES_REPO", "HERMES_AGENT_ROOT"] {
            assert_eq!(env.get(OsStr::new(name)), Some(&None), "{name}");
        }
        let legacy = fixture.resolve(&json!({"schema_version":1,"mode":"development","project_root":"runtime/iris","python":"runtime/iris/python.exe"})).unwrap();
        let legacy_command = controller_command(&legacy);
        assert_eq!(legacy_command.get_args().collect::<Vec<_>>(), ["-u", "-m", "core.desktop"].map(OsStr::new));
        let legacy_env = legacy_command.get_envs().collect::<std::collections::BTreeMap<_, _>>();
        for name in ["HERMES_REPO", "HERMES_AGENT_ROOT"] {
            assert!(!legacy_env.contains_key(OsStr::new(name)), "development inherits {name}");
        }
    }
}
