//! Background backup/log operations and reviewable restore preparation.

use super::*;
use crate::{
    backup::{BackupInfo, BackupKind, BackupManager, PreparedRestore, RestoreReport},
    ui::SettingItem,
};

use super::logging::{LogManager, LogOutcome};

pub(super) struct DataManagementState {
    pub manager: BackupManager,
    pub logs: LogManager,
    pub backups: Vec<BackupInfo>,
    pub pending_restore: Option<PreparedRestore>,
    pub inflight: Option<u64>,
    pub last_export: Option<PathBuf>,
    pub last_message: String,
}

impl DataManagementState {
    pub fn open(data_dir: &Path, write_logs: bool) -> Result<Self> {
        Ok(Self {
            manager: BackupManager::open(data_dir)?,
            logs: LogManager::open(data_dir, write_logs)?,
            backups: Vec::new(),
            pending_restore: None,
            inflight: None,
            last_export: None,
            last_message: String::new(),
        })
    }

    /// Release the slot before discarding lifecycle-stale job messages.
    pub fn finish_job(&mut self, id: u64) -> bool {
        if self.inflight == Some(id) {
            self.inflight = None;
            true
        } else {
            false
        }
    }

    pub fn take_pending_restore(&mut self) -> Result<PreparedRestore> {
        self.pending_restore
            .take()
            .context("No checked metadata restore is awaiting confirmation")
    }

    pub fn cancel_pending_restore(&mut self) {
        self.pending_restore = None;
    }
}

pub(super) enum DataManagementResult {
    BackupCreated(BackupInfo),
    BackupsListed(Vec<BackupInfo>),
    RestorePrepared(PreparedRestore),
    RestoreApplied(RestoreReport),
    LogsExported(PathBuf),
    LogsCleared,
}

impl App {
    /// Handle settings commands and choice-row identifiers. The final
    /// `data-restore-confirm` action intentionally belongs to the integration
    /// owner, which must drain workers and fence source epochs before calling
    /// BackupManager::apply_restore on a background worker.
    pub(super) fn data_management_action(&mut self, action: &str, value: &str) -> Result<bool> {
        if let Some(id) = action.strip_prefix("data-backup:") {
            return self.data_management_action("backup-prepare", id);
        }
        match action {
            "backup-create" | "backup-now" => {
                self.ensure_data_management_idle()?;
                self.save_reader_progress()?;
                self.checkpoint_navigation(true)?;
                let manager = self.data_management.manager.clone();
                let logs = self.data_management.logs.clone();
                self.start_data_management_job(move || {
                    let info = manager.create()?;
                    let _ = logs.record("backup.created", LogOutcome::Success);
                    Ok(DataManagementResult::BackupCreated(info))
                })?;
            }
            "backup-restore" | "restore-backup" => {
                self.ensure_data_management_idle()?;
                let manager = self.data_management.manager.clone();
                self.start_data_management_job(move || {
                    Ok(DataManagementResult::BackupsListed(manager.list()?))
                })?;
            }
            "backup-prepare" => {
                self.ensure_data_management_idle()?;
                self.data_management.cancel_pending_restore();
                let manager = self.data_management.manager.clone();
                let id = value.to_owned();
                self.start_data_management_job(move || {
                    Ok(DataManagementResult::RestorePrepared(
                        manager.prepare_restore(&id)?,
                    ))
                })?;
            }
            "logs-export" | "export-logs" => {
                self.ensure_data_management_idle()?;
                let logs = self.data_management.logs.clone();
                self.start_data_management_job(move || {
                    let path = logs.export()?;
                    let _ = logs.record("logs.exported", LogOutcome::Success);
                    Ok(DataManagementResult::LogsExported(path))
                })?;
            }
            "logs-clear" | "clear-logs" => {
                self.show_choices(
                    &tr("clear_logs"),
                    "logs-clear-confirm",
                    vec![
                        (
                            "data-logs-clear-confirm".into(),
                            tr("clear_logs"),
                            tr("clear_logs_help"),
                        ),
                        ("close".into(), tr("cancel"), String::new()),
                    ],
                )?;
            }
            "data-logs-clear-confirm" | "logs-clear-confirm" => {
                anyhow::ensure!(
                    self.modal_context == "logs-clear-confirm",
                    "The log-clear confirmation is no longer active"
                );
                self.ensure_data_management_idle()?;
                self.modal.clear();
                self.modal_context.clear();
                let logs = self.data_management.logs.clone();
                self.start_data_management_job(move || {
                    logs.clear()?;
                    Ok(DataManagementResult::LogsCleared)
                })?;
            }
            "data-restore-cancel" => {
                self.data_management.cancel_pending_restore();
                self.modal.clear();
                self.modal_context.clear();
            }
            _ => return Ok(false),
        }
        Ok(true)
    }

