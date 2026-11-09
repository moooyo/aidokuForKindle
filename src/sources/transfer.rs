//! Shared transfer admission and in-progress deduplication for one data root.

use std::{
    collections::{BTreeMap, BTreeSet, HashMap},
    fmt, fs,
    path::{Path, PathBuf},
    sync::{
        Arc, Condvar, Mutex, MutexGuard, OnceLock, RwLock, Weak,
        atomic::{AtomicBool, AtomicU8, AtomicU64, Ordering},
    },
    time::{Duration, Instant},
};

use anyhow::{Context, Result, anyhow, ensure};

/// A smaller rank receives the next available transfer slot first.
#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
#[repr(u8)]
pub enum FetchPriority {
    Interactive = 0,
    Background = 1,
    Prefetch = 2,
}

/// Background work cannot consume the slots reserved for foreground work.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct DownloadLimits {
    pub background: usize,
    pub foreground: usize,
}

impl Default for DownloadLimits {
    fn default() -> Self {
        Self {
            background: 2,
            foreground: 1,
        }
    }
}

/// A lifecycle interruption can be recognized through anyhow context chains.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct NetworkInterrupted {
    pub epoch: u64,
}

impl fmt::Display for NetworkInterrupted {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            formatter,
            "The network transfer was interrupted in epoch {}",
            self.epoch
        )
    }
}

impl std::error::Error for NetworkInterrupted {}

pub fn is_network_interrupted(error: &anyhow::Error) -> bool {
    error.downcast_ref::<NetworkInterrupted>().is_some()
}

#[derive(Default)]
struct Lifecycle {
    epoch: AtomicU64,
    suspended: AtomicBool,
    transitions: Mutex<()>,
    commits: RwLock<()>,
}

impl Lifecycle {
    fn check(&self, expected_epoch: u64) -> Result<()> {
        let epoch = self.epoch.load(Ordering::Acquire);
        if self.suspended.load(Ordering::Acquire) || epoch != expected_epoch {
            return Err(NetworkInterrupted { epoch }.into());
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Debug)]
pub(super) struct RequestPolicy {
    pub max_parallel: usize,
    pub min_interval: Duration,
}

impl Default for RequestPolicy {
    fn default() -> Self {
        Self {
            max_parallel: 3,
            min_interval: Duration::ZERO,
        }
    }
}

struct PriorityCell {
    base: AtomicU8,
    alive: AtomicBool,
    parents: Mutex<Vec<Weak<PriorityCell>>>,
}

impl PriorityCell {
    fn rank(&self) -> u8 {
        self.rank_inner(&mut BTreeSet::new())
    }

    fn rank_inner(&self, visited: &mut BTreeSet<usize>) -> u8 {
        if !visited.insert(self as *const Self as usize) {
            return FetchPriority::Prefetch as u8;
        }
        let mut rank = self.base.load(Ordering::Acquire);
        for parent in self.live_parents() {
            rank = rank.min(parent.rank_inner(visited));
        }
        rank
    }

    fn live_parents(&self) -> Vec<Arc<PriorityCell>> {
        lock(&self.parents)
            .iter()
            .filter_map(Weak::upgrade)
            .filter(|parent| parent.alive.load(Ordering::Acquire))
            .collect()
    }

    fn reaches(
        &self,
        target: &Arc<PriorityCell>,
        visited: &mut BTreeSet<usize>,
        depth: usize,
    ) -> Result<bool> {
        ensure!(
            depth <= 64,
            "The transfer priority dependency chain is too deep"
        );
        let address = self as *const Self as usize;
        if address == Arc::as_ptr(target) as usize {
            return Ok(true);
        }
        if !visited.insert(address) {
            return Ok(false);
        }
        for parent in self.live_parents() {
            if parent.reaches(target, visited, depth + 1)? {
                return Ok(true);
            }
        }
        Ok(false)
    }
}

/// Priority changes also wake queued dependent archive transfers.
pub(super) struct TransferToken {
    cell: Arc<PriorityCell>,
    gate: Arc<Gate>,
    epoch: u64,
}

impl TransferToken {
    fn new(gate: Arc<Gate>, priority: FetchPriority) -> Self {
        let epoch = gate.lifecycle.epoch.load(Ordering::Acquire);
        Self {
            cell: Arc::new(PriorityCell {
                base: AtomicU8::new(priority as u8),
                alive: AtomicBool::new(true),
                parents: Mutex::new(Vec::new()),
            }),
            gate,
            epoch,
        }
    }

    pub(super) fn priority(&self) -> FetchPriority {
        match self.cell.rank() {
            0 => FetchPriority::Interactive,
            1 => FetchPriority::Background,
            _ => FetchPriority::Prefetch,
        }
    }

    pub(super) fn promote(&self, priority: FetchPriority) {
        // Updating the predicate under the same mutex used by Condvar::wait
        // prevents a promotion notification from being lost before a wait.
        let _state = lock(&self.gate.state);
        if self.check_active().is_err() {
            return;
        }
        if self.cell.base.fetch_min(priority as u8, Ordering::AcqRel) > priority as u8 {
            self.gate.changed.notify_all();
        }
    }

    pub(super) fn check_active(&self) -> Result<()> {
        self.gate.lifecycle.check(self.epoch)
    }

    /// Only the final cache publication belongs inside this closure. Network
    /// I/O and artifact validation must run before obtaining the read fence.
    pub(super) fn commit_if_active(
        &self,
        commit: impl FnOnce() -> Result<PathBuf>,
    ) -> Result<PathBuf> {
        self.check_active()?;
        let _commit = self
            .gate
            .lifecycle
            .commits
            .read()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        self.check_active()?;
        commit()
    }

    fn add_parent(&self, parent: &TransferToken) -> Result<()> {
        ensure!(
            Arc::ptr_eq(&self.gate, &parent.gate),
            "A transfer parent belongs to another data root"
        );
        let _state = lock(&self.gate.state);
        self.check_active()?;
        parent.check_active()?;
        ensure!(
            !parent.cell.reaches(&self.cell, &mut BTreeSet::new(), 0)?,
            "A transfer cannot depend on its own flight"
        );
        let mut parents = lock(&self.cell.parents);
        parents.retain(|parent| {
            parent
                .upgrade()
                .is_some_and(|parent| parent.alive.load(Ordering::Acquire))
        });
        if !parents
            .iter()
            .filter_map(Weak::upgrade)
            .any(|existing| Arc::ptr_eq(&existing, &parent.cell))
        {
            parents.push(Arc::downgrade(&parent.cell));
            self.gate.changed.notify_all();
        }
        Ok(())
    }
}

impl Drop for TransferToken {
    fn drop(&mut self) {
        let _state = lock(&self.gate.state);
        self.cell.alive.store(false, Ordering::Release);
        self.gate.changed.notify_all();
    }
}

struct Entry {
    origin: String,
    source: Option<String>,
    policy: RequestPolicy,
    priority: Arc<PriorityCell>,
}

struct OriginState {
    last_start: Instant,
    last_interval: Duration,
}

#[derive(Default)]
struct GateState {
    next_ticket: u64,
    waiting: BTreeMap<u64, Entry>,
    active: BTreeMap<u64, Entry>,
    origins: HashMap<String, OriginState>,
    sources: HashMap<String, OriginState>,
}

struct Gate {
    limits: DownloadLimits,
    total: usize,
    state: Mutex<GateState>,
    changed: Condvar,
    lifecycle: Lifecycle,
}

#[derive(Default)]
struct OriginBudget {
    parallel: Option<usize>,
    interval: Duration,
    active: usize,
    background: usize,
}

impl OriginBudget {
    fn observe(&mut self, policy: RequestPolicy, active: bool, background: bool) {
        self.parallel = Some(
            self.parallel
                .map_or(policy.max_parallel, |limit| limit.min(policy.max_parallel)),
        );
        self.interval = self.interval.max(policy.min_interval);
        if active {
            self.active += 1;
            if background {
                self.background += 1;
            }
        }
    }

