#include <signal.h>
#include <stdlib.h>
#include <string.h>

static unsigned char frame[32 * 42];
static void *(*volatile fill)(void *, int, size_t) = memset;

int main(void) {
    for (int row = 0; row < 42; ++row) fill(frame + 32 * row, 255, 32);
    raise(SIGURG);
    for (int row = 0; row < 42; ++row) fill(frame + 32 * row, 255, 32);
    for (int row = 4; row < 34; ++row) fill(frame + 32 * row, 255, 32);
    raise(SIGURG);
    for (int row = 0; row < 42; ++row) fill(frame + 32 * row, 255, 32);
    return frame[0] != 255;
}
