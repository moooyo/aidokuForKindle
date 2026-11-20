#include <arm_neon.h>
#include <stddef.h>
#include <stdint.h>

/* Lanes are adjacent output columns, never taps of the same column. The
 * caller supplies consecutive source rows and disables FP contraction. */
void aidoku_cover_weighted_rows(float *restrict out, const uint8_t *restrict src,
                               size_t width, const float *restrict weights, size_t taps) {
    if (taps == 0) return;
    size_t x = 0;
    for (; width - x >= 8; x += 8) {
        float32x4_t lo = vld1q_f32(out + x);
        float32x4_t hi = vld1q_f32(out + x + 4);
        for (size_t t = 0; t < taps; ++t) {
            uint16x8_t u = vmovl_u8(vld1_u8(src + t * width + x));
            float32x4_t a = vcvtq_f32_u32(vmovl_u16(vget_low_u16(u)));
            float32x4_t b = vcvtq_f32_u32(vmovl_u16(vget_high_u16(u)));
            lo = vaddq_f32(lo, vmulq_n_f32(a, weights[t]));
            hi = vaddq_f32(hi, vmulq_n_f32(b, weights[t]));
        }
        vst1q_f32(out + x, lo);
        vst1q_f32(out + x + 4, hi);
    }
    for (; x < width; ++x) {
        float sum = out[x];
        for (size_t t = 0; t < taps; ++t) sum += (float)src[t * width + x] * weights[t];
        out[x] = sum;
    }
}

static inline uint16x4_t cover_luma4(uint16x4_t red, uint16x4_t green, uint16x4_t blue) {
    uint32x4_t n = vmull_n_u16(red, 1063);
    n = vmlal_n_u16(n, green, 3576);
    n = vmlal_n_u16(n, blue, 361);
    /* image 0.25.10's (2126R + 7152G + 722B)/10000 simplifies to n/5000.
     * n <= 1275000. The reciprocal's maximum positive error, measured in
     * quotient units, is 1275000*(429497*5000-2^31)/(5000*2^31) < 1/5000.
     * The high doubling multiply therefore gives the exact integer floor. */
    int32x4_t q = vqdmulhq_n_s32(vreinterpretq_s32_u32(n), 429497);
    return vmovn_u32(vreinterpretq_u32_s32(q));
}

void aidoku_cover_rgb8_luma(uint8_t *restrict out, const uint8_t *restrict src, size_t count) {
    size_t x = 0;
    for (; count - x >= 8; x += 8) {
        uint8x8x3_t rgb = vld3_u8(src + 3 * x);
        uint16x8_t r = vmovl_u8(rgb.val[0]);
        uint16x8_t g = vmovl_u8(rgb.val[1]);
        uint16x8_t b = vmovl_u8(rgb.val[2]);
        uint16x4_t lo = cover_luma4(vget_low_u16(r), vget_low_u16(g), vget_low_u16(b));
        uint16x4_t hi = cover_luma4(vget_high_u16(r), vget_high_u16(g), vget_high_u16(b));
        vst1_u8(out + x, vmovn_u16(vcombine_u16(lo, hi)));
    }
    for (; x < count; ++x) {
        const uint8_t *p = src + 3 * x;
        out[x] = (uint8_t)((2126u * p[0] + 7152u * p[1] + 722u * p[2]) / 10000u);
    }
}
