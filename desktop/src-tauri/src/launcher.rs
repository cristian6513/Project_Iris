//! Root launcher of a versioned installation (A08.8).
//!
//! The installer puts `Iris.exe` at the installation root next to
//! `install.json`; each version lives complete in `v/<id>/`. The root copy only
//! starts the active version. It reads three fields, a format every version
//! keeps, and ignores the rest. Its only write is the launch counter of a
//! version that has not confirmed yet: past `TRIAL_LIMIT` it starts the
//! previous version, whose controller takes the activation back (Python
//! `core.iris_install.startup_check`). A version folder never has
//! `install.json` beside its own `Iris.exe`, so a version runs as before.
use serde::{Deserialize, Serialize};
use std::{
    fs,
    io::ErrorKind,
    path::{Path, PathBuf},
};

pub const STATE: &str = "install.json";
pub const TRIAL: &str = "install.trial";
pub const TRIAL_LIMIT: u32 = 3;
const MAX_STATE_BYTES: u64 = 256 * 1024;

#[derive(Deserialize)]
struct State {
    schema: u32,
    active: String,
    previous: Option<String>,
    confirmed: bool,
}

#[derive(Deserialize, Serialize)]
struct Trial {
    target: String,
    launches: u32,
}

fn valid_id(id: &str) -> bool {
    id.len() == 8 && id.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
}

/// `v/<id>/Iris.exe` when it is a regular file that stays inside the installation.
fn version_executable(root: &Path, id: &str) -> Option<PathBuf> {
    if !valid_id(id) {
        return None;
    }
    let executable = root.join("v").join(id).join("Iris.exe");
    let metadata = fs::symlink_metadata(&executable).ok()?;
    if !metadata.is_file() {
        return None;
    }
    let base = root.canonicalize().ok()?;
    executable.canonicalize().ok().filter(|resolved| resolved.starts_with(&base))?;
    Some(executable)
}

fn read_trial(root: &Path) -> Option<Trial> {
    let bytes = fs::read(root.join(TRIAL)).ok()?;
    if bytes.len() > 4096 {
        return None;
    }
    serde_json::from_slice(&bytes).ok()
}

