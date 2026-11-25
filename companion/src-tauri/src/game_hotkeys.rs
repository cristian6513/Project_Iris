//! Only explicit game-session shortcuts. No keyboard hook or input capture.
use serde::Deserialize;
use std::sync::{mpsc, Mutex};
use std::time::Duration;
use tauri::{Emitter, EventTarget, Manager};

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Bindings {
    pub help: String,
    pub mute: String,
    pub exit: String,
}

#[derive(Clone, Debug)]
struct Key {
    modifiers: u32,
    code: u32,
}

fn parse_key(text: &str) -> Result<Key, String> {
    let parts: Vec<_> = text.split('+').map(str::trim).collect();
    let mut modifiers = 0;
    if parts.len() < 3 || parts.len() > 4 {
        return Err("Usa dos modificadores y una tecla: Ctrl+Alt+F8.".into());
    }
    for modifier in &parts[..parts.len() - 1] {
        let bit = match *modifier {
            "Ctrl" => 2,
            "Alt" => 1,
            "Shift" => 4,
            _ => return Err("Modificadores admitidos: Ctrl, Alt y Shift.".into()),
        };
        if modifiers & bit != 0 {
            return Err("No repitas modificadores.".into());
        }
        modifiers |= bit;
    }
    let name = parts[parts.len() - 1];
    let code = if name.len() == 1
        && name
            .bytes()
            .all(|c| c.is_ascii_uppercase() || c.is_ascii_digit())
    {
        name.as_bytes()[0] as u32
    } else if let Some(number) = name.strip_prefix('F').and_then(|v| v.parse::<u32>().ok()) {
        if !(1..=11).contains(&number) {
            return Err("Usa F1–F11; F12 está reservada.".into());
        }
        0x70 + number - 1
    } else {
        return Err("Usa una letra mayúscula, un número o F1–F11.".into());
    };
    Ok(Key { modifiers, code })
}

fn validated(bindings: &Bindings) -> Result<Vec<Key>, String> {
    let keys: Vec<_> = [&bindings.help, &bindings.mute, &bindings.exit]
        .into_iter()
        .map(|text| parse_key(text))
        .collect::<Result<_, _>>()?;
    for (i, key) in keys.iter().enumerate() {
        if keys[..i]
            .iter()
            .any(|old| old.code == key.code && old.modifiers == key.modifiers)
        {
            return Err("Elige combinaciones distintas para las tres acciones.".into());
        }
    }
    Ok(keys)
}

enum Command {
    Configure(String, Option<Vec<Key>>, mpsc::Sender<Result<(), String>>),
    Clear,
    Quit,
}

#[derive(Default)]
pub struct GameHotkeys {
    sender: Mutex<Option<mpsc::Sender<Command>>>,
}

impl GameHotkeys {
    pub fn clear(&self) {
        if let Ok(guard) = self.sender.lock() {
            if let Some(sender) = guard.as_ref() {
                let _ = sender.send(Command::Clear);
            }
        }
    }

    pub fn configure(
        &self,
        app: tauri::AppHandle,
        session: String,
        bindings: Option<Bindings>,
    ) -> Result<(), String> {
        // Validate before touching any registered key. The worker alone calls Win32.
        let keys = bindings.as_ref().map(validated).transpose()?;
        if keys.is_some()
            && (session.len() != 32 || !session.bytes().all(|c| c.is_ascii_hexdigit()))
        {
            return Err("Activa primero una sesión de juego.".into());
        }
        let mut guard = self
            .sender
            .lock()
            .map_err(|_| "No pude acceder a los atajos.")?;
        let sender = guard
            .get_or_insert_with(|| {
                let (sender, receiver) = mpsc::channel();
                std::thread::spawn(move || worker(app, receiver));
                sender
            })
            .clone();
        drop(guard); // hide/exit must not wait for the registration acknowledgement
        let (reply, answer) = mpsc::channel();
        sender
            .send(Command::Configure(session, keys, reply))
            .map_err(|_| "El servicio de atajos no está disponible.")?;
        match answer.recv_timeout(Duration::from_secs(2)) {
            Ok(result) => result,
            Err(_) => {
                let _ = sender.send(Command::Clear);
                Err("Los atajos no respondieron; se solicitó desactivarlos.".into())
            }
        }
    }
}

impl Drop for GameHotkeys {
    fn drop(&mut self) {
        if let Ok(guard) = self.sender.lock() {
            if let Some(sender) = guard.as_ref() {
                let _ = sender.send(Command::Quit);
            }
        }
    }
}

