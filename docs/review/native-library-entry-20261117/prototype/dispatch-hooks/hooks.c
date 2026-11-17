/* The original handler keeps its LIPC protocol, privilege transition, scanner
 * request, and Scriptlet interpreter. Only an opted-in Aidoku handoff can wake
 * its main wait early and retire its redundant post-handoff xrefresh. */
#define _GNU_SOURCE
#include "common.h"
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <poll.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <sys/syscall.h>
#include <sys/time.h>
#include <sys/wait.h>

static int (*original_utimes)(const char *,const struct timeval[2]);
static pid_t (*original_fork)(void);
static unsigned (*original_sleep)(unsigned);
static pid_t (*original_waitpid)(pid_t,int *,int);
static int (*original_system)(const char *);
static bool enabled;
static int wake_descriptors[2]={-1,-1};
static int trace_descriptor=-1;
static atomic_uint request_count;
static atomic_bool managed;
static atomic_int managed_child;
static atomic_bool handoff_succeeded;
static atomic_bool child_reaped;

static bool opted_in(const char *path){
    char resolved[PATH_MAX];
    if(!path || !realpath(path,resolved) || strcmp(resolved,ENTRY_DOCUMENT_PATH)!=0)return false;
    int descriptor=open(ENTRY_DOCUMENT_PATH,O_RDONLY|O_CLOEXEC|O_NOFOLLOW);
    if(descriptor<0)return false;
    FILE *file=fdopen(descriptor,"r");if(!file){close(descriptor);return false;}
    bool declaration=false,fbink=true,hooks=false;
    for(unsigned line=0;line<6;line++){
        char prefix[64]={0};size_t used=0;int value;
        while((value=fgetc(file))!=EOF && value!='\r' && value!='\n')if(used+1<sizeof(prefix))prefix[used++]=(char)value;
        if(strcmp(prefix,ENTRY_DECLARATION)==0)declaration=true;
        if(strncmp(prefix,"# DontUseFBInk",sizeof("# DontUseFBInk")-1)==0)fbink=false;
        if(strncmp(prefix,"# UseHooks",sizeof("# UseHooks")-1)==0)hooks=true;
        if(value==EOF)break;
    }
    fclose(file);return declaration && !fbink && !hooks;
}
static uint64_t now_ns(void){struct timespec t;if(clock_gettime(CLOCK_MONOTONIC,&t)!=0)return 0;return(uint64_t)t.tv_sec*1000000000+(uint64_t)t.tv_nsec;}

