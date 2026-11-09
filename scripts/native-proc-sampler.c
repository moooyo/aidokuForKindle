#define _POSIX_C_SOURCE 200809L
#define _XOPEN_SOURCE 700
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

/* Development-only Linux process sampler. It never opens a Kindle display. */
static volatile sig_atomic_t stopping;

static void stop_requested(int signal_number) {
    (void)signal_number;
    stopping = 1;
}

static double monotonic_seconds(void) {
    struct timespec value;
    if (clock_gettime(CLOCK_MONOTONIC, &value) != 0) return 0;
    return (double)value.tv_sec + (double)value.tv_nsec / 1000000000.0;
}

static void pause_milliseconds(long milliseconds) {
    struct timespec delay = {milliseconds / 1000, (milliseconds % 1000) * 1000000};
    while (nanosleep(&delay, &delay) != 0 && errno == EINTR && !stopping) {}
}

static int read_text(const char *path, char *buffer, size_t capacity) {
    FILE *file = fopen(path, "r");
    if (!file) return -1;
    size_t count = fread(buffer, 1, capacity - 1, file);
    buffer[count] = 0;
    fclose(file);
    return 0;
}

static int executable_matches(pid_t pid, const char *expected) {
    char path[80], actual[PATH_MAX];
    snprintf(path, sizeof(path), "/proc/%ld/exe", (long)pid);
    ssize_t count = readlink(path, actual, sizeof(actual) - 1);
    if (count < 0) return 0;
    actual[count] = 0;
    return strcmp(actual, expected) == 0;
}

static pid_t scenario_child(pid_t wrapper, const char *expected) {
    char path[100], children[4096];
    snprintf(path, sizeof(path), "/proc/%ld/task/%ld/children", (long)wrapper, (long)wrapper);
    if (read_text(path, children, sizeof(children)) != 0) return 0;
    char *cursor = children;
    while (*cursor) {
        char *end;
        long child = strtol(cursor, &end, 10);
        if (end == cursor) break;
        if (child > 0 && executable_matches((pid_t)child, expected)) return (pid_t)child;
        cursor = end;
        while (*cursor == ' ' || *cursor == '\n') cursor++;
    }
    return 0;
}

static unsigned long field_kib(const char *status, const char *field) {
    const char *cursor = strstr(status, field);
    return cursor ? strtoul(cursor + strlen(field), NULL, 10) : 0;
}

static void cpu_ticks(pid_t pid, unsigned long long *user, unsigned long long *system) {
    char path[80], buffer[8192];
    snprintf(path, sizeof(path), "/proc/%ld/stat", (long)pid);
    if (read_text(path, buffer, sizeof(buffer)) != 0) return;
    char *cursor = strrchr(buffer, ')');
    if (!cursor || !cursor[1]) return;
    cursor += 2;
    for (int field = 3; field <= 15; field++) {
        char *end = strchr(cursor, ' ');
        if (!end) break;
        *end = 0;
        if (field == 14) *user = strtoull(cursor, NULL, 10);
        if (field == 15) *system = strtoull(cursor, NULL, 10);
        cursor = end + 1;
    }
}

static pid_t resource_pid(const char *path) {
    char buffer[32768];
    if (read_text(path, buffer, sizeof(buffer)) != 0) return 0;
    char *cursor = strstr(buffer, "\"pid\"");
    if (!cursor || !(cursor = strchr(cursor, ':'))) return 0;
    return (pid_t)strtol(cursor + 1, NULL, 10);
}

static int self_test(int argc, char **argv) {
    if (argc > 1 && strcmp(argv[1], "--self-test-child") == 0) {
        volatile unsigned char *memory = malloc(16 * 1024 * 1024);
        if (!memory) return 125;
        for (size_t index = 0; index < 16 * 1024 * 1024; index += 4096) memory[index] = (unsigned char)index;
        double started = monotonic_seconds();
        volatile uint64_t accumulator = 0;
        while (monotonic_seconds() - started < 0.25) accumulator++;
        (void)accumulator;
        pause_milliseconds(600);
        free((void *)memory);
        return 0;
    }
    if (argc > 2 && strcmp(argv[1], "--self-test-wrapper") == 0) {
        pid_t child = fork();
        if (child < 0) return 125;
        if (child == 0) {
            execl(argv[0], argv[0], "--self-test-child", (char *)NULL);
            _exit(127);
        }
        int status = 0;
        while (waitpid(child, &status, 0) < 0 && errno == EINTR) {}
        FILE *report = fopen(argv[2], "w");
        if (!report) return 125;
        fprintf(report, "{\"pid\":%ld,\"exit_code\":%d}\n", (long)child, WIFEXITED(status) ? WEXITSTATUS(status) : 128 + WTERMSIG(status));
        fclose(report);
        return WIFEXITED(status) ? WEXITSTATUS(status) : 125;
    }
    return -1;
}