    pub(super) fn accept_data_management(&mut self, result: DataManagementResult) -> Result<()> {
        match result {
            DataManagementResult::BackupCreated(info) => {
                self.data_management
                    .backups
                    .retain(|existing| existing.id != info.id);
                self.data_management.backups.insert(0, info);
                self.data_management.last_message = tr("metadata_backup_created");
                self.notify(self.data_management.last_message.clone());
            }
            DataManagementResult::BackupsListed(backups) => {
                self.data_management.backups = backups;
                if self.route != "settings" || self.interactions.settings_section != "backup" {
                    return Ok(());
                } else if self.data_management.backups.is_empty() {
                    self.notify(tr("metadata_backup_empty"));
                } else {
                    let mut rows = self
                        .data_management
                        .backups
                        .iter()
                        .map(|info| {
                            (
                                format!("data-backup:{}", info.id),
                                backup_title(info),
                                format!(
                                    "{} / {} · {}",
                                    info.counts.books,
                                    info.counts.chapters,
                                    tr("metadata_backup_counts")
                                ),
                            )
                        })
                        .collect::<Vec<_>>();
                    rows.push(("close".into(), tr("cancel"), String::new()));
                    self.show_choices(&tr("restore_backup"), "backup-select", rows)?;
                }
            }
            DataManagementResult::RestorePrepared(prepared) => {
                if self.route != "settings" || self.interactions.settings_section != "backup" {
                    // Dropping the checked staging copy cancels a review that
                    // became irrelevant while its background checks ran.
                    return Ok(());
                }
                let summary = format!(
                    "{} · {} / {}",
                    prepared.info.created_at,
                    prepared.info.counts.books,
                    prepared.info.counts.chapters
                );
                self.data_management.pending_restore = Some(prepared);
                self.show_choices(
                    &tr("metadata_restore_confirm"),
                    "backup-restore-confirm",
                    vec![
                        (
                            "data-restore-confirm".into(),
                            tr("restore_backup"),
                            tr("metadata_restore_help"),
                        ),
                        ("data-restore-cancel".into(), tr("cancel"), summary),
                    ],
                )?;
            }
            DataManagementResult::RestoreApplied(report) => {
                self.data_management.pending_restore = None;
                self.data_management
                    .backups
                    .retain(|info| info.id != report.rollback.id);
                let current_id = report.rollback.id.clone();
                self.data_management.backups.insert(0, report.rollback);
                self.data_management.backups.sort_by(|left, right| {
                    let left_current = left.id == current_id;
                    let right_current = right.id == current_id;
                    right_current
                        .cmp(&left_current)
                        .then_with(|| right.sequence.cmp(&left.sequence))
                        .then_with(|| right.created_at.cmp(&left.created_at))
                        .then_with(|| right.id.cmp(&left.id))
                });
                let mut automatic = 0;
                self.data_management.backups.retain(|info| {
                    if info.kind == BackupKind::Manual {
                        return true;
                    }
                    automatic += 1;
                    automatic <= 2
                });
                self.data_management.last_message = tr("metadata_restore_complete");
                self.notify(self.data_management.last_message.clone());
            }
            DataManagementResult::LogsExported(path) => {
                self.data_management.last_export = Some(path);
                self.data_management.last_message = tr("logs_exported");
                self.notify(self.data_management.last_message.clone());
            }
            DataManagementResult::LogsCleared => {
                self.data_management.last_message = tr("logs_cleared");
                self.notify(self.data_management.last_message.clone());
            }
        }
        if self.route == "settings" {
            self.refresh_setting_details()?;
        }
        Ok(())
    }

    /// Project cached data only. Filesystem enumeration and integrity work run
    /// after an explicit action on the background worker.
    pub(super) fn data_management_setting_items(
        &self,
        section: &str,
    ) -> Option<(Vec<SettingItem>, String)> {
        let item = |id: String, title: String, value: String, enabled: bool| SettingItem {
            id: id.into(),
            title: title.into(),
            value: value.into(),
            group: section.into(),
            enabled,
        };
        match section {
            "backup" => Some((
                self.data_management
                    .backups
                    .iter()
                    .map(|info| {
                        item(
                            format!("data-backup:{}", info.id),
                            backup_title(info),
                            format!("{} / {}", info.counts.books, info.counts.chapters),
                            self.data_management.inflight.is_none(),
                        )
                    })
                    .collect(),
                tr("metadata_backup_scope"),
            )),
            "logs" => {
                let mut items = vec![item(
                    String::new(),
                    tr("local_data_exports"),
                    self.data_dir
                        .join("exports")
                        .join("logs")
                        .to_string_lossy()
                        .into_owned(),
                    false,
                )];
                if let Some(path) = self.data_management.last_export.as_ref() {
                    items.push(item(
                        String::new(),
                        tr("export_logs"),
                        path.to_string_lossy().into_owned(),
                        false,
                    ));
                }
                Some((items, self.data_management.last_message.clone()))
            }
            _ => None,
        }
    }

    fn ensure_data_management_idle(&self) -> Result<()> {
        anyhow::ensure!(
            self.data_management.inflight.is_none(),
            "{}",
            tr("data_operation_busy")
        );
        Ok(())
    }

    fn start_data_management_job(
        &mut self,
        operation: impl FnOnce() -> Result<DataManagementResult> + Send + 'static,
    ) -> Result<()> {
        let id = self.spawn_local_job(true, JobKind::General, move || {
            operation().map(JobResult::DataManagement)
        })?;
        self.data_management.inflight = Some(id);
        Ok(())
    }
}

fn backup_title(info: &BackupInfo) -> String {
    if info.kind == BackupKind::AutomaticRollback {
        format!("{} · {}", tr("metadata_backup_automatic"), info.created_at)
    } else {
        info.created_at.clone()
    }
}
