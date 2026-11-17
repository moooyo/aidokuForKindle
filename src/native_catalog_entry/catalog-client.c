/* Research client for one fixed catalog entry. Credentials and catalog IDs
 * never leave this process through diagnostics. Firmware libraries are not
 * redistributed. All writes use the catalog service and its normal notices. */
#define _GNU_SOURCE
#include "common.h"
#include <arpa/inet.h>
#include <ctype.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <poll.h>
#include <signal.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/socket.h>

#ifndef CATALOG_PORT
#define CATALOG_PORT 9101
#endif
#ifndef CATALOG_UTILS_LIBRARY
#define CATALOG_UTILS_LIBRARY "liblab126utils.so.1.0"
#endif
#ifndef CATALOG_JSON_LIBRARY
#define CATALOG_JSON_LIBRARY "libcjson.so.1"
#endif
#ifndef CATALOG_BUDGET_MS
#define CATALOG_BUDGET_MS 800
#endif
#define RESPONSE_LIMIT 32768

typedef struct json_value json_value;
struct json_api {
    json_value *(*parse)(const char *, const char **, int);
    void (*delete)(json_value *);
    json_value *(*get)(const json_value *, const char *);
    int (*array_size)(const json_value *);
    json_value *(*array_item)(const json_value *, int);
    char *(*string)(const json_value *);
    double (*number)(const json_value *);
    int (*is_object)(const json_value *);
    int (*is_array)(const json_value *);
    int (*is_true)(const json_value *);
};
static struct json_api json;
static const char *token;
static long long deadline;
static const char *failure = "initialization";

static long long now_ms(void) {
    struct timespec value;
    if (clock_gettime(CLOCK_MONOTONIC, &value) != 0) return 0;
    return (long long)value.tv_sec * 1000 + value.tv_nsec / 1000000;
}

static bool wait_for(int descriptor, short events) {
    for (;;) {
        long long remaining = deadline - now_ms();
        if (remaining <= 0) { failure = "deadline"; return false; }
        struct pollfd fd = { .fd = descriptor, .events = events };
        int result = poll(&fd, 1, (int)remaining);
        if (result < 0 && errno == EINTR) continue;
        return result > 0 && (fd.revents & (events | POLLHUP)) != 0;
    }
}

static bool load_api(void) {
    void *library = dlopen(CATALOG_JSON_LIBRARY, RTLD_NOW | RTLD_LOCAL);
    if (!library) { failure = "json-library"; return false; }
#define JSON_SYMBOL(field, name) do { \
    *(void **)(&json.field) = dlsym(library, name); \
    if (!json.field) { failure = "json-api"; return false; } \
} while (0)
    JSON_SYMBOL(parse, "cJSON_ParseWithOpts");
    JSON_SYMBOL(delete, "cJSON_Delete");
    JSON_SYMBOL(get, "cJSON_GetObjectItemCaseSensitive");
    JSON_SYMBOL(array_size, "cJSON_GetArraySize");
    JSON_SYMBOL(array_item, "cJSON_GetArrayItem");
    JSON_SYMBOL(string, "cJSON_GetStringValue");
    JSON_SYMBOL(number, "cJSON_GetNumberValue");
    JSON_SYMBOL(is_object, "cJSON_IsObject");
    JSON_SYMBOL(is_array, "cJSON_IsArray");
    JSON_SYMBOL(is_true, "cJSON_IsTrue");
#undef JSON_SYMBOL
    library = dlopen(CATALOG_UTILS_LIBRARY, RTLD_NOW | RTLD_LOCAL);
    if (!library) { failure = "token-library"; return false; }
    char *(*read_token)(void) = NULL;
    *(void **)(&read_token) = dlsym(library, "get_lab126_session_token");
    if (!read_token) { failure = "token-api"; return false; }
    token = read_token();
    size_t size = token ? strnlen(token, 513) : 0;
    if (!size || size > 512) { failure = "token-format"; return false; }
    for (size_t i = 0; i < size; i++) {
        if ((unsigned char)token[i] < 33 || (unsigned char)token[i] > 126) {
            failure = "token-format";
            return false;
        }
    }
    return true;
}

static bool send_all(int descriptor, const char *data, size_t size) {
    while (size) {
        ssize_t sent = send(descriptor, data, size, MSG_NOSIGNAL);
        if (sent > 0) { data += sent; size -= (size_t)sent; continue; }
        if (sent < 0 && errno == EINTR) continue;
        if (sent < 0 && (errno == EAGAIN || errno == EWOULDBLOCK) && wait_for(descriptor, POLLOUT)) continue;
        return false;
    }
    return true;
}

