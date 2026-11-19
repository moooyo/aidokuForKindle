#define _GNU_SOURCE
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <poll.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

enum { INITIAL_WAITER_FD = 8, FAILED = 125 };
static volatile sig_atomic_t notify_fd = -1;
static volatile sig_atomic_t cancel_fd = -1;
static volatile sig_atomic_t cancel_signal = 0;

static void child_exited(int signo) {
    (void)signo;
    int saved = errno;
    if (notify_fd >= 0) {
        const char ch = 1;
        (void)write(notify_fd, &ch, sizeof(ch));
    }
    errno = saved;
}

static void initial_waiter_cancelled(int signo) {
    int saved = errno;
    if (cancel_fd >= 0 && cancel_signal == 0) {
        const char request = 'C';
        cancel_signal = signo;
        (void)send(cancel_fd, &request, sizeof(request), MSG_NOSIGNAL | MSG_DONTWAIT);
    }
    errno = saved;
}

static int waiter_signals(void (*handler)(int)) {
    struct sigaction action = { .sa_handler = handler };
    if (sigemptyset(&action.sa_mask) < 0 || sigaddset(&action.sa_mask, SIGHUP) < 0 ||
        sigaddset(&action.sa_mask, SIGINT) < 0 || sigaddset(&action.sa_mask, SIGTERM) < 0 ||
        sigaction(SIGHUP, &action, NULL) < 0 || sigaction(SIGINT, &action, NULL) < 0 ||
        sigaction(SIGTERM, &action, NULL) < 0 || sigprocmask(SIG_UNBLOCK, &action.sa_mask, NULL) < 0) return -1;
    return 0;
}

static int close_ambient(int keep) {
    DIR *fds = opendir("/proc/self/fd");
    if (fds == NULL) return -1;
    const int own = dirfd(fds);
    int result = 0;
    for (;;) {
        errno = 0;
        struct dirent *entry = readdir(fds);
        if (entry == NULL) {
            if (errno != 0) result = -1;
            break;
        }
        char *end;
        long fd = strtol(entry->d_name, &end, 10);
        if (fd > STDERR_FILENO && fd <= INT_MAX && *end == '\0' && fd != own && fd != keep) {
            (void)close((int)fd);
        }
    }
    closedir(fds);
    return result;
}

static void reply(int fd, uint8_t kind, uint8_t status) {
    const uint8_t result[2] = { kind, status };
    ssize_t n;
    do { n = send(fd, result, sizeof(result), MSG_NOSIGNAL); } while (n < 0 && errno == EINTR);
}

static bool original_worker_settled(int fd, pid_t worker, uid_t uid, gid_t gid) {
    char ch;
    struct iovec payload = { .iov_base = &ch, .iov_len = sizeof(ch) };
    union {
        struct cmsghdr align;
        char bytes[CMSG_SPACE(sizeof(struct ucred))];
    } control;
    struct msghdr msg = {
        .msg_iov = &payload, .msg_iovlen = 1,
        .msg_control = control.bytes, .msg_controllen = sizeof(control.bytes),
    };
    const ssize_t n = recvmsg(fd, &msg, MSG_DONTWAIT);
    if (n != 1 || ch != 'S' || (msg.msg_flags & (MSG_TRUNC | MSG_CTRUNC))) return false;
    struct cmsghdr *header = CMSG_FIRSTHDR(&msg);
    if (header == NULL || header->cmsg_level != SOL_SOCKET || header->cmsg_type != SCM_CREDENTIALS ||
        header->cmsg_len != CMSG_LEN(sizeof(struct ucred))) return false;
    struct ucred actual;
    memcpy(&actual, CMSG_DATA(header), sizeof(actual));
    return actual.pid == worker && actual.uid == uid && actual.gid == gid;
}

static void worker_main(char **command, pid_t guardian, int settle) {
    // This signal invokes the existing worker's cleanup if its guardian dies.
    if (prctl(PR_SET_PDEATHSIG, SIGTERM, 0L, 0L, 0L) < 0 || getppid() != guardian) _exit(FAILED);
    if (signal(SIGCHLD, SIG_DFL) == SIG_ERR || dup2(settle, INITIAL_WAITER_FD) < 0 ||
        fcntl(INITIAL_WAITER_FD, F_SETFD, 0) < 0 || close_ambient(INITIAL_WAITER_FD) < 0 ||
        setenv("AIDOKU_INITIAL_WAITER_FD", "8", 1) < 0) _exit(FAILED);
    execv(command[0], command);
    _exit(errno == ENOENT ? 127 : 126);
}

