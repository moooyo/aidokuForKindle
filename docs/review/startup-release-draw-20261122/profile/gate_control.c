#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <time.h>

static volatile uint64_t value;
static uint64_t now(void) {
    struct timespec time;
    return clock_gettime(CLOCK_MONOTONIC, &time) == 0 ? (uint64_t)time.tv_sec * 1000000000 + time.tv_nsec : 0;
}
__attribute__((noinline)) static void region_before(void) {
    for (unsigned index = 0; index < 40000000; index++) value = value * 7 + index;
}
__attribute__((noinline)) static void region_inside(void) {
    for (unsigned index = 0; index < 40000000; index++) value = value * 11 + index;
}
__attribute__((noinline)) static void region_after(void) {
    for (unsigned index = 0; index < 40000000; index++) value = value * 17 + index;
}
int main(void) {
    region_before();
    if (raise(SIGURG)) return 2;
    uint64_t begin = now();
    region_inside();
    uint64_t end = now();
    if (raise(SIGURG)) return 2;
    region_after();
    printf("%llu %llu\n", (unsigned long long)begin, (unsigned long long)end);
    return 0;
}
