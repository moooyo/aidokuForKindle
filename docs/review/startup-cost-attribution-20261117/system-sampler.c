#define _GNU_SOURCE
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
struct process { int pid; char role[32]; unsigned long long birth; };
static int read_text(const char *path,char *buffer,size_t capacity){int fd=open(path,O_RDONLY|O_CLOEXEC);if(fd<0)return-1;ssize_t n=read(fd,buffer,capacity-1);close(fd);if(n<0)return-1;buffer[n]=0;return 0;}
static unsigned long long now_us(void){struct timespec t;if(clock_gettime(CLOCK_BOOTTIME,&t)!=0)return 0;return(unsigned long long)t.tv_sec*1000000+t.tv_nsec/1000;}
static int numbers(int pid,unsigned long long *birth,unsigned long long *user,unsigned long long *system,unsigned long long *minor,unsigned long long *major){
 char path[80],buffer[4096];snprintf(path,sizeof(path),"/proc/%d/stat",pid);if(read_text(path,buffer,sizeof(buffer)))return 0;char *cursor=strrchr(buffer,')');if(!cursor)return 0;cursor+=2;
 for(int field=3;field<=22;field++){char *end=strchr(cursor,' ');if(!end)return 0;*end=0;unsigned long long value=strtoull(cursor,NULL,10);if(field==10)*minor=value;if(field==12)*major=value;if(field==14)*user=value;if(field==15)*system=value;if(field==22)*birth=value;cursor=end+1;}
 return 1;
}
static int role_selected(const char *name){return strstr(name,"KPPMainApp")||strstr(name,"scanner")||strstr(name,"appmgrd")||strstr(name,"ccat")||strcmp(name,"awesome")==0||strcmp(name,"cvm")==0;}
int main(int argc,char **argv){
 if(argc!=4)return 64;int duration=atoi(argv[3]);if(duration<1||duration>20)return 64;
 FILE *out=fopen(argv[1],"wx");if(!out)return 73;setvbuf(out,NULL,_IOFBF,32768);
 struct process processes[40];size_t count=0;DIR *directory=opendir("/proc");if(!directory)return 70;struct dirent *entry;
 while((entry=readdir(directory))&&count<39){char *end;long pid=strtol(entry->d_name,&end,10);if(*end||pid<=0)continue;char path[80],name[64];snprintf(path,sizeof(path),"/proc/%ld/comm",pid);if(read_text(path,name,sizeof(name)))continue;name[strcspn(name,"\n")]=0;if(!role_selected(name))continue;
  unsigned long long birth=0,u=0,s=0,mi=0,ma=0;if(!numbers((int)pid,&birth,&u,&s,&mi,&ma))continue;processes[count].pid=(int)pid;processes[count].birth=birth;snprintf(processes[count].role,sizeof(processes[count].role),"%.31s",name);count++;
 }
 closedir(directory);size_t fixed=count;fprintf(out,"# clock_ticks_per_second=%ld\n",sysconf(_SC_CLK_TCK));fprintf(out,"boottime_us,kind,role,pid,birth,user_ticks,system_ticks,minor_faults,major_faults,frequency_khz,cpu_user,cpu_nice,cpu_system,cpu_idle,cpu_iowait,cpu_irq,cpu_softirq,cpu_steal,main_run_ns,main_wait_ns\n");
 unsigned long long deadline=now_us()+(unsigned long long)duration*1000000;
 while(now_us()<deadline){
  unsigned long long at=now_us();char text[4096];unsigned long frequency=0;
  if(!read_text("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq",text,sizeof(text)))frequency=strtoul(text,NULL,10);
  unsigned long long cpu[8]={0};if(!read_text("/proc/stat",text,sizeof(text)))sscanf(text,"cpu %llu %llu %llu %llu %llu %llu %llu %llu",&cpu[0],&cpu[1],&cpu[2],&cpu[3],&cpu[4],&cpu[5],&cpu[6],&cpu[7]);
  fprintf(out,"%llu,system,all,0,0,0,0,0,0,%lu,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu,0,0\n",at,frequency,cpu[0],cpu[1],cpu[2],cpu[3],cpu[4],cpu[5],cpu[6],cpu[7]);
  if(count==fixed&&!read_text("/tmp/aidoku-launcher.lock/application-pid",text,sizeof(text))){
   int pid=atoi(text);char path[80],exe[1024];snprintf(path,sizeof(path),"/proc/%d/exe",pid);ssize_t n=readlink(path,exe,sizeof(exe)-1);if(n>0){exe[n]=0;if(strcmp(exe,argv[2])==0){unsigned long long birth=0,u=0,s=0,mi=0,ma=0;if(numbers(pid,&birth,&u,&s,&mi,&ma)){processes[count].pid=pid;processes[count].birth=birth;strcpy(processes[count].role,"aidoku");count++;}}}
  }
  for(size_t i=0;i<count;i++){unsigned long long birth=0,u=0,s=0,mi=0,ma=0;if(numbers(processes[i].pid,&birth,&u,&s,&mi,&ma)&&birth==processes[i].birth){char schedpath[80],sched[256];unsigned long long run=0,wait=0;snprintf(schedpath,sizeof(schedpath),"/proc/%d/schedstat",processes[i].pid);if(!read_text(schedpath,sched,sizeof(sched)))sscanf(sched,"%llu %llu",&run,&wait);fprintf(out,"%llu,process,%s,%d,%llu,%llu,%llu,%llu,%llu,0,0,0,0,0,0,0,0,0,%llu,%llu\n",at,processes[i].role,processes[i].pid,birth,u,s,mi,ma,run,wait);}}
  struct timespec pause={0,100000000};while(nanosleep(&pause,&pause)&&errno==EINTR){}
 }
 return fclose(out)==0?0:74;
}
