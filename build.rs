use std::{env, fs, path::PathBuf};

fn main() {
    for locale in ["zh-CN", "zh-TW", "en", "ja"] {
        println!("cargo:rerun-if-changed=assets/i18n/{locale}.json");
    }
    let output = PathBuf::from(env::var_os("OUT_DIR").expect("Cargo output directory"));
    let labels: serde_json::Map<String, serde_json::Value> = serde_json::from_slice(
        &fs::read("assets/i18n/zh-CN.json").expect("Read interface translations"),
    )
    .expect("Parse interface translations");
    let mut translations = String::from(
        "export global Strings {\n    in-out property <string> locale-code: \"zh-CN\";\n",
    );
    let mut setters = String::from(
        "pub fn apply_ui_labels(ui: &crate::ui::AppWindow) -> bool {\n\
         use slint::ComponentHandle;\n\
         let labels = ui.global::<crate::ui::Strings>();\n\
         let code = language();\n\
         if labels.get_locale_code().as_str() == code { return false; }\n",
    );
    for (key, value) in labels {
        let value = serde_json::to_string(value.as_str().expect("Translation must be text"))
            .expect("Escape interface translation");
        translations.push_str(&format!(
            "    in-out property <string> {}: {};\n",
            key.replace('_', "-"),
            value
        ));
        setters.push_str(&format!(
            "    labels.set_{key}(translate({key:?}).into());\n"
        ));
    }
    translations.push_str("}\n");
    setters.push_str("    labels.set_locale_code(code.into());\n    true\n}\n");
    fs::write(output.join("strings.slint"), translations).expect("Write generated translations");
    fs::write(output.join("translation_setters.rs"), setters)
        .expect("Write generated translation setters");
    slint_build::compile_with_config(
        "ui/app.slint",
        slint_build::CompilerConfiguration::new()
            .with_include_paths(vec![output])
            .with_style("fluent".into()),
    )
    .expect("Compile the application interface");
}