    fn allows(&self, interactive: bool, total: usize) -> bool {
        let parallel = self.parallel.unwrap_or(total).min(total);
        if self.active >= parallel {
            return false;
        }
        // A one-request budget cannot be subdivided; its active request
        // completes before the next, foreground-first admission.
        let background = if parallel > 1 { parallel - 1 } else { 1 };
        interactive || self.background < background
    }
}

struct Admission {
    ticket: u64,
    origin_interval: Duration,
    source_interval: Option<Duration>,
}

struct Decision {
    next: Option<Admission>,
    wake_at: Option<Instant>,
}

impl Gate {
    fn decision(&self, state: &GateState, now: Instant) -> Decision {
        let mut decision = Decision {
            next: None,
            wake_at: None,
        };
        if state.active.len() >= self.total {
            return decision;
        }
        let mut budgets = HashMap::<&str, OriginBudget>::new();
        let mut sources = HashMap::<&str, OriginBudget>::new();
        let mut background = 0;
        for (active, entries) in [(true, &state.active), (false, &state.waiting)] {
            for entry in entries.values() {
                let is_background = entry.priority.rank() != FetchPriority::Interactive as u8;
                budgets.entry(&entry.origin).or_default().observe(
                    entry.policy,
                    active,
                    is_background,
                );
                if let Some(source) = &entry.source {
                    sources
                        .entry(source)
                        .or_default()
                        .observe(entry.policy, active, is_background);
                }
                if active && is_background {
                    background += 1;
                }
            }
        }
        let mut selected: Option<(u8, u64, Duration, Option<Duration>)> = None;
        for (ticket, entry) in &state.waiting {
            let rank = entry.priority.rank();
            let interactive = rank == FetchPriority::Interactive as u8;
            if !interactive && background >= self.limits.background {
                continue;
            }
            let budget = &budgets[entry.origin.as_str()];
            if !budget.allows(interactive, self.total) {
                continue;
            }
            let interval = budget.interval;
            let mut ready_at = now;
            if let Some(origin) = state.origins.get(&entry.origin) {
                ready_at = ready_at.max(
                    origin
                        .last_start
                        .checked_add(interval.max(origin.last_interval))
                        .unwrap_or(now),
                );
            }
            let source_interval = if let Some(source) = &entry.source {
                let budget = &sources[source.as_str()];
                if !budget.allows(interactive, self.total) {
                    continue;
                }
                if let Some(history) = state.sources.get(source) {
                    ready_at = ready_at.max(
                        history
                            .last_start
                            .checked_add(budget.interval.max(history.last_interval))
                            .unwrap_or(now),
                    );
                }
                Some(budget.interval)
            } else {
                None
            };
            if ready_at > now {
                decision.wake_at = Some(
                    decision
                        .wake_at
                        .map_or(ready_at, |previous| previous.min(ready_at)),
                );
                continue;
            }
            let candidate = (rank, *ticket, interval, source_interval);
            if selected
                .as_ref()
                .is_none_or(|selected| (candidate.0, candidate.1) < (selected.0, selected.1))
            {
                selected = Some(candidate);
            }
        }
        decision.next = selected.map(|(_, ticket, origin_interval, source_interval)| Admission {
            ticket,
            origin_interval,
            source_interval,
        });
        decision
    }
}

/// Dropping the permit releases its global, origin, and optional source slots.
pub(super) struct TransferPermit {
    gate: Arc<Gate>,
    ticket: u64,
}

impl Drop for TransferPermit {
    fn drop(&mut self) {
        let mut state = lock(&self.gate.state);
        state.active.remove(&self.ticket);
        self.gate.changed.notify_all();
    }
}

#[derive(Clone)]
enum FlightOutcome {
    Ready(PathBuf),
    Failed(Arc<str>),
    Interrupted(NetworkInterrupted),
}

impl FlightOutcome {
    fn result(&self) -> Result<PathBuf> {
        match self {
            Self::Ready(path) => Ok(path.clone()),
            Self::Failed(message) => Err(anyhow!(message.to_string())),
            Self::Interrupted(interrupted) => Err((*interrupted).into()),
        }
    }
}

struct Flight {
    token: TransferToken,
    outcome: Mutex<Option<FlightOutcome>>,
    completed: Condvar,
}

/// One coordinator is shared by all managers using the same canonical root.
pub(super) struct TransferCoordinator {
    gate: Arc<Gate>,
    flights: Mutex<HashMap<String, Arc<Flight>>>,
}

/// Owns the root's network lifecycle even when no source manager is alive.
#[derive(Clone)]
pub struct NetworkControl {
    coordinator: Arc<TransferCoordinator>,
}

impl NetworkControl {
    pub fn open(data_root: impl AsRef<Path>) -> Result<Self> {
        Self::open_with_limits(data_root, DownloadLimits::default())
    }

    pub fn open_with_limits(data_root: impl AsRef<Path>, limits: DownloadLimits) -> Result<Self> {
        Ok(TransferCoordinator::open(data_root.as_ref(), limits)?.control())
    }

    pub fn epoch(&self) -> u64 {
        self.coordinator
            .gate
            .lifecycle
            .epoch
            .load(Ordering::Acquire)
    }

    pub fn is_suspended(&self) -> bool {
        self.coordinator
            .gate
            .lifecycle
            .suspended
            .load(Ordering::Acquire)
    }

    pub fn check_epoch(&self, epoch: u64) -> Result<()> {
        self.coordinator.gate.lifecycle.check(epoch)
    }

    /// Cancel queued work immediately, then fence final disk publications.
    /// Call from a normal control thread, never from a signal handler. This
    /// does not wait for HTTP responses or release their live permits.
    pub fn suspend(&self) -> u64 {
        let lifecycle = &self.coordinator.gate.lifecycle;
        let _transition = lock(&lifecycle.transitions);
        let epoch = {
            let mut state = lock(&self.coordinator.gate.state);
            if lifecycle.suspended.load(Ordering::Acquire) {
                return self.epoch();
            }
            lifecycle.suspended.store(true, Ordering::Release);
            let epoch = lifecycle
                .epoch
                .fetch_add(1, Ordering::AcqRel)
                .wrapping_add(1);
            state.waiting.clear();
            self.coordinator.gate.changed.notify_all();
            epoch
        };
        // Never hold the flight map while acquiring an outcome lock. A late
        // leader is allowed to finish, but cannot overwrite this interruption.
        let flights = {
            let mut flights = lock(&self.coordinator.flights);
            flights
                .drain()
                .map(|(_, flight)| flight)
                .collect::<Vec<_>>()
        };
        for flight in flights {
            let mut outcome = lock(&flight.outcome);
            if outcome.is_none() {
                *outcome = Some(FlightOutcome::Interrupted(NetworkInterrupted { epoch }));
            }
            drop(outcome);
            flight.completed.notify_all();
        }
        // Epoch closure above prevents new readers from entering a commit.
        // An existing commit may finish its checksum/sync/publication before
        // this method acknowledges suspension. The admission mutex stays free.
        let _publication_fence = lifecycle
            .commits
            .write()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        epoch
    }

