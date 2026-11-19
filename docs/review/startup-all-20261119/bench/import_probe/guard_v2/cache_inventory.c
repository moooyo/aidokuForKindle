/* Read-only inventory of the two installed artifact trees. All output is private. */
#define _GNU_SOURCE
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#include "sha256.h"

#ifndef INVENTORY_ROOT
#define INVENTORY_ROOT "/mnt/us/aidoku/data/cache/shared-v1"
#endif
#define MAX_FILES 8192u
#define MAX_BYTES (512ULL * 1024 * 1024)
#define MAX_ENTRIES 16384u
#define MAX_SECONDS 60u
#define RFLAGS (O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NOATIME | O_NONBLOCK)
static unsigned files, entries;
static uint64_t bytes;

static int same(const struct stat *a, const struct stat *b) {
    return a->st_dev == b->st_dev && a->st_ino == b->st_ino && a->st_mode == b->st_mode &&
        a->st_size == b->st_size && a->st_mtim.tv_sec == b->st_mtim.tv_sec && a->st_mtim.tv_nsec == b->st_mtim.tv_nsec &&
        a->st_ctim.tv_sec == b->st_ctim.tv_sec && a->st_ctim.tv_nsec == b->st_ctim.tv_nsec &&
        a->st_atim.tv_sec == b->st_atim.tv_sec && a->st_atim.tv_nsec == b->st_atim.tv_nsec;
}

static int root_fd(void) {
    char copy[] = INVENTORY_ROOT;
    if (copy[0] != '/') return -1;
    int fd = open("/", RFLAGS | O_DIRECTORY);
    char *next = 0;
    for (char *part = strtok_r(copy, "/", &next); part && fd >= 0; part = strtok_r(0, "/", &next)) {
        if (!strcmp(part,".") || !strcmp(part,"..")) { close(fd); return -1; }
        int child = openat(fd, part, RFLAGS | O_DIRECTORY);
        close(fd); fd = child;
    }
    return fd;
}

static int file_digest(int fd, const struct stat *before, const char *path) {
    if (++files > MAX_FILES || before->st_size < 0 || (uint64_t)before->st_size > MAX_BYTES - bytes) return -1;
    unsigned char buf[64*1024], raw[32]; char hex[65]; uint64_t count=0;
    struct entry_sha256 ctx; entry_sha256_init(&ctx);
    for (;;) {
        ssize_t n=read(fd,buf,sizeof(buf));
        if(n<0 && errno==EINTR) continue;
        if(n<0) return -1;
        if(!n) break;
        count+=(uint64_t)n; if(count>(uint64_t)before->st_size) return -1;
        entry_sha256_update(&ctx,buf,(size_t)n);
    }
    if(count!=(uint64_t)before->st_size)return -1;
    bytes+=count; entry_sha256_finish(&ctx,raw);
    for(size_t i=0;i<32;i++) snprintf(hex+2*i,3,"%02x",raw[i]);
    return printf("F\t%s\t%" PRIu64 "\t%" PRId64 "\t%09ld\t%s\n",path,count,
                  (int64_t)before->st_mtim.tv_sec,before->st_mtim.tv_nsec,hex)>0 ? 0:-1;
}

static int walk(int fd, const char *prefix, unsigned depth);
static int child(int fd,const char *name,const char *path,unsigned depth, int dir_only) {
    struct stat a,b,c;
    if(fstatat(fd,name,&a,AT_SYMLINK_NOFOLLOW)!=0)return -1;
    int is_dir=S_ISDIR(a.st_mode);
    if (!(is_dir || (!dir_only && S_ISREG(a.st_mode))))return -1;
    /* No conventional-read fallback: this observer cannot change access time to obtain a hash. */
    int sub=openat(fd,name,RFLAGS|(is_dir?O_DIRECTORY:0)); if(sub<0)return -1;
    int result=-1;
    if(fstat(sub,&b)==0 && same(&a,&b) &&
       (is_dir?walk(sub,path,depth):file_digest(sub,&a,path))==0 &&
       fstat(sub,&b)==0 && fstatat(fd,name,&c,AT_SYMLINK_NOFOLLOW)==0 && same(&a,&b) && same(&a,&c))result=0;
    close(sub);return result;
}

static int walk(int fd,const char *prefix,unsigned depth) {
    if(depth>4)return -1;
    int copy=fcntl(fd,F_DUPFD_CLOEXEC,3); if(copy<0)return -1;
    DIR *dir=fdopendir(copy);if(!dir){close(copy);return -1;}
    int result=-1;
    for (;;) {
        errno=0;struct dirent *entry=readdir(dir);
        if(!entry){if(!errno)result=0;break;}
        const char *n=entry->d_name;
        if(!strcmp(n,".") || !strcmp(n,".."))continue;
        if(++entries>MAX_ENTRIES)break;
        /* Delimiters in unexpected names must never masquerade as another private record. */
        if(strspn(n,"-.0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefghijklmnopqrstuvwxyz")!=strlen(n))break;
        char path[512];int length=snprintf(path,sizeof(path),"%s/%s",prefix,n);
        if(length<=0 || length>=(int)sizeof(path) || child(fd,n,path,depth+1,0)!=0)break;
    }
    closedir(dir); return result;
}

int main(int argc,char **argv) {
    (void)argv; if(argc!=1)return 64;alarm(MAX_SECONDS);
    int root=root_fd();if(root<0)return 65;
    int ok=child(root,"raw","raw",0,1)==0 && child(root,"processed","processed",0,1)==0;
    close(root);
    if(!ok){fputs("Private cache inventory could not complete without a stable, bounded no-atime read.\n",stderr);return 66;}
    if(printf("END\t%u\t%" PRIu64 "\n",files,bytes)<0 || fflush(stdout))return 67;
    return 0;
}
