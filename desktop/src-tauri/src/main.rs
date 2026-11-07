// Mini-TMS CAT: десктопная оболочка над веб-клиентом (см. tauri.conf.json frontendDist).
// Адрес сервера вводится на экране входа и хранится в localStorage вебвью.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    tauri::Builder::default()
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