    /// Open the epoch created by suspend without advancing it a second time.
    pub fn resume(&self) -> u64 {
        let lifecycle = &self.coordinator.gate.lifecycle;
        let _transition = lock(&lifecycle.transitions);
        let _state = lock(&self.coordinator.gate.state);
        if lifecycle.suspended.swap(false, Ordering::AcqRel) {
            self.coordinator.gate.changed.notify_all();
        }
        self.epoch()
    }
}

static COORDINATORS: OnceLock<Mutex<HashMap<PathBuf, Weak<TransferCoordinator>>>> = OnceLock::new();

impl TransferCoordinator {
    pub(super) fn open(data_root: &Path, limits: DownloadLimits) -> Result<Arc<Self>> {
        ensure!(
            limits.background > 0 && limits.foreground > 0,
            "Transfer limits must reserve positive background and foreground capacity"
        );
        let total = limits
            .background
            .checked_add(limits.foreground)
            .context("Transfer limits exceed the platform capacity")?;
        fs::create_dir_all(data_root).context("Create the transfer data root")?;
        let root = data_root
            .canonicalize()
            .context("Resolve the transfer data root")?;
        let mut registry = lock(COORDINATORS.get_or_init(|| Mutex::new(HashMap::new())));
        registry.retain(|_, coordinator| coordinator.strong_count() > 0);
        if let Some(coordinator) = registry.get(&root).and_then(Weak::upgrade) {
            ensure!(
                coordinator.gate.limits == limits,
                "Transfer limits differ from another manager using this data root"
            );
            return Ok(coordinator);
        }
        let coordinator = Arc::new(Self {
            gate: Arc::new(Gate {
                limits,
                total,
                state: Mutex::new(GateState::default()),
                changed: Condvar::new(),
                lifecycle: Lifecycle::default(),
            }),
            flights: Mutex::new(HashMap::new()),
        });
        registry.insert(root, Arc::downgrade(&coordinator));
        Ok(coordinator)
    }

    pub(super) fn control(self: &Arc<Self>) -> NetworkControl {
        NetworkControl {
            coordinator: Arc::clone(self),
        }
    }

    pub(super) fn run(
        &self,
        key: &str,
        priority: FetchPriority,
        work: impl FnOnce(&TransferToken) -> Result<PathBuf>,
    ) -> Result<PathBuf> {
        self.run_with_parent(key, priority, None, work)
    }

    pub(super) fn run_in_epoch(
        &self,
        key: &str,
        priority: FetchPriority,
        expected_epoch: u64,
        work: impl FnOnce(&TransferToken) -> Result<PathBuf>,
    ) -> Result<PathBuf> {
        self.run_registered(key, priority, None, Some(expected_epoch), work)
    }

    pub(super) fn run_with_parent(
        &self,
        key: &str,
        priority: FetchPriority,
        parent: Option<&TransferToken>,
        work: impl FnOnce(&TransferToken) -> Result<PathBuf>,
    ) -> Result<PathBuf> {
        self.run_registered(key, priority, parent, None, work)
    }

    fn run_registered(
        &self,
        key: &str,
        priority: FetchPriority,
        parent: Option<&TransferToken>,
        expected_epoch: Option<u64>,
        work: impl FnOnce(&TransferToken) -> Result<PathBuf>,
    ) -> Result<PathBuf> {
        if let Some(parent) = parent {
            ensure!(
                Arc::ptr_eq(&self.gate, &parent.gate),
                "A transfer parent belongs to another data root"
            );
            parent.check_active()?;
        }
        let (flight, leader) = {
            let mut flights = lock(&self.flights);
            let _admission = lock(&self.gate.state);
            self.gate.lifecycle.check(
                expected_epoch.unwrap_or_else(|| self.gate.lifecycle.epoch.load(Ordering::Acquire)),
            )?;
            if let Some(parent) = parent {
                parent.check_active()?;
            }
            if let Some(flight) = flights.get(key) {
                flight.token.check_active()?;
                (Arc::clone(flight), false)
            } else {
                let flight = Arc::new(Flight {
                    token: TransferToken::new(Arc::clone(&self.gate), priority),
                    outcome: Mutex::new(None),
                    completed: Condvar::new(),
                });
                flights.insert(key.to_owned(), Arc::clone(&flight));
                (flight, true)
            }
        };
        if leader {
            let completion = FlightCompletion {
                coordinator: self,
                key: key.to_owned(),
                flight: Arc::clone(&flight),
                finished: false,
            };
            if let Some(parent) = parent {
                flight.token.add_parent(parent)?;
            }
            let result = flight
                .token
                .check_active()
                .and_then(|_| work(&flight.token))
                .and_then(|path| {
                    flight.token.check_active()?;
                    path.canonicalize()
                        .context("Resolve the completed transfer path")
                });
            completion.finish(&result)
        } else {
            if let Some(parent) = parent {
                flight.token.add_parent(parent)?;
            }
            flight.token.promote(priority);
            let mut outcome = lock(&flight.outcome);
            loop {
                if let Some(outcome) = outcome.as_ref() {
                    return outcome.result();
                }
                flight.token.check_active()?;
                outcome = flight
                    .completed
                    .wait(outcome)
                    .unwrap_or_else(|poisoned| poisoned.into_inner());
            }
        }
    }

    pub(super) fn acquire(
        &self,
        token: &TransferToken,
        origin: &str,
        policy: RequestPolicy,
    ) -> Result<TransferPermit> {
        self.acquire_scoped(token, origin, None, policy)
    }

