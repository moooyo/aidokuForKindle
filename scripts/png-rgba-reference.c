/* Host-side PNG oracle for physical codec qualification.
 * Build and run only on the designated verification worker.
 * Unlike a generic image conversion, this explicitly strips 16-bit samples
 * to their high byte and performs no gamma or color-profile transformation.
 */
#include <png.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

struct reference {
    FILE *input;
    FILE *output;
    png_structp png;
    png_infop info;
    png_bytep pixels;
    png_bytepp rows;
};

int main(int argc, char **argv) {
    if (argc != 3) {
        fprintf(stderr, "usage: png-rgba-reference INPUT.png OUTPUT.rgba\n");
        return 2;
    }
    struct reference *state = calloc(1, sizeof(*state));
    if (!state) return 1;
    int result = 1;
    state->input = fopen(argv[1], "rb");
    state->png = png_create_read_struct(PNG_LIBPNG_VER_STRING, NULL, NULL, NULL);
    if (!state->input || !state->png) goto cleanup;
    state->info = png_create_info_struct(state->png);
    if (!state->info || setjmp(png_jmpbuf(state->png))) goto cleanup;
    png_init_io(state->png, state->input);
    png_read_info(state->png, state->info);
    png_uint_32 width, height;
    int depth, color, interlace, compression, filter;
    png_get_IHDR(state->png, state->info, &width, &height, &depth, &color,
                 &interlace, &compression, &filter);
    if (!width || !height || width > 8192 || height > 8192
        || (uint64_t)width * height > 16U * 1024U * 1024U) goto cleanup;
    int transparency = png_get_valid(state->png, state->info, PNG_INFO_tRNS);
    if (depth == 16) png_set_strip_16(state->png);
    if (color == PNG_COLOR_TYPE_PALETTE) png_set_palette_to_rgb(state->png);
    if (color == PNG_COLOR_TYPE_GRAY && depth < 8)
        png_set_expand_gray_1_2_4_to_8(state->png);
    if (transparency) png_set_tRNS_to_alpha(state->png);
    if (color == PNG_COLOR_TYPE_GRAY || color == PNG_COLOR_TYPE_GRAY_ALPHA)
        png_set_gray_to_rgb(state->png);
    if (!(color & PNG_COLOR_MASK_ALPHA) && !transparency)
        png_set_add_alpha(state->png, 255, PNG_FILLER_AFTER);
    png_set_interlace_handling(state->png);
    png_read_update_info(state->png, state->info);
    size_t stride = (size_t)width * 4;
    if (png_get_rowbytes(state->png, state->info) != stride
        || png_get_bit_depth(state->png, state->info) != 8
        || png_get_channels(state->png, state->info) != 4) goto cleanup;
    size_t length = stride * height;
    state->pixels = malloc(length);
    state->rows = malloc((size_t)height * sizeof(*state->rows));
    if (!state->pixels || !state->rows) goto cleanup;
    for (png_uint_32 y = 0; y < height; ++y)
        state->rows[y] = state->pixels + (size_t)y * stride;
    png_read_image(state->png, state->rows);
    png_read_end(state->png, state->info);
    state->output = fopen(argv[2], "wb");
    if (!state->output || fwrite(state->pixels, 1, length, state->output) != length
        || fflush(state->output)) goto cleanup;
    printf("{\"width\":%u,\"height\":%u,\"source_bit_depth\":%d,"
           "\"source_color_type\":%d,\"interlace\":%d,\"bytes\":%zu,"
           "\"reference\":\"RGBA8; strip low bytes; no gamma transform\"}\n",
           width, height, depth, color, interlace, length);
    result = 0;
cleanup:
    if (state->png) png_destroy_read_struct(&state->png, &state->info, NULL);
    if (state->input) fclose(state->input);
    if (state->output && fclose(state->output)) result = 1;
    free(state->pixels);
    free(state->rows);
    free(state);
    return result;
}
