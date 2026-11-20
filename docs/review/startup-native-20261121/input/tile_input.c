#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <linux/fb.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#include "mark_points.h"

typedef struct _XDisplay Display;
extern Display *XOpenDisplay(const char *);
extern int XCloseDisplay(Display *), XDefaultScreen(Display *);
extern int XDisplayWidth(Display *, int), XDisplayHeight(Display *, int);
extern unsigned long XDefaultRootWindow(Display *);
extern int XQueryPointer(Display *, unsigned long, unsigned long *, unsigned long *,
                         int *, int *, int *, int *, unsigned *);
extern int XTestQueryExtension(Display *, int *, int *, int *, int *);
extern int XTestFakeMotionEvent(Display *, int, int, int, unsigned long);
extern int XTestFakeButtonEvent(Display *, unsigned int, int, unsigned long);
extern int XSync(Display *, int);

enum { WIDTH=1860, HEIGHT=2480, STRIDE=1872, BODY_TOP=150, BODY_BOTTOM=2330 };
struct guard { char magic[8]; uint32_t left,top,width,height,footer,rows; };
struct point { int column, row; };
struct run { int start, end; };

static void fail(const char *reason) {
    fprintf(stderr, "refused %s\n", reason);
    exit(2);
}

static uint64_t now(void) {
    struct timespec value;
    if (clock_gettime(CLOCK_BOOTTIME, &value) < 0) fail("tap_clock");
    return (uint64_t)value.tv_sec * 1000000 + (uint64_t)value.tv_nsec / 1000;
}

static uint8_t *frame(void) {
    const size_t size = (size_t)WIDTH*HEIGHT;
    uint8_t *data = malloc(size);
    if (!data) fail("frame_memory");
#ifdef AIDOKU_TEST_FRAME
    FILE *fixture = fopen("test.frame", "rb");
    if (!fixture || fread(data,1,size,fixture) != size || fgetc(fixture) != EOF) fail("fixture_frame");
    fclose(fixture);
#else
    int handle = open("/dev/fb0", O_RDONLY|O_CLOEXEC);
    struct fb_var_screeninfo config, after;
    struct fb_fix_screeninfo fixed, final;
    if (handle < 0 || ioctl(handle,FBIOGET_VSCREENINFO,&config) || ioctl(handle,FBIOGET_FSCREENINFO,&fixed) ||
        config.xres!=WIDTH || config.yres!=HEIGHT || config.bits_per_pixel!=8 ||
        (config.rotate!=1 && config.rotate!=3) || config.xoffset ||
        (config.xres_virtual!=WIDTH && config.xres_virtual!=STRIDE) ||
        (config.yres_virtual!=HEIGHT && config.yres_virtual!=2*HEIGHT) ||
        (config.yoffset!=0 && (config.yoffset!=HEIGHT || config.yres_virtual!=2*HEIGHT)) ||
        fixed.type!=FB_TYPE_PACKED_PIXELS || fixed.line_length!=STRIDE ||
        fixed.smem_len < STRIDE * config.yres_virtual) fail("tap_geometry");
    size_t mapped = (size_t)STRIDE*config.yres_virtual;
    const uint8_t *map = mmap(NULL,mapped,PROT_READ,MAP_SHARED,handle,0);
    if (map==MAP_FAILED) fail("tap_mapping");
    for (size_t row=0; row<HEIGHT; ++row)
        memcpy(data+row*WIDTH,map+(row+config.yoffset)*STRIDE,WIDTH);
    if (ioctl(handle,FBIOGET_VSCREENINFO,&after) || ioctl(handle,FBIOGET_FSCREENINFO,&final) ||
        config.xres!=after.xres || config.yres!=after.yres || config.xres_virtual!=after.xres_virtual ||
        config.yres_virtual!=after.yres_virtual || config.rotate!=after.rotate || config.xoffset!=after.xoffset ||
        config.yoffset!=after.yoffset || config.bits_per_pixel!=after.bits_per_pixel ||
        fixed.line_length!=final.line_length || fixed.type!=final.type || fixed.smem_len!=final.smem_len)
        fail("tap_scanout_changed");
    munmap((void *)map,mapped);
    close(handle);
#endif
    return data;
}

static int near(int value, int expected, int tolerance) {
    return abs(value-expected)<=tolerance;
}

