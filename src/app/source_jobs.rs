//! Worker-side source configuration reuse and foreground completion delivery.

use std::{
    error::Error,
    fmt,
    sync::{Mutex, atomic::AtomicU64},
};

use super::*;

#[derive(Debug)]
struct JobSuperseded;

impl fmt::Display for JobSuperseded {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("The worker belongs to an earlier source or network generation")
    }
}

impl Error for JobSuperseded {}

pub(super) fn is_superseded(error: &anyhow::Error) -> bool {
    error.is::<JobSuperseded>()
}

fn ensure_current(
    revision: &AtomicU64,
    expected_revision: u64,
    network: &crate::sources::NetworkControl,
    expected_epoch: u64,
) -> Result<()> {
    if revision.load(Ordering::Acquire) != expected_revision
        || network.check_epoch(expected_epoch).is_err()
    {
        return Err(JobSuperseded.into());
    }
    Ok(())
}

struct PooledManager {
    revision: u64,
    epoch: u64,
    manager: Arc<SourceManager>,
}

#[derive(Default)]
pub(super) struct SourceManagerPool {
    current: Mutex<Option<PooledManager>>,
    builds: AtomicU64,
}

impl SourceManagerPool {
    pub(super) fn build_count(&self) -> u64 {
        self.builds.load(Ordering::Relaxed)
    }

    fn manager(
        &self,
        data_dir: &Path,
        configurations: &[SourceConfig],
        revision: &AtomicU64,
        expected_revision: u64,
        network: &crate::sources::NetworkControl,
        expected_epoch: u64,
    ) -> Result<Arc<SourceManager>> {
        ensure_current(revision, expected_revision, network, expected_epoch)?;
        // Construction reads package policies. Serialize builders so an older
        // snapshot cannot be published after a newer manager in this pool.
        let mut current = self
            .current
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        ensure_current(revision, expected_revision, network, expected_epoch)?;
        if let Some(pooled) = current.as_ref()
            && pooled.revision == expected_revision
            && pooled.epoch == expected_epoch
        {
            return Ok(pooled.manager.clone());
        }
        self.builds.fetch_add(1, Ordering::Relaxed);
        let manager = SourceManager::new(data_dir, configurations);
        // The epoch is captured on the UI thread, not by the constructor. An
        // old queued job must never inherit the epoch opened after a wake.
        ensure_current(revision, expected_revision, network, expected_epoch)?;
        let manager = Arc::new(manager?);
        if manager.network_epoch() != expected_epoch {
            return Err(JobSuperseded.into());
        }
        *current = Some(PooledManager {
            revision: expected_revision,
            epoch: expected_epoch,
            manager: manager.clone(),
        });
        Ok(manager)
    }
}

impl App {
    pub(super) fn spawn_job(
        &mut self,
        foreground: bool,
        operation: impl FnOnce(Arc<SourceManager>) -> Result<JobResult> + Send + 'static,
    ) -> Result<()> {
        self.spawn_job_kind(foreground, JobKind::General, operation)
            .map(|_| ())
    }

    pub(super) fn spawn_chapter_job(
        &mut self,
        target: ReaderTarget,
        operation: impl FnOnce(Arc<SourceManager>) -> Result<JobResult> + Send + 'static,
    ) -> Result<()> {
        crate::reader_timing::defer_action();
        let id = self.spawn_targeted_job(true, JobKind::Chapter, Some(target), operation)?;
        self.chapter_inflight = Some(id);
        Ok(())
    }

    pub(super) fn spawn_search_job(
        &mut self,
        source_id: String,
        operation: impl FnOnce(Arc<SourceManager>) -> Result<JobResult> + Send + 'static,
    ) -> Result<()> {
        self.spawn_source_job(true, JobKind::Search, None, Some(source_id), operation)
            .map(|_| ())
    }

    pub(super) fn spawn_catalog_job(
        &mut self,
        operation: impl FnOnce(Arc<SourceManager>) -> Result<JobResult> + Send + 'static,
    ) -> Result<()> {
        self.spawn_job_kind(true, JobKind::Catalog, operation)
            .map(|_| ())
    }

    pub(super) fn spawn_job_kind(
        &mut self,
        foreground: bool,
        kind: JobKind,
        operation: impl FnOnce(Arc<SourceManager>) -> Result<JobResult> + Send + 'static,
    ) -> Result<u64> {
        let target = if matches!(kind, JobKind::Reader) {
            self.reader_target()
        } else {
            None
        };
        self.spawn_targeted_job(foreground, kind, target, operation)
    }

