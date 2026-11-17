/* Aidoku's short-lived, application-manager-aware Library entry.
 * Firmware LIPC libraries remain on the device. No frame or input I/O occurs
 * here: the existing owned launcher retains those responsibilities.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <poll.h>
#include <pthread.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/time.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#ifndef ENTRY_SERVICE_ID
#define ENTRY_SERVICE_ID "org.aidoku.kindle.entry"
#endif
#ifndef ENTRY_DOCUMENT_PATH
#define ENTRY_DOCUMENT_PATH "/mnt/us/documents/Aidoku.sh"
#endif
#ifndef ENTRY_SU_PATH
#define ENTRY_SU_PATH "/var/local/mkk/su"
#endif
#define APPMGR "com.lab126.appmgrd"
#define GENERIC_ENTRY "tech.hackerdude.shell_integration.launcher"
#define REQUEST_LIMIT 8192

typedef void Lipc;
typedef int (*PropertyCallback)(Lipc *, const char *, void *, void *);
struct Api {
    Lipc *(*open)(const char *, int *);
    Lipc *(*open_anonymous)(void);
    void (*close)(Lipc *);
    int (*register_string)(Lipc *, const char *, PropertyCallback, PropertyCallback, void *);
    int (*set_string)(Lipc *, const char *, const char *, const char *);
    int (*set_integer)(Lipc *, const char *, const char *, int);
};
struct Context {
    struct Api api;
    Lipc *lipc;
    pthread_mutex_t mutex;
    int wake[2];
    int trace;
    bool probe;
    bool requested;
    bool go_acked;
    bool stop_requested;
    bool unload_acked;
    bool cancelled;
    bool generic;
};
static volatile sig_atomic_t interrupted;

static uint64_t milliseconds(void) {
    struct timespec value;
    if (clock_gettime(CLOCK_BOOTTIME, &value) != 0) return 0;
    return (uint64_t)value.tv_sec * 1000 + (uint64_t)value.tv_nsec / 1000000;
}
static void trace_event(struct Context *context, const char *event) {
    if (context->trace < 0) return;
    struct timespec value;
    if (clock_gettime(CLOCK_BOOTTIME, &value) != 0) return;
    char line[160];
    int size = snprintf(line, sizeof(line), "%lld.%06ld %s\n", (long long)value.tv_sec,
                        value.tv_nsec / 1000, event);
    if (size > 0 && (size_t)size < sizeof(line)) (void)write(context->trace, line, (size_t)size);
}
static void wake_main(struct Context *context) {
    unsigned char value = 1;
    (void)write(context->wake[1], &value, 1);
}
static void stop_signal(int signal_number) { (void)signal_number; interrupted = 1; }
static int owned_pipe(int descriptors[2], bool nonblocking) {
    if (pipe(descriptors) != 0) return -1;
    for (unsigned index = 0; index < 2; index++) {
        int flags = fcntl(descriptors[index], F_GETFD);
        if (flags < 0 || fcntl(descriptors[index], F_SETFD, flags | FD_CLOEXEC) < 0) goto failed;
        if (nonblocking) {
            flags = fcntl(descriptors[index], F_GETFL);
            if (flags < 0 || fcntl(descriptors[index], F_SETFL, flags | O_NONBLOCK) < 0) goto failed;
        }
    }
    return 0;
failed:
    close(descriptors[0]); close(descriptors[1]);
    return -1;
}

static int digit(unsigned char value) {
    if (value >= '0' && value <= '9') return value - '0';
    if (value >= 'a' && value <= 'f') return value - 'a' + 10;
    if (value >= 'A' && value <= 'F') return value - 'A' + 10;
    return -1;
}
static const char *request_body(const char *request, char token[65]) {
    if (request == NULL || strnlen(request, REQUEST_LIMIT + 1) > REQUEST_LIMIT) return NULL;
    const char *separator = strchr(request, ':');
    if (!separator || separator == request || separator - request > 64) return NULL;
    for (const char *position = request; position != separator; position++) {
        unsigned char value = (unsigned char)*position;
        if (!((value >= '0' && value <= '9') || (value >= 'a' && value <= 'z') ||
              (value >= 'A' && value <= 'Z') || value == '-' || value == '_')) return NULL;
    }
    memcpy(token, request, (size_t)(separator - request));
    token[separator - request] = 0;
    return separator + 1;
}
static bool own_document(const char *body) {
    const char prefix[] = "app://" ENTRY_SERVICE_ID;
    size_t size = sizeof(prefix) - 1;
    if (strncmp(body, prefix, size) != 0) return false;
    const char *encoded = body + size;
    if (*encoded == 0 || strcmp(encoded, "/") == 0) encoded = ENTRY_DOCUMENT_PATH;
    if (*encoded != '/') return false;
    char decoded[PATH_MAX];
    size_t used = 0;
    for (size_t index = 0; encoded[index] && encoded[index] != '?'; index++) {
        int value = (unsigned char)encoded[index];
        if (value == '%') {
            if (!encoded[index + 1] || !encoded[index + 2]) return false;
            int high = digit((unsigned char)encoded[index + 1]);
            int low = digit((unsigned char)encoded[index + 2]);
            if (high < 0 || low < 0) return false;
            value = (high << 4) | low;
            index += 2;
        }
        if (value < 32 || value == 127 || used + 1 >= sizeof(decoded)) return false;
        decoded[used++] = (char)value;
    }
    decoded[used] = 0;
    char resolved[PATH_MAX];
    if (!realpath(decoded, resolved) || strcmp(resolved, ENTRY_DOCUMENT_PATH) != 0) return false;
    struct stat status;
    return lstat(ENTRY_DOCUMENT_PATH, &status) == 0 && S_ISREG(status.st_mode);
}

/* Only the two launch-affecting header declarations matter. Long icon/name
 * lines are skipped without allocating an unbounded line buffer. */
