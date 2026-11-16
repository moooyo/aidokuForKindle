/* Controlled spatial-dither readback, derived from the reviewed Y4 probe.
 * Execute only under the managed launcher and the host's fresh device lease.
 * Uses the existing framebuffer path; no V4L2 codec or USERPTR operations.
 */
#include <errno.h>
#include <fcntl.h>
#include <linux/fb.h>
#include <limits.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

struct rect { uint32_t top, left, width, height; };
struct alternate { uint32_t address, width, height; struct rect region; };
struct update {
    struct rect region;
    uint32_t waveform, mode, marker;
    int32_t temperature;
    uint32_t flags;
    int32_t dither_mode, quant_bit;
    struct alternate alternate;
    uint32_t swipe_direction, swipe_steps, histogram_bw, histogram_gray, ts_pxp, ts_epdc;
};
struct marker { uint32_t marker, collision; };
#define SEND_UPDATE _IOW('F', 0x2e, struct update)
#define WAIT_UPDATE _IOWR('F', 0x2f, struct marker)
_Static_assert(sizeof(struct update) == 96, "MediaTek update ABI mismatch");
_Static_assert(sizeof(struct marker) == 8, "Update marker ABI mismatch");
static volatile sig_atomic_t stopping;
static const char *dumps[] = {"/tmp/last_fb.bin", "/tmp/last_image.bin", "/tmp/last_regal.bin", "/tmp/last_wb.bin"};

static void stop(int signal_number) { (void)signal_number; stopping = 1; }

static int text_file(const char *path, char *text, size_t capacity) {
    int fd = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) return -1;
    ssize_t length = read(fd, text, capacity - 1);
    close(fd);
    if (length <= 0 || (size_t)length >= capacity - 1) return -1;
    text[length] = 0;
    while (length && (text[length - 1] == '\n' || text[length - 1] == '\r')) text[--length] = 0;
    return 0;
}

static int managed(void) {
    const char *directory = getenv("AIDOKU_LAUNCHER_DIR"), *nonce = getenv("AIDOKU_LAUNCH_NONCE");
    char recorded[96];
    if (!directory || strcmp(directory, "/tmp/aidoku-launcher.lock") || !nonce || strlen(nonce) != 32
        || text_file("/tmp/aidoku-launcher.lock/nonce", recorded, sizeof(recorded)) || strcmp(recorded, nonce)) return 0;
    for (unsigned int i = 0; i < 100; ++i) {
        if (!text_file("/tmp/aidoku-launcher.lock/application-pid", recorded, sizeof(recorded))
            && strtol(recorded, NULL, 10) == (long)getpid()) return 1;
        usleep(20000);
    }
    return 0;
}

static int scribe(void) {
    unsigned char serial[16];
    int fd = open("/proc/usid", O_RDONLY | O_CLOEXEC);
    if (fd < 0) return 0;
    ssize_t length = read(fd, serial, sizeof(serial));
    close(fd);
    if (length != 16 || serial[0] != 'G') return 0;
    for (unsigned int i = 0; i < sizeof(serial); ++i)
        if (!((serial[i] >= 'A' && serial[i] <= 'Z') || (serial[i] >= '0' && serial[i] <= '9'))) return 0;
    const char *codes[] = {"27J","2BL","263","227","2BM","23L","23M","270",
        "3V0","3V1","3X5","3UV","3X4","3X3","41E","410"};
    for (unsigned int i = 0; i < sizeof(codes) / sizeof(codes[0]); ++i)
        if (!memcmp(serial + 3, codes[i], 3)) return 1;
    return 0;
}

static int save(const char *path, const void *data, size_t bytes) {
    int fd = open(path, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0600);
    if (fd < 0) return -1;
    const unsigned char *p = data;
    while (bytes) {
        ssize_t length = write(fd, p, bytes);
        if (length < 0 && errno == EINTR) continue;
        if (length <= 0) { close(fd); return -1; }
        p += length; bytes -= (size_t)length;
    }
    return close(fd);
}

static long copy_image(const char *destination) {
    int source = open("/tmp/last_image.bin", O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    struct stat info;
    if (source < 0) return -1;
    if (fstat(source, &info) || !S_ISREG(info.st_mode) || info.st_size <= 0 || info.st_size > 32 * 1024 * 1024) {
        close(source); return -1;
    }
    unsigned char *bytes = malloc((size_t)info.st_size);
    if (!bytes) { close(source); return -1; }
    size_t done = 0;
    while (done < (size_t)info.st_size) {
        ssize_t count = read(source, bytes + done, (size_t)info.st_size - done);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) break;
        done += (size_t)count;
    }
    close(source);
    int result = done == (size_t)info.st_size ? save(destination, bytes, done) : -1;
    free(bytes);
    return result == 0 ? (long)done : -1;
}

