/* Fixed LIPC sessions and lighting operations for the firmware's native C ABI.
 *
 * The static musl application cannot load the firmware's glibc libraries.
 * This small, separately linked process retains one LIPC handle per bounded
 * transaction. The parent owns deadlines and validates the returned values.
 * No firmware library or third-party binding implementation is bundled.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <unistd.h>

#define MAX_VALUE 128
#define MAX_REQUESTS 32

typedef void LipcHandle;
struct lipc_api {
    LipcHandle *(*open)(void);
    void (*close)(LipcHandle *);
    int (*get_string)(LipcHandle *, const char *, const char *, char **);
    int (*get_int)(LipcHandle *, const char *, const char *, int *);
    int (*set_int)(LipcHandle *, const char *, const char *, int);
    void (*free_string)(char *);
};

struct property {
    const char *service;
    const char *name;
    int integer;
};

/* Opcodes are shared with native_query.rs. Requests never contain names. */
static const struct property properties[] = {
    {"com.lab126.powerd", "state", 0},
    {"com.lab126.winmgr", "epdcMode", 0},
    {"com.lab126.winmgr", "accelerometer", 0},
    {"com.lab126.wifid", "cmState", 0},
    {"com.lab126.powerd", "battLevel", 1},
    {"com.lab126.powerd", "isCharging", 1},
    {"com.lab126.powerd", "flIntensity", 1},
    {"com.lab126.powerd", "currentAmberLevel", 1},
    {"com.lab126.blanket", "load", 0},
};

static int write_all(const void *buffer, size_t length) {
    const unsigned char *position = buffer;
    while (length != 0) {
        ssize_t written = write(STDOUT_FILENO, position, length);
        if (written < 0 && errno == EINTR) continue;
        if (written <= 0) return -1;
        position += written;
        length -= (size_t)written;
    }
    return 0;
}

static int reply(unsigned char opcode, unsigned char status,
                 const void *value, size_t length) {
    if (length > MAX_VALUE) return -1;
    unsigned char frame[4 + MAX_VALUE] = {
        opcode, status, (unsigned char)(length >> 8), (unsigned char)length
    };
    if (length != 0) memcpy(frame + 4, value, length);
    return write_all(frame, 4 + length);
}

static void *load_api(struct lipc_api *api) {
#ifdef AIDOKU_QUERY_TEST_LIBRARY
    void *library = dlopen(AIDOKU_QUERY_TEST_LIBRARY, RTLD_NOW | RTLD_LOCAL);
#else
    void *library = dlopen("/usr/lib/liblipc.so.1", RTLD_NOW | RTLD_LOCAL);
    if (library == NULL)
        library = dlopen("/usr/lib/liblipc.so.1.0", RTLD_NOW | RTLD_LOCAL);
#endif
    if (library == NULL) return NULL;
    api->open = (LipcHandle *(*)(void))dlsym(library, "LipcOpenNoName");
    api->close = (void (*)(LipcHandle *))dlsym(library, "LipcClose");
    api->get_string = (int (*)(LipcHandle *, const char *, const char *, char **))
        dlsym(library, "LipcGetStringProperty");
    api->get_int = (int (*)(LipcHandle *, const char *, const char *, int *))
        dlsym(library, "LipcGetIntProperty");
    api->set_int = (int (*)(LipcHandle *, const char *, const char *, int))
        dlsym(library, "LipcSetIntProperty");
    api->free_string = (void (*)(char *))dlsym(library, "LipcFreeString");
    if (!api->open || !api->close || !api->get_string || !api->get_int || !api->free_string) {
        dlclose(library);
        return NULL;
    }
    return library;
}

static int lighting_value(const char *text, int *value) {
    if (strcmp(text, "-") == 0) { *value = -1; return 1; }
    size_t length = strlen(text);
    if (length == 0 || length > 2) return 0;
    int result = 0;
    for (size_t index = 0; index < length; index++) {
        if (text[index] < '0' || text[index] > '9') return 0;
        result = result * 10 + text[index] - '0';
    }
    if (result > 24) return 0;
    *value = result;
    return 1;
}