static bool requires_generic_handler(void) {
    int descriptor = open(ENTRY_DOCUMENT_PATH, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (descriptor < 0) return true;
    FILE *file = fdopen(descriptor, "r");
    if (!file) { close(descriptor); return true; }
    bool fbink = true, hooks = false;
    for (unsigned line = 0; line < 6; line++) {
        char prefix[32] = {0};
        size_t used = 0;
        int value;
        while ((value = fgetc(file)) != EOF && value != '\r' && value != '\n') {
            if (used + 1 < sizeof(prefix)) prefix[used++] = (char)value;
        }
        if (strncmp(prefix, "# DontUseFBInk", sizeof("# DontUseFBInk") - 1) == 0) fbink = false;
        if (strncmp(prefix, "# UseHooks", sizeof("# UseHooks") - 1) == 0) hooks = true;
        if (value == EOF) break;
    }
    fclose(file);
    return fbink || hooks;
}
static int reply(struct Context *context, const char *property, const char *request, int code) {
    char token[65];
    if (!request_body(request, token)) return 0x100;
    char name[32], value[80];
    int size = snprintf(name, sizeof(name), "%sresult", property);
    if (size < 0 || (size_t)size >= sizeof(name)) return 0x100;
    snprintf(value, sizeof(value), "%s:%d:", token, code);
    return context->api.set_string(context->lipc, APPMGR, name, value);
}
static int property_callback(Lipc *lipc, const char *property, void *raw, void *data) {
    (void)lipc;
    struct Context *context = data;
    const char *request = raw;
    char token[65];
    const char *body = request_body(request, token);
    if (!body) return 0x100;
    int code = 0;
    if (strcmp(property, "go") == 0) {
        trace_event(context, "go_received");
        bool valid = own_document(body);
        bool generic = valid && requires_generic_handler();
        pthread_mutex_lock(&context->mutex);
        if (!valid || context->cancelled || (context->stop_requested && !context->requested)) code = 1;
        else if (!context->requested) {
            context->generic = generic;
            context->requested = true;
        }
        pthread_mutex_unlock(&context->mutex);
        int result = reply(context, property, request, code);
        pthread_mutex_lock(&context->mutex);
        if (code == 0 && result == 0) context->go_acked = true;
        else if (!context->go_acked) context->cancelled = true;
        pthread_mutex_unlock(&context->mutex);
        trace_event(context, code == 0 && result == 0 ? "go_acknowledged" : "go_rejected");
        wake_main(context);
        return result;
    }
    if (strcmp(property, "pause") == 0 || strcmp(property, "unload") == 0) {
        pthread_mutex_lock(&context->mutex);
        if (!context->stop_requested) context->cancelled = true;
        pthread_mutex_unlock(&context->mutex);
    }
    int result = reply(context, property, request, 0);
    if (strcmp(property, "unload") == 0) {
        pthread_mutex_lock(&context->mutex);
        context->unload_acked = result == 0;
        if (result != 0) context->cancelled = true;
        pthread_mutex_unlock(&context->mutex);
        trace_event(context, result == 0 ? "unload_acknowledged" : "unload_failed");
        wake_main(context);
    }
    return result;
}

static void *load_api(struct Api *api) {
#ifdef ENTRY_TEST_LIBRARY
    void *library = dlopen(ENTRY_TEST_LIBRARY, RTLD_NOW | RTLD_LOCAL);
#else
    void *library = dlopen("/usr/lib/liblipc.so.1", RTLD_NOW | RTLD_LOCAL);
    if (!library) library = dlopen("/usr/lib/liblipc.so.1.0", RTLD_NOW | RTLD_LOCAL);
#endif
    if (!library) return NULL;
    *(void **)(&api->open) = dlsym(library, "LipcOpenEx");
    *(void **)(&api->open_anonymous) = dlsym(library, "LipcOpenNoName");
    *(void **)(&api->close) = dlsym(library, "LipcClose");
    *(void **)(&api->register_string) = dlsym(library, "LipcRegisterStringProperty");
    *(void **)(&api->set_string) = dlsym(library, "LipcSetStringProperty");
    *(void **)(&api->set_integer) = dlsym(library, "LipcSetIntProperty");
    if (!api->open || !api->open_anonymous || !api->close || !api->register_string ||
        !api->set_string || !api->set_integer) { dlclose(library); return NULL; }
    return library;
}
static void close_child_descriptors(int gate, unsigned limit) {
    if (gate != 3) { if (dup2(gate, 3) < 0) _exit(70); close(gate); }
#ifdef SYS_close_range
    if (syscall(SYS_close_range, 4u, UINT_MAX, 0u) == 0) return;
#endif
    for (unsigned descriptor = 4; descriptor < limit; descriptor++) close((int)descriptor);
}
static pid_t gated_child(const char *self, bool generic, int *gate_writer) {
    int gate[2];
    if (owned_pipe(gate, false) != 0) return -1;
    struct rlimit limits;
    if (getrlimit(RLIMIT_NOFILE, &limits) != 0 || limits.rlim_cur > 1048576) {
        close(gate[0]); close(gate[1]); return -1;
    }
    pid_t child = fork();
    if (child == 0) {
        close(gate[1]);
        if (setsid() < 0) _exit(70);
        close_child_descriptors(gate[0], (unsigned)limits.rlim_cur);
        struct pollfd descriptor = {.fd = 3, .events = POLLIN | POLLHUP};
        int ready;
        do { ready = poll(&descriptor, 1, 4500); } while (ready < 0 && errno == EINTR);
        unsigned char permission = 0, trailing = 0;
        if (ready <= 0 || read(3, &permission, 1) != 1 || permission != 'L' ||
            read(3, &trailing, 1) != 0) _exit(75);
        close(3);
        int null = open("/dev/null", O_RDWR);
        if (null < 0) _exit(70);
        for (int index = 0; index <= 2; index++) if (dup2(null, index) < 0) _exit(70);
        if (null > 2) close(null);
        if (generic) execl(self, self, "--forward-generic", (char *)NULL);
        else execl("/bin/sh", "sh", "-l", ENTRY_DOCUMENT_PATH, (char *)NULL);
        _exit(71);
    }
    close(gate[0]);
    if (child < 0) { close(gate[1]); return -1; }
    *gate_writer = gate[1];
    return child;
}

static char *quoted(const char *value) {
    size_t length = strlen(value);
    if (length > PATH_MAX) return NULL;
    char *result = malloc(length * 4 + 3);
    if (!result) return NULL;
    char *out = result; *out++ = '\'';
    for (const char *in = value; *in; in++) {
        if (*in == '\'') { memcpy(out, "'\\''", 4); out += 4; }
        else *out++ = *in;
    }
    *out++ = '\''; *out = 0;
    return result;
}
static int become_root(const char *self, const char *trace, bool probe) {
    char *executable = quoted(self), *directory = trace ? quoted(trace) : NULL;
    if (!executable || (trace && !directory)) { free(executable); free(directory); return 70; }
    char command[PATH_MAX * 8 + 128];
    int length = snprintf(command, sizeof(command), "exec %s --root%s%s%s", executable,
                          probe ? " --probe" : "", trace ? " --trace-dir " : "", directory ? directory : "");
    free(executable); free(directory);
    if (length < 0 || (size_t)length >= sizeof(command)) return 64;
    execl(ENTRY_SU_PATH, "su", "-c", command, (char *)NULL);
    return 71;
}

int main(int argc, char **argv) {
    if (argc == 2 && strcmp(argv[1], "--version") == 0) {
        puts("aidoku-native-entry 1");
        return 0;
    }
    bool root = false, probe = false, forward = false;
    const char *trace_directory = NULL;
    for (int index = 1; index < argc; index++) {
        if (strcmp(argv[index], "--root") == 0) root = true;
        else if (strcmp(argv[index], "--probe") == 0) probe = true;
        else if (strcmp(argv[index], "--forward-generic") == 0) forward = true;
        else if (strcmp(argv[index], "--trace-dir") == 0 && index + 1 < argc) trace_directory = argv[++index];
        else return 64;
    }
    char self[PATH_MAX];
    ssize_t length = readlink("/proc/self/exe", self, sizeof(self) - 1);
    if (length <= 0 || (size_t)length >= sizeof(self) - 1) return 70;
    self[length] = 0;
    if (geteuid() != 0) return root || forward ? 77 : become_root(self, trace_directory, probe);
    umask(077);
    signal(SIGPIPE, SIG_IGN);
    signal(SIGTERM, stop_signal); signal(SIGINT, stop_signal); signal(SIGHUP, stop_signal);
    alarm(8);
    struct Context context = {.mutex = PTHREAD_MUTEX_INITIALIZER, .trace = -1, .probe = probe};
    if (trace_directory) {
        char path[PATH_MAX];
        int size = snprintf(path, sizeof(path), "%s/native-entry.%ld.tsv", trace_directory, (long)getpid());
        if (size < 0 || (size_t)size >= sizeof(path)) return 64;
        context.trace = open(path, O_WRONLY | O_APPEND | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0600);
        if (context.trace < 0) return 73;
    }
    trace_event(&context, "process_entry");
    void *library = load_api(&context.api);
    if (!library) return 69;
    if (forward) {
        context.lipc = context.api.open_anonymous();
        if (!context.lipc) return 69;
        char uri[PATH_MAX * 3 + 128];
        size_t used = (size_t)snprintf(uri, sizeof(uri), "app://" GENERIC_ENTRY "/");
        static const char hex[] = "0123456789ABCDEF";
        for (const unsigned char *in = (const unsigned char *)ENTRY_DOCUMENT_PATH; *in; in++) {
            unsigned char byte = *in;
            if ((byte >= 'a' && byte <= 'z') || (byte >= 'A' && byte <= 'Z') ||
                (byte >= '0' && byte <= '9') || strchr("/._-", byte)) uri[used++] = (char)byte;
            else { uri[used++] = '%'; uri[used++] = hex[byte >> 4]; uri[used++] = hex[byte & 15]; }
        }
        uri[used] = 0;
        int result = context.api.set_string(context.lipc, APPMGR, "start", uri);
        context.api.close(context.lipc); dlclose(library);
        return result == 0 ? 0 : 74;
    }
    if (owned_pipe(context.wake, true) != 0) return 70;
    int status = 0;
    context.lipc = context.api.open(ENTRY_SERVICE_ID, &status);
    if (!context.lipc || status != 0) return 69;
    const char *properties[] = {"load", "pause", "unload", "go"};
    for (size_t index = 0; index < sizeof(properties) / sizeof(properties[0]); index++) {
        if (context.api.register_string(context.lipc, properties[index], NULL, property_callback, &context) != 0) {
            context.api.close(context.lipc); return 69;
        }
    }
    trace_event(&context, "service_ready");
    if (context.api.set_string(context.lipc, APPMGR, "runresult", "0:" ENTRY_SERVICE_ID) != 0) {
        context.api.close(context.lipc); return 74;
    }
    uint64_t deadline = milliseconds() + 3500;
    pid_t child = -1;
    int gate_writer = -1;
    bool commit = false;
    bool probe_complete = false;
    for (;;) {
        pthread_mutex_lock(&context.mutex);
        bool requested = context.go_acked;
        bool stopped = context.stop_requested;
        bool cancelled = context.cancelled;
        bool unloaded = context.unload_acked;
        bool generic = context.generic;
        pthread_mutex_unlock(&context.mutex);
        if (interrupted || milliseconds() >= deadline) break;
        if (unloaded) {
            commit = requested && !cancelled && !probe;
            probe_complete = requested && !cancelled && probe;
            break;
        }
        if ((requested || cancelled) && !stopped) {
            bool prepared = true;
            if (requested && !cancelled && !probe) {
                if (!generic) {
                    prepared = utimes(ENTRY_DOCUMENT_PATH, NULL) == 0;
                    if (prepared) {
                        trace_event(&context, "scanner_requested");
                        prepared = context.api.set_integer(context.lipc, "com.lab126.scanner", "doFullScan", 1) == 0;
                        trace_event(&context, prepared ? "scanner_returned" : "scanner_failed");
                    }
                }
                if (prepared) { child = gated_child(self, generic, &gate_writer); prepared = child > 0; }
            }
            pthread_mutex_lock(&context.mutex);
            context.stop_requested = true;
            if (!prepared) context.cancelled = true;
            pthread_mutex_unlock(&context.mutex);
            trace_event(&context, "stop_requested");
            if (context.api.set_string(context.lipc, APPMGR, "stop", ENTRY_SERVICE_ID) != 0) break;
        }
        struct pollfd descriptor = {.fd = context.wake[0], .events = POLLIN};
        int result = poll(&descriptor, 1, 20);
        if (result < 0 && errno != EINTR) break;
        unsigned char bytes[32];
        while (read(context.wake[0], bytes, sizeof(bytes)) > 0) {}
    }
    context.api.close(context.lipc);
    trace_event(&context, commit ? "handoff_committed" : "service_closed");
    if (gate_writer >= 0) {
        if (commit && !interrupted) { unsigned char permission = 'L'; if (write(gate_writer, &permission, 1) != 1) commit = false; }
        else commit = false;
        close(gate_writer);
    }
    if (child > 0 && !commit) { kill(child, SIGKILL); while (waitpid(child, NULL, 0) < 0 && errno == EINTR) {} }
    close(context.wake[0]); close(context.wake[1]);
    if (context.trace >= 0) close(context.trace);
    dlclose(library);
    return commit || probe_complete ? 0 : 75;
}
