/* Research-only, single-document timestamp intervention. No scanner, wait,
 * process, redraw or privilege operation is intercepted. */
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
#include <sys/time.h>

static int (*original_utimes)(const char *, const struct timeval[2]);
static bool enabled;
static bool preserve;
static pid_t owner_pid;
static int trace_descriptor = -1;
static atomic_uint request_count;

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
    *(void **)(&original_utimes) = dlsym(RTLD_NEXT, "utimes");
    owner_pid = getpid();
    const char *expected = getenv(ENTRY_IDENTITY_ENV);
    struct stat current;
    char identity[192] = {0};
    if (stat("/proc/self/exe", &current) == 0) entry_identity(&current, identity);
    bool qualified = expected && strcmp(identity, expected) == 0;
    const char *policy = getenv(ENTRY_POLICY_ENV);
    bool policy_valid = policy &&
        (strcmp(policy, "original") == 0 || strcmp(policy, "preserve") == 0);
    preserve = policy_valid && strcmp(policy, "preserve") == 0;
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
    enabled = qualified && policy_valid && restored && original_utimes;
    entry_trace(trace_descriptor, enabled ? "catalog_hooks_ready" : "catalog_hooks_disabled");
}

int utimes(const char *path, const struct timeval times[2]) {
    if (!original_utimes) { errno = ENOSYS; return -1; }
    if (enabled && getpid() == owner_pid) {
        unsigned count = atomic_fetch_add_explicit(&request_count, 1, memory_order_acq_rel);
        bool selected = count == 0 && times == NULL && opted_in(path);
        if (selected) {
            entry_trace(trace_descriptor, preserve ? "catalog_timestamp_preserved" : "catalog_timestamp_original");
            if (preserve && access(path, W_OK) == 0) return 0;
        } else entry_trace(trace_descriptor, "unrelated_timestamp_original");
    }
    return original_utimes(path, times);
}