/// The version a root launcher must start; `None` when this executable is a version itself.
pub fn launch_target(root: &Path) -> Result<Option<PathBuf>, String> {
    let path = root.join(STATE);
    let metadata = match fs::symlink_metadata(&path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == ErrorKind::NotFound => return Ok(None),
        Err(_) => return Err("No se pudo leer el estado de la instalación de Iris.".into()),
    };
    if !metadata.is_file() || metadata.len() > MAX_STATE_BYTES {
        return Err("El estado de la instalación de Iris no es válido.".into());
    }
    let bytes = fs::read(&path).map_err(|_| "No se pudo leer el estado de la instalación de Iris.")?;
    let state: State = serde_json::from_slice(&bytes).map_err(|_| "El estado de la instalación de Iris no es válido.")?;
    if state.schema != 1 {
        return Err("El estado de la instalación de Iris tiene un formato desconocido.".into());
    }
    let active = version_executable(root, &state.active)
        .ok_or("Falta la versión activa de Iris en esta instalación. Reinstala Iris.")?;
    if state.confirmed {
        return Ok(Some(active));
    }
    let mut trial = read_trial(root)
        .filter(|trial| trial.target == state.active)
        .unwrap_or(Trial { target: state.active.clone(), launches: 0 });
    trial.launches = trial.launches.saturating_add(1);
    // Best effort: an installation that cannot record launches keeps starting the new version.
    let _ = fs::write(root.join(TRIAL), serde_json::to_vec(&trial).unwrap_or_default());
    if trial.launches > TRIAL_LIMIT {
        if let Some(previous) = state.previous.as_deref().and_then(|id| version_executable(root, id)) {
            return Ok(Some(previous));
        }
    }
    Ok(Some(active))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::time::{SystemTime, UNIX_EPOCH};

    static SERIAL: AtomicUsize = AtomicUsize::new(0);
    const ACTIVE: &str = "0a1b2c3d";
    const PREVIOUS: &str = "9f8e7d6c";

    struct Fixture {
        temporary: PathBuf,
        root: PathBuf,
    }

    impl Fixture {
        fn new() -> Self {
            let stamp = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos();
            let serial = SERIAL.fetch_add(1, Ordering::Relaxed);
            let temporary = std::env::temp_dir().join(format!("iris-launcher-rust-{}-{stamp}-{serial}", std::process::id()));
            let root = temporary.join("Iris OS ñ");
            for id in [ACTIVE, PREVIOUS] {
                fs::create_dir_all(root.join("v").join(id)).unwrap();
                fs::write(root.join("v").join(id).join("Iris.exe"), id).unwrap();
            }
            Self { temporary, root }
        }

        fn state(&self, value: serde_json::Value) {
            fs::write(self.root.join(STATE), serde_json::to_vec(&value).unwrap()).unwrap();
        }

        fn target(&self) -> Result<Option<PathBuf>, String> {
            launch_target(&self.root)
        }
    }

    impl Drop for Fixture {
        fn drop(&mut self) {
            // Only the private directory created above, never a user installation.
            if self.temporary.starts_with(std::env::temp_dir())
                && self.temporary.file_name().unwrap().to_string_lossy().starts_with("iris-launcher-rust-") {
                let _ = fs::remove_dir_all(&self.temporary);
            }
        }
    }

    fn version(fixture: &Fixture, id: &str) -> PathBuf {
        fixture.root.join("v").join(id).join("Iris.exe")
    }

    #[test]
    fn a_version_folder_without_install_state_runs_itself() {
        let fixture = Fixture::new();
        assert_eq!(launch_target(&fixture.root.join("v").join(ACTIVE)).unwrap(), None);
    }

    #[test]
    fn confirmed_active_version_starts_and_unknown_fields_are_ignored() {
        let fixture = Fixture::new();
        fixture.state(json!({"schema": 1, "active": ACTIVE, "previous": PREVIOUS, "confirmed": true,
                             "pending": null, "future_field": {"anything": [1, 2]}}));
        assert_eq!(fixture.target().unwrap(), Some(version(&fixture, ACTIVE)));
        assert!(!fixture.root.join(TRIAL).exists());
    }

    #[test]
    fn unconfirmed_version_gets_limited_launches_then_previous() {
        let fixture = Fixture::new();
        fixture.state(json!({"schema": 1, "active": ACTIVE, "previous": PREVIOUS, "confirmed": false}));
        for _ in 0..TRIAL_LIMIT {
            assert_eq!(fixture.target().unwrap(), Some(version(&fixture, ACTIVE)));
        }
        assert_eq!(fixture.target().unwrap(), Some(version(&fixture, PREVIOUS)));
        let trial: Trial = serde_json::from_slice(&fs::read(fixture.root.join(TRIAL)).unwrap()).unwrap();
        assert_eq!((trial.target.as_str(), trial.launches), (ACTIVE, TRIAL_LIMIT + 1));
    }

    #[test]
    fn a_counter_for_another_version_starts_over() {
        let fixture = Fixture::new();
        fixture.state(json!({"schema": 1, "active": ACTIVE, "previous": PREVIOUS, "confirmed": false}));
        fs::write(fixture.root.join(TRIAL), json!({"target": PREVIOUS, "launches": 99}).to_string()).unwrap();
        assert_eq!(fixture.target().unwrap(), Some(version(&fixture, ACTIVE)));
    }

    #[test]
    fn without_previous_the_unconfirmed_version_keeps_starting() {
        let fixture = Fixture::new();
        fixture.state(json!({"schema": 1, "active": ACTIVE, "previous": null, "confirmed": false}));
        for _ in 0..(TRIAL_LIMIT + 2) {
            assert_eq!(fixture.target().unwrap(), Some(version(&fixture, ACTIVE)));
        }
    }

    #[test]
    fn invalid_state_ids_and_missing_versions_are_reported() {
        let fixture = Fixture::new();
        for value in [json!({"schema": 2, "active": ACTIVE, "previous": null, "confirmed": true}),
                      json!({"schema": 1, "active": "../../x", "previous": null, "confirmed": true}),
                      json!({"schema": 1, "active": "0A1B2C3D", "previous": null, "confirmed": true}),
                      json!({"schema": 1, "active": "deadbeef", "previous": null, "confirmed": true}),
                      json!({"schema": 1, "previous": null, "confirmed": true})] {
            fixture.state(value);
            assert!(fixture.target().is_err());
        }
        fs::write(fixture.root.join(STATE), b"{not json").unwrap();
        assert!(fixture.target().is_err());
        fs::write(fixture.root.join(STATE), vec![b' '; (MAX_STATE_BYTES + 1) as usize]).unwrap();
        assert!(fixture.target().is_err());
    }

    #[test]
    fn a_directory_named_iris_exe_is_not_a_version() {
        let fixture = Fixture::new();
        fs::remove_file(version(&fixture, ACTIVE)).unwrap();
        fs::create_dir(version(&fixture, ACTIVE)).unwrap();
        fixture.state(json!({"schema": 1, "active": ACTIVE, "previous": PREVIOUS, "confirmed": true}));
        assert!(fixture.target().is_err());
    }

    #[cfg(windows)]
    #[test]
    fn a_junction_cannot_redirect_a_version_outside_the_installation() {
        let fixture = Fixture::new();
        let outside = fixture.temporary.join("outside");
        fs::create_dir_all(&outside).unwrap();
        fs::write(outside.join("Iris.exe"), "outside").unwrap();
        let link = fixture.root.join("v").join("11111111");
        let made = std::process::Command::new("cmd").args(["/C", "mklink", "/J"]).arg(&link).arg(&outside)
            .output().map(|o| o.status.success()).unwrap_or(false);
        if !made {
            return;
        }
        fixture.state(json!({"schema": 1, "active": "11111111", "previous": null, "confirmed": true}));
        assert!(fixture.target().is_err());
    }
}