__attribute__((constructor)) static void initialize(void){
    *(void **)(&original_utimes)=dlsym(RTLD_NEXT,"utimes");
    *(void **)(&original_fork)=dlsym(RTLD_NEXT,"fork");
    *(void **)(&original_sleep)=dlsym(RTLD_NEXT,"sleep");
    *(void **)(&original_waitpid)=dlsym(RTLD_NEXT,"waitpid");
    *(void **)(&original_system)=dlsym(RTLD_NEXT,"system");
    const char *expected=getenv(ENTRY_IDENTITY_ENV);
    struct stat current;char identity[192]={0};
    if(stat("/proc/self/exe",&current)==0)entry_identity(&current,identity);
    bool qualified=expected && strcmp(identity,expected)==0;
    const char *fd=getenv(ENTRY_TRACE_ENV);
    if(fd){char *end=NULL;long value=strtol(fd,&end,10);if(*fd && end && !*end && value>=3 && value<=INT_MAX){trace_descriptor=(int)value;int flags=fcntl(trace_descriptor,F_GETFD);if(flags>=0)fcntl(trace_descriptor,F_SETFD,flags|FD_CLOEXEC);}}
    /* Restore the caller's environment before any privileged or script child.
     * The marker is a version qualification safeguard, not a security boundary
     * against a process owner deliberately forging its own environment. */
    const char *prior=getenv(ENTRY_PRIOR_ENV),*present=getenv(ENTRY_PRIOR_PRESENT_ENV);
    if(prior && present){if(strcmp(present,"1")==0)setenv("LD_PRELOAD",prior,1);else unsetenv("LD_PRELOAD");}
    unsetenv(ENTRY_IDENTITY_ENV);unsetenv(ENTRY_PRIOR_ENV);unsetenv(ENTRY_PRIOR_PRESENT_ENV);unsetenv(ENTRY_TRACE_ENV);
    if(!qualified || !original_utimes || !original_fork || !original_sleep || !original_waitpid || !original_system)return;
    if(pipe(wake_descriptors)!=0)return;
    for(unsigned i=0;i<2;i++){
        int flags=fcntl(wake_descriptors[i],F_GETFD);
        if(flags<0 || fcntl(wake_descriptors[i],F_SETFD,flags|FD_CLOEXEC)<0)return;
        flags=fcntl(wake_descriptors[i],F_GETFL);
        if(flags<0 || fcntl(wake_descriptors[i],F_SETFL,flags|O_NONBLOCK)<0)return;
    }
    enabled=true;entry_trace(trace_descriptor,"hooks_ready");
}
int utimes(const char *path,const struct timeval times[2]){
    if(!original_utimes){errno=ENOSYS;return-1;}
    if(enabled){
        unsigned count=atomic_fetch_add_explicit(&request_count,1,memory_order_acq_rel);
        bool selected=count==0 && opted_in(path);
        atomic_store_explicit(&managed,selected,memory_order_release);
        entry_trace(trace_descriptor,selected?"aidoku_handoff_selected":"legacy_scriptlet_selected");
    }
    return original_utimes(path,times);
}
pid_t fork(void){
    if(!original_fork){errno=ENOSYS;return-1;}
    pid_t child=original_fork();
    if(child==0){
        enabled=false;
        if(wake_descriptors[0]>=0)close(wake_descriptors[0]);
        if(wake_descriptors[1]>=0)close(wake_descriptors[1]);
        if(trace_descriptor>=0)close(trace_descriptor);
        wake_descriptors[0]=wake_descriptors[1]=-1;trace_descriptor=-1;
    }else if(child>0 && enabled && atomic_load_explicit(&managed,memory_order_acquire)){
        int expected=0;
        if(atomic_compare_exchange_strong_explicit(&managed_child,&expected,child,memory_order_acq_rel,memory_order_acquire)){
            entry_trace(trace_descriptor,"managed_child_started");
            unsigned char byte=1;(void)write(wake_descriptors[1],&byte,1);
        }
    }
    return child;
}
unsigned sleep(unsigned seconds){
    if(!original_sleep){errno=ENOSYS;return seconds;}
    if(!enabled || (pid_t)syscall(SYS_gettid)!=getpid() || seconds!=1)return original_sleep(seconds);
    uint64_t deadline=now_ns()+1000000000;
    for(;;){
        if(atomic_load_explicit(&managed,memory_order_acquire) &&
           atomic_load_explicit(&managed_child,memory_order_acquire)>0 &&
           !atomic_load_explicit(&child_reaped,memory_order_acquire)){
            /* The upstream assignment follows fork()'s return. A bounded yield
             * lets that callback publish app_pid without spinning its main loop. */
            struct timespec pause={.tv_sec=0,.tv_nsec=1000000};nanosleep(&pause,NULL);
            entry_trace(trace_descriptor,"main_wait_released");return 0;
        }
        uint64_t current=now_ns();if(current>=deadline)return 0;
        int wait=(int)((deadline-current+999999)/1000000);
        struct pollfd descriptor={.fd=wake_descriptors[0],.events=POLLIN};
        int result=poll(&descriptor,1,wait);
        if(result<0)return 1;
        unsigned char bytes[32];while(read(wake_descriptors[0],bytes,sizeof(bytes))>0){}
    }
}
pid_t waitpid(pid_t pid,int *status,int options){
    if(!original_waitpid){errno=ENOSYS;return-1;}
    int observed=0;int *output=status?status:&observed;
    pid_t result=original_waitpid(pid,output,options);
    if(enabled && result>0 && result==atomic_load_explicit(&managed_child,memory_order_acquire)){
        bool success=WIFEXITED(*output) && WEXITSTATUS(*output)==0;
        atomic_store_explicit(&child_reaped,true,memory_order_release);
        atomic_store_explicit(&handoff_succeeded,success,memory_order_release);
        entry_trace(trace_descriptor,success?"managed_child_completed":"managed_child_failed");
    }
    return result;
}
int system(const char *command){
    if(!original_system){errno=ENOSYS;return-1;}
    if(enabled && command && strcmp(command,"/usr/bin/xrefresh -d :0.0")==0 &&
       atomic_load_explicit(&managed,memory_order_acquire) &&
       atomic_load_explicit(&handoff_succeeded,memory_order_acquire)){
        entry_trace(trace_descriptor,"owned_restoration_retained");return 0;
    }
    return original_system(command);
}
