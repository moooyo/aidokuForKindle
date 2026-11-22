#define main render_watch_main
#include "render_watch.c"
#undef main
#undef NDEBUG
#include <assert.h>

static void check_layout(void) {
    struct fb_var_screeninfo var = {.xres = WIDTH, .yres = HEIGHT, .bits_per_pixel = 8,
                                    .rotate = 1, .xres_virtual = STRIDE,
                                    .yres_virtual = 2 * HEIGHT, .yoffset = HEIGHT};
    struct fb_fix_screeninfo fix = {.type = FB_TYPE_PACKED_PIXELS, .line_length = STRIDE,
                                    .smem_len = 2 * STRIDE * HEIGHT};
    assert(supported(&var, &fix));
    var.rotate = 3;
    assert(supported(&var, &fix));
    var.rotate = 0;
    assert(!supported(&var, &fix));
    var.rotate = 1;
    var.yoffset = HEIGHT + 1;
    assert(!supported(&var, &fix));
    var.yoffset = 0;
    fix.line_length = WIDTH;
    assert(!supported(&var, &fix));
}

static void check_samples(unsigned turn) {
    struct rule rule = {.turn = turn};
    for (unsigned index = 0; index < POINTS; ++index)
        rule.target[index] = (unsigned char)((index * 17 + 19) % 251);
    assert(make_regions(&rule));
    unsigned char *map = calloc(2 * STRIDE * HEIGHT, 1);
    assert(map);
    for (unsigned index = 0; index < POINTS; ++index) {
        size_t offset = (OFFSET + index / COLUMNS * STEP) * (size_t)STRIDE +
                        OFFSET + index % COLUMNS * STEP;
        map[(size_t)STRIDE * HEIGHT + offset] = rule.target[index];
    }
    unsigned text_diff;
    int covers;
    sample(&rule, map, HEIGHT, &text_diff, &covers);
    assert(text_diff == 0 && covers);
    for (unsigned region = 1; region <= REGIONS; ++region) {
        unsigned index = 0;
        while (index < POINTS && rule.region[index] != region) ++index;
        assert(index < POINTS);
        size_t offset = (OFFSET + index / COLUMNS * STEP) * (size_t)STRIDE +
                        OFFSET + index % COLUMNS * STEP;
        unsigned char *pixel = map + (size_t)STRIDE * HEIGHT + offset;
        *pixel ^= 255;
        sample(&rule, map, HEIGHT, &text_diff, &covers);
        assert(text_diff == (region <= TEXT_REGIONS ? 1u << (region - 1) : 0));
        assert(covers == (region <= TEXT_REGIONS));
        *pixel ^= 255;
    }
    free(map);
}

static void check_state(void) {
    struct state state = {0};
    struct result result = {0};
    assert(advance(&state, 1000000, 1000100, 31, 0, &result) == 0);
    assert(advance(&state, 1120000, 1120300, 0, 0, &result) == 0);
    assert(advance(&state, 1240000, 1240100, 0, 1, &result) == 0);
    assert(advance(&state, 1360000, 1360400, 0, 1, &result) == 1);
    assert(result.last_negative == 1000000 && result.first_match == 1120300 &&
           result.confirmed == 1360400 && !result.first_cover && result.confirmed_cover);
    state = (struct state){0};
    assert(advance(&state, 1000000, 1000001, 0, 1, &result) == -2);
    state = (struct state){0};
    assert(advance(&state, 1000000, 1050001, 31, 0, &result) == -1);
    state = (struct state){0};
    assert(advance(&state, 1000000, 1000100, 31, 0, &result) == 0);
    assert(advance(&state, 1150101, 1150102, 0, 0, &result) == -1);
    state = (struct state){0};
    assert(advance(&state, 1000000, 1000100, 31, 0, &result) == 0);
    assert(advance(&state, 1120000, 1120100, 0, 0, &result) == 0);
    assert(advance(&state, 1240000, 1240100, 1, 0, &result) == 0);
    assert(advance(&state, 1360000, 1360100, 0, 1, &result) == 0);
    assert(advance(&state, 1480000, 1480100, 0, 1, &result) == 0);
    assert(advance(&state, 1600000, 1600100, 0, 1, &result) == 1);
    assert(result.last_negative == 1240000 && result.first_match == 1360100);
    state = (struct state){0};
    assert(advance(&state, 1000000, 1000100, 31, 0, &result) == 0);
    assert(advance(&state, 1100000, 1100100, 0, 0, &result) == 0);
    assert(advance(&state, 1190000, 1190100, 0, 0, &result) == 0);
    assert(advance(&state, 1280000, 1280100, 0, 0, &result) == -3);
}

int main(int argc, char **argv) {
    if (argc == 3 && !strcmp(argv[1], "mask")) {
        struct rule rule = {.turn = (unsigned)strtoul(argv[2], NULL, 10)};
        if (rule.turn != 0 && rule.turn != 180) return 2;
        for (unsigned index = 0; index < POINTS; ++index)
            rule.target[index] = (unsigned char)((index * 17 + 19) % 251);
        if (!make_regions(&rule) || fwrite(rule.region, 1, POINTS, stdout) != POINTS) return 2;
        return 0;
    }
    if (argc != 1) return 2;
    check_layout();
    check_samples(0);
    check_samples(180);
    check_state();
    puts("PASS");
    return 0;
}
