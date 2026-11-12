//! Strict, pinned private records for the launcher-authorized sleep cover.
//!
//! The framing and private-directory contract follow BiliComics' reviewed power
//! records and Aidoku's native portrait publication, with the local space ABI.

use std::{
    fs::{self, File, Metadata, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
};

use anyhow::{Context, Result, ensure};

const MAX_RECORD_BYTES: usize = 512;

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct CoverRequest {
    pub generation: u64,
    pub pid: u32,
    pub nonce: String,
    pub epoch: u64,
    pub sequence: u64,
}

impl CoverRequest {
    pub(crate) fn parse(raw: &str) -> Option<Self> {
        let record = single_record(raw)?;
        let fields = record.split(' ').collect::<Vec<_>>();
        if fields.len() != 6
            || fields[5] != "suspend"
            || fields[2].len() != 32
            || !fields[2].bytes().all(|byte| byte.is_ascii_hexdigit())
            || [0, 1, 3, 4].iter().any(|index| {
                fields[*index].is_empty()
                    || !fields[*index].bytes().all(|byte| byte.is_ascii_digit())
            })
        {
            return None;
        }
        let request = Self {
            generation: fields[0].parse().ok().filter(|value| *value > 0)?,
            pid: fields[1].parse().ok().filter(|value| *value > 1)?,
            nonce: fields[2].to_owned(),
            epoch: fields[3].parse().ok().filter(|value| *value > 0)?,
            sequence: fields[4].parse().ok().filter(|value| *value > 0)?,
        };
        (request.line() == raw).then_some(request)
    }

    pub(crate) fn line(&self) -> String {
        format!(
            "{} {} {} {} {} suspend\n",
            self.generation, self.pid, self.nonce, self.epoch, self.sequence
        )
    }
}

fn single_record(value: &str) -> Option<&str> {
    let record = value.strip_suffix('\n')?;
    (!record.is_empty() && !record.contains(['\n', '\r']) && value.len() <= MAX_RECORD_BYTES)
        .then_some(record)
}

pub(crate) struct CoverRecords {
    path: PathBuf,
    directory: File,
    identity: Metadata,
}

impl CoverRecords {
    pub(crate) fn open(path: &Path) -> Result<Self> {
        ensure!(
            path.is_absolute(),
            "The cover record directory must be absolute"
        );
        let mut options = OpenOptions::new();
        options.read(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.custom_flags(libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC);
        }
        #[cfg(windows)]
        {
            use std::os::windows::fs::OpenOptionsExt;
            options.custom_flags(0x0200_0000 | 0x0020_0000);
        }
        let directory = options
            .open(path)
            .context("Open private sleep-cover records")?;
        let identity = directory.metadata()?;
        ensure!(
            private(&identity, true),
            "The cover directory is not owned and private"
        );
        let records = Self {
            path: path.into(),
            directory,
            identity,
        };
        records.ensure_current()?;
        Ok(records)
    }

    fn ensure_current(&self) -> Result<()> {
        let current = fs::symlink_metadata(&self.path)?;
        ensure!(
            private(&current, true) && same_identity(&self.identity, &current),
            "The private cover directory was replaced"
        );
        Ok(())
    }

    pub(crate) fn read(&self, name: &str) -> Result<String> {
        ensure!(
            !name.is_empty()
                && name
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-'),
            "Invalid private cover leaf"
        );
        self.ensure_current()?;
        let mut file = self.open_leaf(name)?;
        let before = file.metadata()?;
        ensure!(
            private(&before, false) && before.len() <= MAX_RECORD_BYTES as u64,
            "Unsafe private cover record"
        );
        let mut bytes = Vec::new();
        Read::by_ref(&mut file)
            .take((MAX_RECORD_BYTES + 1) as u64)
            .read_to_end(&mut bytes)?;
        let after = file.metadata()?;
        let named = self.open_leaf(name)?.metadata()?;
        ensure!(
            bytes.len() as u64 == before.len()
                && bytes.len() <= MAX_RECORD_BYTES
                && private(&after, false)
                && private(&named, false)
                && stable(&before, &after)
                && stable(&after, &named),
            "The private cover record changed while reading"
        );
        self.ensure_current()?;
        let raw = String::from_utf8(bytes)?;
        ensure!(
            single_record(&raw).is_some(),
            "The private cover record has invalid framing"
        );
        Ok(raw)
    }

    pub(crate) fn optional(&self, name: &str) -> Result<Option<String>> {
        match self.read(name) {
            Ok(value) => Ok(Some(value)),
            Err(error)
                if error
                    .downcast_ref::<std::io::Error>()
                    .is_some_and(|error| error.kind() == std::io::ErrorKind::NotFound) =>
            {
                Ok(None)
            }
            Err(error) => Err(error),
        }
    }

    pub(crate) fn publish_ready(
        &self,
        request: &CoverRequest,
        validate: impl Fn() -> Result<()>,
    ) -> Result<()> {
        self.ensure_current()?;
        validate()?;
        ensure!(
            self.optional("sleep-cover-ready")?.is_none(),
            "A cover acknowledgement already exists"
        );
        #[cfg(unix)]
        {
            use std::{
                ffi::CString,
                os::fd::{AsRawFd, FromRawFd},
            };
            let pending = CString::new("sleep-cover-ready.pending")?;
            let target = CString::new("sleep-cover-ready")?;
            // SAFETY: the pinned directory fd and fixed terminated leaves stay
            // live. O_EXCL never follows or overwrites another pending record.
            let descriptor = unsafe {
                libc::openat(
                    self.directory.as_raw_fd(),
                    pending.as_ptr(),
                    libc::O_WRONLY
                        | libc::O_CREAT
                        | libc::O_EXCL
                        | libc::O_NOFOLLOW
                        | libc::O_CLOEXEC,
                    0o600,
                )
            };
            if descriptor < 0 {
                return Err(std::io::Error::last_os_error().into());
            }
            // SAFETY: this new descriptor has exactly one File owner.
            let mut file = unsafe { File::from_raw_fd(descriptor) };
            file.write_all(request.line().as_bytes())?;
            file.sync_all()?;
            let created = file.metadata()?;
            validate()?;
            self.ensure_current()?;
            let named = self.open_leaf("sleep-cover-ready.pending")?.metadata()?;
            ensure!(
                private(&created, false) && private(&named, false) && stable(&created, &named),
                "The pending cover acknowledgement changed"
            );
            ensure!(
                self.optional("sleep-cover-ready")?.is_none(),
                "The cover acknowledgement appeared before commit"
            );
            // SAFETY: both leaves are in the same pinned private directory.
            ensure!(
                unsafe {
                    libc::renameat(
                        self.directory.as_raw_fd(),
                        pending.as_ptr(),
                        self.directory.as_raw_fd(),
                        target.as_ptr(),
                    )
                } == 0,
                "Publish the cover acknowledgement"
            );
            let published = self.open_leaf("sleep-cover-ready")?.metadata()?;
            ensure!(
                private(&published, false)
                    && same_identity(&created, &published)
                    && created.len() == published.len(),
                "The cover acknowledgement was replaced"
            );
            self.ensure_current()?;
            return Ok(());
        }
        #[cfg(not(unix))]
        {
            let _ = request;
            anyhow::bail!("Private native cover publication requires Unix");
        }
    }

    #[cfg(unix)]
    fn open_leaf(&self, name: &str) -> Result<File> {
        use std::{
            ffi::CString,
            os::fd::{AsRawFd, FromRawFd},
        };
        let leaf = CString::new(name)?;
        // SAFETY: the pinned directory and terminated leaf remain live;
        // O_NONBLOCK prevents opening a substituted FIFO from waiting.
        let descriptor = unsafe {
            libc::openat(
                self.directory.as_raw_fd(),
                leaf.as_ptr(),
                libc::O_RDONLY | libc::O_NOFOLLOW | libc::O_NONBLOCK | libc::O_CLOEXEC,
            )
        };
        if descriptor < 0 {
            return Err(std::io::Error::last_os_error().into());
        }
        // SAFETY: this new descriptor has exactly one File owner.
        Ok(unsafe { File::from_raw_fd(descriptor) })
    }

    #[cfg(not(unix))]
    fn open_leaf(&self, name: &str) -> Result<File> {
        Ok(File::open(self.path.join(name))?)
    }
}

fn private(metadata: &Metadata, directory: bool) -> bool {
    if metadata.file_type().is_symlink()
        || if directory {
            !metadata.is_dir()
        } else {
            !metadata.is_file()
        }
    {
        return false;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        // SAFETY: geteuid reads process identity without pointer arguments.
        metadata.uid() == unsafe { libc::geteuid() }
            && metadata.mode() & 0o077 == 0
            && (directory || metadata.nlink() == 1)
    }
    #[cfg(not(unix))]
    {
        true
    }
}

fn same_identity(left: &Metadata, right: &Metadata) -> bool {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        left.dev() == right.dev() && left.ino() == right.ino()
    }
    #[cfg(not(unix))]
    {
        left.created().ok() == right.created().ok()
    }
}