    pub(super) fn spawn_targeted_job(
        &mut self,
        foreground: bool,
        kind: JobKind,
        reader_target: Option<ReaderTarget>,
        operation: impl FnOnce(Arc<SourceManager>) -> Result<JobResult> + Send + 'static,
    ) -> Result<u64> {
        self.spawn_source_job(foreground, kind, reader_target, None, operation)
    }

    fn spawn_source_job(
        &mut self,
        foreground: bool,
        kind: JobKind,
        reader_target: Option<ReaderTarget>,
        source: Option<String>,
        operation: impl FnOnce(Arc<SourceManager>) -> Result<JobResult> + Send + 'static,
    ) -> Result<u64> {
        let expected_epoch = self.network_control.epoch();
        let expected_revision = self.source_configuration_revision.load(Ordering::Acquire);
        let configurations = match self.source_configurations.as_ref() {
            Some(configurations) => configurations.clone(),
            None => {
                let configurations = Arc::new(self.store.sources()?);
                self.source_configurations = Some(configurations.clone());
                configurations
            }
        };
        let pool = self.source_manager_pool.clone();
        let revision = self.source_configuration_revision.clone();
        let network = self.network_control.clone();
        let data_dir = self.data_dir.clone();
        let timing = matches!(kind, JobKind::Reader)
            .then(|| crate::reader_timing::token(self.reader_generation, self.lifecycle_generation))
            .flatten();
        self.spawn_worker(foreground, true, kind, reader_target, source, move || {
            let manager_timing =
                crate::reader_timing::Stage::new(timing, "source_manager_finished");
            let manager = pool.manager(
                &data_dir,
                &configurations,
                &revision,
                expected_revision,
                &network,
                expected_epoch,
            )?;
            drop(manager_timing);
            ensure_current(&revision, expected_revision, &network, expected_epoch)?;
            let result = operation(manager);
            ensure_current(&revision, expected_revision, &network, expected_epoch)?;
            result
        })
    }

    pub(super) fn spawn_local_job(
        &mut self,
        foreground: bool,
        kind: JobKind,
        operation: impl FnOnce() -> Result<JobResult> + Send + 'static,
    ) -> Result<u64> {
        self.spawn_worker(foreground, false, kind, None, None, operation)
    }

