#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod launcher;
mod runtime;

use std::{
    fs::{self, OpenOptions},
    io::{BufRead, BufReader},
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::{atomic::{AtomicBool, Ordering}, Arc, Mutex},
    time::Duration,
};
use tauri::{
    menu::{Menu, MenuItem}, tray::TrayIconBuilder, webview::NewWindowResponse,
    Manager, WebviewUrl, WebviewWindowBuilder, WindowEvent,
};

#[derive(Default)]
struct RuntimeState {
    child: Mutex<Option<Child>>,
    error: Mutex<Option<String>>,
    exiting: AtomicBool,
    launcher_error: Mutex<Option<String>>,
}

fn is_local_url(url: &tauri::Url) -> bool {
    url.scheme() == "http" && url.host_str() == Some("127.0.0.1")
        && url.port().is_some() && url.username().is_empty() && url.password().is_none()
}

fn is_external_url(url: &tauri::Url) -> bool {
    matches!(url.scheme(), "https" | "http") && url.host_str().is_some()
        && url.username().is_empty() && url.password().is_none()
        && !matches!(url.host_str(), Some("127.0.0.1" | "localhost" | "[::1]" | "tauri.localhost"))
}

fn hidden_command(command: &mut Command) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000); // CREATE_NO_WINDOW, no console flashes.
    }
    #[cfg(not(windows))]
    let _ = command;
}

fn open_external(url: &tauri::Url) {
    if !is_external_url(url) { return; }
    #[cfg(windows)]
    let mut command = {
        let mut cmd = Command::new("rundll32.exe");
        cmd.args(["url.dll,FileProtocolHandler", url.as_str()]);
        cmd
    };
    #[cfg(target_os = "macos")]
    let mut command = { let mut cmd = Command::new("open"); cmd.arg(url.as_str()); cmd };
    #[cfg(all(not(windows), not(target_os = "macos")))]
    let mut command = { let mut cmd = Command::new("xdg-open"); cmd.arg(url.as_str()); cmd };
    hidden_command(&mut command);
    let _ = command.stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()).spawn();
}

fn show_main(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show(); let _ = window.unminimize(); let _ = window.set_focus();
    }
}

fn fallback_url() -> tauri::Url {
    if cfg!(windows) { "http://tauri.localhost/" } else { "tauri://localhost/" }.parse().unwrap()
}

fn report_error(app: &tauri::AppHandle, state: &RuntimeState, detail: String) {
    *state.error.lock().unwrap() = Some(detail);
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.navigate(fallback_url());
        let _ = window.show();
    }
}

