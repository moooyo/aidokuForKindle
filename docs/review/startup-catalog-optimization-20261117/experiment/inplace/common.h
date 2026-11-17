#ifndef AIDOKU_ENTRY_COMMON_H
#define AIDOKU_ENTRY_COMMON_H
#include <stdio.h>
#include <stdint.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#ifndef ENTRY_LEGACY_PATH
#define ENTRY_LEGACY_PATH "/var/local/kmc/bin/sh_integration_launcher"
#endif
#ifndef ENTRY_LEGACY_SHA256
#define ENTRY_LEGACY_SHA256 "2b3db59ed7856b3f5026e3837f7ef07fc9bb6bc1c042274b48b3cd3fc2e04abc"
#endif
#ifndef ENTRY_HOOKS_SHA256
#define ENTRY_HOOKS_SHA256 ""
#endif
#ifndef ENTRY_CLIENT_SHA256
#define ENTRY_CLIENT_SHA256 ""
#endif
#ifndef ENTRY_DOCUMENT_PATH
#define ENTRY_DOCUMENT_PATH "/mnt/us/documents/Aidoku.sh"
#endif
#define ENTRY_DECLARATION "# AidokuCatalogDiagnostic: v1"
#define ENTRY_IDENTITY_ENV "AIDOKU_ENTRY_VERIFIED_EXECUTABLE"
#define ENTRY_PRIOR_ENV "AIDOKU_ENTRY_PRIOR_PRELOAD"
#define ENTRY_PRIOR_PRESENT_ENV "AIDOKU_ENTRY_PRIOR_PRELOAD_PRESENT"
#define ENTRY_TRACE_ENV "AIDOKU_ENTRY_TRACE_FD"
#define ENTRY_POLICY_ENV "AIDOKU_ENTRY_CATALOG_POLICY"
#define ENTRY_CLIENT_ENV "AIDOKU_ENTRY_CATALOG_CLIENT"
#define ENTRY_CLIENT_IDENTITY_ENV "AIDOKU_ENTRY_CATALOG_CLIENT_IDENTITY"
static inline void entry_identity(const struct stat *status, char output[192]) {
    snprintf(output,192,"%llu:%llu:%llu:%lld:%ld:%lld:%ld",
             (unsigned long long)status->st_dev,(unsigned long long)status->st_ino,
             (unsigned long long)status->st_size,(long long)status->st_mtim.tv_sec,status->st_mtim.tv_nsec,
             (long long)status->st_ctim.tv_sec,status->st_ctim.tv_nsec);
}
static inline void entry_trace(int descriptor, const char *event) {
    if (descriptor<0) return;
    struct timespec value;
    if (clock_gettime(CLOCK_BOOTTIME,&value)!=0) return;
    char line[160];
    int size=snprintf(line,sizeof(line),"%lld.%06ld %s\n",(long long)value.tv_sec,value.tv_nsec/1000,event);
    if (size>0 && (size_t)size<sizeof(line)) (void)write(descriptor,line,(size_t)size);
}
#endif