fn stable(left: &Metadata, right: &Metadata) -> bool {
    if !same_identity(left, right) || left.len() != right.len() {
        return false;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        left.mtime() == right.mtime()
            && left.mtime_nsec() == right.mtime_nsec()
            && left.ctime() == right.ctime()
            && left.ctime_nsec() == right.ctime_nsec()
    }
    #[cfg(not(unix))]
    {
        left.modified().ok() == right.modified().ok()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_cover_request_binds_all_six_fields_and_one_exact_line() {
        let raw = "3 42 0123456789abcdef0123456789abcdef 7 11 suspend\n";
        let request = CoverRequest::parse(raw).unwrap();
        assert_eq!(request.line(), raw);
        for invalid in [
            raw.trim_end().to_owned(),
            format!("{raw}\n"),
            raw.replace(" 42 ", "  42 "),
            raw.replace("suspend", "resume"),
            raw.replace(" 11 ", " 0 "),
            raw.replace("3 42", "0 42"),
            raw.replace("3 42", "03 42"),
            raw.replace('\n', "\r\n"),
        ] {
            assert!(CoverRequest::parse(&invalid).is_none());
        }
    }

    #[cfg(unix)]
    fn private_file(path: &Path, content: &str) {
        use std::os::unix::fs::PermissionsExt;
        fs::write(path, content).unwrap();
        fs::set_permissions(path, fs::Permissions::from_mode(0o600)).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn private_records_reject_links_fifos_and_replaced_directories() {
        use std::os::unix::fs::{PermissionsExt, symlink};
        let parent = tempfile::tempdir().unwrap();
        let path = parent.path().join("records");
        fs::create_dir(&path).unwrap();
        fs::set_permissions(&path, fs::Permissions::from_mode(0o700)).unwrap();
        let records = CoverRecords::open(&path).unwrap();
        assert!(records.optional("absent").unwrap().is_none());
        private_file(&path.join("record"), "current\n");
        assert_eq!(records.read("record").unwrap(), "current\n");
        symlink(path.join("record"), path.join("linked")).unwrap();
        assert!(records.read("linked").is_err());
        fs::hard_link(path.join("record"), path.join("hardlinked")).unwrap();
        assert!(records.read("record").is_err());
        let fifo =
            std::ffi::CString::new(path.join("fifo").as_os_str().as_encoded_bytes()).unwrap();
        // SAFETY: this fixture creates only its new private FIFO. Reading must
        // reject its type without waiting for a writer.
        assert_eq!(unsafe { libc::mkfifo(fifo.as_ptr(), 0o600) }, 0);
        assert!(records.read("fifo").is_err());
        private_file(
            &path.join("oversized"),
            &format!("{}\n", "x".repeat(MAX_RECORD_BYTES)),
        );
        assert!(records.read("oversized").is_err());
        fs::rename(&path, parent.path().join("retired")).unwrap();
        fs::create_dir(&path).unwrap();
        fs::set_permissions(&path, fs::Permissions::from_mode(0o700)).unwrap();
        private_file(&path.join("record"), "replacement\n");
        assert!(records.read("record").is_err());
    }

    #[cfg(unix)]
    #[test]
    fn private_ready_publication_revalidates_before_the_atomic_commit() {
        use std::{cell::Cell, os::unix::fs::PermissionsExt};
        let directory = tempfile::tempdir().unwrap();
        fs::set_permissions(directory.path(), fs::Permissions::from_mode(0o700)).unwrap();
        let records = CoverRecords::open(directory.path()).unwrap();
        let request =
            CoverRequest::parse("3 42 0123456789abcdef0123456789abcdef 7 11 suspend\n").unwrap();
        let validations = Cell::new(0);
        assert!(
            records
                .publish_ready(&request, || {
                    validations.set(validations.get() + 1);
                    anyhow::ensure!(validations.get() == 1, "The current request was replaced");
                    Ok(())
                })
                .is_err()
        );
        assert_eq!(validations.get(), 2);
        assert!(!directory.path().join("sleep-cover-ready").exists());
        // The rejected pending leaf remains owned evidence until the launcher
        // performs its exact four-leaf retirement.
        assert!(directory.path().join("sleep-cover-ready.pending").exists());
        fs::remove_file(directory.path().join("sleep-cover-ready.pending")).unwrap();
        records.publish_ready(&request, || Ok(())).unwrap();
        assert_eq!(records.read("sleep-cover-ready").unwrap(), request.line());
        assert!(records.publish_ready(&request, || Ok(())).is_err());
        assert_eq!(records.read("sleep-cover-ready").unwrap(), request.line());
    }
}