static int possible_other_logo(const uint8_t *shot, int left, int first_top, int wide, int tall) {
    int total[2]={0,0}, agreement[2]={0,0};
    for (size_t sample=0; sample<sizeof(mark_points)/sizeof(mark_points[0]); ++sample) {
        int column=left+(mark_points[sample][0]-95)*wide/122;
        int row=first_top+(mark_points[sample][1]-93)*tall/193;
        if (column<0 || column>=WIDTH || row<BODY_TOP || row>=BODY_BOTTOM) return 0;
        int dark=mark_points[sample][2];
        ++total[dark];
        if ((shot[(size_t)row*WIDTH+column]<190)==dark) ++agreement[dark];
    }
    return 100*agreement[1]>=69*total[1] && 100*agreement[0]>=70*total[0];
}

static void exclude_other_marks(const uint8_t *shot, struct point target, const struct guard *plan) {
    int observed=0;
    for (int col=8; col<WIDTH-8; col+=8) {
        struct run bars[4]={0};
        int count=0, start=0;
        for (int row=BODY_TOP; row<=BODY_BOTTOM; ++row) {
            if (row<BODY_BOTTOM && shot[(size_t)row*WIDTH+col]<190) {
                if (!start) start=row;
                continue;
            }
            if (!start) continue;
            memmove(bars,bars+1,sizeof(bars[0])*3);
            bars[3]=(struct run){start,row};
            start=0;
            if (++count<4) continue;
            int scale=bars[3].start-bars[0].start;
            if (scale<24 || scale>324) continue;
            int tolerance=scale/24+3;
            if (!near(bars[1].start-bars[0].start,64*scale/193,tolerance) ||
                !near(bars[2].start-bars[1].start,65*scale/193,tolerance)) continue;
            int aligned=1;
            for (int bar=0; bar<4; ++bar)
                if (!near(bars[bar].end-bars[bar].start,(bar==1 ? 43 : 41)*scale/193,tolerance))
                    aligned=0;
            if (!aligned) continue;
            int mid=(bars[0].start+bars[0].end)/2, left=col, right=col+1;
            const uint8_t *line=shot+(size_t)mid*WIDTH;
            while (left>0 && line[left-1]<190) --left;
            while (right<WIDTH && line[right]<190) ++right;
            int wide=right-left;
            if (wide*19300 < 72*122*scale || wide*19300 > 135*122*scale) continue;
            int center_x=left+205*wide/122, center_y=bars[0].start+307*scale/193;
            if (possible_other_logo(shot,left,bars[0].start,wide,scale)) {
                if (!near(target.column,center_x,(int)plan->width/3) ||
                    !near(target.row,center_y,(int)plan->height/2)) fail("additional_possible_logo");
                if (near(target.column,center_x,(int)plan->width/9+3) &&
                    near(target.row,center_y,(int)plan->height/9+3)) observed=1;
            }
        }
    }
    if (!observed) fail("public_logo_pattern_missing");
}

static struct point match(const struct guard *plan, const uint8_t *cover, const uint8_t *footer) {
    uint8_t *shot=frame();
    int width=(int)plan->width, height=(int)plan->height;
    if (memcmp(shot+(size_t)plan->footer*WIDTH,footer,(size_t)WIDTH*plan->rows))
        fail("library_footer_changed");
    int anchor=0, maximum=0;
    for (int row=height/3; row<height-height/6; ++row) {
        int changes=0, previous=cover[(size_t)row*width] < 180;
        for (int col=1; col<width; ++col) {
            int current=cover[(size_t)row*width+col] < 180;
            changes += (current != previous);
            previous=current;
        }
        if (changes>maximum) { anchor=row; maximum=changes; }
    }
    if (maximum < 8) fail("uninformative_artwork");
    const uint8_t *key=cover+(size_t)anchor*width;
    struct point point={-1,-1};
    int hits=0;
    for (int top=BODY_TOP; top+height<=BODY_BOTTOM; ++top) {
        const uint8_t *base=shot+(size_t)(top+anchor)*WIDTH;
        int start=0;
        while (start+width<=WIDTH) {
            const uint8_t *hit=memmem(base+start,(size_t)(WIDTH-start),key,(size_t)width);
            if (!hit) break;
            int left=(int)(hit-base), compatible=1;
            for (int row=0; row<height; ++row) {
                if (memcmp(shot+(size_t)(top+row)*WIDTH+left,cover+(size_t)row*width,(size_t)width)) {
                    compatible=0;
                    break;
                }
            }
            if (compatible) {
                if (++hits>1) fail("ambiguous_stock_tile");
                point=(struct point){left+width/2,top+height/2};
            }
            start=left+1;
        }
    }
    if (hits!=1) fail("missing_stock_tile");
    exclude_other_marks(shot,point,plan);
    free(shot);
    return point;
}