static int load_frame(const char *path, unsigned char *mapping, size_t stride, uint32_t width, uint32_t height, int inverted) {
    int fd = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) return -1;
    struct stat info;
    size_t bytes = (size_t)width * height;
    if (fstat(fd, &info) || !S_ISREG(info.st_mode) || info.st_size != (off_t)bytes) { close(fd); errno = EINVAL; return -1; }
    unsigned char *input = malloc(bytes);
    if (!input) { close(fd); return -1; }
    size_t done = 0;
    while (done < bytes) {
        ssize_t count = read(fd, input + done, bytes - done);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) break;
        done += (size_t)count;
    }
    close(fd);
    if (done != bytes) { free(input); errno = EIO; return -1; }
    memset(mapping, inverted ? 0 : 255, stride * height);
    for (uint32_t y = 0; y < height; ++y) {
        for (uint32_t x = 0; x < width; ++x) {
            unsigned char value = input[(size_t)y * width + x];
            mapping[(size_t)y * stride + x] = inverted ? 255 - value : value;
        }
    }
    free(input);
    return 0;
}

static int refresh(int fd, uint32_t width, uint32_t height, uint32_t waveform, uint32_t flags, uint32_t id) {
    struct update update = {.region = {0, 0, width, height}, .waveform = waveform,
        .mode = 1, .marker = id, .temperature = 0x1000, .flags = flags,
        .histogram_bw = 1, .histogram_gray = waveform};
    if (ioctl(fd, SEND_UPDATE, &update) < 0) return -1;
    struct marker marker = {id, 0};
    int result;
    do { result = ioctl(fd, WAIT_UPDATE, &marker); } while (result < 0 && errno == EINTR);
    return result;
}

