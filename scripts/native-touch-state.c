/* Read-only evdev state probe for the isolated Aidoku firmware guest.
 * It queries current key/absolute/slot state and never reads the event queue.
 */
#define _GNU_SOURCE
#include <dirent.h>
#include <fcntl.h>
#include <linux/input.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <time.h>
#include <unistd.h>

static void quoted(const char *text) {
    putchar('"');
    for (; *text; ++text) {
        if (*text == '"' || *text == '\\') putchar('\\');
        if ((unsigned char)*text < 32) printf("\\u%04x", (unsigned char)*text);
        else putchar(*text);
    }
    putchar('"');
}

static int bit(const unsigned char *bits, unsigned value) {
    return !!(bits[value / 8] & (1u << (value % 8)));
}

int main(void) {
    char marker[80] = {0};
    int identity = open("/var/local/aidoku-emulator-id", O_RDONLY | O_CLOEXEC);
    if (identity < 0) return 2;
    ssize_t size = read(identity, marker, sizeof(marker) - 1);
    close(identity);
    if (size < 0) return 2;
    while (size && (marker[size - 1] == '\n' || marker[size - 1] == '\r')) marker[--size] = 0;
    if (strcmp(marker, "aidoku-pw6-5201") != 0) return 2;
    struct timespec time;
    clock_gettime(CLOCK_MONOTONIC, &time);
    printf("{\"guest_monotonic\":%.9f,\"read_only_no_event_consumption\":true,\"devices\":[", (double)time.tv_sec + (double)time.tv_nsec / 1000000000.0);
    DIR *directory = opendir("/dev/input");
    if (!directory) return 2;
    struct dirent *entry;
    int found = 0;
    while ((entry = readdir(directory))) {
        if (strncmp(entry->d_name, "event", 5) != 0) continue;
        char path[300], name[256] = {0};
        snprintf(path, sizeof(path), "/dev/input/%s", entry->d_name);
        int fd = open(path, O_RDONLY | O_NONBLOCK | O_CLOEXEC);
        if (fd < 0) continue;
        struct input_absinfo x = {0}, y = {0}, mt_x = {0}, mt_y = {0}, tracking = {0}, slot = {0};
        bool absolute = ioctl(fd, EVIOCGABS(ABS_X), &x) == 0 && ioctl(fd, EVIOCGABS(ABS_Y), &y) == 0;
        bool multitouch = ioctl(fd, EVIOCGABS(ABS_MT_POSITION_X), &mt_x) == 0 && ioctl(fd, EVIOCGABS(ABS_MT_POSITION_Y), &mt_y) == 0;
        if (!absolute && !multitouch) { close(fd); continue; }
        ioctl(fd, EVIOCGNAME(sizeof(name)), name);
        bool has_tracking = ioctl(fd, EVIOCGABS(ABS_MT_TRACKING_ID), &tracking) == 0;
        bool has_slots = ioctl(fd, EVIOCGABS(ABS_MT_SLOT), &slot) == 0;
        unsigned char keys[(KEY_MAX + 8) / 8] = {0};
        bool has_keys = ioctl(fd, EVIOCGKEY(sizeof(keys)), keys) >= 0;
        if (found++) putchar(',');
        printf("{\"path\":"); quoted(path); printf(",\"name\":"); quoted(name);
        printf(",\"key_query_available\":%s,\"btn_touch\":%d,\"btn_left\":%d,\"absolute\":%s,\"x\":%d,\"y\":%d,\"multitouch\":%s,\"mt_x\":%d,\"mt_y\":%d,\"has_tracking\":%s,\"tracking_id\":%d,\"has_slots\":%s,\"current_slot\":%d,\"maximum_slot\":%d,\"slot_tracking_ids\":[",
            has_keys ? "true" : "false", bit(keys, BTN_TOUCH), bit(keys, BTN_LEFT), absolute ? "true" : "false", x.value, y.value,
            multitouch ? "true" : "false", mt_x.value, mt_y.value, has_tracking ? "true" : "false", tracking.value,
            has_slots ? "true" : "false", slot.value, slot.maximum);
        int slots[33] = { ABS_MT_TRACKING_ID };
        int count = has_slots ? slot.maximum + 1 : 0;
        if (count > 32) count = 32;
        if (count > 0 && ioctl(fd, EVIOCGMTSLOTS((count + 1) * sizeof(int)), slots) == 0) {
            for (int index = 0; index < count; ++index) printf("%s%d", index ? "," : "", slots[index + 1]);
        }
        printf("]}");
        close(fd);
    }
    closedir(directory);
    printf("]}\n");
    return found ? 0 : 1;
}
