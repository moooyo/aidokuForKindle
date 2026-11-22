#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <linux/fb.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

enum { WIDTH = 1860, HEIGHT = 2480, STRIDE = 1872, STEP = 32, OFFSET = 16,
       COLUMNS = 58, ROWS = 77, POINTS = COLUMNS * ROWS, TEXT_REGIONS = 5,
       ART_REGIONS = 7, REGIONS = TEXT_REGIONS + ART_REGIONS,
       PERIOD_US = 120000, WINDOW_US = 12000000, MAX_SCAN_US = 50000,
       MAX_GAP_US = 150000, DWELL_US = 200000, REQUIRED = 3 };

struct layout {
    uint32_t rotate, xvirtual, yvirtual, pan;
    size_t bytes;
};

struct rule {
    uint32_t rotate, xvirtual, yvirtual, turn;
    unsigned char target[POINTS];
    unsigned char region[POINTS];
};

struct state {
    uint64_t previous_end, last_negative, first_match;
    unsigned scans, streak;
    int first_cover;
};

struct result {
    uint64_t last_negative, first_match, confirmed;
    int first_cover, confirmed_cover;
};

struct rectangle { int left, top, width, height; };

static void fail(const char *reason) {
    fprintf(stderr, "ARL1_ERR %s\n", reason);
    exit(2);
}

static uint64_t clock_us(clockid_t clock) {
    struct timespec value;
    if (clock_gettime(clock, &value) != 0) fail("CLOCK");
    return (uint64_t)value.tv_sec * 1000000 + (uint64_t)value.tv_nsec / 1000;
}

static int supported(const struct fb_var_screeninfo *var, const struct fb_fix_screeninfo *fix) {
    return var->xres == WIDTH && var->yres == HEIGHT && var->bits_per_pixel == 8 &&
           (var->rotate == 1 || var->rotate == 3) && var->xoffset == 0 &&
           (var->xres_virtual == WIDTH || var->xres_virtual == STRIDE) &&
           (var->yres_virtual == HEIGHT || var->yres_virtual == 2 * HEIGHT) &&
           (var->yoffset == 0 || (var->yoffset == HEIGHT && var->yres_virtual == 2 * HEIGHT)) &&
           fix->type == FB_TYPE_PACKED_PIXELS && fix->line_length == STRIDE &&
           fix->smem_len >= (uint32_t)STRIDE * var->yres_virtual;
}

static struct layout layout_at(int descriptor) {
    struct fb_var_screeninfo var;
    struct fb_fix_screeninfo fix;
    if (ioctl(descriptor, FBIOGET_FSCREENINFO, &fix) != 0 ||
        ioctl(descriptor, FBIOGET_VSCREENINFO, &var) != 0 || !supported(&var, &fix))
        fail("LAYOUT");
    return (struct layout){var.rotate, var.xres_virtual, var.yres_virtual,
                           var.yoffset, (size_t)STRIDE * var.yres_virtual};
}

static int same_layout(struct layout left, struct layout right) {
    return left.rotate == right.rotate && left.xvirtual == right.xvirtual &&
           left.yvirtual == right.yvirtual && left.bytes == right.bytes;
}

static int parse_uint(const char **cursor, uint64_t *value) {
    if (**cursor < '0' || **cursor > '9') return 0;
    uint64_t number = 0;
    do {
        unsigned digit = (unsigned)(*(*cursor)++ - '0');
        if (number > (UINT64_MAX - digit) / 10) return 0;
        number = number * 10 + digit;
    } while (**cursor >= '0' && **cursor <= '9');
    *value = number;
    return 1;
}

static int read_exact(int descriptor, void *buffer, size_t length) {
    unsigned char *bytes = buffer;
    while (length) {
        ssize_t got = read(descriptor, bytes, length);
        if (got < 0 && errno == EINTR) continue;
        if (got <= 0) return 0;
        bytes += (size_t)got;
        length -= (size_t)got;
    }
    return 1;
}