fn launch_runtime(app: tauri::AppHandle, state: Arc<RuntimeState>) {
    let result = (|| -> Result<(), String> {
        if let Some(message) = state.launcher_error.lock().unwrap().take() {
            return Err(message);
        }
        let base = std::env::current_exe().map_err(|e| e.to_string())?
            .parent().ok_or("No se encuentra la carpeta de Iris.")?.to_path_buf();
        // Compiled only into debug builds: a release executable must not carry
        // the folder it was built in (A08.7 staging is moved and distributed).
        #[cfg(debug_assertions)]
        let debug_project = Some(PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../.."));
        #[cfg(not(debug_assertions))]
        let debug_project: Option<PathBuf> = None;
        let runtime = runtime::resolve_runtime(&base, debug_project.as_deref())?;
        let logs = app.path().app_log_dir().map_err(|e| e.to_string())?;
        fs::create_dir_all(&logs).map_err(|e| e.to_string())?;
        let log_path = logs.join("desktop.log");
        let log = OpenOptions::new().create(true).append(true).open(&log_path).map_err(|e| e.to_string())?;
        let mut command = runtime::controller_command(&runtime);
        command.stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(log);
        hidden_command(&mut command);
        let mut child = command.spawn().map_err(|e| format!("No se pudo iniciar Iris: {e}"))?;
        let output = child.stdout.take().ok_or("No se pudo abrir el canal de inicio.")?;
        *state.child.lock().unwrap() = Some(child);
        let (tx, rx) = std::sync::mpsc::channel();
        std::thread::spawn(move || {
            let mut line = String::new();
            let read = BufReader::new(output).read_line(&mut line);
            let _ = tx.send(read.map(|_| line));
        });
        let line = rx.recv_timeout(Duration::from_secs(45))
            .map_err(|_| format!("Iris tardó demasiado en iniciar. Registro: {}", log_path.display()))?
            .map_err(|e| e.to_string())?;
        let hello: serde_json::Value = serde_json::from_str(&line)
            .map_err(|_| format!("El servicio de inicio se detuvo. Registro: {}", log_path.display()))?;
        if hello["type"] == "desktop_error" {
            // The controller explains a startup it refused (profile in use by another
            // version, folder too long...). Only its text is shown, never markup.
            let message = hello["message"].as_str().unwrap_or("No se pudo iniciar el servicio de Iris.");
            return Err(format!("{message} Registro: {}", log_path.display()));
        }
        let url: tauri::Url = hello["url"].as_str().ok_or("Respuesta de inicio incompleta.")?
            .parse().map_err(|_| "Dirección de inicio inválida.")?;
        if hello["type"] != "desktop_server" || !is_local_url(&url) || url.path() != "/desktop/" {
            return Err("El servicio devolvió una dirección no válida.".into());
        }
        if state.exiting.load(Ordering::SeqCst) { return Ok(()); }
        if let Some(window) = app.get_webview_window("main") {
            window.navigate(url).map_err(|e| e.to_string())?;
        }
        while !state.exiting.load(Ordering::SeqCst) {
            std::thread::sleep(Duration::from_secs(1));
            let mut guard = state.child.lock().unwrap();
            if let Some(child) = guard.as_mut() {
                if child.try_wait().map_err(|e| e.to_string())?.is_some() {
                    return Err(format!("El servicio de inicio se cerró. Vuelve a abrir Iris. Registro: {}", log_path.display()));
                }
            } else { break; }
        }
        Ok(())
    })();
    if let Err(error) = result {
        if !state.exiting.load(Ordering::SeqCst) {
            report_error(&app, &state, error);
        }
        stop_controller(&state);
    }
}

fn stop_controller(state: &RuntimeState) {
    if let Some(mut child) = state.child.lock().unwrap().take() {
        drop(child.stdin.take()); // EOF asks Python to close its own HTTP server.
        for _ in 0..30 {
            if matches!(child.try_wait(), Ok(Some(_))) { return; }
            std::thread::sleep(Duration::from_millis(100));
        }
        // Only the Child handle created above, never Core, Hermes or PID-file targets.
        let _ = child.kill();
        let _ = child.wait();
    }
}

/// A08.8: the root `Iris.exe` of a versioned installation starts the active version and exits.
fn redirect_to_active_version() -> Result<bool, String> {
    let executable = std::env::current_exe().map_err(|e| e.to_string())?;
    let root = executable.parent().ok_or("No se encuentra la carpeta de Iris.")?;
    let Some(target) = launcher::launch_target(root)? else { return Ok(false) };
    let mut command = Command::new(&target);
    command.args(std::env::args_os().skip(1));
    if let Some(folder) = target.parent() {
        command.current_dir(folder);
    }
    command.spawn().map_err(|e| format!("No se pudo abrir la versión activa de Iris: {e}"))?;
    Ok(true)
}

fn main() {
    let launcher_error = match redirect_to_active_version() {
        Ok(true) => return,
        Ok(false) => None,
        Err(message) => Some(message),
    };
    let state = Arc::new(RuntimeState::default());
    *state.launcher_error.lock().unwrap() = launcher_error;
    let setup_state = state.clone();
    let exit_state = state.clone();
    let close_state = state.clone();
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, args, _| {
            if args.iter().any(|arg| arg == "--iris-exit") { app.exit(0); }
            else { show_main(app); }
        }))
        .plugin(tauri_plugin_window_state::Builder::default()
            .with_state_flags(tauri_plugin_window_state::StateFlags::POSITION
                | tauri_plugin_window_state::StateFlags::SIZE
                | tauri_plugin_window_state::StateFlags::MAXIMIZED).build())
        .setup(move |app| {
            let error_state = setup_state.clone();
            WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .title("Iris OS").theme(Some(tauri::Theme::Dark)).inner_size(1280.0, 840.0).min_inner_size(800.0, 600.0)
                .background_color(tauri::window::Color(16, 17, 16, 255))
                .on_navigation(|url| {
                    let allowed = is_local_url(url) || url == &fallback_url();
                    if !allowed { open_external(url); }
                    allowed
                })
                .on_new_window(|url, _| { open_external(&url); NewWindowResponse::Deny })
                .on_page_load(move |window, _| {
                    if let Some(error) = error_state.error.lock().unwrap().as_ref() {
                        let detail = serde_json::to_string(error).unwrap();
                        let _ = window.eval(&format!(
                            "document.getElementById('message').textContent='No se pudo abrir Iris';document.getElementById('detail').textContent={detail};"
                        ));
                    }
                }).build()?;
            let open = MenuItem::with_id(app, "open", "Abrir Iris", true, None::<&str>)?;
            let exit = MenuItem::with_id(app, "exit", "Salir de Iris Desktop", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&open, &exit])?;
            let mut tray = TrayIconBuilder::new().tooltip("Iris · servicios en segundo plano")
                .menu(&menu).on_menu_event(|app, event| match event.id.as_ref() {
                    "open" => show_main(app), "exit" => app.exit(0), _ => {}
                });
            if let Some(icon) = app.default_window_icon() { tray = tray.icon(icon.clone()); }
            tray.build(app)?;
            let handle = app.handle().clone();
            let runtime_state = setup_state.clone();
            std::thread::spawn(move || launch_runtime(handle, runtime_state));
            Ok(())
        })
        .on_window_event(move |window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                if !close_state.exiting.load(Ordering::SeqCst) {
                    api.prevent_close(); let _ = window.hide();
                }
            }
        })
        .build(tauri::generate_context!()).expect("Unable to build Iris Desktop");
    app.run(move |_, event| {
        if let tauri::RunEvent::Exit = event {
            exit_state.exiting.store(true, Ordering::SeqCst);
            stop_controller(&exit_state);
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn local_urls_are_literal_loopback_only() {
        for value in ["http://127.0.0.1:8000/desktop/", "http://127.0.0.1:8766/face/"] {
            assert!(is_local_url(&value.parse().unwrap()));
        }
        for value in ["https://example.com/", "http://127.0.0.1.evil.test:8000/", "file:///c:/private", "http://user:secret@127.0.0.1:8000/"] {
            assert!(!is_local_url(&value.parse().unwrap()));
        }
    }
    #[test]
    fn external_navigation_rejects_native_protocols_and_local_hosts() {
        assert!(is_external_url(&"https://auth.example.com/authorize?state=x".parse().unwrap()));
        for value in ["file:///C:/Windows", "javascript:alert(1)", "ms-settings:privacy", "http://127.0.0.1:9/", "http://localhost:8000/", "https://user:secret@example.com/"] {
            assert!(!is_external_url(&value.parse().unwrap()));
        }
    }
}