static json_value *post(const char *path, const char *body) {
    failure = "http-transport";
    int descriptor = socket(AF_INET, SOCK_STREAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
    if (descriptor < 0) return NULL;
    struct sockaddr_in address = { .sin_family = AF_INET, .sin_port = htons(CATALOG_PORT) };
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    int connected = connect(descriptor, (struct sockaddr *)&address, sizeof(address));
    if (connected < 0) {
        if (errno != EINPROGRESS || !wait_for(descriptor, POLLOUT)) goto failed;
        int error = 0;
        socklen_t size = sizeof(error);
        if (getsockopt(descriptor, SOL_SOCKET, SO_ERROR, &error, &size) != 0 || error) goto failed;
    }
    char header[1024];
    int header_size = snprintf(header, sizeof(header),
        "POST %s HTTP/1.0\r\nHost: localhost:%d\r\nAuthToken: %s\r\n"
        "Content-Type: application/json\r\nConnection: close\r\nContent-Length: %zu\r\n\r\n",
        path, CATALOG_PORT, token, strlen(body));
    if (header_size <= 0 || (size_t)header_size >= sizeof(header) ||
        !send_all(descriptor, header, (size_t)header_size) ||
        !send_all(descriptor, body, strlen(body))) goto failed;
    memset(header, 0, sizeof(header));

    char response[RESPONSE_LIMIT + 1];
    size_t used = 0;
    while (used < RESPONSE_LIMIT) {
        ssize_t received = recv(descriptor, response + used, RESPONSE_LIMIT - used, 0);
        if (received > 0) { used += (size_t)received; continue; }
        if (!received) break;
        if (errno == EINTR) continue;
        if ((errno == EAGAIN || errno == EWOULDBLOCK) && wait_for(descriptor, POLLIN)) continue;
        goto failed;
    }
    close(descriptor);
    descriptor = -1;
    if (used == RESPONSE_LIMIT) { failure = "response-limit"; return NULL; }
    response[used] = 0;
    char *boundary = strstr(response, "\r\n\r\n");
    if (!boundary || boundary - response > 8192) { failure = "http-header"; return NULL; }
    int major = 0, minor = 0, status = 0;
    if (sscanf(response, "HTTP/%d.%d %d", &major, &minor, &status) != 3 ||
        major != 1 || minor > 1 || status != 200) { failure = "http-status"; return NULL; }
    char *payload = boundary + 4;
    size_t payload_size = used - (size_t)(payload - response);
    bool length_seen = false;
    char *line = strstr(response, "\r\n");
    if (!line) { failure = "http-header"; return NULL; }
    line += 2;
    while (line < boundary) {
        char *end = strstr(line, "\r\n");
        if (!end || end > boundary) { failure = "http-header"; return NULL; }
        *end = 0;
        if (strncasecmp(line, "Transfer-Encoding:", 18) == 0) {
            failure = "http-encoding";
            return NULL;
        }
        if (strncasecmp(line, "Content-Length:", 15) == 0) {
            if (length_seen) { failure = "http-length"; return NULL; }
            length_seen = true;
            char *value = line + 15;
            while (*value == ' ' || *value == '\t') value++;
            if (!isdigit((unsigned char)*value)) { failure = "http-length"; return NULL; }
            errno = 0;
            char *tail = NULL;
            unsigned long declared = strtoul(value, &tail, 10);
            while (tail && (*tail == ' ' || *tail == '\t')) tail++;
            if (errno || !tail || *tail || declared != payload_size) {
                failure = "http-length";
                return NULL;
            }
        }
        line = end + 2;
    }
    failure = "response-json";
    return json.parse(payload, NULL, 1);
failed:
    if (descriptor >= 0) close(descriptor);
    return NULL;
}

static bool equal_string(const json_value *object, const char *name, const char *expected) {
    const char *value = json.string(json.get(object, name));
    return value && strcmp(value, expected) == 0;
}

static bool valid_uuid(const char *value) {
    if (!value || strnlen(value, 37) != 36) return false;
    for (unsigned i = 0; i < 36; i++) {
        if (i == 8 || i == 13 || i == 18 || i == 23) {
            if (value[i] != '-') return false;
        } else if (!isxdigit((unsigned char)value[i])) return false;
    }
    return true;
}

static bool same_file(const struct stat *first) {
    struct stat current;
    return lstat(ENTRY_DOCUMENT_PATH, &current) == 0 && S_ISREG(current.st_mode) &&
        current.st_dev == first->st_dev && current.st_ino == first->st_ino &&
        current.st_size == first->st_size &&
        current.st_mtim.tv_sec == first->st_mtim.tv_sec &&
        current.st_mtim.tv_nsec == first->st_mtim.tv_nsec &&
        current.st_ctim.tv_sec == first->st_ctim.tv_sec &&
        current.st_ctim.tv_nsec == first->st_ctim.tv_nsec;
}

static bool query_entry(const struct stat *file, char uuid[37], double *last_access) {
    const char *query = "{\"type\":\"QueryRequest\",\"resultType\":\"full\",\"noCount\":true,"
        "\"maxResults\":2,\"filter\":{\"And\":[{\"Equals\":{\"path\":\"location\","
        "\"value\":\"" ENTRY_DOCUMENT_PATH "\"}},{\"Equals\":{\"path\":\"mimeType\","
        "\"value\":\"text/x-shellscript\"}}]}}";
    json_value *root = post("/query", query);
    if (!root) return false;
    bool ok = false;
    failure = "catalog-identity";
    json_value *values = json.get(root, "values");
    if (!json.is_object(root) || !json.is_array(values) || json.array_size(values) != 1) goto done;
    json_value *entry = json.array_item(values, 0);
    const char *identity = json.string(json.get(entry, "uuid"));
    if (!json.is_object(entry) || !valid_uuid(identity) ||
        !equal_string(entry, "location", ENTRY_DOCUMENT_PATH) ||
        !equal_string(entry, "mimeType", "text/x-shellscript") ||
        json.number(json.get(entry, "modificationTime")) != (double)file->st_mtim.tv_sec ||
        json.number(json.get(entry, "contentSize")) != (double)file->st_size) goto done;
    json_value *tags = json.get(entry, "displayTags");
    if (tags && (!json.is_array(tags) || json.array_size(tags) > 16)) goto done;
    for (int i = 0; tags && i < json.array_size(tags); i++) {
        const char *tag = json.string(json.array_item(tags, i));
        if (!tag || strcmp(tag, "NEW") == 0) { failure = "new-item-fallback"; goto done; }
    }
    double access = json.number(json.get(entry, "lastAccess"));
    if (!isfinite(access) || access < 0 || access > 1e12 ||
        access != (double)(long long)access || !same_file(file)) goto done;
    memcpy(uuid, identity, 37);
    *last_access = access;
    ok = true;
done:
    json.delete(root);
    return ok;
}

int main(int argc, char **argv) {
    if (argc != 2 || (strcmp(argv[1], "--probe") != 0 && strcmp(argv[1], "--touch") != 0 &&
        strcmp(argv[1], "--touch-quiet") != 0 && strcmp(argv[1], "--notify") != 0 &&
        strcmp(argv[1], "--notify-protocol") != 0)) return 64;
    signal(SIGALRM, SIG_DFL);
    alarm(2);
    bool protocol = strcmp(argv[1], "--notify-protocol") == 0;
    bool quiet = strcmp(argv[1], "--touch-quiet") == 0;
    bool notify = protocol || strcmp(argv[1], "--notify") == 0;
    long long started = now_ms();
    deadline = started + CATALOG_BUDGET_MS;
    struct stat file;
    bool ok = false;
    if (lstat(ENTRY_DOCUMENT_PATH, &file) != 0 || !S_ISREG(file.st_mode) ||
        file.st_size <= 0 || file.st_size > 65536) { failure = "document"; goto finished; }
    if (!load_api()) goto finished;
    char uuid[37];
    double prior;
    if (!query_entry(&file, uuid, &prior)) goto finished;
    if (strcmp(argv[1], "--probe") == 0) { ok = true; goto finished; }
    time_t wall = time(NULL);
    if (wall <= 0 || !same_file(&file)) { failure = "document-or-clock"; goto finished; }
    double access = notify || prior > (double)wall ? prior : (double)wall;
    char body[256];
    int size = snprintf(body, sizeof(body), "{\"type\":\"ChangeRequest\",%s\"commands\":[{\"update\":{"
        "\"uuid\":\"%s\",\"lastAccess\":%.0f,\"percentFinished\":0}}]}",
        quiet ? "\"noNotify\":true," : "", uuid, access);
    if (size <= 0 || (size_t)size >= sizeof(body)) { failure = "request-size"; goto finished; }
    json_value *response = post("/change", body);
    if (!response) goto finished;
    failure = "change-rejected";
    bool acknowledged = json.is_object(response) && json.is_true(json.get(response, "ok")) &&
        equal_string(response, "type", "ChangeResponse") && json.number(json.get(response, "changes")) == 1;
    json.delete(response);
    if (!acknowledged) goto finished;
    char observed_uuid[37];
    double observed_access;
    if (!query_entry(&file, observed_uuid, &observed_access)) goto finished;
    failure = "readback-mismatch";
    ok = strcmp(uuid, observed_uuid) == 0 && observed_access == access && same_file(&file);
finished:
    if (protocol) {
        puts(ok ? "NOTIFIED" : "UNAVAILABLE");
        return ok ? 0 : 1;
    }
    printf("{\"success\":%s,\"operation\":\"%s\",\"stage\":\"%s\",\"elapsed_ms\":%lld}\n",
        ok ? "true" : "false", argv[1] + 2, ok ? "complete" : failure, now_ms() - started);
    return ok ? 0 : 1;
}
