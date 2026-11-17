/* Version-qualified Aidoku catalog access. The unmodified shared handler
 * retains dispatch, privilege, scanning and native application lifetime. */
#define _GNU_SOURCE
#include "common.h"
#include "sha256.h"
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdlib.h>
#include <string.h>

static int qualify_file(const char *path,const char *expected,char identity[192]) {
    int descriptor=open(path,O_RDONLY|O_CLOEXEC);
    if (descriptor<0) return 0;
    struct stat before,after;
    if (fstat(descriptor,&before)!=0 || !S_ISREG(before.st_mode) || before.st_size<=0 || before.st_size>1024*1024) { close(descriptor);return 0; }
    struct entry_sha256 hash;entry_sha256_init(&hash);
    unsigned char buffer[8192];
    for (;;) {
        ssize_t size=read(descriptor,buffer,sizeof(buffer));
        if (size<0 && errno==EINTR) continue;
        if (size<0) { close(descriptor);return 0; }
        if (size==0) break;
        entry_sha256_update(&hash,buffer,(size_t)size);
    }
    if (fstat(descriptor,&after)!=0) {close(descriptor);return 0;}
    close(descriptor);
    char first[192],last[192];entry_identity(&before,first);entry_identity(&after,last);
    if (strcmp(first,last)!=0) return 0;
    unsigned char digest[32];entry_sha256_finish(&hash,digest);
    char hex[65];const char digits[]="0123456789abcdef";
    for(unsigned index=0;index<32;index++){hex[index*2]=digits[digest[index]>>4];hex[index*2+1]=digits[digest[index]&15];}
    hex[64]=0;
    if(strcmp(hex,expected)!=0)return 0;
    if(identity)memcpy(identity,first,strlen(first)+1);
    return 1;
}
int main(int argc,char **argv) {
    if(argc==2 && strcmp(argv[1],"--version")==0){puts("aidoku-catalog-entry 1");return 0;}
    const char *trace_directory=NULL;
    const char *policy_file=NULL;
    for(int index=1;index<argc;index++){
        if(strcmp(argv[index],"--trace-dir")==0 && index+1<argc)trace_directory=argv[++index];
        else if(strcmp(argv[index],"--policy-file")==0 && index+1<argc)policy_file=argv[++index];
        else return 64;
    }
    int policy_enabled=1;
    const char *catalog_policy="quiet";
    if(policy_file){
        char value[16]={0};struct stat status;
        int descriptor=open(policy_file,O_RDONLY|O_CLOEXEC|O_NOFOLLOW|O_NONBLOCK);
        ssize_t size=-1;
        if(descriptor>=0 && fstat(descriptor,&status)==0 && S_ISREG(status.st_mode))
            size=read(descriptor,value,sizeof(value));
        if(descriptor>=0)close(descriptor);
        policy_enabled=(size==9 && (memcmp(value,"original\n",9)==0 || memcmp(value,"preserve\n",9)==0)) ||
            (size==7 && memcmp(value,"update\n",7)==0) || (size==6 && memcmp(value,"quiet\n",6)==0);
        if(size==9 && memcmp(value,"original\n",9)==0)catalog_policy="original";
        if(size==9 && memcmp(value,"preserve\n",9)==0)catalog_policy="preserve";
        if(size==7 && memcmp(value,"update\n",7)==0)catalog_policy="update";
        if(size==6 && memcmp(value,"quiet\n",6)==0)catalog_policy="quiet";
    }
    int trace=-1;
    if(trace_directory){
        char path[PATH_MAX];int size=snprintf(path,sizeof(path),"%s/native-entry.%ld.tsv",trace_directory,(long)getpid());
        if(size<0 || (size_t)size>=sizeof(path))return 64;
        trace=open(path,O_WRONLY|O_APPEND|O_CREAT|O_EXCL|O_NOFOLLOW,0600);
        if(trace<0)return 73;
    }
    entry_trace(trace,"wrapper_entered");
    if(!policy_enabled)entry_trace(trace,"diagnostic_policy_disabled");
    int hooked=0;
    char identity[192],self[PATH_MAX];
    ssize_t length=readlink("/proc/self/exe",self,sizeof(self)-1);
    if(!qualify_file(ENTRY_CATALOG_SOURCE,ENTRY_CATALOG_SHA256,NULL)){
        entry_trace(trace,"catalog_abi_unqualified_fallback");
        goto original;
    }
    if(policy_enabled && length>0 && (size_t)length<sizeof(self)-1 && qualify_file(ENTRY_LEGACY_PATH,ENTRY_LEGACY_SHA256,identity)){
        self[length]=0;char *separator=strrchr(self,'/');
        if(!separator)return 70;
        *separator=0;
        char hooks[PATH_MAX];int size=snprintf(hooks,sizeof(hooks),"%s/aidoku-entry-hooks.so",self);
        if(size>0 && (size_t)size<sizeof(hooks) && qualify_file(hooks,ENTRY_HOOKS_SHA256,NULL)){
            char client[PATH_MAX],client_identity[192];
            int client_size=snprintf(client,sizeof(client),"%s/aidoku-catalog-client",self);
            if(client_size<=0 || (size_t)client_size>=sizeof(client) ||
               !qualify_file(client,ENTRY_CLIENT_SHA256,client_identity)){
                entry_trace(trace,"catalog_client_unavailable_fallback");
                goto original;
            }
            const char *prior=getenv("LD_PRELOAD");
            size_t previous=prior?strlen(prior):0;
            if(previous>8192)return 64;
            char preload[PATH_MAX+8194];
            snprintf(preload,sizeof(preload),"%s%s%s",hooks,previous?":":"",prior?prior:"");
            if(setenv(ENTRY_PRIOR_ENV,prior?prior:"",1)!=0 ||
               setenv(ENTRY_PRIOR_PRESENT_ENV,prior?"1":"0",1)!=0 ||
               setenv(ENTRY_IDENTITY_ENV,identity,1)!=0 ||
               setenv(ENTRY_POLICY_ENV,catalog_policy,1)!=0 ||
               setenv(ENTRY_CLIENT_ENV,client,1)!=0 ||
               setenv(ENTRY_CLIENT_IDENTITY_ENV,client_identity,1)!=0 ||
               setenv("LD_PRELOAD",preload,1)!=0)return 70;
            if(trace>=0){char value[24];snprintf(value,sizeof(value),"%d",trace);if(setenv(ENTRY_TRACE_ENV,value,1)!=0)return 70;}
            entry_trace(trace,"shared_handler_qualified");
            hooked=1;
        }else entry_trace(trace,"hooks_unavailable_fallback");
    }else entry_trace(trace,"unqualified_handler_fallback");
original:
    if(!hooked && trace>=0){int flags=fcntl(trace,F_GETFD);if(flags>=0)fcntl(trace,F_SETFD,flags|FD_CLOEXEC);}
    char *arguments[]={(char *)ENTRY_LEGACY_PATH,NULL};
    execv(ENTRY_LEGACY_PATH,arguments);
    return 71;
}
