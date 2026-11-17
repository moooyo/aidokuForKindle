/* Aidoku-only catalog access. The launcher owns the eventual notification;
 * all other handler operations retain their original implementation. */
#define _GNU_SOURCE
#include "common.h"
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>
#include <sys/prctl.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <sys/wait.h>

static int (*original_utimes)(const char *, const struct timeval[2]);
static bool enabled;
static bool preserve;
static bool update_catalog;
static bool quiet_catalog;
static pid_t owner_pid;
static int trace_descriptor = -1;
static atomic_uint request_count;
static char client_path[PATH_MAX];
static char client_identity[192];

static void clear_notification_handoff(void) {
    unsetenv(ENTRY_NOTIFY_HELPER_ENV);
    unsetenv(ENTRY_NOTIFY_HASH_ENV);
}

struct child_record { pid_t parent, group, session; unsigned long long birth; };

static bool read_child(pid_t pid, struct child_record *record) {
    char path[64], buffer[1024];
    snprintf(path, sizeof(path), "/proc/%ld/stat", (long)pid);
    int descriptor = open(path, O_RDONLY | O_CLOEXEC);
    if (descriptor < 0) return false;
    ssize_t size = read(descriptor, buffer, sizeof(buffer) - 1);
    close(descriptor);
    if (size <= 0 || (size_t)size >= sizeof(buffer) - 1) return false;
    buffer[size] = 0;
    char *tail = strrchr(buffer, ')');
    if (!tail || tail[1] != ' ') return false;
    char *save = NULL;
    char *field = strtok_r(tail + 2, " ", &save);
    for (unsigned index = 0; index <= 19; index++) {
        if (!field) return false;
        if (index == 1) record->parent = (pid_t)strtol(field, NULL, 10);
        if (index == 2) record->group = (pid_t)strtol(field, NULL, 10);
        if (index == 3) record->session = (pid_t)strtol(field, NULL, 10);
        if (index == 19) record->birth = strtoull(field, NULL, 10);
        field = strtok_r(NULL, " ", &save);
    }
    return record->birth != 0;
}

static unsigned long long monotonic_ms(void) {
    struct timespec value;
    if (clock_gettime(CLOCK_MONOTONIC, &value) != 0) return 0;
    return (unsigned long long)value.tv_sec * 1000 + value.tv_nsec / 1000000;
}