static int make_regions(struct rule *rule) {
    const struct rectangle rectangles[REGIONS] = {
        {0, 72, WIDTH, 128}, {0, 2320, WIDTH, 160}, {272, 234, 1030, 144},
        {72, 936, 1716, 92}, {72, 1479, 324, 92},
        {80, 224, 152, 224}, {80, 504, 308, 419}, {428, 504, 308, 419},
        {776, 504, 308, 419}, {1124, 504, 308, 419}, {1472, 504, 308, 419},
        {80, 1047, 308, 419}
    };
    unsigned counts[REGIONS] = {0};
    memset(rule->region, 0, sizeof rule->region);
    for (unsigned row = 0; row < ROWS; ++row) {
        for (unsigned col = 0; col < COLUMNS; ++col) {
            int raw_x = OFFSET + (int)col * STEP, raw_y = OFFSET + (int)row * STEP;
            int x = rule->turn ? WIDTH - 1 - raw_x : raw_x;
            int y = rule->turn ? HEIGHT - 1 - raw_y : raw_y;
            unsigned index = row * COLUMNS + col;
            for (unsigned region = 0; region < REGIONS; ++region) {
                struct rectangle rect = rectangles[region];
                if (x >= rect.left && x < rect.left + rect.width &&
                    y >= rect.top && y < rect.top + rect.height) {
                    if (rule->region[index]) return 0;
                    rule->region[index] = (unsigned char)(region + 1);
                    ++counts[region];
                }
            }
        }
    }
    for (unsigned region = 0; region < REGIONS; ++region) {
        if (counts[region] < 16) return 0;
        unsigned tones[256] = {0}, distinct = 0;
        for (unsigned index = 0; index < POINTS; ++index)
            if (rule->region[index] == region + 1) ++tones[rule->target[index]];
        unsigned mode = 0;
        for (unsigned tone = 0; tone < 256; ++tone) {
            if (tones[tone] > tones[mode]) mode = tone;
            distinct += tones[tone] != 0;
        }
        unsigned contrast = 0;
        for (unsigned tone = 0; tone < 256; ++tone)
            if (abs((int)tone - (int)mode) >= 16) contrast += tones[tone];
        unsigned minimum = (counts[region] * 3 + 999) / 1000;
        if (minimum < 3) minimum = 3;
        if (contrast < minimum || (region >= TEXT_REGIONS && distinct < 3)) return 0;
    }
    return 1;
}

static int load_rule(const char *path, struct rule *rule) {
    int descriptor = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (descriptor < 0) return 0;
    struct stat status;
    char header[256];
    size_t used = 0;
    int ok = fstat(descriptor, &status) == 0 && S_ISREG(status.st_mode) &&
             status.st_size > POINTS && status.st_size <= POINTS + (off_t)sizeof header;
    while (ok && used + 1 < sizeof header) {
        if (!read_exact(descriptor, header + used, 1)) { ok = 0; break; }
        if (header[used++] == '\n') break;
    }
    if (!used || header[used - 1] != '\n') ok = 0;
    header[used] = '\0';
    uint64_t fields[6] = {0};
    const char *cursor = header;
    if (ok && strncmp(cursor, "AFR3RULE ", 9) == 0) cursor += 9;
    else ok = 0;
    for (unsigned index = 0; ok && index < 6; ++index) {
        if (!parse_uint(&cursor, &fields[index]) ||
            *cursor++ != (index == 5 ? '\n' : ' ')) ok = 0;
    }
    if (ok && (*cursor != '\0' || (fields[0] != 1 && fields[0] != 3) ||
               (fields[1] != WIDTH && fields[1] != STRIDE) ||
               (fields[2] != HEIGHT && fields[2] != 2 * HEIGHT) ||
               (fields[3] != 0 && fields[3] != 180) || fields[4] != 6 || !fields[5] ||
               status.st_size != (off_t)(used + POINTS))) ok = 0;
    if (ok) {
        rule->rotate = (uint32_t)fields[0];
        rule->xvirtual = (uint32_t)fields[1];
        rule->yvirtual = (uint32_t)fields[2];
        rule->turn = (uint32_t)fields[3];
        ok = read_exact(descriptor, rule->target, POINTS) && make_regions(rule);
    }
    if (close(descriptor) != 0) ok = 0;
    return ok;
}

static void classify(const struct rule *rule, const unsigned char *pixels,
                     unsigned *text_diff, int *cover_match) {
    unsigned differs[REGIONS] = {0};
    for (unsigned index = 0; index < POINTS; ++index) {
        unsigned region = rule->region[index];
        if (!region) continue;
        differs[region - 1] |= pixels[index] != rule->target[index];
    }
    *text_diff = 0;
    for (unsigned region = 0; region < TEXT_REGIONS; ++region)
        *text_diff |= differs[region] << region;
    *cover_match = 1;
    for (unsigned region = TEXT_REGIONS; region < REGIONS; ++region)
        if (differs[region]) *cover_match = 0;
}