int main(int argc, char **argv) {
    int startup = argc == 2 && strcmp(argv[1], "--startup-session") == 0;
    int lighting = argc == 4 && strcmp(argv[1], "--apply-lighting") == 0;
    int brightness = -1, warmth = -1;
    if (!startup && !lighting && (argc != 2 || strcmp(argv[1], "--session") != 0)) return 64;
    if (lighting && (!lighting_value(argv[2], &brightness) || !lighting_value(argv[3], &warmth))) return 64;
    pid_t parent = getppid();
    if (parent <= 1 || prctl(PR_SET_PDEATHSIG, SIGKILL) != 0 || getppid() != parent)
        return 70;
    signal(SIGPIPE, SIG_IGN);
    /* A lost parent or abandoned session cannot leave a permanent helper. */
    alarm(5);
    struct lipc_api api = {0};
    void *library = load_api(&api);
    if (library == NULL) return 69;
    if (lighting && api.set_int == NULL) { dlclose(library); return 69; }
    LipcHandle *handle = api.open();
    if (handle == NULL) {
        dlclose(library);
        return 69;
    }
    if (lighting) {
        int status = 0;
        if (brightness >= 0)
            status = api.set_int(handle, "com.lab126.powerd", "flIntensity", brightness);
        if (status == 0 && warmth >= 0)
            status = api.set_int(handle, "com.lab126.powerd", "currentAmberLevel", warmth);
        if (status == 0) status = write_all("APPLIED\n", 8);
        api.close(handle);
        dlclose(library);
        return status == 0 ? 0 : 74;
    }
    int result = 0;
    /* The original command keeps AQ1 and its eight read-only opcodes. Only
     * the explicit extended command advertises the optional blanket read. */
    if (write_all(startup ? "AQ2\n" : "AQ1\n", 4) != 0) result = 74;
    unsigned int requests = 0;
    while (result == 0) {
        unsigned char opcode;
        ssize_t count = read(STDIN_FILENO, &opcode, 1);
        if (count < 0 && errno == EINTR) continue;
        if (count == 0) break;
        if (count != 1 || opcode == 0 || opcode > (startup ? 9 : 8)
            || requests++ >= MAX_REQUESTS) {
            result = 64;
            break;
        }
        const struct property *property = &properties[opcode - 1];
        if (property->integer) {
            int value = 0;
            int status = api.get_int(handle, property->service, property->name, &value);
            char text[32];
            int length = snprintf(text, sizeof(text), "%d", value);
            if (status != 0) result = reply(opcode, 1, NULL, 0);
            else if (length < 0 || (size_t)length >= sizeof(text))
                result = reply(opcode, 2, NULL, 0);
            else result = reply(opcode, 0, text, (size_t)length);
        } else {
            char *value = NULL;
            int status = api.get_string(handle, property->service, property->name, &value);
            if (status != 0) result = reply(opcode, 1, NULL, 0);
            else if (value == NULL) result = reply(opcode, 2, NULL, 0);
            else {
                size_t length = strnlen(value, MAX_VALUE + 1);
                if (opcode == 9) {
                    /* Preserve the legacy CLI's trailing newline and total
                     * byte budget for the strict module parser. */
                    char text[MAX_VALUE];
                    if (length >= MAX_VALUE) result = reply(opcode, 2, NULL, 0);
                    else {
                        memcpy(text, value, length);
                        text[length] = '\n';
                        result = reply(opcode, 0, text, length + 1);
                    }
                } else {
                    result = length > MAX_VALUE ? reply(opcode, 2, NULL, 0)
                                                : reply(opcode, 0, value, length);
                }
                api.free_string(value);
            }
        }
    }
    api.close(handle);
    dlclose(library);
    return result == 0 ? 0 : 74;
}
