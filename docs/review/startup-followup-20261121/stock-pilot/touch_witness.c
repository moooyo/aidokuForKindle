#define _GNU_SOURCE
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <limits.h>
#include <linux/input.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <time.h>
#include <unistd.h>

enum {
    PERIOD_US = 12000,
    DEFAULT_WINDOW_MS = 12000,
    MAX_SLOTS = 32,
    MAX_SAMPLE_GAP_US = 250000
};

#define WORD_BITS (8u * (unsigned)sizeof(unsigned long))
#define BIT_ARRAY_SIZE(max_code) (((unsigned)(max_code) + WORD_BITS) / WORD_BITS)

struct sample {
    uint64_t before;
    uint64_t after;
    int btn_touch_known;
    int btn_touch;
    int active_slots;
    int tracking_known;
    int tracking_id;
};

struct witness {
    int phase;
    int saw_idle_before_touch;
    int have_last;
    int last_contact;
    int last_tracking_known;
    int last_tracking_id;
    uint64_t last_before;
    uint64_t last_after;
    uint64_t down_before;
    uint64_t down_after;
    uint64_t up_before;
    uint64_t up_after;
    unsigned samples;
};

struct device_caps {
    int has_ev_abs;
    int has_abs_x;
    int has_abs_y;
    int has_abs_slot;
    int has_abs_tracking;
    int has_ev_key;
    int has_btn_touch;
    struct input_absinfo mt_x;
    struct input_absinfo mt_y;
    struct input_absinfo mt_slot;
    struct input_absinfo mt_tracking;
};

static uint64_t clock_us(clockid_t clock_id) {
    struct timespec value;
    if (clock_gettime(clock_id, &value) != 0) return 0;
    return (uint64_t)value.tv_sec * 1000000u + (uint64_t)value.tv_nsec / 1000u;
}

static int wait_until(uint64_t deadline_us) {
    struct timespec deadline = {
        (time_t)(deadline_us / 1000000u),
        (long)((deadline_us % 1000000u) * 1000u)
    };
    int status = 0;
    do { status = clock_nanosleep(CLOCK_BOOTTIME, TIMER_ABSTIME, &deadline, NULL); } while (status == EINTR);
    return status;
}

static int read_window_ms(const char *text, uint64_t *window_us) {
    char *end = NULL;
    errno = 0;
    unsigned long value = strtoul(text, &end, 10);
    if (errno || !end || *end || value < 500 || value > 120000) return 0;
    *window_us = (uint64_t)value * 1000u;
    return 1;
}

static int bit_set(const unsigned long *bits, unsigned bit) {
    return (bits[bit / WORD_BITS] >> (bit % WORD_BITS)) & 1u;
}

static int classify_contact(const struct sample *sample) {
    if (sample->active_slots < 0 || sample->active_slots > MAX_SLOTS) return -1;
    if (sample->active_slots > 1) return -1;
    if (sample->active_slots == 1) {
        if (sample->tracking_known && sample->tracking_id < 0) return -1;
        if (sample->btn_touch_known && !sample->btn_touch) return -1;
        return 1;
    }
    if (sample->btn_touch_known && sample->btn_touch) return -1;
    if (sample->tracking_known && sample->tracking_id >= 0) return -1;
    return 0;
}

static int witness_step(struct witness *witness, const struct sample *sample, const char **reason) {
    if (sample->before == 0 || sample->after == 0 || sample->before > sample->after) {
        *reason = "sample_clock";
        return 0;
    }
    if (witness->have_last &&
        (sample->before < witness->last_before || sample->after < witness->last_after || sample->before < witness->last_after)) {
        *reason = "sample_overlap";
        return 0;
    }
    if (witness->have_last && sample->before - witness->last_before > MAX_SAMPLE_GAP_US) {
        *reason = "sampling_gap";
        return 0;
    }
    int contact = classify_contact(sample);
    if (contact < 0) {
        *reason = "ambiguous_contact";
        return 0;
    }
    witness->samples++;
    if (!witness->have_last) {
        if (contact) {
            *reason = "missing_idle_before_touch";
            return 0;
        }
        witness->saw_idle_before_touch = 1;
        witness->last_contact = contact;
        witness->last_tracking_known = sample->tracking_known;
        witness->last_tracking_id = sample->tracking_id;
        witness->last_before = sample->before;
        witness->last_after = sample->after;
        witness->have_last = 1;
        return 1;
    }
    if (witness->phase == 2 && contact) {
        *reason = "multiple_contacts";
        return 0;
    }
    if (!contact && witness->phase == 0) witness->saw_idle_before_touch = 1;
    if (!witness->last_contact && contact) {
        if (!witness->saw_idle_before_touch) {
            *reason = "missing_idle_before_touch";
            return 0;
        }
        witness->down_before = witness->last_before;
        witness->down_after = sample->after;
        if (witness->down_before > witness->down_after) {
            *reason = "invalid_down_bracket";
            return 0;
        }
        witness->phase = 1;
    } else if (witness->last_contact && !contact) {
        witness->up_before = witness->last_before;
        witness->up_after = sample->after;
        if (witness->up_before > witness->up_after || witness->down_after > witness->up_after) {
            *reason = "invalid_up_bracket";
            return 0;
        }
        witness->phase = 2;
    } else if (witness->last_contact && contact) {
        if (witness->last_tracking_known && sample->tracking_known &&
            witness->last_tracking_id != sample->tracking_id) {
            *reason = "tracking_switch";
            return 0;
        }
    }
    witness->last_contact = contact;
    witness->last_tracking_known = sample->tracking_known;
    witness->last_tracking_id = sample->tracking_id;
    witness->last_before = sample->before;
    witness->last_after = sample->after;
    return 1;
}

