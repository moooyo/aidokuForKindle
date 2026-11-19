/* Qualification only. Retire the exact private data keeper via a kernel PID fd.
 * An unsupported kernel skips the trial. A numeric kill fallback is forbidden. */
#define _GNU_SOURCE
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>

#ifndef SYS_pidfd_open
#define SYS_pidfd_open 434
#endif
#ifndef SYS_pidfd_send_signal
#define SYS_pidfd_send_signal 424
#endif
#ifndef REOPEN_BENCH_STAGE_PREFIX
#define REOPEN_BENCH_STAGE_PREFIX "/mnt/us/.aidoku-all-"
#endif
#ifndef REOPEN_EXPECTED_ELF_BYTES
#define REOPEN_EXPECTED_ELF_BYTES 53710336
#endif

static int read_record(const char *path, char *out, size_t cap) {
    int fd = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
    if (fd < 0) return -1;
    ssize_t n = read(fd, out, cap);
    close(fd);
    if (n <= 0 || (size_t)n == cap) return -1;
    out[n] = 0;
    return (int)n;
}
static int unsigned_number(const char *s, unsigned long long *out) {
    if (!*s || (s[0] == '0' && s[1] != 0)) return 0;
    for (const char *p = s; *p; ++p) if (!isdigit((unsigned char)*p)) return 0;
    char *end = NULL;
    errno = 0; *out = strtoull(s, &end, 10);
    return !errno && end && !*end;
}
static int exact(const char *path, const char *want, size_t size) {
    char buf[512];
    return read_record(path, buf, sizeof(buf)) == (int)size && memcmp(buf, want, size) == 0;
}
static int child_stat(const char *path, unsigned long long wanted_birth, int pid) {
    char buf[2048], *save = NULL;
    if (read_record(path, buf, sizeof(buf)) < 0) return 0;
    char *tail = strrchr(buf, ')');
    if (!tail || tail[1]!=' ') return 0;
    char *f=strtok_r(tail+2, " ", &save);
    for (int i=0; i<=19; ++i) {
        if (!f) return 0;
        if (i==0 && (*f=='Z' || *f=='X')) return 0;
        if (i==3 || i==19) {
            unsigned long long value;
            if (!unsigned_number(f,&value) || value!=(i==3?(unsigned long long)pid:wanted_birth)) return 0;
        }
        f=strtok_r(NULL," ",&save);
    }
    return 1;
}
int main(int argc, char **argv) {
    if (argc==2 && !strcmp(argv[1],"--supported")) {
        int self=(int)syscall(SYS_pidfd_open,getpid(),0);
        if (self<0) return 77;
        int ok=syscall(SYS_pidfd_send_signal,self,0,NULL,0)==0;close(self);
        return ok?0:77;
    }
    unsigned long long id,birth;
    if (argc!=3 || !unsigned_number(argv[1],&id) || id<2 || id>INT_MAX || !unsigned_number(argv[2],&birth) || !birth) return 64;
    int pid=(int)id, pfd=(int)syscall(SYS_pidfd_open,pid,0);
    if (pfd<0) return 77;
    char stage[PATH_MAX], path[PATH_MAX], record[256], child[PATH_MAX], target[PATH_MAX];
    ssize_t n=readlink("/proc/self/exe",stage,sizeof(stage)-1);
    if (n<0 || (size_t)n>=sizeof(stage)-1) return 65;
    stage[n]=0;char *sep=strrchr(stage,'/');
    if (!sep || strcmp(sep+1,"drop-owned-keeper")) return 65;
    *sep=0;if (strncmp(stage,REOPEN_BENCH_STAGE_PREFIX,strlen(REOPEN_BENCH_STAGE_PREFIX))!=0) return 65;
    const char *suffix=stage+strlen(REOPEN_BENCH_STAGE_PREFIX);
    if (!*suffix || strlen(suffix)>60) return 65;
    for (const char *p=suffix;*p;++p) if (!(islower((unsigned char)*p) || isdigit((unsigned char)*p) || *p=='-')) return 65;
    if (snprintf(record,sizeof(record),"aidoku-all-%s\n",suffix)>=(int)sizeof(record)) return 65;
    if (snprintf(path,sizeof(path),"%s/owner",stage)>=(int)sizeof(path) || !exact(path,record,strlen(record))) return 65;
    const char active[]="candidate-qualify-prime\n";
    if (snprintf(path,sizeof(path),"%s/case",stage)>=(int)sizeof(path) || !exact(path,active,sizeof(active)-1)) return 65;
    if (snprintf(path,sizeof(path),"%s/candidate/app/aidoku",stage)>=(int)sizeof(path) || !realpath(path,target) || strcmp(path,target)) return 65;
    snprintf(path,sizeof(path),"/proc/%d/exe",pid);n=readlink(path,child,sizeof(child)-1);
    if (n<0 || (size_t)n>=sizeof(child)-1) return 65;
    child[n]=0;if (strcmp(target,child)) return 65;
    struct stat one,two;
    if (stat(target,&one) || stat(path,&two) || !S_ISREG(one.st_mode) || one.st_dev!=two.st_dev || one.st_ino!=two.st_ino || (REOPEN_EXPECTED_ELF_BYTES && one.st_size!=REOPEN_EXPECTED_ELF_BYTES)) return 65;
    snprintf(path,sizeof(path),"/proc/%d/cmdline",pid);
    const char role[]="aidoku-resident-keeper\0--resident-keeper";
    if (!exact(path,role,sizeof(role))) return 65;
    snprintf(path,sizeof(path),"/proc/%d/stat",pid);
    if (!child_stat(path,birth,pid)) return 65;
    if (syscall(SYS_pidfd_send_signal,pfd,SIGTERM,NULL,0)) return 66;
    struct pollfd pollfd={.fd=pfd,.events=POLLIN};
    if (poll(&pollfd,1,1600)!=1 || !(pollfd.revents&POLLIN)) return 67;
    close(pfd);puts("owned-keeper-terminated");return 0;
}
