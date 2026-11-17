#define _GNU_SOURCE
#include <dlfcn.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>

static void stamp(FILE *file, const char *name) {
    struct timespec value;
    if (clock_gettime(CLOCK_BOOTTIME, &value) == 0) {
        fprintf(file, "%lld.%06ld %s\n", (long long)value.tv_sec, value.tv_nsec / 1000, name);
        fflush(file);
    }
}
int main(int argc, char **argv) {
    if (argc == 2 && strcmp(argv[1], "--self-test") == 0) {
        struct timespec value;
        if (clock_gettime(CLOCK_BOOTTIME, &value) != 0) return 70;
        puts("READY"); return 0;
    }
    if (argc != 2 || argv[1][0] != '/') return 64;
    const char *directory = getenv("AIDOKU_RESEARCH_OUT");
    if (!directory) return 64;
    char output[1024];
    int length = snprintf(output, sizeof(output), "%s/direct-dispatch.tsv", directory);
    if (length < 0 || (size_t)length >= sizeof(output)) return 64;
    int descriptor = open(output, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0600);
    if (descriptor < 0) return 73;
    FILE *log = fdopen(descriptor, "w");
    if (!log) { close(descriptor); return 73; }
    stamp(log, "diagnostic_entered");
    void *library = dlopen("/usr/lib/liblipc.so.1", RTLD_NOW | RTLD_LOCAL);
    if (!library) library = dlopen("/usr/lib/liblipc.so.1.0", RTLD_NOW | RTLD_LOCAL);
    if (!library) return 69;
    void *(*open_lipc)(void) = (void *(*)(void))dlsym(library, "LipcOpenNoName");
    int (*set_integer)(void *, const char *, const char *, int) =
        (int (*)(void *, const char *, const char *, int))dlsym(library, "LipcSetIntProperty");
    void (*close_lipc)(void *) = (void (*)(void *))dlsym(library, "LipcClose");
    if (!open_lipc || !set_integer || !close_lipc) return 69;
    void *handle = open_lipc();
    if (!handle) return 69;
    /* Preserve the reference Scriptlet's timestamp and scanner calls. This
     * diagnostic bypasses framework registration, privilege and dispatch work;
     * it is not an alternative production Library entry. */
    if (utimes(argv[1], NULL) != 0) return 74;
    stamp(log, "scanner_requested");
    int status = set_integer(handle, "com.lab126.scanner", "doFullScan", 1);
    stamp(log, "scanner_returned");
    if (status != 0) { close_lipc(handle); dlclose(library); fclose(log); return 74; }
    pid_t child = fork();
    if (child == 0) {
        fclose(log);
        execl("/bin/sh", "sh", "-l", argv[1], (char *)NULL);
        _exit(71);
    }
    stamp(log, "child_dispatched");
    close_lipc(handle); dlclose(library); fclose(log);
    return child < 0 ? 71 : 0;
}
