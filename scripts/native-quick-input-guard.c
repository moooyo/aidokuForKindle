/* Test-only notification scheduling for the isolated Aidoku firmware guest.
 * It never creates or modifies a launcher token. After the real supervisor
 * publishes the next resume-request, it duplicates USR2 while the proven ELF
 * is still stopped. Only the supervisor supplies the normal CONT. A six-second
 * safety deadline and the caller's finally path must recover any stopped ELF.
 *
 * Usage: guard PID BIRTH PPID NONCE LOCK_DIR NEXT_EPOCH OUTPUT_JSON
 * Build on test-env with Zig's arm-linux-musleabihf static C target.
 */
#define _GNU_SOURCE
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <poll.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/inotify.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

static double now_seconds(void) {
    struct timespec value;
    clock_gettime(CLOCK_MONOTONIC, &value);
    return (double)value.tv_sec + (double)value.tv_nsec / 1000000000.0;
}

static bool read_text(const char *path, char *buffer, size_t capacity) {
    int fd = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) return false;
    ssize_t count = read(fd, buffer, capacity - 1);
    close(fd);
    if (count < 0 || (size_t)count >= capacity - 1) return false;
    buffer[count] = 0;
    while (count > 0 && isspace((unsigned char)buffer[count - 1])) buffer[--count] = 0;
    return true;
}

static bool process_owned(int pid, unsigned long long birth, int parent, char *state) {
    char path[80], buffer[4096];
    snprintf(path, sizeof(path), "/proc/%d/stat", pid);
    if (!read_text(path, buffer, sizeof(buffer))) return false;
    char *tail = strrchr(buffer, ')');
    if (!tail || tail[1] != ' ') return false;
    char *save = NULL;
    char *field = strtok_r(tail + 2, " ", &save);
    unsigned long long observed_birth = 0;
    int observed_parent = 0;
    for (int number = 3; field; ++number, field = strtok_r(NULL, " ", &save)) {
        if (number == 3) *state = field[0];
        if (number == 4) observed_parent = atoi(field);
        if (number == 22) { observed_birth = strtoull(field, NULL, 10); break; }
    }
    return observed_birth == birth && observed_parent == parent && *state != 'Z' && *state != 'X';
}

static bool record_matches(const char *directory, const char *name, const char *expected) {
    char path[PATH_MAX], buffer[512];
    if (snprintf(path, sizeof(path), "%s/%s", directory, name) >= (int)sizeof(path)) return false;
    return read_text(path, buffer, sizeof(buffer)) && strcmp(buffer, expected) == 0;
}

static bool valid_path(const char *path) {
    for (; *path; ++path) {
        if (!isalnum((unsigned char)*path) && *path != '/' && *path != '-' && *path != '_' && *path != '.') return false;
    }
    return true;
}

