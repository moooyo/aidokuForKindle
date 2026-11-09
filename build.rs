use std::{env, fs, path::PathBuf};

fn main() {
    println!("cargo:rerun-if-changed=assets/i18n/zh-CN.json");
    let output = PathBuf::from(env::var_os("OUT_DIR").expect("Cargo output directory"));
    let labels: serde_json::Map<String, serde_json::Value> = serde_json::from_slice(
        &fs::read("assets/i18n/zh-CN.json").expect("Read interface translations"),
    )
    .expect("Parse interface translations");
    let mut translations = String::from("export global Strings {\n");
    for (key, value) in labels {
        let value = serde_json::to_string(value.as_str().expect("Translation must be text"))
            .expect("Escape interface translation");
        translations.push_str(&format!(
            "    out property <string> {}: {};\n",
            key.replace('_', "-"),
            value
        ));
    }
    translations.push_str("}\n");
    fs::write(output.join("strings.slint"), translations).expect("Write generated translations");
    slint_build::compile_with_config(
        "ui/app.slint",
        slint_build::CompilerConfiguration::new()
            .with_include_paths(vec![output])
            .with_style("fluent".into()),
    )
    .expect("Compile the application interface");
}
