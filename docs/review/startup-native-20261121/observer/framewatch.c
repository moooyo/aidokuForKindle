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
#include <time.h>
#include <unistd.h>

enum { WIDTH = 1860, HEIGHT = 2480, STRIDE = 1872, STEP = 32, OFFSET = 16,
       POINTS = 58 * 77, PERIOD_US = 75000, SLOTS = 160 };

struct layout {
    uint32_t rotate, xvirtual, yvirtual, pan;
    size_t bytes;
};

struct scan {
    uint64_t before, after;
    uint32_t pan;
    unsigned char pixels[POINTS];
};

static void fail(const char *reason) {
    fprintf(stderr, "framewatch: %s\n", reason);
    exit(2);
}

static uint64_t clock_us(clockid_t clock) {
    struct timespec value;
    if (clock_gettime(clock, &value) != 0) fail("clock");
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
        fail("unsupported or unreadable scanout");
    return (struct layout){var.rotate, var.xres_virtual, var.yres_virtual,
                           var.yoffset, (size_t)STRIDE * var.yres_virtual};
}

static int same_layout(struct layout left, struct layout right) {
    return left.rotate == right.rotate && left.xvirtual == right.xvirtual &&
           left.yvirtual == right.yvirtual && left.bytes == right.bytes;
}

static void read_pixels(const unsigned char *map, uint32_t pan, unsigned char *out, int full) {
    const unsigned char *page = map + (size_t)STRIDE * pan;
    if (full) {
        for (size_t row = 0; row < HEIGHT; ++row)
            memcpy(out + row * WIDTH, page + row * STRIDE, WIDTH);
    } else {
        size_t used = 0;
        for (size_t row = OFFSET; row < HEIGHT; row += STEP)
            for (size_t col = OFFSET; col < WIDTH; col += STEP)
                out[used++] = page[row * STRIDE + col];
        if (used != POINTS) fail("lattice");
    }
}

static void observe(int descriptor, struct layout initial, const unsigned char *map,
                    unsigned char *output, int full, uint64_t *before, uint64_t *after, uint32_t *pan) {
    *before = clock_us(CLOCK_BOOTTIME);
    struct layout first = layout_at(descriptor);
    if (!same_layout(initial, first)) fail("changed layout before scan");
    read_pixels(map, first.pan, output, full);
    struct layout last = layout_at(descriptor);
    *after = clock_us(CLOCK_BOOTTIME);
    if (!same_layout(first, last) || first.pan != last.pan) fail("changed scanout during scan");
    *pan = first.pan;
}

static void write_bytes(FILE *file, const void *value, size_t count) {
    if (fwrite(value, 1, count, file) != count) fail("write");
}

static void capture(int descriptor, struct layout layout, const unsigned char *map, FILE *out) {
    unsigned char *pixels = malloc((size_t)WIDTH * HEIGHT);
    uint64_t before, after;
    uint32_t pan;
    if (!pixels) fail("capture memory");
    observe(descriptor, layout, map, pixels, 1, &before, &after, &pan);
    fprintf(out, "AFR2 %u %u %u %u %" PRIu64 " %" PRIu64 "\n",
            layout.rotate, layout.xvirtual, layout.yvirtual, pan, before, after);
    write_bytes(out, pixels, (size_t)WIDTH * HEIGHT);
    free(pixels);
}

static void wait_until(uint64_t deadline) {
    struct timespec time = {(time_t)(deadline / 1000000), (long)(deadline % 1000000) * 1000};
    int status;
    do { status = clock_nanosleep(CLOCK_BOOTTIME, TIMER_ABSTIME, &time, NULL); } while (status == EINTR);
    if (status != 0) fail("wait");
}

static void watch(int descriptor, struct layout layout, const unsigned char *map, FILE *out) {
    struct scan *scans = calloc(SLOTS, sizeof *scans);
    if (!scans) fail("trace memory");
    uint64_t cpu = clock_us(CLOCK_PROCESS_CPUTIME_ID), start = clock_us(CLOCK_BOOTTIME);
    size_t count = 0;
    for (unsigned slot = 0; slot < SLOTS;) {
        wait_until(start + (uint64_t)slot * PERIOD_US);
        unsigned actual = (unsigned)((clock_us(CLOCK_BOOTTIME) - start) / PERIOD_US);
        if (actual >= SLOTS) break;
        struct scan *scan = &scans[count++];
        observe(descriptor, layout, map, scan->pixels, 0, &scan->before, &scan->after, &scan->pan);
        if (count == 1) {
            fputs("READY\n", stderr);
            fflush(stderr);
        }
        slot = actual + 1;
    }
    uint64_t end = clock_us(CLOCK_BOOTTIME);
    cpu = clock_us(CLOCK_PROCESS_CPUTIME_ID) - cpu;
    if (!count) fail("no scans");
    fprintf(out, "AFW2 %u %u %u %" PRIu64 " %" PRIu64 " %" PRIu64 " %zu %zu\n",
            layout.rotate, layout.xvirtual, layout.yvirtual, start, end, cpu, count, SLOTS - count);
    for (size_t index = 0; index < count; ++index) {
        struct scan *scan = &scans[index];
        write_bytes(out, &scan->before, 8);
        write_bytes(out, &scan->after, 8);
        write_bytes(out, &scan->pan, 4);
        write_bytes(out, scan->pixels, POINTS);
    }
    free(scans);
}

int main(int argc, char **argv) {
    if (argc == 2 && !strcmp(argv[1], "clock")) {
        printf("%" PRIu64 "\n", clock_us(CLOCK_BOOTTIME));
        return 0;
    }
    if (argc != 3 || (strcmp(argv[1], "capture") && strcmp(argv[1], "watch")))
        fail("usage: framewatch clock | capture|watch NEW_PRIVATE_FILE");
    int descriptor = open("/dev/fb0", O_RDONLY | O_CLOEXEC);
    if (descriptor < 0) fail("open screen");
    struct layout layout = layout_at(descriptor);
    void *map = mmap(NULL, layout.bytes, PROT_READ, MAP_SHARED, descriptor, 0);
    if (map == MAP_FAILED) fail("map screen");
    int destination = open(argv[2], O_WRONLY | O_CLOEXEC | O_NOFOLLOW | O_EXCL | O_CREAT, 0600);
    FILE *out = destination < 0 ? NULL : fdopen(destination, "wb");
    if (!out) fail("new output");
    if (!strcmp(argv[1], "capture")) capture(descriptor, layout, map, out);
    else watch(descriptor, layout, map, out);
    if (fclose(out) || munmap(map, layout.bytes) || close(descriptor)) fail("close");
    return 0;
}