static bool run_catalog_client(void) {
    unsigned long long started = monotonic_ms();
    if (!started || !*client_path || !*client_identity) return false;
    int executable = open(client_path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (executable < 0) return false;
    if (executable < 3) {
        int duplicated = fcntl(executable, F_DUPFD_CLOEXEC, 3);
        close(executable);
        executable = duplicated;
        if (executable < 0) return false;
    }
    struct stat status;
    char observed[192], elf[4];
    if (fstat(executable, &status) != 0 || !S_ISREG(status.st_mode) ||
        pread(executable, elf, sizeof(elf), 0) != sizeof(elf) || memcmp(elf, "\177ELF", 4) != 0) {
        close(executable);
        return false;
    }
    entry_identity(&status, observed);
    if (strcmp(observed, client_identity) != 0) { close(executable); return false; }
    char execute_path[64];
    snprintf(execute_path, sizeof(execute_path), "/proc/self/fd/%d", executable);
    int gate[2];
    if (socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, gate) != 0) {
        close(executable);
        return false;
    }
    pid_t child = fork();
    if (child == 0) {
        close(gate[0]);
        if (prctl(PR_SET_PDEATHSIG, SIGKILL) != 0 || getppid() != owner_pid || setsid() < 0) _exit(71);
        char permission = 0;
        ssize_t size;
        do { size = recv(gate[1], &permission, 1, 0); } while (size < 0 && errno == EINTR);
        close(gate[1]);
        if (size != 1 || permission != 'G') _exit(72);
        int sink = open("/dev/null", O_RDWR);
        if (sink < 0 || dup2(sink, 0) < 0 || dup2(sink, 1) < 0 || dup2(sink, 2) < 0) _exit(73);
        if (sink > 2) close(sink);
        char *arguments[] = { (char *)"aidoku-catalog-client",
            quiet_catalog ? (char *)"--touch-quiet" : (char *)"--touch", NULL };
        execv(execute_path, arguments);
        _exit(74);
    }
    close(gate[1]);
    close(executable);
    if (child < 0) { close(gate[0]); return false; }
    unsigned long long birth = 0;
    bool released = false, completed = false, success = false;
    for (;;) {
        int result = 0;
        pid_t waited = waitpid(child, &result, WNOHANG);
        if (waited == child) { completed = true; success = released && WIFEXITED(result) && WEXITSTATUS(result) == 0; break; }
        if (waited < 0 && errno != EINTR) { completed = true; break; }
        struct child_record record;
        if (read_child(child, &record) && record.parent == owner_pid) {
            if (!birth) birth = record.birth;
            if (!released && record.birth == birth && record.group == child && record.session == child) {
                if (send(gate[0], "G", 1, MSG_NOSIGNAL) != 1) break;
                released = true;
            }
        }
        unsigned long long current = monotonic_ms();
        if (!current || current - started >= 1200) break;
        struct timespec pause = { .tv_sec = 0, .tv_nsec = 5000000 };
        nanosleep(&pause, NULL);
    }
    close(gate[0]);
    if (!completed) {
        struct child_record record;
        if (birth && read_child(child, &record) && record.parent == owner_pid && record.birth == birth) {
            if (record.group == child && record.session == child) kill(-child, SIGKILL);
            else kill(child, SIGKILL);
        }
        /* Bound reaping too. A closed gate forbids an unreleased child from
         * starting a request; an owned released group has received SIGKILL. */
        for (unsigned attempt = 0; attempt < 20; attempt++) {
            int result;
            pid_t waited = waitpid(child, &result, WNOHANG);
            if (waited == child || (waited < 0 && errno != EINTR)) break;
            struct timespec pause = { .tv_sec = 0, .tv_nsec = 5000000 };
            nanosleep(&pause, NULL);
        }
    }
    return success;
}