static int validate_caps(const struct device_caps *caps, int *slots, int *btn_touch_seen, const char **reason) {
    if (!caps->has_ev_abs) {
        if (reason) *reason = "missing_ev_abs";
        return 0;
    }
    if (!caps->has_abs_x || !caps->has_abs_y || !caps->has_abs_slot || !caps->has_abs_tracking) {
        if (reason) *reason = "missing_abs_capability";
        return 0;
    }
    if (caps->mt_x.maximum <= caps->mt_x.minimum) {
        if (reason) *reason = "invalid_mt_x_axis";
        return 0;
    }
    if (caps->mt_y.maximum <= caps->mt_y.minimum) {
        if (reason) *reason = "invalid_mt_y_axis";
        return 0;
    }
    if (caps->mt_slot.minimum != 0 || caps->mt_slot.maximum < 0 || caps->mt_slot.maximum + 1 > MAX_SLOTS) {
        if (reason) *reason = "invalid_mt_slot_axis";
        return 0;
    }
    if (caps->mt_tracking.maximum <= caps->mt_tracking.minimum) {
        if (reason) *reason = "invalid_mt_tracking_axis";
        return 0;
    }
    *slots = caps->mt_slot.maximum + 1;
    *btn_touch_seen = caps->has_ev_key && caps->has_btn_touch;
    return 1;
}

static int validate_touch_device(int descriptor, int *slots, int *btn_touch_seen) {
    unsigned long ev_bits[BIT_ARRAY_SIZE(EV_MAX)] = {0};
    unsigned long abs_bits[BIT_ARRAY_SIZE(ABS_MAX)] = {0};
    unsigned long key_bits[BIT_ARRAY_SIZE(KEY_MAX)] = {0};
    struct device_caps caps = {0};
    const char *reason = NULL;
    if (ioctl(descriptor, EVIOCGBIT(0, sizeof(ev_bits)), ev_bits) < 0) return 0;
    caps.has_ev_abs = bit_set(ev_bits, EV_ABS);
    caps.has_ev_key = bit_set(ev_bits, EV_KEY);
    if (!caps.has_ev_abs) return 0;
    if (ioctl(descriptor, EVIOCGBIT(EV_ABS, sizeof(abs_bits)), abs_bits) < 0) return 0;
    caps.has_abs_x = bit_set(abs_bits, ABS_MT_POSITION_X);
    caps.has_abs_y = bit_set(abs_bits, ABS_MT_POSITION_Y);
    caps.has_abs_slot = bit_set(abs_bits, ABS_MT_SLOT);
    caps.has_abs_tracking = bit_set(abs_bits, ABS_MT_TRACKING_ID);
    if (caps.has_ev_key) {
        if (ioctl(descriptor, EVIOCGBIT(EV_KEY, sizeof(key_bits)), key_bits) < 0) return 0;
        caps.has_btn_touch = bit_set(key_bits, BTN_TOUCH);
    }
    if (!caps.has_abs_x || !caps.has_abs_y || !caps.has_abs_slot || !caps.has_abs_tracking) return 0;
    if (ioctl(descriptor, EVIOCGABS(ABS_MT_POSITION_X), &caps.mt_x) != 0 ||
        ioctl(descriptor, EVIOCGABS(ABS_MT_POSITION_Y), &caps.mt_y) != 0 ||
        ioctl(descriptor, EVIOCGABS(ABS_MT_SLOT), &caps.mt_slot) != 0 ||
        ioctl(descriptor, EVIOCGABS(ABS_MT_TRACKING_ID), &caps.mt_tracking) != 0)
        return 0;
    return validate_caps(&caps, slots, btn_touch_seen, &reason);
}