int main(int argc, char **argv) {
    if (argc != 8) {
        fprintf(stderr, "Usage: %s PID BIRTH PPID NONCE LOCK_DIR NEXT_EPOCH OUTPUT_JSON\n", argv[0]);
        return 2;
    }
    int pid = atoi(argv[1]), parent = atoi(argv[3]);
    unsigned long long birth = strtoull(argv[2], NULL, 10), epoch = strtoull(argv[6], NULL, 10);
    const char *nonce = argv[4], *directory = argv[5], *output = argv[7];
    if (pid <= 1 || parent <= 1 || !birth || !epoch || strlen(nonce) != 32 ||
        strncmp(directory, "/tmp/aidoku-sleep-", 18) != 0 ||
        strncmp(output, "/mnt/us/aidoku-sleep-tests/", 27) != 0 || !valid_path(directory) || !valid_path(output)) return 2;
    for (const char *p = nonce; *p; ++p) if (!isxdigit((unsigned char)*p)) return 2;
    char marker[80], text_pid[32], expected[160], state = '?';
    if (!read_text("/var/local/aidoku-emulator-id", marker, sizeof(marker)) || strcmp(marker, "aidoku-pw6-5201") != 0) return 2;
    snprintf(text_pid, sizeof(text_pid), "%d", pid);
    snprintf(expected, sizeof(expected), "%d %s %llu", pid, nonce, epoch);
    if (!record_matches(directory, "nonce", nonce) || !record_matches(directory, "application-pid", text_pid) || !process_owned(pid, birth, parent, &state)) return 2;
    int notify = inotify_init1(IN_NONBLOCK | IN_CLOEXEC);
    if (notify < 0 || inotify_add_watch(notify, directory, IN_MOVED_TO | IN_CLOSE_WRITE | IN_CREATE) < 0) return 2;
    char ready_path[PATH_MAX];
    snprintf(ready_path, sizeof(ready_path), "%s.ready", output);
    int ready = open(ready_path, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
    if (ready < 0) { close(notify); return 2; }
    dprintf(ready, "%d\n", (int)getpid());
    close(ready);
    double started = now_seconds(), armed_at = 0, delivered_at = 0;
    bool saw_sleep = false, saw_resume = false, duplicate = false, safety = false, acknowledged = false, late = false;
    int result = 1;
    const char *reason = "uninitialized";
    while (true) {
        double now = now_seconds();
        if (!process_owned(pid, birth, parent, &state) || !record_matches(directory, "nonce", nonce) || !record_matches(directory, "application-pid", text_pid)) { reason = "ownership-lost"; break; }
        if (!armed_at && state == 'T') armed_at = now;
        if (!armed_at && now - started > 60) { reason = "no-test-stop-within-setup-budget"; break; }
        saw_sleep = saw_sleep || record_matches(directory, "sleep-request", expected);
        if (saw_sleep && record_matches(directory, "resume-request", expected)) {
            saw_resume = true;
            if (!duplicate) {
                if (state != 'T') { late = true; reason = "supervisor-continued-before-test-notification"; break; }
                if (kill(pid, SIGUSR2) != 0) { reason = "duplicate-notification-failed"; break; }
                duplicate = true;
                delivered_at = now_seconds();
            }
        }
        if (duplicate && record_matches(directory, "resume-ready", expected)) { acknowledged = true; reason = "resume-acknowledged"; result = 0; break; }
        if (armed_at && now - armed_at >= 6) {
            if (process_owned(pid, birth, parent, &state)) { kill(pid, SIGCONT); safety = true; }
            reason = "bounded-stop-safety-timeout";
            break;
        }
        struct pollfd descriptor = { .fd = notify, .events = POLLIN };
        poll(&descriptor, 1, 10);
        if (descriptor.revents & POLLIN) {
            char events[4096];
            while (read(notify, events, sizeof(events)) > 0) {}
        }
    }
    close(notify);
    /* A rejected ordering sample must never leave the owned application frozen. */
    if (result != 0 && process_owned(pid, birth, parent, &state) && state == 'T') { kill(pid, SIGCONT); safety = true; }
    int fd = open(output, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
    if (fd < 0) return 2;
    dprintf(fd, "{\"pid\":%d,\"birth\":%llu,\"parent\":%d,\"nonce\":\"%s\",\"epoch\":%llu,\"armed_guest_monotonic\":%.9f,\"duplicate_guest_monotonic\":%.9f,\"saw_real_sleep_request\":%s,\"saw_real_resume_request\":%s,\"duplicate_usr2_sent_while_stopped\":%s,\"resume_acknowledged\":%s,\"late_ordering\":%s,\"safety_cont_sent\":%s,\"reason\":\"%s\"}\n",
        pid, birth, parent, nonce, epoch, armed_at, delivered_at, saw_sleep ? "true" : "false", saw_resume ? "true" : "false", duplicate ? "true" : "false", acknowledged ? "true" : "false", late ? "true" : "false", safety ? "true" : "false", reason);
    close(fd);
    return result;
}
