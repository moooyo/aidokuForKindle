#define main framewatch_main
#include "framewatch.c"
#undef main
#include <assert.h>

int main(void) {
    struct fb_var_screeninfo var = {.xres = WIDTH, .yres = HEIGHT, .bits_per_pixel = 8,
                                    .rotate = 1, .xres_virtual = STRIDE, .yres_virtual = 2 * HEIGHT,
                                    .yoffset = HEIGHT};
    struct fb_fix_screeninfo fix = {.type = FB_TYPE_PACKED_PIXELS, .line_length = STRIDE,
                                    .smem_len = 2 * STRIDE * HEIGHT};
    for (unsigned rotation = 1; rotation <= 3; rotation += 2) {
        var.rotate = rotation;
        assert(supported(&var, &fix));
    }
    var.rotate = 2;
    assert(!supported(&var, &fix));
    var.rotate = 1;
    var.bits_per_pixel = 4;
    assert(!supported(&var, &fix));
    var.bits_per_pixel = 8;
    var.yoffset = HEIGHT + 1;
    assert(!supported(&var, &fix));
    var.yoffset = HEIGHT;
    var.yres_virtual = HEIGHT;
    assert(!supported(&var, &fix));
    var.yoffset = 0;
    assert(supported(&var, &fix));
    var.yres_virtual = 2 * HEIGHT;
    fix.smem_len--;
    assert(!supported(&var, &fix));
    fix.smem_len++;
    var.xoffset = 1;
    assert(!supported(&var, &fix));
    unsigned char *map = malloc(fix.smem_len);
    unsigned char *full = malloc((size_t)WIDTH * HEIGHT);
    unsigned char samples[POINTS];
    assert(map && full);
    memset(map, 251, fix.smem_len);
    for (unsigned page = 0; page < 2; ++page) {
        for (size_t row = 0; row < HEIGHT; ++row)
            for (size_t column = 0; column < WIDTH; ++column)
                map[(page * HEIGHT + row) * STRIDE + column] = (page * 49 + row * 3 + column) % 251;
        read_pixels(map, page * HEIGHT, full, 1);
        read_pixels(map, page * HEIGHT, samples, 0);
        unsigned seen = 0;
        for (size_t row = OFFSET; row < HEIGHT; row += STEP)
            for (size_t column = OFFSET; column < WIDTH; column += STEP) {
                assert(samples[seen++] == (page * 49 + row * 3 + column) % 251);
                assert(full[row * WIDTH + column] == (page * 49 + row * 3 + column) % 251);
            }
        assert(seen == POINTS && full[WIDTH] == (page * 49 + 3) % 251);
    }
    free(full);
    free(map);
    return 0;
}