#[cfg(windows)]
fn worker(app: tauri::AppHandle, receiver: mpsc::Receiver<Command>) {
    use windows_sys::Win32::UI::{
        Input::KeyboardAndMouse::{RegisterHotKey, UnregisterHotKey, MOD_NOREPEAT},
        WindowsAndMessaging::{PeekMessageW, MSG, PM_REMOVE, WM_HOTKEY},
    };
    let clear = || {
        for id in 810..813 {
            unsafe {
                UnregisterHotKey(std::ptr::null_mut(), id);
            }
        }
    };
    let mut session = String::new();
    loop {
        match receiver.recv_timeout(Duration::from_millis(10)) {
            Ok(Command::Quit) | Err(mpsc::RecvTimeoutError::Disconnected) => break,
            Ok(Command::Clear) => {
                session.clear();
                clear();
            }
            Ok(Command::Configure(next_session, keys, reply)) => {
                session.clear();
                clear();
                // Discard messages belonging to the preceding bindings/session.
                let mut old: MSG = unsafe { std::mem::zeroed() };
                while unsafe {
                    PeekMessageW(
                        &mut old,
                        std::ptr::null_mut(),
                        WM_HOTKEY,
                        WM_HOTKEY,
                        PM_REMOVE,
                    )
                } != 0
                {}
                let mut result = Ok(());
                if let Some(keys) = keys {
                    if !app
                        .get_webview_window("companion")
                        .and_then(|window| window.is_visible().ok())
                        .unwrap_or(false)
                    {
                        let _ = reply.send(Err(
                            "Companion está oculto; vuelve a abrirlo antes de activar atajos."
                                .into(),
                        ));
                        continue;
                    }
                    for (index, key) in keys.iter().enumerate() {
                        if unsafe {
                            RegisterHotKey(
                                std::ptr::null_mut(),
                                810 + index as i32,
                                key.modifiers | MOD_NOREPEAT,
                                key.code,
                            )
                        } == 0
                        {
                            clear();
                            result = Err("Windows no pudo registrar una combinación; puede estar ocupada. Ningún atajo de juego quedó activo.".into());
                            break;
                        }
                    }
                    if result.is_ok() {
                        session = next_session;
                    }
                }
                let _ = reply.send(result);
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {}
        }
        let mut msg: MSG = unsafe { std::mem::zeroed() };
        while unsafe {
            PeekMessageW(
                &mut msg,
                std::ptr::null_mut(),
                WM_HOTKEY,
                WM_HOTKEY,
                PM_REMOVE,
            )
        } != 0
        {
            if session.is_empty() {
                continue;
            }
            let Some(window) = app.get_webview_window("companion") else {
                session.clear();
                clear();
                continue;
            };
            if !window.is_visible().unwrap_or(false) {
                session.clear();
                clear();
                continue;
            }
            let action = match msg.wParam {
                810 => "help",
                811 => "mute",
                812 => "exit",
                _ => continue,
            };
            let _ = app.emit_to(
                EventTarget::webview_window("companion"),
                "iris-game-hotkey",
                serde_json::json!({"action": action, "session_id": session}),
            );
        }
    }
    clear();
}

#[cfg(not(windows))]
fn worker(_app: tauri::AppHandle, receiver: mpsc::Receiver<Command>) {
    for command in receiver {
        match command {
            Command::Configure(_, _, reply) => {
                let _ = reply.send(Err("Atajos de juego disponibles en Windows.".into()));
            }
            Command::Quit => break,
            Command::Clear => {}
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn recognizes_order_and_virtual_keys() {
        let a = parse_key("Ctrl+Alt+F8").unwrap();
        let b = parse_key("Alt+Ctrl+F8").unwrap();
        assert_eq!((a.modifiers, a.code), (b.modifiers, b.code));
        assert_eq!(a.code, 0x77);
        assert_eq!(parse_key("Ctrl+Shift+J").unwrap().code, 0x4a);
    }
    #[test]
    fn rejects_bare_reserved_and_malformed_keys() {
        for text in [
            "M",
            "Ctrl+M",
            "Ctrl+Alt+F12",
            "Win+Alt+A",
            "Ctrl+Ctrl+A",
            "Ctrl+Alt+F99",
            "Ctrl+Alt+Enter",
            "Ctrl+Alt+F0",
            "Ctrl+Alt+Shift+F8+X",
        ] {
            assert!(parse_key(text).is_err(), "{text}");
        }
    }
    #[test]
    fn rejects_equivalent_duplicates() {
        assert!(validated(&Bindings {
            help: "Ctrl+Alt+F8".into(),
            mute: "Alt+Ctrl+F8".into(),
            exit: "Ctrl+Alt+F10".into()
        })
        .is_err());
    }
}
