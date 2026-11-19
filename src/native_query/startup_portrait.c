/* Bounded supervisor for the sibling application's startup portrait transaction. */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <signal.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

enum { OUTPUT_LIMIT = 128 };
static pid_t group_id;

/* Either guardian can die first. A member can only stop its own private
 * group, whose ID cannot be reused while that member is still alive. */
static void stop_group(int signo) {
    (void)signo;
    if (group_id > 1 && getpgrp() == group_id && getsid(0) == group_id)
        (void)kill(0, SIGKILL);
    _exit(1);
}

static int arm_guard(pid_t parent) {
    struct sigaction action = {.sa_handler = stop_group};
    sigemptyset(&action.sa_mask);
    sigset_t signals;
    sigemptyset(&signals);
    const int guarded[] = {SIGTERM, SIGINT, SIGHUP, SIGALRM};
    for (unsigned i = 0; i < sizeof(guarded) / sizeof(*guarded); ++i) {
        if (sigaction(guarded[i], &action, NULL) < 0) return -1;
        sigaddset(&signals, guarded[i]);
    }
    if (sigprocmask(SIG_UNBLOCK, &signals, NULL) < 0 ||
        prctl(PR_SET_PDEATHSIG, SIGTERM) < 0 || getppid() != parent) return -1;
    alarm(2);
    return 0;
}

static bool positive_decimal(const char *value) {
    if (!value || value[0] < '1' || value[0] > '9') return false;
    for (const char *p = value + 1; *p; ++p)
        if (*p < '0' || *p > '9') return false;
    return true;
}

static bool known_profile(const char *value) {
    const char *const profiles[] = {"pw5", "pw6", "scribe", "scribe2", "scribe3", "scribe3-nofrontlight"};
    for (unsigned i = 0; i < sizeof(profiles) / sizeof(*profiles); ++i)
        if (!strcmp(value, profiles[i])) return true;
    return false;
}

static bool directory_current(int directory, const char *path) {
    struct stat file, name;
    return fstat(directory, &file) == 0 && lstat(path, &name) == 0 &&
        S_ISDIR(name.st_mode) && name.st_uid == geteuid() &&
        (name.st_mode & 0777) == 0700 && name.st_dev == file.st_dev && name.st_ino == file.st_ino;
}

enum match { MISMATCH, MATCH, MISSING };

static enum match exact_record(int directory, const char *leaf, const char *expected) {
    int fd = openat(directory, leaf, O_RDONLY | O_NONBLOCK | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) return errno == ENOENT ? MISSING : MISMATCH;
    struct stat before, after;
    char buffer[132];
    size_t length = strlen(expected);
    bool valid = fstat(fd, &before) == 0 && S_ISREG(before.st_mode) &&
        before.st_uid == geteuid() && before.st_nlink == 1 &&
        ((before.st_mode & 0777) == 0600 || (before.st_mode & 0777) == 0400) &&
        before.st_size == (off_t)length && length < sizeof(buffer) &&
        read(fd, buffer, sizeof(buffer)) == (ssize_t)length && !memcmp(buffer, expected, length) &&
        fstatat(directory, leaf, &after, AT_SYMLINK_NOFOLLOW) == 0 &&
        before.st_dev == after.st_dev && before.st_ino == after.st_ino &&
        before.st_ctim.tv_sec == after.st_ctim.tv_sec && before.st_ctim.tv_nsec == after.st_ctim.tv_nsec;
    close(fd);
    return valid ? MATCH : MISMATCH;
}

static bool current(int directory, const char *path, const char *owner,
                    const char *nonce_record, const char *permission) {
    return directory_current(directory, path) && exact_record(directory, "pid", owner) == MATCH &&
        exact_record(directory, "nonce", nonce_record) == MATCH &&
        exact_record(directory, "native-read-go", permission) == MATCH;
}

static bool publish_done(int directory, const char *done) {
    int fd = openat(directory, "native-read-done.pending",
                    O_WRONLY | O_CLOEXEC | O_CREAT | O_EXCL | O_NOFOLLOW, 0600);
    if (fd < 0) return false;
    size_t length = strlen(done);
    bool wrote = write(fd, done, length) == (ssize_t)length;
    if (close(fd) < 0 || !wrote) return false;
    return renameat(directory, "native-read-done.pending", directory, "native-read-done") == 0;
}

