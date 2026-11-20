#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <linux/fs.h>
#include <sys/syscall.h>
#include <unistd.h>

int main(int argc, char **argv) {
    if (argc != 3 || argv[1][0] != '/' || argv[2][0] != '/')
        return 64;
    if (syscall(SYS_renameat2, AT_FDCWD, argv[1], AT_FDCWD, argv[2], RENAME_NOREPLACE) == 0)
        return 0;
    return errno == EEXIST ? 3 : 1;
}