static bool opted_in(const char *path) {
    char resolved[PATH_MAX];
    if (!path || !realpath(path, resolved) || strcmp(resolved, ENTRY_DOCUMENT_PATH) != 0)
        return false;
    int descriptor = open(ENTRY_DOCUMENT_PATH, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (descriptor < 0) return false;
    struct stat status;
    if (fstat(descriptor, &status) != 0 || !S_ISREG(status.st_mode)) {
        close(descriptor);
        return false;
    }
    FILE *file = fdopen(descriptor, "r");
    if (!file) { close(descriptor); return false; }
    bool declaration = false, fbink = true, hooks = false;
    unsigned bytes = 0;
    for (unsigned line = 0; line < 6; line++) {
        char prefix[64] = {0};
        size_t used = 0;
        int value;
        bool overflow = false;
        while ((value = fgetc(file)) != EOF && value != '\r' && value != '\n') {
            if (++bytes > 4096) { fclose(file); return false; }
            if (value == 0) overflow = true;
            if (used + 1 < sizeof(prefix)) prefix[used++] = (char)value;
            else overflow = true;
        }
        if (!overflow && strcmp(prefix, ENTRY_DECLARATION) == 0) declaration = true;
        if (!overflow && strcmp(prefix, "# DontUseFBInk") == 0) fbink = false;
        if (strncmp(prefix, "# UseHooks", sizeof("# UseHooks") - 1) == 0) hooks = true;
        if (value == '\r') {
            value = fgetc(file);
            if (value != '\n' && value != EOF) ungetc(value, file);
        }
        if (value == EOF) break;
    }
    fclose(file);
    return declaration && !fbink && !hooks;
}

__attribute__((constructor)) static void initialize(void) {
    clear_notification_handoff();
    *(void **)(&original_utimes) = dlsym(RTLD_NEXT, "utimes");
    owner_pid = getpid();
    const char *expected = getenv(ENTRY_IDENTITY_ENV);
    struct stat current;
    char identity[192] = {0};
    if (stat("/proc/self/exe", &current) == 0) entry_identity(&current, identity);
    bool qualified = expected && strcmp(identity, expected) == 0;
    const char *policy = getenv(ENTRY_POLICY_ENV);
    bool policy_valid = policy &&
        (strcmp(policy, "original") == 0 || strcmp(policy, "preserve") == 0 ||
         strcmp(policy, "update") == 0 || strcmp(policy, "quiet") == 0);
    preserve = policy_valid && strcmp(policy, "preserve") == 0;
    quiet_catalog = policy_valid && strcmp(policy, "quiet") == 0;
    update_catalog = quiet_catalog || (policy_valid && strcmp(policy, "update") == 0);
    const char *path = getenv(ENTRY_CLIENT_ENV), *client = getenv(ENTRY_CLIENT_IDENTITY_ENV);
    if (path && path[0] == '/' && strlen(path) < sizeof(client_path) &&
        client && strlen(client) < sizeof(client_identity)) {
        strcpy(client_path, path);
        strcpy(client_identity, client);
    }
    const char *fd = getenv(ENTRY_TRACE_ENV);
    if (fd) {
        char *end = NULL;
        long value = strtol(fd, &end, 10);
        if (*fd && end && !*end && value >= 3 && value <= INT_MAX) {
            trace_descriptor = (int)value;
            int flags = fcntl(trace_descriptor, F_GETFD);
            if (flags >= 0) fcntl(trace_descriptor, F_SETFD, flags | FD_CLOEXEC);
        }
    }
    const char *prior = getenv(ENTRY_PRIOR_ENV);
    const char *present = getenv(ENTRY_PRIOR_PRESENT_ENV);
    bool restored = false;
    if (prior && present) {
        if (strcmp(present, "1") == 0) restored = setenv("LD_PRELOAD", prior, 1) == 0;
        else if (strcmp(present, "0") == 0) restored = unsetenv("LD_PRELOAD") == 0;
    }
    unsetenv(ENTRY_IDENTITY_ENV);
    unsetenv(ENTRY_PRIOR_ENV);
    unsetenv(ENTRY_PRIOR_PRESENT_ENV);
    unsetenv(ENTRY_TRACE_ENV);
    unsetenv(ENTRY_POLICY_ENV);
    unsetenv(ENTRY_CLIENT_ENV);
    unsetenv(ENTRY_CLIENT_IDENTITY_ENV);
    enabled = qualified && policy_valid && restored && original_utimes;
    entry_trace(trace_descriptor, enabled ? "catalog_hooks_ready" : "catalog_hooks_disabled");
}

int utimes(const char *path, const struct timeval times[2]) {
    if (!original_utimes) { errno = ENOSYS; return -1; }
    if (enabled && getpid() == owner_pid) {
        clear_notification_handoff();
        unsigned count = atomic_fetch_add_explicit(&request_count, 1, memory_order_acq_rel);
        bool selected = count == 0 && times == NULL && opted_in(path);
        if (selected) {
            if (update_catalog) {
                bool updated = run_catalog_client();
                if (updated && quiet_catalog &&
                    (setenv(ENTRY_NOTIFY_HELPER_ENV,client_path,1) != 0 ||
                     setenv(ENTRY_NOTIFY_HASH_ENV,ENTRY_CLIENT_SHA256,1) != 0)) {
                    clear_notification_handoff();
                    updated = false;
                }
                entry_trace(trace_descriptor, updated ?
                    (quiet_catalog ? "catalog_last_access_updated_quiet" : "catalog_last_access_updated") :
                    "catalog_update_failed_fallback");
                if (updated) return 0;
            }
            entry_trace(trace_descriptor, preserve ? "catalog_timestamp_preserved" : "catalog_timestamp_original");
            if (preserve && access(path, W_OK) == 0) return 0;
        } else entry_trace(trace_descriptor, "unrelated_timestamp_original");
    }
    return original_utimes(path, times);
}
