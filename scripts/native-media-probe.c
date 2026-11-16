/* One-frame V4L2 compatibility probe for a leased physical Kindle.
 * Use only documented formats found in the matching MediaTek driver sources.
 * Every context owns its formats, buffers and streams; no framebuffer is used.
 * Codec execution is disabled after the Scribe JPEG USERPTR trial caused a
 * kernel NULL-pointer fault. Retain that investigation separately from MDP.
 * Deadlines do not guarantee cancellation: this driver's job_abort is empty.
 * A completed ioctl sequence still requires independent output-pixel checks.
 */
#include <errno.h>
#include <fcntl.h>
#include <linux/videodev2.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#define LIMIT (32U * 1024U * 1024U)
#define ALIGN16(value) (((value) + 15U) & ~15U)

struct buffer {
    enum v4l2_buf_type type;
    enum v4l2_memory memory_type;
    void *memory;
    unsigned int length;
    int streaming;
};

static double milliseconds(void) {
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return now.tv_sec * 1000.0 + now.tv_nsec / 1000000.0;
}

static unsigned int number(const char *text) {
    char *end = NULL;
    unsigned long value = strtoul(text, &end, 10);
    if (!text[0] || !end || *end || value > 8192) return UINT32_MAX;
    return (unsigned int)value;
}

static int format(int fd, struct v4l2_format *f, enum v4l2_buf_type type,
                  unsigned int pixel, unsigned int width, unsigned int height,
                  unsigned int bpp) {
    memset(f, 0, sizeof(*f));
    f->type = type;
    struct v4l2_pix_format_mplane *p = &f->fmt.pix_mp;
    p->width = width; p->height = height; p->pixelformat = pixel;
    p->field = V4L2_FIELD_NONE; p->num_planes = 1;
    p->colorspace = V4L2_COLORSPACE_JPEG;
    p->quantization = V4L2_QUANTIZATION_FULL_RANGE;
    p->plane_fmt[0].bytesperline = ALIGN16(width * bpp);
    p->plane_fmt[0].sizeimage = p->plane_fmt[0].bytesperline * ALIGN16(height);
    if (ioctl(fd, VIDIOC_S_FMT, f) < 0) return -1;
    struct v4l2_pix_format_mplane set = *p;
    if (ioctl(fd, VIDIOC_G_FMT, f) < 0) return -1;
    if (p->pixelformat != pixel || p->num_planes != 1 || !p->plane_fmt[0].sizeimage
        || p->plane_fmt[0].sizeimage > LIMIT || p->width != width || p->height != height
        || set.pixelformat != p->pixelformat || set.num_planes != p->num_planes
        || set.width != p->width || set.height != p->height
        || set.plane_fmt[0].bytesperline != p->plane_fmt[0].bytesperline
        || set.plane_fmt[0].sizeimage != p->plane_fmt[0].sizeimage) {
        errno = EINVAL; return -1;
    }
    return 0;
}

static int identify_mdp(int fd) {
    struct v4l2_capability capability;
    memset(&capability, 0, sizeof(capability));
    if (ioctl(fd, VIDIOC_QUERYCAP, &capability) < 0) return -1;
    if (!memchr(capability.driver, '\0', sizeof(capability.driver))) {
        errno = ENODEV; return -1;
    }
    const char *driver = (const char *)capability.driver;
    int known = !strcmp(driver, "mtk-mdp") || !strcmp(driver, "mtk-mdp-1")
        || !strcmp(driver, "mtk-mdp-2") || !strcmp(driver, "mtk-mdp-3");
    unsigned int caps = capability.capabilities & V4L2_CAP_DEVICE_CAPS
        ? capability.device_caps : capability.capabilities;
    unsigned int required = V4L2_CAP_VIDEO_M2M_MPLANE | V4L2_CAP_STREAMING;
    if (!known || (caps & required) != required) { errno = ENODEV; return -1; }
    return 0;
}

