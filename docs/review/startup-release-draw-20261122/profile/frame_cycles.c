#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <linux/perf_event.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

#define RING_PAGES 64
#define SAMPLE_PERIOD 2000000

static int counter = -1, output = -1, leader;
static size_t page_size;
static struct perf_event_mmap_page *mapped;
static volatile sig_atomic_t phase;
static uint64_t start_before, start_after, end_before, end_after;
static struct sigaction previous;

static void stop(void) { _exit(87); }

static uint64_t now_ns(void) {
    struct timespec stamp;
    if (clock_gettime(CLOCK_MONOTONIC, &stamp) < 0) stop();
    return (uint64_t)stamp.tv_sec * 1000000000 + (uint64_t)stamp.tv_nsec;
}

static void gate(int number, siginfo_t *origin, void *context) {
    (void)context;
    if (number != SIGURG || origin->si_pid != leader || origin->si_code != SI_TKILL || syscall(SYS_gettid) != leader) stop();
    if (phase == 0) {
        if (ioctl(counter, PERF_EVENT_IOC_RESET, 0) != 0) stop();
        start_before = now_ns();
        if (ioctl(counter, PERF_EVENT_IOC_ENABLE, 0) != 0) stop();
        start_after = now_ns();
        phase = 1;
    } else if (phase == 1) {
        end_before = now_ns();
        if (ioctl(counter, PERF_EVENT_IOC_DISABLE, 0) != 0) stop();
        end_after = now_ns();
        phase = 2;
    } else stop();
}

static void copy_maps(int fd) {
    int maps = open("/proc/self/maps", O_RDONLY | O_CLOEXEC);
    if (maps < 0) stop();
    char buffer[4096];
    ssize_t count;
    while ((count = read(maps, buffer, sizeof(buffer))) > 0) {
        if (write(fd, buffer, (size_t)count) != count) stop();
    }
    if (count < 0 || close(maps) || close(fd)) stop();
}

__attribute__((constructor)) static void setup(void) {
    const char *path = getenv("AIDOKU_FRAME_CYCLES");
    if (!path) return;
    leader = getpid();
    if (syscall(SYS_gettid) != leader || !getenv("AIDOKU_FRAME_MAPS")) stop();
    output = open(path, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0600);
    int maps = open(getenv("AIDOKU_FRAME_MAPS"), O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0600);
    if (maps < 0 || output < 0) stop();
    copy_maps(maps);
    struct perf_event_attr attr = {0};
    attr.type = PERF_TYPE_HARDWARE;
    attr.size = sizeof(attr);
    attr.config = PERF_COUNT_HW_CPU_CYCLES;
    attr.sample_period = SAMPLE_PERIOD;
    attr.sample_type = PERF_SAMPLE_IP | PERF_SAMPLE_TID | PERF_SAMPLE_TIME | PERF_SAMPLE_PERIOD | PERF_SAMPLE_CALLCHAIN;
    attr.sample_max_stack = 127;
    attr.disabled = 1;
    attr.pinned = 1;
    attr.exclude_kernel = 1;
    attr.exclude_hv = 1;
    attr.exclude_callchain_kernel = 1;
    attr.use_clockid = 1;
    attr.clockid = CLOCK_MONOTONIC;
    counter = (int)syscall(SYS_perf_event_open, &attr, 0, -1, -1, PERF_FLAG_FD_CLOEXEC);
    page_size = (size_t)sysconf(_SC_PAGESIZE);
    if (counter < 0 || page_size != 4096) stop();
    mapped = mmap(NULL, page_size * (1 + RING_PAGES), PROT_READ | PROT_WRITE, MAP_SHARED, counter, 0);
    if (mapped == MAP_FAILED || mapped->data_offset != page_size || mapped->data_size != page_size * RING_PAGES) stop();
    struct sigaction handler = {0};
    handler.sa_sigaction = gate;
    handler.sa_flags = SA_SIGINFO;
    sigemptyset(&handler.sa_mask);
    if (sigaction(SIGURG, &handler, &previous) < 0 || (previous.sa_handler != SIG_DFL && previous.sa_handler != SIG_IGN)) stop();
}

__attribute__((destructor)) static void finish(void) {
    if (output < 0) return;
    if (phase != 2 || counter < 0 || sigaction(SIGURG, &previous, NULL) < 0) stop();
    uint64_t head = __atomic_load_n(&mapped->data_head, __ATOMIC_ACQUIRE);
    if (mapped->data_tail != 0 || head > mapped->data_size || !head) stop();
    uint64_t count;
    if (read(counter, &count, sizeof(count)) != sizeof(count)) stop();
    uint64_t header[] = {1, (uint64_t)leader, start_before, start_after, end_before, end_after, count, head, SAMPLE_PERIOD};
    if (write(output, header, sizeof(header)) != sizeof(header) ||
        write(output, (char *)mapped + page_size, head) != (ssize_t)head || close(output) < 0) stop();
    __atomic_store_n(&mapped->data_tail, head, __ATOMIC_RELEASE);
    if (munmap(mapped, page_size * (1 + RING_PAGES)) || close(counter)) stop();
}
