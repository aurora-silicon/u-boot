#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0+
"""Execute the actual native ANS removal path with injected stop failures."""
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
out = Path(sys.argv[1]).resolve()
out.mkdir(parents=True, exist_ok=True)
source = (root / 'drivers/nvme/nvme_apple_apif.c').read_text()
body = source[source.index('static int ans_remove('):source.index('/* Validate the retained queue contract')]
harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>
#define NVME_Q_NUM 2
#define APPLE_RTKIT_PWR_STATE_QUIESCED 16
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
struct apple_nvme_apif {
    bool failed;
    struct {bool registered;} pending[2];
    void *rtkit;
    bool rtkit_release_failed;
    bool grants[5];
    uint64_t queue[4], scratch_pa;
};
struct udevice {struct apple_nvme_apif *priv;const char *name;};
static unsigned phase, releases, fault;
static struct apple_nvme_apif *dev_get_priv(struct udevice *dev) {return dev->priv;}
#define dev_err(dev,...) ((void)(dev))
static int nvme_retire_io_queues(struct udevice *dev) {
    (void)dev;assert(phase==0);phase=1;return fault==1?-EIO:0;
}
static int nvme_shutdown(struct udevice *dev) {
    (void)dev;assert(phase==1);phase=2;return fault==2?-ETIMEDOUT:0;
}
static int apple_rtkit_shutdown(void *rtkit,int state) {
    assert(rtkit && phase==2 && state==16);phase=3;return fault==3?-ETIMEDOUT:0;
}
static void apple_rtkit_free(void *rtkit) {assert(rtkit && phase==3);phase=4;}
static int ans_sart(uint64_t page,bool map) {
    assert(page && !map && phase==4);releases++;return fault==4?-EIO:0;
}
''' + body + r'''
int main(void) {
    struct apple_nvme_apif priv;
    struct udevice dev={&priv,"ans-fixture"};
    for(fault=0;fault<=4;fault++) {
        memset(&priv,0,sizeof(priv));phase=releases=0;
        priv.rtkit=&priv;
        for(unsigned i=0;i<5;i++)priv.grants[i]=true;
        for(unsigned i=0;i<4;i++)priv.queue[i]=0x100000+i*0x4000;
        priv.scratch_pa=0x110000;
        int ret=ans_remove(&dev);
        if(!fault) {
            assert(!ret && releases==5 && priv.rtkit==NULL);
            for(unsigned i=0;i<5;i++)assert(!priv.grants[i]);
        } else {
            assert(ret<0 && phase==fault);
            assert(releases==(fault==4?1:0));
            for(unsigned i=0;i<5;i++)assert(priv.grants[i]);
            if(fault<4)assert(priv.rtkit!=NULL);
        }
    }
    phase=releases=0;priv.failed=true;
    assert(ans_remove(&dev)==-EIO && !phase && !releases);
    priv.failed=false;priv.pending[1].registered=true;
    assert(ans_remove(&dev)==-EBUSY && !phase && !releases);
    puts("PASS ANS queue-retire/controller-stop/RTKit/release ordering; all failure boundaries retain ownership");
}
'''
path = out / 'stop.c'
path.write_text(harness)
binary = out / 'stop'
subprocess.run(['cc', '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                '-fsanitize=address,undefined', str(path), '-o', str(binary)], check=True)
subprocess.run([str(binary)], check=True)
