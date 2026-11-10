//! Category editing through the existing keyboard and paginated settings views.

use super::*;
use crate::{
    store::categories::{Category, CategoryError},
    ui::SettingItem,
};

fn category_name(category: &Category) -> String {
    if category.builtin && category.name == category.id {
        tr(&category.id)
    } else {
        category.name.clone()
    }
}

impl App {
    pub(crate) fn category_action(&mut self, action: &str, value: &str) -> Result<bool> {
        match action {
            "category-create" => {
                self.open_category_keyboard("category-create", "", "create_category")?;
            }
            "category-rename" => {
                let category = self
                    .store
                    .categories()?
                    .into_iter()
                    .find(|category| category.id == value);
                let Some(category) = category else {
                    self.notify(tr("category_missing"));
                    return Ok(true);
                };
                self.open_category_keyboard(
                    &format!("category-rename:{}", category.id),
                    &category_name(&category),
                    "rename",
                )?;
            }
            _ => return Ok(false),
        }
        Ok(true)
    }

    pub(crate) fn category_keyboard_submit(&mut self, target: &str, value: &str) -> Result<bool> {
        let rename = target.strip_prefix("category-rename:");
        if target != "category-create" && rename.is_none() {
            return Ok(false);
        }
        let category_labels = self.store.categories()?;
        let requested = value.trim().to_lowercase();
        let duplicate_label = category_labels.iter().any(|category| {
            Some(category.id.as_str()) != rename
                && category_name(category).to_lowercase() == requested
        });
        let result = if duplicate_label {
            Err(CategoryError::DuplicateName.into())
        } else if let Some(id) = rename {
            self.store.rename_category(id, value)
        } else {
            self.store.create_category(value)
        };
        if let Err(error) = result {
            self.open_category_keyboard(
                target,
                value,
                if rename.is_some() {
                    "rename"
                } else {
                    "create_category"
                },
            )?;
            let label = match error.downcast_ref::<CategoryError>() {
                Some(CategoryError::InvalidName) => "category_name_invalid",
                Some(CategoryError::DuplicateName) => "category_name_duplicate",
                Some(CategoryError::MissingCategory) => "category_missing",
                None => "error",
            };
            self.notify(tr(label));
            return Ok(true);
        }
        self.modal.clear();
        self.modal_context.clear();
        self.keyboard.set_text("");
        self.keyboard_target.clear();
        self.refresh_setting_details()?;
        self.notify(tr("saved"));
        Ok(true)
    }

    pub(crate) fn category_setting_items(&self) -> Result<Vec<SettingItem>> {
        let mut rows: Vec<_> = self
            .store
            .categories()?
            .into_iter()
            .map(|category| SettingItem {
                id: category.id.clone().into(),
                title: category_name(&category).into(),
                value: category.book_count.to_string().into(),
                group: "".into(),
                enabled: true,
            })
            .collect();
        rows.push(SettingItem {
            id: "".into(),
            title: tr("uncategorized").into(),
            value: self.store.uncategorized_book_count()?.to_string().into(),
            group: "".into(),
            enabled: false,
        });
        Ok(rows)
    }

    pub(crate) fn category_choice_rows(&self) -> Result<Vec<(String, String, String)>> {
        let mut rows: Vec<_> = self
            .store
            .categories()?
            .into_iter()
            .map(|category| {
                (
                    format!("category:{}", category.id),
                    category_name(&category),
                    category.book_count.to_string(),
                )
            })
            .collect();
        rows.push(("category:".into(), tr("uncategorized"), String::new()));
        Ok(rows)
    }

    fn open_category_keyboard(&mut self, target: &str, value: &str, title: &str) -> Result<()> {
        self.open_keyboard(target, value)?;
        self.keyboard.set_mode("pinyin");
        if let Some(ui) = self.ui.upgrade() {
            let state = ui.global::<State>();
            state.set_keyboard_mode("pinyin".into());
            state.set_modal_title(tr(title).into());
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{app::tests::DemoRouteFixture, platform::DeviceProfile};

    #[test]
    fn builtin_display_names_are_localized_until_overridden() {
        let mut category = Category {
            id: "reading".into(),
            name: "reading".into(),
            builtin: true,
            book_count: 0,
        };
        assert_eq!(category_name(&category), tr("reading"));
        category.name = "In progress".into();
        assert_eq!(category_name(&category), "In progress");
        category.builtin = false;
        category.name = "Custom".into();
        assert_eq!(category_name(&category), "Custom");
    }

    #[test]
    fn category_creation_and_custom_rename_use_the_existing_keyboard_route() {
        let mut fixture = DemoRouteFixture::new("settings", DeviceProfile::Scribe, false);
        let app = &mut fixture.app;
        assert!(app.category_action("category-create", "").unwrap());
        assert_eq!(app.modal, "keyboard");
        assert_eq!(app.keyboard_target, "category-create");
        assert!(
            app.category_keyboard_submit("category-create", "Favorites")
                .unwrap()
        );
        assert!(app.modal.is_empty());
        assert!(app.category_action("category-rename", "Favorites").unwrap());
        assert_eq!(app.keyboard_target, "category-rename:Favorites");
        assert!(
            app.category_keyboard_submit("category-rename:Favorites", "Archive")
                .unwrap()
        );
        assert!(
            app.store
                .categories()
                .unwrap()
                .iter()
                .any(|category| category.id == "Archive")
        );
        assert!(app.category_setting_items().unwrap().iter().any(|row| {
            row.id.as_str() == "Archive" && row.title.as_str() == "Archive" && row.enabled
        }));
        assert!(
            app.category_choice_rows()
                .unwrap()
                .iter()
                .any(|row| row.0 == "category:Archive")
        );
        assert!(
            app.category_choice_rows()
                .unwrap()
                .iter()
                .any(|row| row.0 == "category:")
        );
    }

    #[test]
    fn localized_duplicate_and_invalid_names_leave_the_keyboard_open() {
        let mut fixture = DemoRouteFixture::new("settings", DeviceProfile::Pw6, false);
        let app = &mut fixture.app;
        assert!(
            app.category_keyboard_submit("category-create", &tr("reading"))
                .unwrap()
        );
        assert_eq!(app.modal, "keyboard");
        assert_eq!(app.keyboard_target, "category-create");
        assert_eq!(app.store.categories().unwrap().len(), 3);
        assert!(
            app.category_keyboard_submit("category-create", "\n")
                .unwrap()
        );
        assert_eq!(app.modal, "keyboard");
        assert!(!app.category_action("another-action", "").unwrap());
        assert!(!app.category_keyboard_submit("search", "term").unwrap());
    }
}