static int selection(int fd, enum v4l2_buf_type type, unsigned int target,
                     unsigned int width, unsigned int height) {
    struct v4l2_selection s;
    memset(&s, 0, sizeof(s));
    s.type = type; s.target = target; s.r.width = width; s.r.height = height;
    if (ioctl(fd, VIDIOC_S_SELECTION, &s) < 0) return -1;
    if (s.r.width != width || s.r.height != height || s.r.left || s.r.top) {
        errno = ERANGE; return -1;
    }
    return 0;
}

static int allocate(int fd, struct buffer *b, enum v4l2_buf_type type) {
    b->type = type;
    b->memory_type = V4L2_MEMORY_MMAP;
    struct v4l2_requestbuffers request = {.count = 1, .type = type, .memory = V4L2_MEMORY_MMAP};
    if (ioctl(fd, VIDIOC_REQBUFS, &request) < 0 || !request.count) return -1;
    struct v4l2_plane plane;
    memset(&plane, 0, sizeof(plane));
    struct v4l2_buffer query = {.type = type, .memory = V4L2_MEMORY_MMAP, .index = 0, .length = 1};
    query.m.planes = &plane;
    if (ioctl(fd, VIDIOC_QUERYBUF, &query) < 0) return -1;
    if (query.length != 1 || !plane.length || plane.length > LIMIT) {
        errno = EOVERFLOW; return -1;
    }
    b->length = plane.length;
    b->memory = mmap(NULL, plane.length, PROT_READ | PROT_WRITE, MAP_SHARED, fd, plane.m.mem_offset);
    if (b->memory == MAP_FAILED) { b->memory = NULL; return -1; }
    memset(b->memory, 0xa5, b->length);
    return 0;
}

static int queue(int fd, const struct buffer *b, unsigned int bytes) {
    if (bytes > b->length) { errno = EOVERFLOW; return -1; }
    struct v4l2_plane plane;
    memset(&plane, 0, sizeof(plane));
    plane.length = b->length; plane.bytesused = bytes;
    struct v4l2_buffer value = {.type = b->type, .memory = b->memory_type, .index = 0, .length = 1};
    value.m.planes = &plane;
    return ioctl(fd, VIDIOC_QBUF, &value);
}

static int start(int fd, struct buffer *b) {
    if (ioctl(fd, VIDIOC_STREAMON, &b->type) < 0) return -1;
    b->streaming = 1;
    return 0;
}

static int finish(int fd, struct buffer *b, unsigned int *flags, unsigned int *bytes) {
    double deadline = milliseconds() + 4000;
    for (;;) {
        struct v4l2_plane plane;
        memset(&plane, 0, sizeof(plane));
        struct v4l2_buffer value = {.type = b->type, .memory = b->memory_type, .length = 1};
        value.m.planes = &plane;
        if (ioctl(fd, VIDIOC_DQBUF, &value) == 0) {
            *flags = value.flags; *bytes = plane.bytesused;
            if (value.index != 0 || value.length != 1 || plane.bytesused > b->length) {
                errno = EOVERFLOW; return -1;
            }
            if (value.flags & V4L2_BUF_FLAG_ERROR) { errno = EIO; return -1; }
            return 0;
        }
        if (errno != EAGAIN && errno != EINTR) return -1;
        int remaining = (int)(deadline - milliseconds());
        if (remaining <= 0) { errno = ETIMEDOUT; return -1; }
        struct pollfd event = {.fd = fd, .events = POLLIN | POLLOUT | POLLERR};
        if (poll(&event, 1, remaining > 100 ? 100 : remaining) < 0 && errno != EINTR) return -1;
    }
}

static int write_all(int fd, const void *data, size_t count) {
    const unsigned char *p = data;
    while (count) {
        ssize_t size = write(fd, p, count);
        if (size < 0 && errno == EINTR) continue;
        if (size <= 0) return -1;
        p += size; count -= (size_t)size;
    }
    return 0;
}