static int sample_device(int descriptor, int slots, int btn_touch_seen, struct sample *sample) {
    int buffer[MAX_SLOTS + 1];
    unsigned long keys[BIT_ARRAY_SIZE(KEY_MAX)] = {0};
    sample->before = clock_us(CLOCK_BOOTTIME);
    if (!sample->before) return -1;
    int key_status = -1;
    if (btn_touch_seen) {
        key_status = ioctl(descriptor, EVIOCGKEY(sizeof(keys)), keys);
        if (key_status < 0) return -1;
    }
    buffer[0] = ABS_MT_TRACKING_ID;
    if (ioctl(descriptor, EVIOCGMTSLOTS((int)((slots + 1) * sizeof(int))), buffer) != 0) return -1;
    sample->after = clock_us(CLOCK_BOOTTIME);
    if (!sample->after) return -1;
    sample->btn_touch_known = btn_touch_seen && key_status >= 0;
    sample->btn_touch = sample->btn_touch_known ? bit_set(keys, BTN_TOUCH) : 0;
    sample->active_slots = 0;
    sample->tracking_known = 1;
    sample->tracking_id = -1;
    for (int index = 0; index < slots; ++index) {
        if (buffer[index + 1] >= 0) {
            sample->active_slots++;
            if (sample->active_slots == 1) sample->tracking_id = buffer[index + 1];
        }
    }
    if (sample->active_slots != 1) sample->tracking_id = -1;
    return 0;
}

static int sample_fixture(FILE *fixture, struct sample *sample) {
    long long before = 0, after = 0, tracking = -1;
    int btn_touch = 0, active_slots = 0;
    char line[256];
    for (;;) {
        if (!fgets(line, sizeof(line), fixture)) return feof(fixture) ? 1 : -1;
        if (line[0] == '#' || line[0] == '\n' || line[0] == '\r') continue;
        int fields = sscanf(line, "%lld %lld %d %d %lld", &before, &after, &btn_touch, &active_slots, &tracking);
        if (fields != 4 && fields != 5) return -1;
        if (before <= 0 || after <= 0 || (btn_touch != -1 && btn_touch != 0 && btn_touch != 1)) return -1;
        if (fields == 5 && (tracking < INT_MIN || tracking > INT_MAX)) return -1;
        sample->before = (uint64_t)before;
        sample->after = (uint64_t)after;
        sample->btn_touch_known = btn_touch >= 0;
        sample->btn_touch = btn_touch > 0;
        sample->active_slots = active_slots;
        sample->tracking_known = fields == 5;
        sample->tracking_id = fields == 5 ? (int)tracking : -1;
        return 0;
    }
}

static int print_result(const char *status, uint64_t start, uint64_t end, const struct witness *witness) {
    printf("ATW1 %s %" PRIu64 " %" PRIu64 " %" PRIu64 " %" PRIu64 " %" PRIu64 " %" PRIu64 " %u\n",
           status, start, end, witness->down_before, witness->down_after,
           witness->up_before, witness->up_after, witness->samples);
    return ferror(stdout) ? 2 : 0;
}

static int open_watch_device(const char *selector, int *descriptor, int *slots, int *btn_touch_seen, const char **reason) {
    *descriptor = -1;
    *slots = 0;
    *btn_touch_seen = 0;
    if (!strcmp(selector, "auto")) {
        DIR *folder = opendir("/dev/input");
        if (!folder) {
            *reason = "open_input_dir";
            return 2;
        }
        struct dirent *entry;
        int found = 0;
        while ((entry = readdir(folder)) != NULL) {
            if (strncmp(entry->d_name, "event", 5) != 0) continue;
            char path[PATH_MAX];
            if ((size_t)snprintf(path, sizeof(path), "/dev/input/%s", entry->d_name) >= sizeof(path)) continue;
            int fd = open(path, O_RDONLY | O_NONBLOCK | O_CLOEXEC);
            if (fd < 0) continue;
            int candidate_slots = 0;
            int candidate_btn_touch_seen = 0;
            if (!validate_touch_device(fd, &candidate_slots, &candidate_btn_touch_seen)) {
                close(fd);
                continue;
            }
            found++;
            if (found == 1) {
                *descriptor = fd;
                *slots = candidate_slots;
                *btn_touch_seen = candidate_btn_touch_seen;
            } else {
                close(fd);
                close(*descriptor);
                closedir(folder);
                *descriptor = -1;
                *slots = 0;
                *reason = "ambiguous_multitouch_device";
                return 1;
            }
        }
        closedir(folder);
        if (!found) {
            *reason = "no_multitouch_device";
            return 1;
        }
        return 0;
    }
    int fd = open(selector, O_RDONLY | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0) {
        *reason = "open_device";
        return 2;
    }
    int candidate_slots = 0;
    int candidate_btn_touch_seen = 0;
    if (!validate_touch_device(fd, &candidate_slots, &candidate_btn_touch_seen)) {
        close(fd);
        *reason = "unsupported_multitouch_device";
        return 1;
    }
    *descriptor = fd;
    *slots = candidate_slots;
    *btn_touch_seen = candidate_btn_touch_seen;
    return 0;
}