    pub(super) fn spawn_worker(
        &mut self,
        foreground: bool,
        source_dependent: bool,
        kind: JobKind,
        reader_target: Option<ReaderTarget>,
        source: Option<String>,
        operation: impl FnOnce() -> Result<JobResult> + Send + 'static,
    ) -> Result<u64> {
        anyhow::ensure!(
            !self.metadata_restore_active() || matches!(kind, JobKind::MetadataRestore),
            "Metadata restoration is waiting for existing work to finish"
        );
        let id = self.next_job;
        self.next_job += 1;
        let context = JobContext {
            kind,
            reader_target,
            reader_generation: self.reader_generation,
            reader_refresh: self.reader_refresh,
            search_generation: self.search_generation,
            catalog_generation: self.catalog_generation,
            query: self.query.clone(),
            search_scope: self.search_scope.clone(),
            source: source.unwrap_or_else(|| self.current_source.clone()),
            catalog_filter: self.catalog_filter,
            source_revision: self.source_revision,
            lifecycle_generation: self.lifecycle_generation,
        };
        if foreground {
            self.pending.insert(id);
            self.busy = true;
        }
        self.active_jobs.insert(id);
        let tx = self.job_tx.clone();
        let ui = self.ui.clone();
        let ready_pending = self.foreground_ready_pending.clone();
        let ready_posts = self.foreground_ready_posts.clone();
        let timing = matches!(context.kind, JobKind::Reader)
            .then(|| {
                crate::reader_timing::token(context.reader_generation, context.lifecycle_generation)
            })
            .flatten();
        crate::reader_timing::record("request_queued", timing);
        let spawned = std::thread::Builder::new()
            .name("aidoku-source".into())
            .spawn(move || {
                crate::reader_timing::record("worker_started", timing);
                let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(operation))
                    .unwrap_or_else(|_| Err(anyhow::anyhow!("A source worker failed")));
                crate::reader_timing::record("worker_finished", timing);
                if tx
                    .send(JobMessage {
                        id,
                        foreground,
                        source_dependent,
                        context,
                        result,
                    })
                    .is_ok()
                    && foreground
                    && !ready_pending.swap(true, Ordering::AcqRel)
                {
                    let posted = ui.upgrade_in_event_loop(|ui| {
                        ui.global::<Commands>().invoke_job_ready();
                    });
                    if posted.is_ok() {
                        ready_posts.fetch_add(1, Ordering::Relaxed);
                    } else {
                        ready_pending.store(false, Ordering::Release);
                    }
                }
            });
        if let Err(error) = spawned {
            crate::reader_timing::failed(timing);
            self.active_jobs.remove(&id);
            self.pending.remove(&id);
            self.busy = !self.pending.is_empty();
            return Err(error).context("Start the source worker");
        }
        Ok(id)
    }

    /// Release coalescing even when the lifecycle callback keeps work parked.
    pub fn acknowledge_foreground_jobs_ready(&self) {
        self.foreground_ready_pending
            .store(false, Ordering::Release);
    }

    /// Foreground results may be consumed only after lifecycle control runs.
    pub fn poll_foreground_jobs(&mut self) {
        self.acknowledge_foreground_jobs_ready();
        self.poll();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_warm_generation_reuses_one_manager() {
        let directory = tempfile::tempdir().unwrap();
        let network = crate::sources::NetworkControl::open(directory.path()).unwrap();
        let revision = AtomicU64::new(0);
        let pool = SourceManagerPool::default();
        let first = pool
            .manager(
                directory.path(),
                &[],
                &revision,
                0,
                &network,
                network.epoch(),
            )
            .unwrap();
        let second = pool
            .manager(
                directory.path(),
                &[],
                &revision,
                0,
                &network,
                network.epoch(),
            )
            .unwrap();
        assert!(Arc::ptr_eq(&first, &second));
        assert_eq!(pool.build_count(), 1);
    }

    #[test]
    fn an_old_configuration_cannot_replace_the_current_manager() {
        let directory = tempfile::tempdir().unwrap();
        let network = crate::sources::NetworkControl::open(directory.path()).unwrap();
        let revision = AtomicU64::new(0);
        let pool = SourceManagerPool::default();
        let first = pool
            .manager(
                directory.path(),
                &[],
                &revision,
                0,
                &network,
                network.epoch(),
            )
            .unwrap();
        revision.store(1, Ordering::Release);
        let second = pool
            .manager(
                directory.path(),
                &[],
                &revision,
                1,
                &network,
                network.epoch(),
            )
            .unwrap();
        assert!(!Arc::ptr_eq(&first, &second));
        let error = pool
            .manager(
                directory.path(),
                &[],
                &revision,
                0,
                &network,
                network.epoch(),
            )
            .err()
            .unwrap();
        assert!(is_superseded(&error));
        let current = pool
            .manager(
                directory.path(),
                &[],
                &revision,
                1,
                &network,
                network.epoch(),
            )
            .unwrap();
        assert!(Arc::ptr_eq(&second, &current));
        assert_eq!(pool.build_count(), 2);
    }

    #[test]
    fn a_job_dispatched_before_sleep_cannot_build_after_wake() {
        let directory = tempfile::tempdir().unwrap();
        let network = crate::sources::NetworkControl::open(directory.path()).unwrap();
        let revision = AtomicU64::new(0);
        let pool = SourceManagerPool::default();
        let dispatched_epoch = network.epoch();
        network.suspend();
        network.resume();
        let error = pool
            .manager(
                directory.path(),
                &[],
                &revision,
                0,
                &network,
                dispatched_epoch,
            )
            .err()
            .unwrap();
        assert!(is_superseded(&error));
        assert_eq!(pool.build_count(), 0);
        let current = pool
            .manager(
                directory.path(),
                &[],
                &revision,
                0,
                &network,
                network.epoch(),
            )
            .unwrap();
        assert_eq!(current.network_epoch(), network.epoch());
        assert_eq!(pool.build_count(), 1);
    }

    #[test]
    fn concurrent_workers_share_the_same_generation_build() {
        let directory = tempfile::tempdir().unwrap();
        let network = crate::sources::NetworkControl::open(directory.path()).unwrap();
        let revision = AtomicU64::new(0);
        let pool = SourceManagerPool::default();
        let managers = std::thread::scope(|scope| {
            (0..8)
                .map(|_| {
                    scope.spawn(|| {
                        pool.manager(
                            directory.path(),
                            &[],
                            &revision,
                            0,
                            &network,
                            network.epoch(),
                        )
                        .unwrap()
                    })
                })
                .collect::<Vec<_>>()
                .into_iter()
                .map(|thread| thread.join().unwrap())
                .collect::<Vec<_>>()
        });
        assert!(
            managers
                .iter()
                .all(|manager| Arc::ptr_eq(&managers[0], manager))
        );
        assert_eq!(pool.build_count(), 1);
    }
}