int main(int argc, char **argv) {
    if (argc == 2 && !strcmp(argv[1], "--check-layout")) {
        printf("{\"update_size\":%zu,\"marker_size\":%zu,\"send_ioctl\":%lu,\"wait_ioctl\":%lu}\n",
            sizeof(struct update), sizeof(struct marker), (unsigned long)SEND_UPDATE, (unsigned long)WAIT_UPDATE);
        return 0;
    }
    const char *home = getenv("AIDOKU_HOME");
    char expected[512], resolved[PATH_MAX];
    if (argc != 2 || !home || strlen(argv[1]) >= sizeof(resolved)
        || strncmp(home, "/mnt/us/aidoku-display-", strlen("/mnt/us/aidoku-display-"))) return 2;
    if (snprintf(expected, sizeof(expected), "%s/y4-output", home) >= (int)sizeof(expected)
        || !realpath(argv[1], resolved) || strcmp(expected, resolved) || !managed() || !scribe()) return 2;
    for (unsigned int i = 0; i < 4; ++i) {
        struct stat metadata;
        if (lstat(dumps[i], &metadata) == 0 || errno != ENOENT) {
            fprintf(stderr, "Existing driver dump files must be preserved; refusing this run.\n");
            return 2;
        }
    }
    signal(SIGTERM, stop); signal(SIGINT, stop); signal(SIGHUP, stop);
    signal(SIGUSR1, stop); signal(SIGALRM, stop); alarm(60);
    int fd = -1, failure = 0, modified = 0, restored = 0, owned_dumps = 0;
    unsigned char *mapping = MAP_FAILED, *original = NULL;
    size_t span = 0;
    struct fb_var_screeninfo variable = {0};
    struct fb_fix_screeninfo fixed = {0};
    uint32_t waveform = 2, id = 0x60000000U | (((uint32_t)getpid() & 0xffffU) << 8);
    long image_sizes[7] = {0};
    unsigned int completed = 0;
    unsigned int cmap_values = 0;
    int cmap_read = 0;
    fd = open("/dev/fb0", O_RDWR | O_CLOEXEC);
    if (fd < 0 || ioctl(fd, FBIOGET_VSCREENINFO, &variable) || ioctl(fd, FBIOGET_FSCREENINFO, &fixed)) goto failed;
    if (variable.bits_per_pixel != 8 || (variable.grayscale != 1 && variable.grayscale != 2)
        || variable.xoffset || variable.yoffset || !variable.xres || !variable.yres
        || fixed.line_length < variable.xres || (uint64_t)fixed.line_length * variable.yres > fixed.smem_len
        || fixed.smem_len > 32U * 1024U * 1024U) { errno = EINVAL; goto failed; }
    waveform = variable.grayscale == 2 ? 8 : 2;
    uint16_t red[256] = {0}, green[256] = {0}, blue[256] = {0};
    struct fb_cmap cmap = {.start = 0, .len = 256, .red = red, .green = green, .blue = blue};
    if (ioctl(fd, FBIOGETCMAP, &cmap) == 0) {
        cmap_read = 1;
        for (unsigned int i = 0; i < 256; ++i) {
            unsigned int j = 0;
            while (j < i && red[j] != red[i]) ++j;
            if (j == i) ++cmap_values;
        }
    }
    span = (size_t)fixed.line_length * variable.yres;
    mapping = mmap(NULL, span, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (mapping == MAP_FAILED) goto failed;
    original = malloc(span);
    if (!original) goto failed;
    memcpy(original, mapping, span);
    if (refresh(fd, variable.xres, variable.yres, waveform, 0, ++id)) goto failed;
    const char *methods[] = {"gray8", "current_bayer4", "current_fs_q4", "bayer8", "fs_q8", "gray8", "gray8"};
    for (unsigned int i = 0; i < 7 && !stopping; ++i) {
        uint32_t flags = i == 5 ? 0x4000 : 0;
        char input_path[640];
        snprintf(input_path, sizeof(input_path), "%s/y4-input/%s.raw", home, methods[i]);
        modified = 1;
        if (load_frame(input_path, mapping, fixed.line_length, variable.xres, variable.yres, variable.grayscale == 2)) goto failed;
        if (refresh(fd, variable.xres, variable.yres, waveform, flags, ++id)) goto failed;
        char path[640];
        snprintf(path, sizeof(path), "%s/input-%u.bin", expected, i);
        if (save(path, mapping, span)) goto failed;
        int command = open("/proc/hwtcon/cmd", O_WRONLY | O_CLOEXEC);
        if (command < 0) goto failed;
        owned_dumps = 1;
        ssize_t written = write(command, "dump_last\n", 10);
        close(command);
        if (written != 10) goto failed;
        snprintf(path, sizeof(path), "%s/image-%u.bin", expected, i);
        image_sizes[i] = copy_image(path);
        for (unsigned int j = 0; j < 4; ++j) unlink(dumps[j]);
        owned_dumps = 0;
        if (image_sizes[i] <= 0) goto failed;
        ++completed;
        printf("Y4_CASE index=%u marker=%u waveform=%u flags=%u bytes=%ld\n", i, id, waveform, flags, image_sizes[i]);
        fflush(stdout);
    }
    if (completed != 7) { errno = EINTR; goto failed; }
    goto cleanup;
failed:
    failure = errno ? errno : EIO;
cleanup:
    if (owned_dumps) for (unsigned int j = 0; j < 4; ++j) unlink(dumps[j]);
    if (modified && original && mapping != MAP_FAILED) {
        memcpy(mapping, original, span);
        restored = refresh(fd, variable.xres, variable.yres, waveform, 0, ++id) == 0
            && memcmp(mapping, original, span) == 0;
    } else restored = 1;
    if (mapping != MAP_FAILED) munmap(mapping, span);
    free(original);
    if (fd >= 0) close(fd);
    char report[1024], path[640];
    int length = snprintf(report, sizeof(report), "{\"schema\":1,\"errno\":%d,\"completed_cases\":%u,\"framebuffer_restored\":%s,\"width\":%u,\"height\":%u,\"stride\":%u,\"grayscale\":%u,\"rotation\":%u,\"wire_waveform\":%u,\"cmap_read\":%s,\"cmap_red_distinct\":%u,\"image_sizes\":[%ld,%ld,%ld,%ld,%ld,%ld,%ld],\"evidence_scope\":\"Input and post-MDP buffers; wait return is not an optical measurement\"}\n",
        failure, completed, restored ? "true" : "false", variable.xres, variable.yres,
        fixed.line_length, variable.grayscale, variable.rotate, waveform, cmap_read ? "true" : "false", cmap_values,
        image_sizes[0], image_sizes[1], image_sizes[2], image_sizes[3], image_sizes[4], image_sizes[5], image_sizes[6]);
    snprintf(path, sizeof(path), "%s/report.json", expected);
    if (length > 0 && length < (int)sizeof(report)) save(path, report, (size_t)length);
    return failure || !restored ? 1 : 0;
}
