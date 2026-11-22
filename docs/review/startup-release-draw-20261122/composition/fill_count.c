#define _GNU_SOURCE
#include <dlfcn.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

typedef void *(*fill_function)(void *, int, size_t);
typedef struct { uintptr_t first; size_t count; } fill_run;

static fill_function real_fill;
static __thread int resolving;
static __thread volatile sig_atomic_t active;
static volatile sig_atomic_t transitions;
static fill_run runs[16384];
static size_t run_count, overflow_count, width, height;
static int paper;
static const char *receipt_path;

static void toggle(int signal_number) {
    (void)signal_number;
    ++transitions;
    active = transitions == 1;
}

static void resolve_fill(void) {
    resolving = 1;
    real_fill = (fill_function)dlsym(RTLD_NEXT, "memset");
    resolving = 0;
    if (!real_fill) _exit(88);
}

void *memset(void *destination, int value, size_t length) {
    if (!real_fill) {
        if (resolving) {
            volatile unsigned char *bytes = destination;
            for (size_t index = 0; index < length; ++index) bytes[index] = (unsigned char)value;
            return destination;
        }
        resolve_fill();
    }
    if (active && (unsigned char)value == paper && length == width) {
        uintptr_t start = (uintptr_t)destination;
        fill_run *last = run_count ? &runs[run_count - 1] : NULL;
        if (last && start == last->first + width * last->count) {
            ++last->count;
        } else if (run_count < sizeof(runs) / sizeof(runs[0])) {
            runs[run_count++] = (fill_run){start, 1};
        } else {
            ++overflow_count;
        }
    }
    return real_fill(destination, value, length);
}

__attribute__((constructor)) static void setup(void) {
    resolve_fill();
    receipt_path = getenv("AIDOKU_FILL_RECEIPT");
    if (!receipt_path) return;
    const char *dimensions = getenv("AIDOKU_FILL_SIZE");
    const char *level = getenv("AIDOKU_FILL_PAPER");
    unsigned int selected = 0;
    if (!dimensions || sscanf(dimensions, "%zux%zu", &width, &height) != 2 ||
        !width || width > 4096 || !height || height > 4096 ||
        !level || sscanf(level, "%u", &selected) != 1 || selected > 255) _exit(89);
    paper = (int)selected;
    struct sigaction action = {.sa_handler = toggle};
    sigemptyset(&action.sa_mask);
    if (sigaction(SIGURG, &action, NULL)) _exit(90);
}

__attribute__((destructor)) static void report(void) {
    if (!receipt_path) return;
    active = 0;
    FILE *output = fopen(receipt_path, "wx");
    if (!output) _exit(91);
    uintptr_t base = 0;
    size_t whole = 0, count = 0;
    for (size_t index = 0; index < run_count; ++index) {
        count += runs[index].count;
        if (runs[index].count == height) { ++whole; base = runs[index].first; }
    }
    fprintf(output, "{\"transitions\":%d,\"width\":%zu,\"height\":%zu,\"paper\":%d,"
            "\"overflow\":%zu,\"full_width_fills\":%zu,\"whole_runs\":%zu,\"runs\":[",
            (int)transitions, width, height, paper, overflow_count, count, whole);
    for (size_t index = 0; index < run_count; ++index) {
        uintptr_t start = runs[index].first;
        long relative_y = -1;
        if (whole == 1 && start >= base && start - base < width * height && (start - base) % width == 0)
            relative_y = (long)((start - base) / width);
        fprintf(output, "%s[%ld,%zu]", index ? "," : "", relative_y, runs[index].count);
    }
    fputs("]}\n", output);
    if (fclose(output)) _exit(92);
}