static void sample(const struct rule *rule, const unsigned char *map, uint32_t pan,
                   unsigned *text_diff, int *cover_match) {
    const volatile unsigned char *page = map + (size_t)STRIDE * pan;
    unsigned char pixels[POINTS];
    for (unsigned index = 0; index < POINTS; ++index) {
        if (!rule->region[index]) continue;
        unsigned row = index / COLUMNS, col = index % COLUMNS;
        size_t offset = (OFFSET + row * STEP) * (size_t)STRIDE + OFFSET + col * STEP;
        pixels[index] = page[offset];
    }
    classify(rule, pixels, text_diff, cover_match);
}

static int advance(struct state *state, uint64_t before, uint64_t after,
                   unsigned text_diff, int cover_match, struct result *result) {
    if (!before || after < before || after - before > MAX_SCAN_US ||
        (state->scans && (before < state->previous_end || before - state->previous_end > MAX_GAP_US)))
        return -1;
    if (!state->scans && text_diff != (1u << TEXT_REGIONS) - 1) return -2;
    state->previous_end = after;
    ++state->scans;
    if (text_diff) {
        state->last_negative = before;
        state->streak = 0;
        return 0;
    }
    if (!state->streak) {
        state->first_match = after;
        state->first_cover = cover_match;
    }
    ++state->streak;
    if (state->streak < REQUIRED) return 0;
    if (before - state->first_match < DWELL_US) return -3;
    *result = (struct result){state->last_negative, state->first_match, after,
                              state->first_cover, cover_match};
    return 1;
}

static void wait_until(uint64_t deadline) {
    struct timespec time = {(time_t)(deadline / 1000000), (long)(deadline % 1000000) * 1000};
    int status;
    do { status = clock_nanosleep(CLOCK_BOOTTIME, TIMER_ABSTIME, &time, NULL); } while (status == EINTR);
    if (status != 0) fail("WAIT");
}

static struct result watch(int descriptor, struct layout initial, const unsigned char *map,
                           const struct rule *rule) {
    struct state state = {0};
    struct result result = {0};
    uint64_t start = clock_us(CLOCK_BOOTTIME), cpu_start = clock_us(CLOCK_PROCESS_CPUTIME_ID);
    for (uint64_t slot = 0;;) {
        wait_until(start + slot * PERIOD_US);
        uint64_t now = clock_us(CLOCK_BOOTTIME);
        if (now - start >= WINDOW_US) fail("TIMEOUT");
        uint64_t before = now;
        struct layout first = layout_at(descriptor);
        if (!same_layout(initial, first)) fail("LAYOUT");
        unsigned text_diff;
        int cover_match;
        sample(rule, map, first.pan, &text_diff, &cover_match);
        struct layout last = layout_at(descriptor);
        uint64_t after = clock_us(CLOCK_BOOTTIME);
        if (!same_layout(first, last) || first.pan != last.pan) fail("PAN");
        int progress = advance(&state, before, after, text_diff, cover_match, &result);
        if (progress < 0) fail(progress == -1 ? "SCAN" : progress == -2 ? "INITIAL" : "DWELL");
        if (state.scans == 1) {
            if (fputs("READY\n", stderr) == EOF || fflush(stderr)) fail("OUTPUT");
        }
        if (progress == 1) {
            uint64_t end = clock_us(CLOCK_BOOTTIME);
            uint64_t cpu = clock_us(CLOCK_PROCESS_CPUTIME_ID) - cpu_start;
            if (end <= start || cpu > (end - start) / 10) fail("CPU");
            return result;
        }
        slot = (after - start) / PERIOD_US + 1;
    }
}

int main(int argc, char **argv) {
    if (argc != 2) fail("USAGE");
    struct rule rule = {0};
    if (!load_rule(argv[1], &rule)) fail("RULE");
    int descriptor = open("/dev/fb0", O_RDONLY | O_CLOEXEC);
    if (descriptor < 0) fail("OPEN");
    struct layout layout = layout_at(descriptor);
    if (layout.rotate != rule.rotate || layout.xvirtual != rule.xvirtual ||
        layout.yvirtual != rule.yvirtual) fail("RULE_LAYOUT");
    void *map = mmap(NULL, layout.bytes, PROT_READ, MAP_SHARED, descriptor, 0);
    if (map == MAP_FAILED) fail("MAP");
    struct result result = watch(descriptor, layout, map, &rule);
    if (munmap(map, layout.bytes) || close(descriptor)) fail("CLOSE");
    if (printf("ARL1 %" PRIu64 " %" PRIu64 " %" PRIu64 " %s %s\n",
               result.last_negative, result.first_match, result.confirmed,
               result.first_cover ? "MATCH" : "DIFFER",
               result.confirmed_cover ? "MATCH" : "DIFFER") < 0 || fflush(stdout)) fail("OUTPUT");
    return 0;
}