static int run_device_watch(const char *selector, uint64_t window_us) {
    int descriptor = -1;
    int slots = 0;
    int btn_touch_seen = 0;
    const char *reason = NULL;
    int open_status = open_watch_device(selector, &descriptor, &slots, &btn_touch_seen, &reason);
    if (open_status != 0) {
        if (open_status == 1) fprintf(stderr, "refused %s\n", reason ? reason : "watch_device");
        else fprintf(stderr, "touch-witness: %s\n", reason ? reason : "watch_device");
        return open_status;
    }
    const uint64_t start = clock_us(CLOCK_BOOTTIME);
    if (!start) {
        close(descriptor);
        fprintf(stderr, "touch-witness: clock\n");
        return 2;
    }
    const uint64_t stop = start + window_us;
    uint64_t deadline = start;
    struct witness witness = {0};
    uint64_t end = start;
    int ready_emitted = 0;
    for (;;) {
        if (wait_until(deadline) != 0) {
            close(descriptor);
            fprintf(stderr, "touch-witness: wait\n");
            return 2;
        }
        struct sample sample = {0};
        if (sample_device(descriptor, slots, btn_touch_seen, &sample) != 0) {
            close(descriptor);
            fprintf(stderr, "touch-witness: sample\n");
            return 2;
        }
        if (!ready_emitted) {
            fputs("READY\n", stderr);
            fflush(stderr);
            ready_emitted = 1;
        }
        if (!witness_step(&witness, &sample, &reason)) {
            close(descriptor);
            fprintf(stderr, "refused %s\n", reason ? reason : "touch_contract");
            return 1;
        }
        end = sample.after;
        if (sample.after >= stop) break;
        deadline += PERIOD_US;
        if (deadline > stop) deadline = stop;
    }
    close(descriptor);
    if (!witness.samples) {
        fprintf(stderr, "refused no_samples\n");
        return 1;
    }
    if (witness.phase == 0) return print_result("TIMEOUT", start, end, &witness);
    if (witness.phase == 1) {
        fprintf(stderr, "refused no_release\n");
        return 1;
    }
    return print_result("SINGLE", start, end, &witness);
}

static int run_fixture_watch(const char *fixture_path, uint64_t window_us) {
    FILE *fixture = fopen(fixture_path, "rb");
    if (!fixture) {
        fprintf(stderr, "touch-witness: open_fixture\n");
        return 2;
    }
    struct witness witness = {0};
    const char *reason = NULL;
    uint64_t start = 0, deadline = 0, end = 0;
    int ready_emitted = 0;
    for (;;) {
        struct sample sample = {0};
        int status = sample_fixture(fixture, &sample);
        if (status == 1) break;
        if (status != 0) {
            fclose(fixture);
            fprintf(stderr, "touch-witness: invalid_fixture\n");
            return 2;
        }
        if (!start) {
            start = sample.before;
            deadline = start + window_us;
        }
        if (sample.before > deadline) break;
        if (!ready_emitted) {
            fputs("READY\n", stderr);
            fflush(stderr);
            ready_emitted = 1;
        }
        if (!witness_step(&witness, &sample, &reason)) {
            fclose(fixture);
            fprintf(stderr, "refused %s\n", reason ? reason : "touch_contract");
            return 1;
        }
        end = sample.after;
    }
    fclose(fixture);
    if (!witness.samples) {
        fprintf(stderr, "refused no_samples\n");
        return 1;
    }
    if (witness.phase == 0) return print_result("TIMEOUT", start, end, &witness);
    if (witness.phase == 1) {
        fprintf(stderr, "refused no_release\n");
        return 1;
    }
    return print_result("SINGLE", start, end, &witness);
}