static int guardian_main(char **command, int waiter) {
    int settle[2], notify[2];
    int enabled = 1;
    if (setsid() < 0 || waiter_signals(SIG_DFL) < 0 || close_ambient(waiter) < 0 ||
        socketpair(AF_UNIX, SOCK_SEQPACKET, 0, settle) < 0 ||
        setsockopt(settle[0], SOL_SOCKET, SO_PASSCRED, &enabled, sizeof(enabled)) < 0 ||
        pipe(notify) < 0 || fcntl(notify[1], F_SETFL, O_NONBLOCK) < 0) return FAILED;

    struct sigaction action = { .sa_handler = child_exited, .sa_flags = SA_NOCLDSTOP };
    sigset_t child_signal;
    if (sigemptyset(&action.sa_mask) < 0 || sigemptyset(&child_signal) < 0 ||
        sigaddset(&child_signal, SIGCHLD) < 0) return FAILED;
    notify_fd = notify[1];
    if (sigaction(SIGCHLD, &action, NULL) < 0 || sigprocmask(SIG_UNBLOCK, &child_signal, NULL) < 0) return FAILED;

    const pid_t guardian = getpid();
    const uid_t uid = getuid();
    const gid_t gid = getgid();
    const pid_t worker = fork();
    if (worker < 0) return FAILED;
    if (worker == 0) worker_main(command, guardian, settle[1]);

    close(settle[1]);
    close(STDIN_FILENO);
    close(STDOUT_FILENO);
    close(STDERR_FILENO);
    (void)chdir("/");

    struct pollfd observe[] = {
        { .fd = notify[0], .events = POLLIN },
        { .fd = settle[0], .events = POLLIN },
        { .fd = waiter, .events = POLLIN },
    };
    bool cancelled = false;
    for (;;) {
        int status;
        const pid_t done = waitpid(worker, &status, WNOHANG);
        if (done == worker) {
            const int code = WIFEXITED(status) ? WEXITSTATUS(status) : 128 + WTERMSIG(status);
            if (waiter >= 0) reply(waiter, 'X', (uint8_t)code);
            return code;
        }
        if (done < 0 && errno != EINTR) return FAILED;
        const int ready = poll(observe, sizeof(observe) / sizeof(observe[0]), -1);
        if (ready < 0 && errno != EINTR) return FAILED;
        if (ready <= 0 || observe[0].revents) continue;
        if (observe[2].revents) {
            char request;
            ssize_t n = recv(waiter, &request, sizeof(request), MSG_DONTWAIT);
            if (n == 1 && request == 'C' && !cancelled) {
                // This child cannot be recycled before our waitpid reaps it.
                cancelled = true;
                (void)kill(worker, SIGTERM);
            }
            if (n == 0 || (observe[2].revents & (POLLERR | POLLHUP | POLLNVAL))) {
                close(waiter);
                waiter = -1;
                observe[2].fd = -1;
            }
        }
        if ((observe[1].revents & POLLIN) && original_worker_settled(settle[0], worker, uid, gid)) {
            if (waiter >= 0 && !cancelled) {
                reply(waiter, 'S', 0);
                close(waiter);
                waiter = -1;
                observe[2].fd = -1;
            }
            close(settle[0]);
            observe[1].fd = -1;
        } else if (observe[1].revents & (POLLERR | POLLHUP | POLLNVAL)) {
            close(settle[0]);
            observe[1].fd = -1;
        }
    }
}

int main(int argc, char **argv) {
    if (argc < 3 || strcmp(argv[1], "--") != 0 || argv[2][0] != '/') return FAILED;
    int channel[2];
    if (socketpair(AF_UNIX, SOCK_SEQPACKET, 0, channel) < 0) return FAILED;
    cancel_fd = channel[0];
    if (waiter_signals(initial_waiter_cancelled) < 0) return FAILED;
    const pid_t guardian = fork();
    if (guardian < 0) return FAILED;
    if (guardian == 0) {
        close(channel[0]);
        _exit(guardian_main(argv + 2, channel[1]));
    }

    close(channel[1]);
    uint8_t result[2];
    ssize_t n;
    do { n = recv(channel[0], result, sizeof(result), 0); } while (n < 0 && errno == EINTR);
    cancel_fd = -1;
    close(channel[0]);
    if (n != sizeof(result) || result[0] == 'X') {
        while (waitpid(guardian, NULL, 0) < 0 && errno == EINTR) {}
    }
    if (cancel_signal != 0) return 128 + cancel_signal;
    return n == sizeof(result) && (result[0] == 'X' || result[0] == 'S') ? result[1] : FAILED;
}