static bool collect(const char *profile, bool capability, char output[OUTPUT_LIMIT + 1], size_t *used) {
    char application[PATH_MAX];
    ssize_t n = readlink("/proc/self/exe", application, sizeof(application) - 1);
    if (n < 1 || (size_t)n >= sizeof(application) - 1) return false;
    application[n] = 0;
    char *leaf = strrchr(application, '/');
    if (!leaf || sizeof(application) - (size_t)(leaf - application) < sizeof("/aidoku")) return false;
    memcpy(leaf, "/aidoku", sizeof("/aidoku"));
    int output_pipe[2];
    if (pipe(output_pipe) < 0) return false;
    pid_t child = fork();
    if (child == 0) {
        close(output_pipe[0]);
        if (getppid() != group_id || dup2(output_pipe[1], STDOUT_FILENO) < 0) _exit(1);
        close(output_pipe[1]);
        char *const args[] = {application, "--kindle", "--device", (char *)profile,
            "--capture-native-portrait", capability ? "--capture-sleep-capability" : NULL, NULL};
        execv(application, args);
        _exit(1);
    }
    close(output_pipe[1]);
    if (child < 0) { close(output_pipe[0]); return false; }
    size_t total = 0;
    while (total <= OUTPUT_LIMIT) {
        ssize_t got = read(output_pipe[0], output + total, OUTPUT_LIMIT + 1 - total);
        if (got < 0 && errno == EINTR) continue;
        if (got < 0) { close(output_pipe[0]); return false; }
        if (!got) break;
        total += (size_t)got;
    }
    close(output_pipe[0]);
    if (total > OUTPUT_LIMIT) return false;
    int status;
    pid_t waited;
    do { waited = waitpid(child, &status, 0); } while (waited < 0 && errno == EINTR);
    if (waited != child || !WIFEXITED(status) || WEXITSTATUS(status) != 0) return false;
    *used = total;
    return (total == sizeof("PUBLISHED\n") - 1 && !memcmp(output, "PUBLISHED\n", total)) ||
        (total == sizeof("PUBLISHED 0\n") - 1 &&
         (!memcmp(output, "PUBLISHED 0\n", total) || !memcmp(output, "PUBLISHED 1\n", total)));
}

int main(int argc, char **argv) {
    if ((argc != 4 && argc != 5) || !positive_decimal(argv[1]) || !positive_decimal(argv[2]) ||
        strlen(argv[2]) > 20 || !known_profile(argv[3]) ||
        (argc == 5 && strcmp(argv[4], "--capture-sleep-capability"))) return 64;
    errno = 0;
    long parent_number = strtol(argv[1], NULL, 10);
    if (errno || parent_number <= 1 || parent_number > INT_MAX || (pid_t)parent_number != getppid()) return 1;
    errno = 0;
    (void)strtoull(argv[2], NULL, 10);
    if (errno) return 64;
    const char *path = getenv("AIDOKU_LAUNCHER_DIR"), *nonce = getenv("AIDOKU_LAUNCH_NONCE");
    if (!path || *path != '/' || !nonce || strlen(nonce) != 32 ||
        strspn(nonce, "0123456789abcdefABCDEF") != 32) return 1;

    group_id = getpid();
    if (getsid(0) != group_id && setsid() != group_id) return 1;
    if (getpgrp() != group_id || getsid(0) != group_id || arm_guard((pid_t)parent_number) < 0) return 1;
    pid_t watchdog = fork();
    if (watchdog < 0) return 1;
    if (watchdog == 0) {
        if (arm_guard(group_id) < 0) stop_group(0);
        for (;;) pause();
    }

    int dir = open(path, O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    if (dir < 0 || !directory_current(dir, path)) stop_group(0);
    char owner[36], nonce_record[34], permission[96], done[96];
    snprintf(owner, sizeof(owner), "%ld\n", parent_number);
    snprintf(nonce_record, sizeof(nonce_record), "%s\n", nonce);
    snprintf(permission, sizeof(permission), "%ld:%s:%s\n", (long)group_id, argv[2], nonce);
    enum match permission_state;
    const struct timespec tick = {.tv_nsec = 1000000};
    while ((permission_state = exact_record(dir, "native-read-go", permission)) == MISSING)
        nanosleep(&tick, NULL);
    if (permission_state != MATCH || !current(dir, path, owner, nonce_record, permission)) stop_group(0);

    char result[OUTPUT_LIMIT + 1];
    size_t used = 0;
    bool ok = collect(argv[3], argc == 5, result, &used);
    if (!current(dir, path, owner, nonce_record, permission)) stop_group(0);
    if (ok) ok = write(STDOUT_FILENO, result, used) == (ssize_t)used;
    snprintf(done, sizeof(done), "%s:%s:%d\n", argv[2], nonce, ok ? 0 : 1);
    if (!publish_done(dir, done)) stop_group(0);
    close(dir);
    /* Keep the proven session leader alive through the launcher's group reap. */
    for (;;) pause();
}
