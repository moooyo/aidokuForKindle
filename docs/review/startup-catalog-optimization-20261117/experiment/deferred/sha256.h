/* Small streaming SHA-256 for qualifying the unchanged shared executable. */
#ifndef AIDOKU_ENTRY_SHA256_H
#define AIDOKU_ENTRY_SHA256_H
#include <stddef.h>
#include <stdint.h>
struct entry_sha256 { uint32_t words[8]; uint64_t bytes; size_t used; unsigned char block[64]; };
void entry_sha256_init(struct entry_sha256 *state);
void entry_sha256_update(struct entry_sha256 *state, const void *bytes, size_t length);
void entry_sha256_finish(struct entry_sha256 *state, unsigned char output[32]);
#endif