static int parse_bool01(long long value, int *out) {
    if (value != 0 && value != 1) return 0;
    *out = (int)value;
    return 1;
}

static int run_validate_fixture(const char *fixture_path) {
    FILE *fixture = fopen(fixture_path, "rb");
    if (!fixture) {
        fprintf(stderr, "touch-witness: open_fixture\n");
        return 2;
    }
    char line[512];
    int cases = 0;
    while (fgets(line, sizeof(line), fixture)) {
        if (line[0] == '#' || line[0] == '\n' || line[0] == '\r') continue;
        long long raw[15] = {0};
        int fields = sscanf(line, "%lld %lld %lld %lld %lld %lld %lld %lld %lld %lld %lld %lld %lld %lld %lld",
                            &raw[0], &raw[1], &raw[2], &raw[3], &raw[4], &raw[5], &raw[6], &raw[7],
                            &raw[8], &raw[9], &raw[10], &raw[11], &raw[12], &raw[13], &raw[14]);
        if (fields != 15) {
            fclose(fixture);
            fprintf(stderr, "touch-witness: invalid_validate_fixture\n");
            return 2;
        }
        struct device_caps caps = {0};
        if (!parse_bool01(raw[0], &caps.has_ev_abs) ||
            !parse_bool01(raw[1], &caps.has_ev_key) ||
            !parse_bool01(raw[2], &caps.has_abs_x) ||
            !parse_bool01(raw[3], &caps.has_abs_y) ||
            !parse_bool01(raw[4], &caps.has_abs_slot) ||
            !parse_bool01(raw[5], &caps.has_abs_tracking) ||
            !parse_bool01(raw[6], &caps.has_btn_touch)) {
            fclose(fixture);
            fprintf(stderr, "touch-witness: invalid_validate_fixture\n");
            return 2;
        }
        caps.mt_x.minimum = (int)raw[7];
        caps.mt_x.maximum = (int)raw[8];
        caps.mt_y.minimum = (int)raw[9];
        caps.mt_y.maximum = (int)raw[10];
        caps.mt_slot.minimum = (int)raw[11];
        caps.mt_slot.maximum = (int)raw[12];
        caps.mt_tracking.minimum = (int)raw[13];
        caps.mt_tracking.maximum = (int)raw[14];
        int slots = 0;
        int btn_touch_seen = 0;
        const char *reason = NULL;
        int ok = validate_caps(&caps, &slots, &btn_touch_seen, &reason);
        if (ok) printf("VC1 VALID %d %d\n", slots, btn_touch_seen);
        else printf("VC1 INVALID %s\n", reason ? reason : "invalid_caps");
        if (ferror(stdout)) {
            fclose(fixture);
            return 2;
        }
        cases++;
    }
    fclose(fixture);
    if (!cases) {
        fprintf(stderr, "refused no_validate_cases\n");
        return 1;
    }
    return 0;
}

int main(int argc, char **argv) {
    uint64_t window_us = (uint64_t)DEFAULT_WINDOW_MS * 1000u;
    if (argc == 2 && !strcmp(argv[1], "clock")) {
        uint64_t now = clock_us(CLOCK_BOOTTIME);
        if (!now) {
            fprintf(stderr, "touch-witness: clock\n");
            return 2;
        }
        printf("%" PRIu64 "\n", now);
        return 0;
    }
    if (argc < 2 || argc > 4) {
        fprintf(stderr, "touch-witness: usage: touch-witness clock | watch auto|DEVICE [WINDOW_MS] | fixture FILE [WINDOW_MS] | validate-fixture FILE\n");
        return 2;
    }
    if (argc == 3 && !strcmp(argv[1], "validate-fixture")) return run_validate_fixture(argv[2]);
    if (argc < 3 || argc > 4) {
        fprintf(stderr, "touch-witness: usage: touch-witness clock | watch auto|DEVICE [WINDOW_MS] | fixture FILE [WINDOW_MS] | validate-fixture FILE\n");
        return 2;
    }
    if (argc == 4 && !read_window_ms(argv[3], &window_us)) {
        fprintf(stderr, "refused window_ms\n");
        return 1;
    }
    if (!strcmp(argv[1], "watch")) return run_device_watch(argv[2], window_us);
    if (!strcmp(argv[1], "fixture")) return run_fixture_watch(argv[2], window_us);
    fprintf(stderr, "refused operation\n");
    return 1;
}