int main(int argc, char **argv) {
    if (argc != 9) {
        fprintf(stderr, "Usage: native-media-probe mdp|mdp-rgb INPUT OUTPUT W H OUT_W OUT_H ROTATION\n");
        return 2;
    }
    int mdp = !strcmp(argv[1], "mdp") || !strcmp(argv[1], "mdp-rgb");
    int rgb = !strcmp(argv[1], "mdp-rgb");
    int jpeg = !strcmp(argv[1], "jpeg");
    int png = !strcmp(argv[1], "png");
    if (jpeg || png) {
        fprintf(stderr, "Hardware codec probes are disabled after a native kernel fault.\n");
        printf("{\"schema\":1,\"ok\":false,\"stage\":\"disabled_unsafe_codec_path\",\"errno\":%d}\n", ENOTSUP);
        return 2;
    }
    unsigned int w = number(argv[4]), h = number(argv[5]);
    unsigned int ow = number(argv[6]), oh = number(argv[7]), rotation = number(argv[8]);
    if (!mdp || !w || !h || !ow || !oh
        || w > 4096 || h > 8192 || ow > 4096 || oh > 8192
        || rotation > 270 || rotation % 90) return 2;
    unsigned int source_bpp = rgb ? 3 : 1, destination_bpp = 1;
    if ((uint64_t)ALIGN16(w * source_bpp) * ALIGN16(h) > LIMIT
        || (uint64_t)ALIGN16(ow * destination_bpp) * ALIGN16(oh) > LIMIT) return 2;
    alarm(12);
    int fd = -1, input = -1, output = -1, error = 0;
    unsigned char *data = NULL;
    struct buffer src = {0}, dst = {0};
    struct v4l2_format sf = {0}, df = {0};
    unsigned int captured_flags = 0, captured_bytes = 0, source_flags = 0, source_bytes = 0;
    unsigned int saved_bytes = 0;
    double began = milliseconds(), submitted = began, completed = began;
    const char *stage = "input";
    struct stat info;
    input = open(argv[2], O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (input < 0 || fstat(input, &info) < 0 || !S_ISREG(info.st_mode)
        || info.st_size <= 0 || info.st_size > LIMIT) goto failed;
    size_t length = (size_t)info.st_size;
    if (length != (size_t)w * h * source_bpp) { errno = EINVAL; goto failed; }
    data = malloc(length);
    if (!data) goto failed;
    size_t offset = 0;
    while (offset < length) {
        ssize_t n = read(input, data + offset, length - offset);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) goto failed;
        offset += (size_t)n;
    }
    close(input); input = -1;
    stage = "open";
    fd = open("/dev/video5", O_RDWR | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0) goto failed;
    stage = "driver_identity";
    if (identify_mdp(fd) < 0) goto failed;
    if (mdp) {
        stage = "rotation";
        struct v4l2_control control = {.id = V4L2_CID_ROTATE, .value = (int)rotation};
        if (ioctl(fd, VIDIOC_S_CTRL, &control) < 0) goto failed;
    }
    unsigned int src_pixel = rgb ? V4L2_PIX_FMT_RGB24 : v4l2_fourcc('Y','8',' ',' ');
    unsigned int dst_pixel = v4l2_fourcc('Y','8',' ',' ');
    stage = "source_format";
    if (format(fd, &sf, V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE, src_pixel,
        ALIGN16(w), ALIGN16(h), source_bpp) < 0) goto failed;
    stage = "capture_format";
    if (format(fd, &df, V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE, dst_pixel,
        ALIGN16(ow), ALIGN16(oh), destination_bpp) < 0) goto failed;
    if (mdp) {
        stage = "source_crop";
        if (selection(fd, V4L2_BUF_TYPE_VIDEO_OUTPUT, V4L2_SEL_TGT_CROP, w, h) < 0) goto failed;
        stage = "capture_compose";
        if (selection(fd, V4L2_BUF_TYPE_VIDEO_CAPTURE, V4L2_SEL_TGT_COMPOSE, ow, oh) < 0) goto failed;
    }
    stage = "source_buffer";
    if (allocate(fd, &src, V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE) < 0) goto failed;
    stage = "capture_buffer";
    if (allocate(fd, &dst, V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE) < 0) goto failed;
    memset(src.memory, 0, src.length);
    unsigned int stride = sf.fmt.pix_mp.plane_fmt[0].bytesperline;
    if (stride < w * source_bpp || (uint64_t)stride * h > src.length) { errno = EOVERFLOW; goto failed; }
    for (unsigned int y = 0; y < h; ++y)
        memcpy((unsigned char *)src.memory + y * stride, data + (size_t)y * w * source_bpp, w * source_bpp);
    free(data); data = NULL;
    stage = "queue_source";
    if (queue(fd, &src, sf.fmt.pix_mp.plane_fmt[0].sizeimage) < 0) goto failed;
    stage = "queue_capture";
    if (df.fmt.pix_mp.plane_fmt[0].sizeimage > dst.length) { errno = EOVERFLOW; goto failed; }
    if (queue(fd, &dst, 0) < 0) goto failed;
    submitted = milliseconds();
    stage = "stream_source";
    if (start(fd, &src) < 0) goto failed;
    stage = "stream_capture";
    if (start(fd, &dst) < 0) goto failed;
    stage = "dequeue_capture";
    if (finish(fd, &dst, &captured_flags, &captured_bytes) < 0) goto failed;
    stage = "dequeue_source";
    if (finish(fd, &src, &source_flags, &source_bytes) < 0) goto failed;
    completed = milliseconds();
    stage = "output";
    saved_bytes = captured_bytes;
    if (!saved_bytes || saved_bytes > dst.length
        || (uint64_t)df.fmt.pix_mp.plane_fmt[0].bytesperline * oh > saved_bytes) {
        errno = EOVERFLOW; goto failed;
    }
    output = open(argv[3], O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0600);
    if (output < 0 || write_all(output, dst.memory, saved_bytes) < 0) goto failed;
    stage = "complete";
    goto cleanup;
failed:
    error = errno ? errno : EIO;
cleanup:
    if (src.streaming) ioctl(fd, VIDIOC_STREAMOFF, &src.type);
    if (dst.streaming) ioctl(fd, VIDIOC_STREAMOFF, &dst.type);
    if (fd >= 0) close(fd);
    if (src.memory) munmap(src.memory, src.length);
    if (dst.memory) munmap(dst.memory, dst.length);
    if (input >= 0) close(input);
    if (output >= 0) close(output);
    free(data);
    printf("{\"schema\":1,\"ok\":%s,\"pixel_validated\":false,\"stage\":\"%s\",\"errno\":%d,"
        "\"source_width\":%u,\"source_height\":%u,\"source_stride\":%u,"
        "\"capture_width\":%u,\"capture_height\":%u,\"capture_stride\":%u,"
        "\"capture_size\":%u,\"capture_fourcc\":%u,\"capture_flags\":%u,\"capture_bytesused\":%u,"
        "\"capture_file_bytes\":%u,\"capture_buffer_bytes\":%u,\"visible_width\":%u,\"visible_height\":%u,"
        "\"memory_type\":%u,\"setup_ms\":%.3f,\"processing_ms\":%.3f,\"total_ms\":%.3f}\n",
        error ? "false" : "true", stage, error,
        sf.fmt.pix_mp.width, sf.fmt.pix_mp.height, sf.fmt.pix_mp.plane_fmt[0].bytesperline,
        df.fmt.pix_mp.width, df.fmt.pix_mp.height, df.fmt.pix_mp.plane_fmt[0].bytesperline,
        df.fmt.pix_mp.plane_fmt[0].sizeimage, df.fmt.pix_mp.pixelformat, captured_flags, captured_bytes,
        saved_bytes, dst.length, ow, oh, src.memory_type, submitted - began,
        completed > submitted ? completed - submitted : 0.0, milliseconds() - began);
    return error ? 1 : 0;
}
