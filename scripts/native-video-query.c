/* Read-only V4L2 capability discovery for an explicitly leased Kindle batch.
 * Build on test-env; execute only on the designated physical device.
 * This tool never changes a format, queues buffers, or starts a stream.
 */
#include <errno.h>
#include <fcntl.h>
#include <linux/fb.h>
#include <linux/videodev2.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

static void json_string(const unsigned char *text, size_t limit) {
    putchar('"');
    for (size_t i = 0; i < limit && text[i]; ++i) {
        unsigned char c = text[i];
        if (c == '"' || c == '\\') printf("\\%c", c);
        else if (c < 32 || c > 126) printf("\\u%04x", c);
        else putchar(c);
    }
    putchar('"');
}

static void fourcc(unsigned int value) {
    unsigned char name[5] = {value & 255, (value >> 8) & 255,
        (value >> 16) & 255, (value >> 24) & 255, 0};
    json_string(name, 4);
}

static void formats(int fd, enum v4l2_buf_type type) {
    printf("{\"type\":%u,\"formats\":[", type);
    int first = 1;
    unsigned int count = 0;
    int end_error = 0;
    for (unsigned int i = 0; i < 64; ++i) {
        struct v4l2_fmtdesc format = {.index = i, .type = type};
        if (ioctl(fd, VIDIOC_ENUM_FMT, &format) < 0) { end_error = errno; break; }
        ++count;
        if (!first) putchar(',');
        first = 0;
        printf("{\"fourcc\":"); fourcc(format.pixelformat);
        printf(",\"value\":%u,\"flags\":%u,\"description\":", format.pixelformat, format.flags);
        json_string(format.description, sizeof(format.description));
        putchar('}');
    }
    printf("],\"count\":%u,\"end_errno\":%d,\"truncated\":%s}", count, end_error, count == 64 ? "true" : "false");
}

static void controls(int fd) {
    const unsigned int ids[] = {V4L2_CID_ROTATE, V4L2_CID_HFLIP, V4L2_CID_VFLIP};
    putchar('[');
    for (size_t i = 0; i < sizeof(ids) / sizeof(ids[0]); ++i) {
        struct v4l2_queryctrl control = {.id = ids[i]};
        errno = 0;
        int result = ioctl(fd, VIDIOC_QUERYCTRL, &control);
        if (i) putchar(',');
        printf("{\"id\":%u,\"supported\":%s,\"errno\":%d", ids[i], result == 0 ? "true" : "false", result == 0 ? 0 : errno);
        if (result == 0) {
            printf(",\"name\":"); json_string(control.name, sizeof(control.name));
            printf(",\"min\":%d,\"max\":%d,\"step\":%d,\"flags\":%u",
                control.minimum, control.maximum, control.step, control.flags);
        }
        putchar('}');
    }
    putchar(']');
}

static void framebuffer(void) {
    int fd = open("/dev/fb0", O_RDONLY | O_CLOEXEC);
    struct fb_var_screeninfo variable;
    struct fb_fix_screeninfo fixed;
    memset(&variable, 0, sizeof(variable));
    memset(&fixed, 0, sizeof(fixed));
    if (fd < 0 || ioctl(fd, FBIOGET_VSCREENINFO, &variable) < 0
        || ioctl(fd, FBIOGET_FSCREENINFO, &fixed) < 0) {
        printf("{\"errno\":%d}", errno);
    } else {
        printf("{\"width\":%u,\"height\":%u,\"bpp\":%u,\"grayscale\":%u,\"rotation\":%u,\"stride\":%u}",
            variable.xres, variable.yres, variable.bits_per_pixel,
            variable.grayscale, variable.rotate, fixed.line_length);
    }
    if (fd >= 0) close(fd);
}

int main(int argc, char **argv) {
    if (argc < 2 || argc > 9) {
        fprintf(stderr, "Usage: native-video-query /dev/videoN [other video nodes]\n");
        return 2;
    }
    for (int i = 1; i < argc; ++i) {
        size_t length = strlen(argv[i]);
        if (length < 11 || length > 14 || strncmp(argv[i], "/dev/video", 10) != 0) return 2;
        for (size_t j = 10; j < length; ++j) if (argv[i][j] < '0' || argv[i][j] > '9') return 2;
    }
    alarm(15);
    printf("{\"schema\":1,\"framebuffer\":"); framebuffer();
    printf(",\"devices\":[");
    for (int i = 1; i < argc; ++i) {
        if (i > 1) putchar(',');
        printf("{\"node\":"); json_string((const unsigned char *)argv[i], 24);
        int fd = open(argv[i], O_RDWR | O_NONBLOCK | O_CLOEXEC);
        if (fd < 0) { printf(",\"open_errno\":%d}", errno); continue; }
        struct v4l2_capability capability;
        memset(&capability, 0, sizeof(capability));
        if (ioctl(fd, VIDIOC_QUERYCAP, &capability) < 0) {
            printf(",\"query_errno\":%d}", errno); close(fd); continue;
        }
        printf(",\"driver\":"); json_string(capability.driver, sizeof(capability.driver));
        printf(",\"card\":"); json_string(capability.card, sizeof(capability.card));
        printf(",\"version\":%u,\"capabilities\":%u,\"device_caps\":%u,\"queues\":[",
            capability.version, capability.capabilities, capability.device_caps);
        formats(fd, V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE); putchar(',');
        formats(fd, V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE); putchar(',');
        formats(fd, V4L2_BUF_TYPE_VIDEO_OUTPUT); putchar(',');
        formats(fd, V4L2_BUF_TYPE_VIDEO_CAPTURE);
        printf("],\"controls\":"); controls(fd); putchar('}');
        close(fd);
    }
    printf("]}\n");
    return 0;
}
