#define main render_watch_main
#include "render_watch.c"
#undef main

static uint32_t little32(const unsigned char *bytes) {
    return (uint32_t)bytes[0] | (uint32_t)bytes[1] << 8 |
           (uint32_t)bytes[2] << 16 | (uint32_t)bytes[3] << 24;
}

static uint64_t little64(const unsigned char *bytes) {
    return (uint64_t)little32(bytes) | (uint64_t)little32(bytes + 4) << 32;
}

int main(int argc, char **argv) {
    if (argc != 2) fail("USAGE");
    struct rule rule = {0};
    if (!load_rule(argv[1], &rule)) fail("RULE");
    char header[256];
    if (!fgets(header, sizeof header, stdin)) fail("TRACE");
    unsigned rotate, horizontal, vertical, count, missed;
    uint64_t start, end, cpu;
    int consumed = 0;
    if (sscanf(header, "AFW2 %u %u %u %" SCNu64 " %" SCNu64 " %" SCNu64 " %u %u%n",
               &rotate, &horizontal, &vertical, &start, &end, &cpu, &count, &missed, &consumed) != 8 ||
        strcmp(header + consumed, "\n") || rotate != rule.rotate || horizontal != rule.xvirtual ||
        vertical != rule.yvirtual || !start || end <= start || cpu > (end - start) / 10 ||
        count < 2 || count > 160 || missed > 160 || count + missed != 160 ||
        end - start < 75000u * 158 || end - start > 75000u * 162) fail("TRACE_HEADER");
    unsigned char record[20], pixels[POINTS];
    struct state state = {0};
    struct result result = {0};
    uint64_t previous = start, selected_begin = 0;
    int complete = 0;
    for (unsigned index = 0; index < count; ++index) {
        if (fread(record, 1, sizeof record, stdin) != sizeof record ||
            fread(pixels, 1, POINTS, stdin) != POINTS) fail("TRACE_LENGTH");
        uint64_t before = little64(record), after = little64(record + 8);
        uint32_t pan = little32(record + 16);
        if (before < previous || after < before || after > end || after - before > MAX_SCAN_US ||
            (pan != 0 && !(pan == HEIGHT && vertical == 2 * HEIGHT))) fail("TRACE_SCAN");
        previous = after;
        if (complete || (selected_begin && before - selected_begin < PERIOD_US)) continue;
        selected_begin = before;
        unsigned text_diff;
        int covers;
        classify(&rule, pixels, &text_diff, &covers);
        int progress = advance(&state, before, after, text_diff, covers, &result);
        if (progress < 0) fail(progress == -1 ? "SCAN" : progress == -2 ? "INITIAL" : "DWELL");
        complete = progress == 1;
    }
    if (fgetc(stdin) != EOF || ferror(stdin)) fail("TRACE_LENGTH");
    if (!complete) fail("NO_MATCH");
    printf("ARL1 %" PRIu64 " %" PRIu64 " %" PRIu64 " %s %s\n",
           result.last_negative, result.first_match, result.confirmed,
           result.first_cover ? "MATCH" : "DIFFER",
           result.confirmed_cover ? "MATCH" : "DIFFER");
    return 0;
}