static int pointer(Display *display, struct point *position, unsigned buttons) {
    unsigned long root,child;
    int local_column,local_row;
    unsigned mask;
    return XQueryPointer(display,XDefaultRootWindow(display),&root,&child,&position->column,&position->row,
                         &local_column,&local_row,&mask) && !(mask & 0xff) && (mask & 0x1f00)==buttons;
}

static int at(Display *display, struct point target, unsigned buttons) {
    struct point position;
    return pointer(display,&position,buttons) && position.column==target.column && position.row==target.row;
}

int main(int argc, char **argv) {
    if (argc!=3 || (strcmp(argv[1],"match") && strcmp(argv[1],"tap"))) fail("unexpected_tap_mode");
    int handle=open(argv[2],O_RDONLY|O_CLOEXEC|O_NOFOLLOW);
    struct stat status;
    struct guard plan;
    if (sizeof(plan)!=32 || handle<0 || fstat(handle,&status) || !S_ISREG(status.st_mode) ||
        read(handle,&plan,sizeof(plan))!=(ssize_t)sizeof(plan) || memcmp(plan.magic,"AIDOTAP1",8) ||
        plan.width<48 || plan.width>1000 || plan.height<50 || plan.height>1300 ||
        plan.left>WIDTH-plan.width || plan.top<BODY_TOP || plan.top>BODY_BOTTOM-plan.height ||
        plan.footer!=2380 || plan.rows!=90) fail("invalid_artwork_guard");
    size_t cover_size=(size_t)plan.width*plan.height;
    size_t size=sizeof(plan)+cover_size+(size_t)WIDTH*plan.rows;
    if (status.st_size!=(off_t)size) fail("invalid_artwork_guard_size");
    const uint8_t *map=mmap(NULL,size,PROT_READ,MAP_PRIVATE,handle,0);
    if (map==MAP_FAILED) fail("artwork_mapping");
    close(handle);
    const uint8_t *cover=map+sizeof(plan), *footer=cover+cover_size;
    struct point target=match(&plan,cover,footer);
    if (!strcmp(argv[1],"match")) { puts("unique_library_artwork"); return 0; }
    alarm(9);
    Display *display=XOpenDisplay(NULL);
    if (!display) fail("x_display");
    int screen=XDefaultScreen(display), event,error,major,minor;
    struct point initial;
    if (XDisplayWidth(display,screen)!=WIDTH || XDisplayHeight(display,screen)!=HEIGHT ||
        !XTestQueryExtension(display,&event,&error,&major,&minor) || !pointer(display,&initial,0) ||
        !XTestFakeMotionEvent(display,screen,target.column,target.row,0)) fail("pointer_admission");
    XSync(display,0);
    struct point check=match(&plan,cover,footer);
    if (check.column!=target.column || check.row!=target.row || !at(display,target,0)) fail("changed_click_target");
    sigset_t blocked, previous;
    sigemptyset(&blocked);
    sigaddset(&blocked,SIGTERM); sigaddset(&blocked,SIGHUP); sigaddset(&blocked,SIGINT); sigaddset(&blocked,SIGALRM);
    if (sigprocmask(SIG_BLOCK,&blocked,&previous)) fail("tap_signal_mask");
    uint64_t down_before=now();
    int sent=XTestFakeButtonEvent(display,1,1,0);
    XSync(display,0);
    uint64_t down_after=now();
    int down=sent && at(display,target,0x100);
    struct timespec hold={0,50000000};
    if (down) while (nanosleep(&hold,&hold) < 0 && errno==EINTR) {}
    uint64_t up_before=now();
    int up=XTestFakeButtonEvent(display,1,0,0);
    XSync(display,0);
    uint64_t up_after=now();
    int clear=at(display,target,0);
    sigprocmask(SIG_SETMASK,&previous,NULL);
    XCloseDisplay(display);
    if (!down || !up || !clear || !down_before || down_after<down_before ||
        up_before<down_after+50000 || up_after<up_before) fail("unconfirmed_click");
    printf("%"PRIu64" %"PRIu64" %"PRIu64" %"PRIu64"\n",down_before,down_after,up_before,up_after);
    return 0;
}
