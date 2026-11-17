#include "sha256.h"
#include <string.h>
static uint32_t rotate(uint32_t value, unsigned bits) { return value >> bits | value << (32 - bits); }
static void transform(struct entry_sha256 *state) {
    static const uint32_t constants[64] = {
        0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
        0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
        0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
        0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
        0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
        0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
        0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
        0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2
    };
    uint32_t schedule[64];
    for (unsigned index = 0; index < 16; index++) {
        const unsigned char *p = state->block + index * 4;
        schedule[index] = (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 8 | p[3];
    }
    for (unsigned index = 16; index < 64; index++) {
        uint32_t a = schedule[index - 15], b = schedule[index - 2];
        schedule[index] = schedule[index - 16] + (rotate(a,7) ^ rotate(a,18) ^ (a >> 3)) +
            schedule[index - 7] + (rotate(b,17) ^ rotate(b,19) ^ (b >> 10));
    }
    uint32_t a=state->words[0],b=state->words[1],c=state->words[2],d=state->words[3];
    uint32_t e=state->words[4],f=state->words[5],g=state->words[6],h=state->words[7];
    for (unsigned index=0; index<64; index++) {
        uint32_t first = h + (rotate(e,6) ^ rotate(e,11) ^ rotate(e,25)) + ((e & f) ^ (~e & g)) + constants[index] + schedule[index];
        uint32_t second = (rotate(a,2) ^ rotate(a,13) ^ rotate(a,22)) + ((a & b) ^ (a & c) ^ (b & c));
        h=g; g=f; f=e; e=d+first; d=c; c=b; b=a; a=first+second;
    }
    uint32_t values[8]={a,b,c,d,e,f,g,h};
    for (unsigned index=0; index<8; index++) state->words[index]+=values[index];
}
void entry_sha256_init(struct entry_sha256 *state) {
    const uint32_t initial[8]={0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19};
    memcpy(state->words,initial,sizeof(initial)); state->bytes=0; state->used=0;
}
void entry_sha256_update(struct entry_sha256 *state, const void *bytes, size_t length) {
    const unsigned char *input=bytes; state->bytes+=length;
    while (length) {
        size_t count=64-state->used; if (count>length) count=length;
        memcpy(state->block+state->used,input,count); state->used+=count; input+=count; length-=count;
        if (state->used==64) { transform(state); state->used=0; }
    }
}
void entry_sha256_finish(struct entry_sha256 *state, unsigned char output[32]) {
    uint64_t bits=state->bytes*8;
    state->block[state->used++]=0x80;
    if (state->used>56) { memset(state->block+state->used,0,64-state->used);transform(state);state->used=0; }
    memset(state->block+state->used,0,56-state->used);
    for (unsigned index=0; index<8; index++) state->block[63-index]=(unsigned char)(bits>>(index*8));
    transform(state);
    for (unsigned index=0; index<32; index++) output[index]=(unsigned char)(state->words[index/4]>>(24-(index%4)*8));
}