int main(int argc, char **argv) {
    int test = self_test(argc, argv);
    if (test >= 0) return test;
    if (argc < 6) {
        fprintf(stderr, "usage: sampler samples.csv summary.json resource.json expected-elf command [args...]\n");
        return 125;
    }
    char expected[PATH_MAX];
    if (!realpath(argv[4], expected)) { perror("resolve expected executable"); return 125; }
    FILE *samples = fopen(argv[1], "w");
    if (!samples) { perror("open samples"); return 125; }
    fcntl(fileno(samples), F_SETFD, FD_CLOEXEC);
    fprintf(samples, "elapsed_ms,pid,ppid,vm_hwm_kib,vm_rss_kib,user_ticks,system_ticks\n");
    pid_t wrapper = fork();
    if (wrapper < 0) { fclose(samples); return 125; }
    if (wrapper == 0) {
        execv(argv[5], &argv[5]);
        perror("exec benchmark wrapper");
        _exit(127);
    }
    signal(SIGTERM, stop_requested);
    signal(SIGINT, stop_requested);
    signal(SIGHUP, stop_requested);
    double started = monotonic_seconds(), stop_at = 0, last_sample = 0;
    pid_t sampled = 0;
    unsigned long count = 0, max_hwm = 0, max_rss = 0;
    unsigned long long user_ticks = 0, system_ticks = 0;
    int status = 0, identity_ok = 1;
    for (;;) {
        if (!sampled) sampled = scenario_child(wrapper, expected);
        if (sampled) {
            char path[80], buffer[8192];
            snprintf(path, sizeof(path), "/proc/%ld/status", (long)sampled);
            if (read_text(path, buffer, sizeof(buffer)) == 0) {
                unsigned long hwm = field_kib(buffer, "VmHWM:"), rss = field_kib(buffer, "VmRSS:");
                unsigned long parent = field_kib(buffer, "PPid:");
                /* A just-exited zombie has no mm or exe; it is not a new identity. */
                if (hwm || rss) {
                    if (parent != (unsigned long)wrapper || !executable_matches(sampled, expected)) identity_ok = 0;
                    cpu_ticks(sampled, &user_ticks, &system_ticks);
                    if (hwm > max_hwm) max_hwm = hwm;
                    if (rss > max_rss) max_rss = rss;
                    last_sample = monotonic_seconds() - started;
                    fprintf(samples, "%.3f,%ld,%lu,%lu,%lu,%llu,%llu\n", last_sample * 1000.0, (long)sampled, parent, hwm, rss, user_ticks, system_ticks);
                    count++;
                }
            }
        }
        pid_t waited = waitpid(wrapper, &status, WNOHANG);
        if (waited == wrapper) break;
        if (waited < 0 && errno != EINTR) { perror("wait benchmark wrapper"); status = 125 << 8; break; }
        if (stopping) {
            if (!stop_at) {
                stop_at = monotonic_seconds();
                kill(wrapper, SIGTERM);
                if (sampled) kill(sampled, SIGTERM);
            } else if (monotonic_seconds() - stop_at > 2.0) {
                kill(wrapper, SIGKILL);
                if (sampled) kill(sampled, SIGKILL);
            }
        }
        pause_milliseconds(20);
    }
    fclose(samples);
    int code = WIFEXITED(status) ? WEXITSTATUS(status) : 128 + WTERMSIG(status);
    pid_t reported = resource_pid(argv[3]);
    int valid = code == 0 && sampled > 0 && sampled == reported && identity_ok && count > 0 && max_hwm > 0;
    FILE *summary = fopen(argv[2], "w");
    if (!summary) { perror("open sampling summary"); return 125; }
    long hz = sysconf(_SC_CLK_TCK);
    fprintf(summary,
        "{\n\"method\":\"20ms sampling of the actual scenario child's /proc status\",\n"
        "\"sampling_interval_ms\":20,\n\"wrapper_pid\":%ld,\n\"sampled_pid\":%ld,\n\"resource_report_pid\":%ld,\n"
        "\"pid_matches_resource_report\":%s,\n\"executable_and_parent_matched\":%s,\n\"samples\":%lu,\n"
        "\"observed_vm_hwm_kib\":%lu,\n\"observed_vm_rss_kib\":%lu,\n\"last_sample_offset_seconds\":%.6f,\n"
        "\"sampler_wall_seconds\":%.6f,\n\"wrapper_exit_code\":%d,\n"
        "\"proc_cpu_ticks_user\":%llu,\n\"proc_cpu_ticks_system\":%llu,\n\"clock_ticks_per_second\":%ld,\n"
        "\"cpu_ticks_available\":%s,\n\"passed\":%s,\n"
        "\"limit\":\"Observed high-water mark, not a claim that wait4 supplied lifetime peak RSS; a final sub-20ms change can be missed.\"\n}\n",
        (long)wrapper, (long)sampled, (long)reported, sampled > 0 && sampled == reported ? "true" : "false", identity_ok ? "true" : "false",
        count, max_hwm, max_rss, last_sample, monotonic_seconds() - started, code,
        user_ticks, system_ticks, hz, user_ticks + system_ticks > 0 && hz > 0 ? "true" : "false", valid ? "true" : "false");
    fclose(summary);
    return valid ? 0 : (code ? code : 125);
}
