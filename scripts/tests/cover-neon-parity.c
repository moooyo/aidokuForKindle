/* Run from the checkout on test-env, with ZIG and QEMU_ARM set to the tools:
 * d=$(mktemp -d); abi='-target arm-linux-musleabihf -mcpu=cortex_a8'
 * fp='-ffp-contract=off -fno-fast-math -O3 -std=c11'
 * "$ZIG" cc $abi $fp -mfpu=neon -c src/imaging/cover_neon.c -o "$d/cover.o"
 * "$ZIG" cc $abi $fp -mfpu=vfpv3-d16 -fno-vectorize -fno-slp-vectorize -static \
 *   scripts/tests/cover-neon-parity.c "$d/cover.o" -lm -o "$d/parity"
 * "$QEMU_ARM" -cpu cortex-a7 "$d/parity"
 * The reference TU uses scalar VFP. No Kindle or private image is needed. */
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/auxv.h>

void aidoku_cover_weighted_rows(float *, const uint8_t *, size_t, const float *, size_t);
void aidoku_cover_rgb8_luma(uint8_t *, const uint8_t *, size_t);

static uint32_t seed = 42;
static uint32_t random_u32(void) { return seed = seed * 1664525u + 1013904223u; }
static uint8_t ref_luma(const uint8_t *p) {
    return (uint8_t)((2126u * p[0] + 7152u * p[1] + 722u * p[2]) / 10000u);
}

static int rgb_cube(void) {
    uint8_t pixels[3 * 256 + 8], out[256 + 8];
    for (unsigned r = 0; r < 256; ++r) {
        for (unsigned g = 0; g < 256; ++g) {
            size_t inoff = g % 8, outoff = r % 8;
            for (unsigned b = 0; b < 256; ++b) {
                uint8_t *p = pixels + inoff + 3 * b;
                p[0] = r; p[1] = g; p[2] = b;
            }
            aidoku_cover_rgb8_luma(out + outoff, pixels + inoff, 256);
            for (unsigned b = 0; b < 256; ++b) {
                if (out[outoff + b] != ref_luma(pixels + inoff + 3 * b)) return 0;
            }
        }
    }
    for (size_t n = 0; n < 128; ++n) {
        size_t inoff = random_u32() % 8, outoff = 1 + random_u32() % 7;
        memset(out, 0xa5, sizeof(out));
        for (size_t i = 0; i < 3 * n; ++i) pixels[inoff + i] = random_u32() >> 24;
        aidoku_cover_rgb8_luma(out + outoff, pixels + inoff, n);
        for (size_t i = 0; i < sizeof(out); ++i) {
            uint8_t expect = i < outoff || i - outoff >= n ? 0xa5 : ref_luma(pixels + inoff + 3 * (i - outoff));
            if (out[i] != expect) return 0;
        }
    }
    return 1;
}

static float sinc(float x) {
    float angle = 3.14159265358979323846f * x;
    return x == 0.0f ? 1.0f : sinf(angle) / angle;
}

static int weighted(size_t *observed) {
    for (unsigned z = 0; z < 1400; ++z) {
        size_t width = 1 + random_u32() % 4096, height = 2 + random_u32() % 3000;
        size_t out = 1 + random_u32() % 1000, y = random_u32() % out;
        if (z == 0) { width = 32768; height = 8; out = 170; y = 0; }
        if (z == 1) { width = 17; height = 32768; out = 1; y = 0; }
        float ratio = (float)height / (float)out, scale = fmaxf(ratio, 1.0f);
        float center = ((float)y + 0.5f) * ratio, support = 3.0f * scale;
        int first = (int)fmaxf(0, fminf((float)height - 1, floorf(center - support)));
        int end = (int)fmaxf(first + 1, fminf((float)height, ceilf(center + support)));
        size_t n = (size_t)(end - first), off = random_u32() % 32, dstoff = 1 + random_u32() % 7;
        uint8_t *raw = malloc(off + width * n);
        float *w = malloc(n * sizeof(*w)), *expect = malloc((width + 8) * sizeof(*expect));
        float *actual = malloc((width + 8) * sizeof(*actual));
        if (!raw || !w || !expect || !actual) return 0;
        uint8_t *p = raw + off;
        for (size_t i = 0; i < width * n; ++i) p[i] = random_u32() >> 24;
        float total = 0.0f;
        center -= 0.5f;
        for (size_t i = 0; i < n; ++i) {
            float v = ((float)(first + (int)i) - center) / scale;
            w[i] = fabsf(v) < 3.0f ? sinc(v) * sinc(v / 3.0f) : 0.0f;
            total += w[i];
        }
        for (size_t i = 0; i < n; ++i) w[i] /= total;
        for (size_t i = 0; i < width + 8; ++i) expect[i] = actual[i] = (float)(random_u32() >> 24) - 128.0f;
        for (size_t i = 0; i < n; ++i) {
            for (size_t x = 0; x < width; ++x) expect[dstoff + x] += (float)p[i * width + x] * w[i];
        }
        aidoku_cover_weighted_rows(actual + dstoff, p, width, w, 0);
        for (size_t t = 0; t < n;) {
            size_t k = 1 + random_u32() % 16;
            if (k > n - t) k = n - t;
            aidoku_cover_weighted_rows(actual + dstoff, p + t * width, width, w + t, k);
            t += k;
        }
        int match = memcmp(expect, actual, (width + 8) * sizeof(*expect)) == 0;
        free(actual); free(expect); free(w); free(raw);
        if (!match) return 0;
        *observed += width;
    }
    return 1;
}

int main(void) {
    if (!(getauxval(AT_HWCAP) & (1u << 12))) { fputs("NEON is required for this parity fixture\n", stderr); return 1; }
    size_t observed = 0;
    if (!rgb_cube()) { fputs("RGB8 luma differs\n", stderr); return 1; }
    if (!weighted(&observed)) { fputs("vertical accumulation differs\n", stderr); return 1; }
    printf("matched 16777216 RGB triples, 128 tails and 1400 rows (%zu f32 sums)\n", observed);
    return 0;
}