    pub(super) fn acquire_scoped(
        &self,
        token: &TransferToken,
        origin: &str,
        source_id: Option<&str>,
        policy: RequestPolicy,
    ) -> Result<TransferPermit> {
        ensure!(
            Arc::ptr_eq(&self.gate, &token.gate),
            "A transfer token belongs to another data root"
        );
        ensure!(!origin.is_empty(), "A transfer origin cannot be empty");
        ensure!(
            source_id.is_none_or(|source| !source.is_empty()),
            "A transfer source scope cannot be empty"
        );
        ensure!(
            policy.max_parallel > 0,
            "A transfer origin must allow at least one request"
        );
        ensure!(
            Instant::now().checked_add(policy.min_interval).is_some(),
            "The transfer request interval is too large"
        );
        let mut state = lock(&self.gate.state);
        token.check_active()?;
        let ticket = state
            .next_ticket
            .checked_add(1)
            .context("Transfer admission ticket space was exhausted")?;
        state.next_ticket = ticket;
        state.waiting.insert(
            ticket,
            Entry {
                origin: origin.to_owned(),
                source: source_id.map(str::to_owned),
                policy,
                priority: Arc::clone(&token.cell),
            },
        );
        self.gate.changed.notify_all();
        loop {
            if let Err(error) = token.check_active() {
                state.waiting.remove(&ticket);
                self.gate.changed.notify_all();
                return Err(error);
            }
            let decision = self.gate.decision(&state, Instant::now());
            if let Some(admission) = decision.next {
                if admission.ticket == ticket {
                    let entry = state
                        .waiting
                        .remove(&ticket)
                        .expect("The admitted transfer is queued");
                    let started = Instant::now();
                    state.origins.insert(
                        entry.origin.clone(),
                        OriginState {
                            last_start: started,
                            last_interval: admission.origin_interval,
                        },
                    );
                    if let (Some(source), Some(interval)) =
                        (&entry.source, admission.source_interval)
                    {
                        state.sources.insert(
                            source.clone(),
                            OriginState {
                                last_start: started,
                                last_interval: interval,
                            },
                        );
                    }
                    state.active.insert(ticket, entry);
                    self.gate.changed.notify_all();
                    return Ok(TransferPermit {
                        gate: Arc::clone(&self.gate),
                        ticket,
                    });
                }
            }
            state = if let Some(wake_at) = decision.wake_at {
                let delay = wake_at.saturating_duration_since(Instant::now());
                if delay.is_zero() {
                    continue;
                }
                self.gate
                    .changed
                    .wait_timeout(state, delay)
                    .unwrap_or_else(|poisoned| poisoned.into_inner())
                    .0
            } else {
                self.gate
                    .changed
                    .wait(state)
                    .unwrap_or_else(|poisoned| poisoned.into_inner())
            };
        }
    }
}

struct FlightCompletion<'a> {
    coordinator: &'a TransferCoordinator,
    key: String,
    flight: Arc<Flight>,
    finished: bool,
}

impl FlightCompletion<'_> {
    fn finish(mut self, result: &Result<PathBuf>) -> Result<PathBuf> {
        let outcome = match result {
            Ok(path) => FlightOutcome::Ready(path.clone()),
            Err(error) => match error.downcast_ref::<NetworkInterrupted>() {
                Some(interrupted) => FlightOutcome::Interrupted(*interrupted),
                None => FlightOutcome::Failed(Arc::from(format!("{error:#}"))),
            },
        };
        let published = self.publish(outcome);
        self.finished = true;
        published.result()
    }

    fn publish(&self, outcome: FlightOutcome) -> FlightOutcome {
        let published = {
            let mut current = lock(&self.flight.outcome);
            if current.is_none() {
                let outcome = if self.flight.token.check_active().is_err() {
                    FlightOutcome::Interrupted(NetworkInterrupted {
                        epoch: self
                            .coordinator
                            .gate
                            .lifecycle
                            .epoch
                            .load(Ordering::Acquire),
                    })
                } else {
                    outcome
                };
                *current = Some(outcome);
            }
            current
                .as_ref()
                .expect("The transfer outcome has been published")
                .clone()
        };
        let mut flights = lock(&self.coordinator.flights);
        if flights
            .get(&self.key)
            .is_some_and(|flight| Arc::ptr_eq(flight, &self.flight))
        {
            flights.remove(&self.key);
        }
        self.flight.completed.notify_all();
        published
    }
}

impl Drop for FlightCompletion<'_> {
    fn drop(&mut self) {
        if !self.finished {
            let _ = self.publish(FlightOutcome::Failed(Arc::from(
                "The transfer leader stopped before producing a result",
            )));
        }
    }
}

fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        sync::{atomic::AtomicUsize, mpsc},
        thread,
    };

    const TIMEOUT: Duration = Duration::from_secs(5);

    fn coordinator(directory: &Path) -> Arc<TransferCoordinator> {
        TransferCoordinator::open(directory, DownloadLimits::default()).unwrap()
    }

    fn token(coordinator: &TransferCoordinator, priority: FetchPriority) -> TransferToken {
        TransferToken::new(Arc::clone(&coordinator.gate), priority)
    }

    fn wait_for_waiters(coordinator: &TransferCoordinator, count: usize) {
        let deadline = Instant::now() + TIMEOUT;
        let mut state = lock(&coordinator.gate.state);
        while state.waiting.len() < count {
            let delay = deadline.saturating_duration_since(Instant::now());
            assert!(
                !delay.is_zero(),
                "Transfers did not enter the admission queue"
            );
            state = coordinator
                .gate
                .changed
                .wait_timeout(state, delay)
                .unwrap_or_else(|poisoned| poisoned.into_inner())
                .0;
        }
    }

    fn wait_for_priority(coordinator: &TransferCoordinator, key: &str, priority: FetchPriority) {
        let flight = Arc::clone(
            lock(&coordinator.flights)
                .get(key)
                .expect("The fixture flight is running"),
        );
        let deadline = Instant::now() + TIMEOUT;
        let mut state = lock(&coordinator.gate.state);
        while flight.token.priority() != priority {
            let delay = deadline.saturating_duration_since(Instant::now());
            assert!(!delay.is_zero(), "The follower did not promote the flight");
            state = coordinator
                .gate
                .changed
                .wait_timeout(state, delay)
                .unwrap_or_else(|poisoned| poisoned.into_inner())
                .0;
        }
    }

    fn assert_interrupted<T>(result: Result<T>) {
        match result {
            Ok(_) => panic!("An interrupted operation unexpectedly succeeded"),
            Err(error) => assert!(
                is_network_interrupted(&error),
                "Unexpected interruption error: {error:#}"
            ),
        }
    }

    fn wait_for_suspension(coordinator: &TransferCoordinator) {
        let deadline = Instant::now() + TIMEOUT;
        let mut state = lock(&coordinator.gate.state);
        while !coordinator.gate.lifecycle.suspended.load(Ordering::Acquire) {
            let delay = deadline.saturating_duration_since(Instant::now());
            assert!(!delay.is_zero(), "The network lifecycle did not suspend");
            state = coordinator
                .gate
                .changed
                .wait_timeout(state, delay)
                .unwrap_or_else(|poisoned| poisoned.into_inner())
                .0;
        }
    }

    fn queued(
        coordinator: Arc<TransferCoordinator>,
        priority: FetchPriority,
        origin: &'static str,
        name: &'static str,
        events: mpsc::Sender<&'static str>,
        policy: RequestPolicy,
    ) -> (mpsc::Sender<()>, thread::JoinHandle<()>) {
        queued_scoped(coordinator, priority, origin, None, name, events, policy)
    }

    fn queued_scoped(
        coordinator: Arc<TransferCoordinator>,
        priority: FetchPriority,
        origin: &'static str,
        source: Option<&'static str>,
        name: &'static str,
        events: mpsc::Sender<&'static str>,
        policy: RequestPolicy,
    ) -> (mpsc::Sender<()>, thread::JoinHandle<()>) {
        let (release, wait) = mpsc::channel();
        let handle = thread::spawn(move || {
            let token = token(&coordinator, priority);
            let _permit = coordinator
                .acquire_scoped(&token, origin, source, policy)
                .unwrap();
            events.send(name).unwrap();
            wait.recv_timeout(TIMEOUT).unwrap();
        });
        (release, handle)
    }

    #[test]
    fn managers_share_one_canonical_root_and_reject_conflicting_limits() {
        let directory = tempfile::tempdir().unwrap();
        let first = coordinator(directory.path());
        let second = coordinator(&directory.path().join("."));
        assert!(Arc::ptr_eq(&first, &second));
        assert!(
            TransferCoordinator::open(
                directory.path(),
                DownloadLimits {
                    background: 3,
                    foreground: 1
                }
            )
            .is_err()
        );
        assert!(
            TransferCoordinator::open(
                directory.path(),
                DownloadLimits {
                    background: 0,
                    foreground: 1
                }
            )
            .is_err()
        );
    }

    #[test]
    fn followers_share_one_running_work_and_the_canonical_result_without_a_permanent_flight_cache()
    {
        let directory = tempfile::tempdir().unwrap();
        fs::create_dir(directory.path().join("objects")).unwrap();
        let path = directory.path().join("objects").join("..").join("page.bin");
        let coordinator = coordinator(directory.path());
        let calls = Arc::new(AtomicUsize::new(0));
        let (started, ready) = mpsc::channel();
        let (release, wait) = mpsc::channel();
        let leader = {
            let coordinator = Arc::clone(&coordinator);
            let path = path.clone();
            let calls = Arc::clone(&calls);
            thread::spawn(move || {
                coordinator.run("page-key", FetchPriority::Prefetch, |_| {
                    calls.fetch_add(1, Ordering::SeqCst);
                    started.send(()).unwrap();
                    wait.recv_timeout(TIMEOUT).unwrap();
                    fs::write(&path, b"shared")?;
                    Ok(path)
                })
            })
        };
        ready.recv_timeout(TIMEOUT).unwrap();
        let follower = {
            let coordinator = Arc::clone(&coordinator);
            thread::spawn(move || {
                coordinator.run("page-key", FetchPriority::Interactive, |_| {
                    panic!("A follower must not execute work")
                })
            })
        };
        wait_for_priority(&coordinator, "page-key", FetchPriority::Interactive);
        release.send(()).unwrap();
        let first = leader.join().unwrap().unwrap();
        assert_eq!(follower.join().unwrap().unwrap(), first);
        assert_eq!(first, path.canonicalize().unwrap());
        assert_eq!(calls.load(Ordering::SeqCst), 1);
        coordinator
            .run("page-key", FetchPriority::Background, |_| {
                calls.fetch_add(1, Ordering::SeqCst);
                Ok(path.clone())
            })
            .unwrap();
        assert_eq!(calls.load(Ordering::SeqCst), 2);
        assert!(lock(&coordinator.flights).is_empty());
    }

    #[test]
    fn failed_flights_wake_followers_and_can_be_retried() {
        let directory = tempfile::tempdir().unwrap();
        let coordinator = coordinator(directory.path());
        let (started, ready) = mpsc::channel();
        let (release, wait) = mpsc::channel();
        let leader = {
            let coordinator = Arc::clone(&coordinator);
            thread::spawn(move || {
                coordinator.run("retry-key", FetchPriority::Prefetch, |_| {
                    started.send(()).unwrap();
                    wait.recv_timeout(TIMEOUT).unwrap();
                    Err(anyhow!("A retryable fixture failure"))
                })
            })
        };
        ready.recv_timeout(TIMEOUT).unwrap();
        let follower = {
            let coordinator = Arc::clone(&coordinator);
            thread::spawn(move || {
                coordinator.run("retry-key", FetchPriority::Interactive, |_| {
                    panic!("A follower must not execute work")
                })
            })
        };
        wait_for_priority(&coordinator, "retry-key", FetchPriority::Interactive);
        release.send(()).unwrap();
        assert!(
            leader
                .join()
                .unwrap()
                .unwrap_err()
                .to_string()
                .contains("retryable")
        );
        assert!(
            follower
                .join()
                .unwrap()
                .unwrap_err()
                .to_string()
                .contains("retryable")
        );
        let path = directory.path().join("retry.bin");
        coordinator
            .run("retry-key", FetchPriority::Background, |_| {
                fs::write(&path, b"retried")?;
                Ok(path.clone())
            })
            .unwrap();
    }

    #[test]
    fn a_panicking_leader_releases_its_flight_and_wakes_followers() {
        let directory = tempfile::tempdir().unwrap();
        let coordinator = coordinator(directory.path());
        let (started, ready) = mpsc::channel();
        let (release, wait) = mpsc::channel();
        let leader = {
            let coordinator = Arc::clone(&coordinator);
            thread::spawn(move || {
                coordinator.run("panic-key", FetchPriority::Prefetch, |token| {
                    let _permit =
                        coordinator.acquire(token, "panic-origin", RequestPolicy::default())?;
                    started.send(()).unwrap();
                    wait.recv_timeout(TIMEOUT).unwrap();
                    panic!("An intentional transfer fixture panic")
                })
            })
        };
        ready.recv_timeout(TIMEOUT).unwrap();
        let follower = {
            let coordinator = Arc::clone(&coordinator);
            thread::spawn(move || {
                coordinator.run("panic-key", FetchPriority::Interactive, |_| {
                    panic!("A follower must not execute work")
                })
            })
        };
        wait_for_priority(&coordinator, "panic-key", FetchPriority::Interactive);
        release.send(()).unwrap();
        assert!(leader.join().is_err());
        assert!(
            follower
                .join()
                .unwrap()
                .unwrap_err()
                .to_string()
                .contains("leader stopped")
        );
        assert!(lock(&coordinator.flights).is_empty());
        assert!(lock(&coordinator.gate.state).active.is_empty());
    }

    #[test]
    fn eligible_requests_use_foreground_first_order_and_fifo_within_one_priority() {
        let directory = tempfile::tempdir().unwrap();
        let coordinator = coordinator(directory.path());
        let policy = RequestPolicy::default();
        let hold_background_a = token(&coordinator, FetchPriority::Background);
        let hold_background_b = token(&coordinator, FetchPriority::Background);
        let hold_foreground = token(&coordinator, FetchPriority::Interactive);
        let first = coordinator
            .acquire(&hold_background_a, "same-origin", policy)
            .unwrap();
        let second = coordinator
            .acquire(&hold_background_b, "same-origin", policy)
            .unwrap();
        let third = coordinator
            .acquire(&hold_foreground, "same-origin", policy)
            .unwrap();
        let (events, admitted) = mpsc::channel();
        let mut releases = BTreeMap::new();
        let mut handles = Vec::new();
        for (index, (name, priority)) in [
            ("prefetch", FetchPriority::Prefetch),
            ("background-a", FetchPriority::Background),
            ("background-b", FetchPriority::Background),
            ("foreground", FetchPriority::Interactive),
        ]
        .into_iter()
        .enumerate()
        {
            let (release, handle) = queued(
                Arc::clone(&coordinator),
                priority,
                "same-origin",
                name,
                events.clone(),
                policy,
            );
            releases.insert(name, release);
            handles.push(handle);
            wait_for_waiters(&coordinator, index + 1);
        }
        drop(first);
        for expected in ["foreground", "background-a", "background-b", "prefetch"] {
            assert_eq!(admitted.recv_timeout(TIMEOUT).unwrap(), expected);
            {
                let state = lock(&coordinator.gate.state);
                assert!(state.active.len() <= 3);
                assert!(
                    state
                        .active
                        .values()
                        .filter(|entry| entry.priority.rank() != 0)
                        .count()
                        <= 2
                );
            }
            releases.remove(expected).unwrap().send(()).unwrap();
        }
        drop(second);
        drop(third);
        for handle in handles {
            handle.join().unwrap();
        }
    }

    #[test]
    fn a_reader_promotes_an_outer_page_and_its_queued_archive_dependency() {
        let directory = tempfile::tempdir().unwrap();
        let coordinator = coordinator(directory.path());
        let policy = RequestPolicy::default();
        let background_a = token(&coordinator, FetchPriority::Background);
        let background_b = token(&coordinator, FetchPriority::Background);
        let first = coordinator.acquire(&background_a, "host", policy).unwrap();
        let second = coordinator.acquire(&background_b, "host", policy).unwrap();
        let path = directory.path().join("archive.bin");
        let (events, admitted) = mpsc::channel();
        let leader = {
            let coordinator = Arc::clone(&coordinator);
            let path = path.clone();
            thread::spawn(move || {
                coordinator.run("page", FetchPriority::Prefetch, |page| {
                    coordinator.run_with_parent(
                        "archive",
                        FetchPriority::Prefetch,
                        Some(page),
                        |archive| {
                            let _permit = coordinator.acquire(archive, "host", policy)?;
                            events.send(archive.priority()).unwrap();
                            fs::write(&path, b"archive")?;
                            Ok(path)
                        },
                    )
                })
            })
        };
        wait_for_waiters(&coordinator, 1);
        assert!(admitted.try_recv().is_err());
        let reader = {
            let coordinator = Arc::clone(&coordinator);
            thread::spawn(move || {
                coordinator.run("page", FetchPriority::Interactive, |_| {
                    panic!("The reader must join the outer flight")
                })
            })
        };
        // The archive uses the reserved slot before either background request completes.
        assert_eq!(
            admitted.recv_timeout(TIMEOUT).unwrap(),
            FetchPriority::Interactive
        );
        assert_eq!(
            leader.join().unwrap().unwrap(),
            reader.join().unwrap().unwrap()
        );
        drop(first);
        drop(second);
    }

    #[test]
    fn origin_capacity_does_not_block_an_eligible_request_to_another_origin() {
        let directory = tempfile::tempdir().unwrap();
        let coordinator = coordinator(directory.path());
        let policy = RequestPolicy {
            max_parallel: 2,
            min_interval: Duration::ZERO,
        };
        let first_token = token(&coordinator, FetchPriority::Interactive);
        let second_token = token(&coordinator, FetchPriority::Interactive);
        let first = coordinator
            .acquire(&first_token, "limited", policy)
            .unwrap();
        let second = coordinator
            .acquire(&second_token, "limited", policy)
            .unwrap();
        let (events, admitted) = mpsc::channel();
        let (release, queued) = queued(
            Arc::clone(&coordinator),
            FetchPriority::Interactive,
            "limited",
            "limited",
            events,
            policy,
        );
        wait_for_waiters(&coordinator, 1);
        let other_token = token(&coordinator, FetchPriority::Interactive);
        let other = coordinator
            .acquire(&other_token, "another-origin", policy)
            .unwrap();
        assert!(admitted.try_recv().is_err());
        drop(other);
        drop(first);
        assert_eq!(admitted.recv_timeout(TIMEOUT).unwrap(), "limited");
        release.send(()).unwrap();
        drop(second);
        queued.join().unwrap();
    }

    #[test]
    fn origin_intervals_are_respected_and_can_relax_after_the_previous_cooldown() {
        let directory = tempfile::tempdir().unwrap();
        let coordinator = coordinator(directory.path());
        let token = token(&coordinator, FetchPriority::Interactive);
        let interval = Duration::from_millis(30);
        let policy = RequestPolicy {
            max_parallel: 3,
            min_interval: interval,
        };
        let first = coordinator.acquire(&token, "paced", policy).unwrap();
        let started = lock(&coordinator.gate.state).origins["paced"].last_start;
        drop(first);
        let second = coordinator.acquire(&token, "paced", policy).unwrap();
        assert!(started.elapsed() >= interval);
        drop(second);
        {
            let mut state = lock(&coordinator.gate.state);
            state.origins.get_mut("paced").unwrap().last_start =
                Instant::now() - Duration::from_secs(1);
        }
        let relaxed = coordinator
            .acquire(&token, "paced", RequestPolicy::default())
            .unwrap();
        assert_eq!(
            lock(&coordinator.gate.state).origins["paced"].last_interval,
            Duration::ZERO
        );
        drop(relaxed);
    }

    #[test]
    fn priority_parent_cycles_return_errors_instead_of_waiting_for_their_own_flights() {
        let directory = tempfile::tempdir().unwrap();
        let coordinator = coordinator(directory.path());
        let result = coordinator.run("outer", FetchPriority::Prefetch, |outer| {
            coordinator.run_with_parent("inner", FetchPriority::Prefetch, Some(outer), |inner| {
                coordinator.run_with_parent("outer", FetchPriority::Prefetch, Some(inner), |_| {
                    panic!("A cyclic follower must not execute work")
                })
            })
        });
        assert!(result.unwrap_err().to_string().contains("own flight"));
        assert!(lock(&coordinator.flights).is_empty());
    }

    #[test]
    fn an_origin_also_keeps_one_slot_available_for_foreground_requests() {
        let directory = tempfile::tempdir().unwrap();
        let coordinator = coordinator(directory.path());
        let policy = RequestPolicy {
            max_parallel: 2,
            min_interval: Duration::ZERO,
        };
        let background = token(&coordinator, FetchPriority::Background);
        let held = coordinator
            .acquire(&background, "reserved-origin", policy)
            .unwrap();
        let (events, admitted) = mpsc::channel();
        let (background_release, background_thread) = queued(
            Arc::clone(&coordinator),
            FetchPriority::Background,
            "reserved-origin",
            "background",
            events.clone(),
            policy,
        );
        wait_for_waiters(&coordinator, 1);
        let (foreground_release, foreground_thread) = queued(
            Arc::clone(&coordinator),
            FetchPriority::Interactive,
            "reserved-origin",
            "foreground",
            events,
            policy,
        );
        assert_eq!(admitted.recv_timeout(TIMEOUT).unwrap(), "foreground");
        foreground_release.send(()).unwrap();
        foreground_thread.join().unwrap();
        assert!(admitted.try_recv().is_err());
        drop(held);
        assert_eq!(admitted.recv_timeout(TIMEOUT).unwrap(), "background");
        background_release.send(()).unwrap();
        background_thread.join().unwrap();
    }

    #[test]
    fn a_single_request_origin_finishes_active_work_before_admitting_queued_foreground() {
        let directory = tempfile::tempdir().unwrap();
        let coordinator = coordinator(directory.path());
        let policy = RequestPolicy {
            max_parallel: 1,
            min_interval: Duration::ZERO,
        };
        let prefetch = token(&coordinator, FetchPriority::Prefetch);
        let held = coordinator
            .acquire(&prefetch, "serial-origin", policy)
            .unwrap();
        let (events, admitted) = mpsc::channel();
        let (background_release, background_thread) = queued(
            Arc::clone(&coordinator),
            FetchPriority::Background,
            "serial-origin",
            "background",
            events.clone(),
            policy,
        );
        wait_for_waiters(&coordinator, 1);
        let (foreground_release, foreground_thread) = queued(
            Arc::clone(&coordinator),
            FetchPriority::Interactive,
            "serial-origin",
            "foreground",
            events,
            policy,
        );
        wait_for_waiters(&coordinator, 2);
        assert!(admitted.try_recv().is_err());
        drop(held);
        assert_eq!(admitted.recv_timeout(TIMEOUT).unwrap(), "foreground");
        foreground_release.send(()).unwrap();
        assert_eq!(admitted.recv_timeout(TIMEOUT).unwrap(), "background");
        background_release.send(()).unwrap();
        foreground_thread.join().unwrap();
        background_thread.join().unwrap();
    }

    #[test]
    fn one_source_cannot_bypass_its_parallel_limit_by_using_another_cdn() {
        let directory = tempfile::tempdir().unwrap();
        let coordinator = coordinator(directory.path());
        let policy = RequestPolicy {
            max_parallel: 1,
            min_interval: Duration::ZERO,
        };
        let first_token = token(&coordinator, FetchPriority::Interactive);
        let first = coordinator
            .acquire_scoped(&first_token, "cdn-a", Some("shared-source"), policy)
            .unwrap();
        let (events, admitted) = mpsc::channel();
        let (release, second) = queued_scoped(
            Arc::clone(&coordinator),
            FetchPriority::Interactive,
            "cdn-b",
            Some("shared-source"),
            "shared-source",
            events,
            policy,
        );
        wait_for_waiters(&coordinator, 1);
        assert!(admitted.try_recv().is_err());
        // An unrelated source still uses one of the free global slots.
        let other_token = token(&coordinator, FetchPriority::Interactive);
        let other = coordinator
            .acquire_scoped(&other_token, "cdn-c", Some("another-source"), policy)
            .unwrap();
        assert_eq!(lock(&coordinator.gate.state).active.len(), 2);
        assert!(admitted.try_recv().is_err());
        drop(first);
        assert_eq!(admitted.recv_timeout(TIMEOUT).unwrap(), "shared-source");
        {
            let state = lock(&coordinator.gate.state);
            assert_eq!(state.active.len(), 2);
            assert_eq!(
                state
                    .active
                    .values()
                    .filter(|entry| entry.source.as_deref() == Some("shared-source"))
                    .count(),
                1
            );
        }
        release.send(()).unwrap();
        second.join().unwrap();
        drop(other);
    }

    #[test]
    fn a_source_interval_applies_between_different_cdns_and_can_relax() {
        let directory = tempfile::tempdir().unwrap();
        let coordinator = coordinator(directory.path());
        let token = token(&coordinator, FetchPriority::Interactive);
        let interval = Duration::from_millis(30);
        let policy = RequestPolicy {
            max_parallel: 3,
            min_interval: interval,
        };
        let first = coordinator
            .acquire_scoped(&token, "cdn-a", Some("paced-source"), policy)
            .unwrap();
        let started = lock(&coordinator.gate.state).sources["paced-source"].last_start;
        drop(first);
        let second = coordinator
            .acquire_scoped(&token, "cdn-b", Some("paced-source"), policy)
            .unwrap();
        assert!(started.elapsed() >= interval);
        assert_eq!(lock(&coordinator.gate.state).active.len(), 1);
        drop(second);
        {
            let mut state = lock(&coordinator.gate.state);
            state.sources.get_mut("paced-source").unwrap().last_start =
                Instant::now() - Duration::from_secs(1);
        }
        let relaxed = coordinator
            .acquire_scoped(
                &token,
                "cdn-c",
                Some("paced-source"),
                RequestPolicy::default(),
            )
            .unwrap();
        assert_eq!(
            lock(&coordinator.gate.state).sources["paced-source"].last_interval,
            Duration::ZERO
        );
        drop(relaxed);
    }

    #[test]
    fn a_source_preserves_a_foreground_slot_across_multiple_cdns() {
        let directory = tempfile::tempdir().unwrap();
        let coordinator = coordinator(directory.path());
        let policy = RequestPolicy {
            max_parallel: 2,
            min_interval: Duration::ZERO,
        };
        let background = token(&coordinator, FetchPriority::Background);
        let held = coordinator
            .acquire_scoped(&background, "cdn-a", Some("reserved-source"), policy)
            .unwrap();
        let (events, admitted) = mpsc::channel();
        let (background_release, background_thread) = queued_scoped(
            Arc::clone(&coordinator),
            FetchPriority::Background,
            "cdn-b",
            Some("reserved-source"),
            "background",
            events.clone(),
            policy,
        );
        wait_for_waiters(&coordinator, 1);
        let (foreground_release, foreground_thread) = queued_scoped(
            Arc::clone(&coordinator),
            FetchPriority::Interactive,
            "cdn-c",
            Some("reserved-source"),
            "foreground",
            events,
            policy,
        );
        assert_eq!(admitted.recv_timeout(TIMEOUT).unwrap(), "foreground");
        assert_eq!(lock(&coordinator.gate.state).active.len(), 2);
        foreground_release.send(()).unwrap();
        foreground_thread.join().unwrap();
        assert!(admitted.try_recv().is_err());
        drop(held);
        assert_eq!(admitted.recv_timeout(TIMEOUT).unwrap(), "background");
        background_release.send(()).unwrap();
        background_thread.join().unwrap();
    }

    #[test]
    fn suspension_cancels_queued_leaders_and_followers_but_keeps_live_permits() {
        let directory = tempfile::tempdir().unwrap();
        let coordinator = coordinator(directory.path());
        let control = coordinator.control();
        let policy = RequestPolicy {
            max_parallel: 1,
            min_interval: Duration::ZERO,
        };
        let held_token = token(&coordinator, FetchPriority::Background);
        let held = coordinator
            .acquire_scoped(&held_token, "origin", Some("source"), policy)
            .unwrap();
        let path = directory.path().join("page.bin");
        let leader = {
            let coordinator = Arc::clone(&coordinator);
            thread::spawn(move || {
                coordinator.run("queued", FetchPriority::Prefetch, |token| {
                    let _permit =
                        coordinator.acquire_scoped(token, "origin", Some("source"), policy)?;
                    Ok(path)
                })
            })
        };
        wait_for_waiters(&coordinator, 1);
        let follower = {
            let coordinator = Arc::clone(&coordinator);
            thread::spawn(move || {
                coordinator.run("queued", FetchPriority::Interactive, |_| {
                    panic!("A follower must not execute work")
                })
            })
        };
        wait_for_priority(&coordinator, "queued", FetchPriority::Interactive);
        let (acknowledged, acknowledgement) = mpsc::channel();
        let suspender = {
            let control = control.clone();
            thread::spawn(move || acknowledged.send(control.suspend()).unwrap())
        };
        assert_eq!(acknowledgement.recv_timeout(TIMEOUT).unwrap(), 1);
        suspender.join().unwrap();
        assert_interrupted(leader.join().unwrap());
        assert_interrupted(follower.join().unwrap());
        assert_interrupted(held_token.check_active());
        assert_interrupted(coordinator.run("closed", FetchPriority::Interactive, |_| {
            panic!("Suspended work must not start")
        }));
        {
            let state = lock(&coordinator.gate.state);
            assert!(state.waiting.is_empty());
            assert_eq!(state.active.len(), 1);
        }
        assert!(lock(&coordinator.flights).is_empty());
        assert_eq!(control.resume(), 1);
        assert_eq!(lock(&coordinator.gate.state).active.len(), 1);
        drop(held);
        assert!(lock(&coordinator.gate.state).active.is_empty());
    }

    #[test]
    fn resumed_same_key_work_is_not_removed_or_overwritten_by_an_old_leader() {
        let directory = tempfile::tempdir().unwrap();
        let coordinator = coordinator(directory.path());
        let control = coordinator.control();
        let old_path = directory.path().join("old.bin");
        fs::write(&old_path, b"old").unwrap();
        let (entered, entered_wait) = mpsc::channel();
        let (old_release, old_wait) = mpsc::channel();
        let old_leader = {
            let coordinator = Arc::clone(&coordinator);
            thread::spawn(move || {
                coordinator.run("same-key", FetchPriority::Prefetch, |token| {
                    let _permit = coordinator.acquire(token, "origin", RequestPolicy::default())?;
                    entered.send(()).unwrap();
                    old_wait.recv_timeout(TIMEOUT).unwrap();
                    // Simulate a blocking transport returning a late success.
                    Ok(old_path)
                })
            })
        };
        entered_wait.recv_timeout(TIMEOUT).unwrap();
        let old_follower = {
            let coordinator = Arc::clone(&coordinator);
            thread::spawn(move || {
                coordinator.run("same-key", FetchPriority::Interactive, |_| {
                    panic!("The old follower must not execute work")
                })
            })
        };
        wait_for_priority(&coordinator, "same-key", FetchPriority::Interactive);
        assert_eq!(control.suspend(), 1);
        assert_interrupted(old_follower.join().unwrap());
        assert_eq!(lock(&coordinator.gate.state).active.len(), 1);
        assert_eq!(control.resume(), 1);
        let new_path = directory.path().join("new.bin");
        let (new_entered, new_entered_wait) = mpsc::channel();
        let (new_release, new_wait) = mpsc::channel();
        let new_leader = {
            let coordinator = Arc::clone(&coordinator);
            let new_path = new_path.clone();
            thread::spawn(move || {
                coordinator.run_in_epoch("same-key", FetchPriority::Prefetch, 1, |token| {
                    new_entered.send(()).unwrap();
                    new_wait.recv_timeout(TIMEOUT).unwrap();
                    token.commit_if_active(|| {
                        fs::write(&new_path, b"new")?;
                        Ok(new_path)
                    })
                })
            })
        };
        new_entered_wait.recv_timeout(TIMEOUT).unwrap();
        let new_flight = Arc::clone(lock(&coordinator.flights).get("same-key").unwrap());
        old_release.send(()).unwrap();
        assert_interrupted(old_leader.join().unwrap());
        assert!(Arc::ptr_eq(
            lock(&coordinator.flights).get("same-key").unwrap(),
            &new_flight
        ));
        assert!(lock(&coordinator.gate.state).active.is_empty());
        let new_follower = {
            let coordinator = Arc::clone(&coordinator);
            thread::spawn(move || {
                coordinator.run_in_epoch("same-key", FetchPriority::Interactive, 1, |_| {
                    panic!("The new follower must share the resumed flight")
                })
            })
        };
        wait_for_priority(&coordinator, "same-key", FetchPriority::Interactive);
        new_release.send(()).unwrap();
        assert_eq!(
            new_leader.join().unwrap().unwrap(),
            new_follower.join().unwrap().unwrap()
        );
        assert_eq!(fs::read(new_path).unwrap(), b"new");
        assert!(lock(&coordinator.flights).is_empty());
    }

    #[test]
    fn suspension_fences_existing_commits_without_holding_the_admission_mutex() {
        let directory = tempfile::tempdir().unwrap();
        let coordinator = coordinator(directory.path());
        let control = coordinator.control();
        let committing_token = Arc::new(token(&coordinator, FetchPriority::Interactive));
        let path = directory.path().join("committed.bin");
        let (entered, entered_wait) = mpsc::channel();
        let (release, wait) = mpsc::channel();
        let commit = {
            let token = Arc::clone(&committing_token);
            let path = path.clone();
            thread::spawn(move || {
                token.commit_if_active(|| {
                    entered.send(()).unwrap();
                    wait.recv_timeout(TIMEOUT).unwrap();
                    fs::write(&path, b"complete-before-ack")?;
                    Ok(path)
                })
            })
        };
        entered_wait.recv_timeout(TIMEOUT).unwrap();
        let (acknowledged, acknowledgement) = mpsc::channel();
        let suspend = {
            let control = control.clone();
            thread::spawn(move || acknowledged.send(control.suspend()).unwrap())
        };
        wait_for_suspension(&coordinator);
        assert!(acknowledgement.try_recv().is_err());
        // The commit fence must not retain the Gate mutex while waiting.
        assert_interrupted(coordinator.acquire(
            &committing_token,
            "another-origin",
            RequestPolicy::default(),
        ));
        release.send(()).unwrap();
        assert_eq!(commit.join().unwrap().unwrap(), path);
        assert_eq!(acknowledgement.recv_timeout(TIMEOUT).unwrap(), 1);
        suspend.join().unwrap();
        let attempted = AtomicBool::new(false);
        assert_interrupted(committing_token.commit_if_active(|| {
            attempted.store(true, Ordering::SeqCst);
            Ok(PathBuf::new())
        }));
        assert!(!attempted.load(Ordering::SeqCst));
        assert_eq!(fs::read(path).unwrap(), b"complete-before-ack");
    }

    #[test]
    fn lifecycle_calls_are_idempotent_and_stale_manager_epochs_cannot_start_work() {
        let directory = tempfile::tempdir().unwrap();
        let control = NetworkControl::open(directory.path()).unwrap();
        let coordinator = coordinator(directory.path());
        assert!(Arc::ptr_eq(&control.coordinator, &coordinator));
        assert_eq!(control.epoch(), 0);
        assert_eq!(control.resume(), 0);
        assert_eq!(control.suspend(), 1);
        assert_eq!(control.suspend(), 1);
        assert!(control.is_suspended());
        assert_interrupted(control.check_epoch(1));
        assert_eq!(control.resume(), 1);
        assert_eq!(control.resume(), 1);
        assert!(!control.is_suspended());
        control.check_epoch(1).unwrap();
        assert_interrupted(control.check_epoch(0));
        assert_interrupted(coordinator.run_in_epoch(
            "stale",
            FetchPriority::Interactive,
            0,
            |_| panic!("A stale manager must not execute work"),
        ));
        assert!(lock(&coordinator.flights).is_empty());
        let path = directory.path().join("current.bin");
        coordinator
            .run_in_epoch("current", FetchPriority::Interactive, 1, |token| {
                token.commit_if_active(|| {
                    fs::write(&path, b"current")?;
                    Ok(path.clone())
                })
            })
            .unwrap();
        assert_eq!(control.resume(), 1);
        assert_eq!(control.suspend(), 2);
        assert_eq!(control.suspend(), 2);
    }

    #[test]
    fn a_stale_parent_cannot_create_a_new_epoch_archive_flight() {
        let directory = tempfile::tempdir().unwrap();
        let coordinator = coordinator(directory.path());
        let control = coordinator.control();
        let parent = token(&coordinator, FetchPriority::Interactive);
        control.suspend();
        control.resume();
        assert_interrupted(coordinator.run_with_parent(
            "archive",
            FetchPriority::Prefetch,
            Some(&parent),
            |_| panic!("An interrupted parent must not create resumed child work"),
        ));
        assert!(lock(&coordinator.flights).is_empty());
    }
}
