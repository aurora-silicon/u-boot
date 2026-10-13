# SPDX-License-Identifier: GPL-2.0+ OR MIT
"""CPU-only production publisher tests with real libfdt and mock devices.

Run: python3 -m pytest -q test/host/test_apple_rtkit_handoff.py
No U-Boot instance or hardware connection is used.
"""
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def publisher(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("apple-rtkit-handoff")
    source_path = Path(os.environ.get("UBOOT_RTKIT_SOURCE", ROOT / "arch/arm/mach-apple/rtkit_helper.c"))
    source = source_path.read_text()
    structs = source[source.index("#define REG_CPU_CTRL"):source.index("static void rtkit_helper_read_dma_window")]
    functions = source[source.index("/* Node offsets move"):source.index("static const struct udevice_id rtkit_helper_ids")]
    board = (ROOT / "arch/arm/mach-apple/board.c").read_text()
    start = board.index("\tif (IS_ENABLED(CONFIG_APPLE_MTP_KEYB))", board.index("int ft_board_setup("))
    end = board.index("\n\t/*", start)
    admission = board[start:end]
    harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <errno.h>
#include <string.h>
#include "libfdt.h"
typedef unsigned long long u64;
typedef uint32_t u32;
#define BIT(n) (1U << (n))
#define SZ_16K 16384
#define CONFIG_APPLE_MTP_KEYB 1
#define IS_ENABLED(x) (x)
struct mbox_chan { int unused; };
struct udevice { const char *name; const char *path; void *priv; const void *driver; bool active, t8140; };
struct uclass { int unused; };
static int mock_driver;
static struct udevice *test_dev;
#define UCLASS_MISC 0
#define DM_DRIVER_GET(name) (&mock_driver)
#define uclass_id_foreach_dev(kind, dev, uc) \
 for ((uc)=NULL,(dev)=test_dev; (dev) && ((void)(uc),1); (dev)=NULL)
#define dev_warn(dev, ...) ((void)(dev), (void)printf(__VA_ARGS__))
static void *dev_get_priv(struct udevice *d) { return d->priv; }
static const char *dev_ofnode(struct udevice *d) { return d->path; }
static int ofnode_get_path(const char *p, char *out, size_t n) { if(strlen(p)>=n) return -EINVAL; strcpy(out,p); return 0; }
static bool device_is_compatible(struct udevice *d, const char *c) { return d->t8140 && !strcmp(c,"apple,t8140-rtk-helper-asc4"); }
static bool device_active(struct udevice *d) { return d->active; }
static u32 readl(void *p) { return *(u32 *)p; }
static bool retained, fail_alloc;
static int retain_error, writes, fault_at, allocations;
static int apple_dart_retain_bypass(struct udevice *d) { if(!d) return -ENODEV; if(retain_error) return retain_error; retained=true; return 0; }
static void apple_dart_release_bypass(struct udevice *d) __attribute__((unused));
static void apple_dart_release_bypass(struct udevice *d) { (void)d; retained=false; }
static void *scratch_alloc(size_t n) { if(fail_alloc) return NULL; void *p=malloc(n); if(p) allocations++; return p; }
static void scratch_free(void *p) { if(p) allocations--; free(p); }
static bool fail_write(void) { writes++; return fault_at && writes==fault_at; }
static int setprop(void *b,int n,const char *p,const void *v,int l) { return fail_write() ? -FDT_ERR_NOSPACE : fdt_setprop(b,n,p,v,l); }
static int setprop_u32(void *b,int n,const char *p,uint32_t v) { return fail_write() ? -FDT_ERR_NOSPACE : fdt_setprop_u32(b,n,p,v); }
static int setprop_empty(void *b,int n,const char *p) { return fail_write() ? -FDT_ERR_NOSPACE : fdt_setprop_empty(b,n,p); }
static int setprop_string(void *b,int n,const char *p,const char *v) { return fail_write() ? -FDT_ERR_NOSPACE : fdt_setprop(b,n,p,v,strlen(v)+1); }
static int appendprop(void *b,int n,const char *p,const void *v,int l) { return fail_write() ? -FDT_ERR_NOSPACE : fdt_appendprop(b,n,p,v,l); }
static int appendprop_u32(void *b,int n,const char *p,uint32_t v) { return fail_write() ? -FDT_ERR_NOSPACE : fdt_appendprop_u32(b,n,p,v); }
static int add_subnode(void *b,int n,const char *p) { return fail_write() ? -FDT_ERR_NOSPACE : fdt_add_subnode(b,n,p); }
#define malloc scratch_alloc
#define free scratch_free
#define fdt_setprop setprop
#define fdt_setprop_u32 setprop_u32
#undef fdt_setprop_empty
#define fdt_setprop_empty setprop_empty
#undef fdt_setprop_string
#define fdt_setprop_string setprop_string
#define fdt_appendprop appendprop
#define fdt_appendprop_u32 appendprop_u32
#define fdt_add_subnode add_subnode
''' + structs + functions + r'''
#undef malloc
#undef free
#undef fdt_setprop
#undef fdt_setprop_u32
#undef fdt_setprop_empty
#undef fdt_setprop_string
#undef fdt_appendprop
#undef fdt_appendprop_u32
#undef fdt_add_subnode
static int board_admit(void *blob) { int ret;
''' + admission + r'''
return 0; }
static union { uint64_t align; char bytes[16384]; } blob_storage, snapshot;
#define blob (blob_storage.bytes)
static uint32_t regs[32];
static struct udevice dart={.name="dart",.path="/dart",.active=true};
static struct rtkit_helper_priv priv;
static struct udevice dev={.name="helper",.path="/helper",.priv=&priv,.driver=&mock_driver,.active=true,.t8140=true};
static int lookup(const char *p) { int n=fdt_path_offset(blob,p); assert(n>=0); return n; }
static void init(void) {
 memset(&priv,0,sizeof(priv));memset(blob,0,sizeof(blob_storage.bytes));
 regs[REG_CPU_CTRL/4]=REG_CPU_CTRL_RUN;
 priv.asc=regs;priv.rtk=(struct apple_rtkit *)regs;priv.dart=&dart;priv.dart_sid=3;
 priv.nbufs=2;priv.bufs[0]=(struct rtkit_helper_buf){0x80000000,0x4000};
 priv.bufs[1]=(struct rtkit_helper_buf){0x80004000,0x8000};
 retained=false;fail_alloc=false;retain_error=0;fault_at=0;writes=0;allocations=0;
 dev.active=true;dev.t8140=true;test_dev=&dev;
 assert(!fdt_create_empty_tree(blob,sizeof(blob_storage.bytes)));
 assert(!fdt_setprop_u32(blob,0,"#address-cells",2));
 assert(!fdt_setprop_u32(blob,0,"#size-cells",2));
 int n=fdt_add_subnode(blob,0,"reserved-memory");assert(n>=0);
 assert(!fdt_setprop_u32(blob,n,"#address-cells",2));
 assert(!fdt_setprop_u32(blob,n,"#size-cells",2));
 assert(!fdt_setprop(blob,n,"ranges",NULL,0));
 assert(fdt_add_subnode(blob,0,"helper")>=0);
 assert(fdt_add_subnode(blob,0,"dart")>=0);
 n=fdt_add_subnode(blob,lookup("/reserved-memory"),"unrelated@90000000");assert(n>=0);
 assert(!fdt_setprop_u32(blob,n,"phandle",0x40));
 assert(!fdt_setprop(blob,n,"owner","unrelated",sizeof("unrelated")));
 assert(!fdt_setprop_u32(blob,lookup("/helper"),"memory-region",0x40));
 assert(!fdt_setprop_u32(blob,lookup("/dart"),"apple,bypass-handoff-streams",7));
}
static void verify(void) {
 assert(priv.handoff_published && retained && allocations==0);
 int len,n=lookup("/helper");u32 phandle=fdt_get_phandle(blob,n);assert(phandle);
 const char *c=fdt_getprop(blob,n,"apple,rtkit-handoff",&len);
 assert(c && len==(int)sizeof("mtp-live-v1") && !strcmp(c,"mtp-live-v1"));
 const fdt32_t *regions=fdt_getprop(blob,n,"memory-region",&len);assert(regions && len==12);
 assert(fdt32_to_cpu(regions[0])==0x40);
 for(int i=0;i<2;i++) {
  int r=fdt_node_offset_by_phandle(blob,fdt32_to_cpu(regions[i+1]));assert(r>=0);
  const fdt64_t *reg=fdt_getprop(blob,r,"reg",&len);assert(reg && len==16);
  assert(fdt64_to_cpu(reg[0])==priv.bufs[i].dva && fdt64_to_cpu(reg[1])==priv.bufs[i].size);
  assert(fdt_getprop(blob,r,"no-map",&len) && len==0);
  const fdt32_t *map=fdt_getprop(blob,r,"iommu-addresses",&len);assert(map && len==20);
  assert(fdt32_to_cpu(map[0])==phandle && !memcmp(map+1,reg,16));
 }
 const fdt32_t *streams=fdt_getprop(blob,lookup("/dart"),"apple,bypass-handoff-streams",&len);
 assert(streams && len==8 && fdt32_to_cpu(streams[0])==7 && fdt32_to_cpu(streams[1])==3);
 c=fdt_getprop(blob,lookup("/reserved-memory/unrelated@90000000"),"owner",&len);
 assert(c && !strcmp(c,"unrelated"));
}
static void save(void) { memcpy(snapshot.bytes,blob,sizeof(blob_storage.bytes)); }
static void unchanged(bool published) {
 assert(!memcmp(snapshot.bytes,blob,sizeof(blob_storage.bytes)));
 assert(priv.handoff_published==published && retained==published && allocations==0);
}
static void negative(const char *name) {
 if(!strcmp(name,"allocation")) fail_alloc=true;
 else if(!strcmp(name,"bypass")) retain_error=-EINVAL;
 else if(!strcmp(name,"cpu")) regs[REG_CPU_CTRL/4]=0;
 else if(!strcmp(name,"count")) priv.nbufs=9;
 else if(!strcmp(name,"zero-count")) priv.nbufs=0;
 else if(!strcmp(name,"size")) priv.bufs[0].size=0;
 else if(!strcmp(name,"overflow")) priv.bufs[0]=(struct rtkit_helper_buf){0xffffffffffffc000ULL,0x8000};
 else if(!strcmp(name,"alignment")) priv.bufs[0].dva++;
 else if(!strcmp(name,"overlap")) priv.bufs[1].dva=priv.bufs[0].dva;
 else if(!strcmp(name,"missing-reservations")) assert(!fdt_del_node(blob,lookup("/reserved-memory")));
 else if(!strcmp(name,"missing-helper")) assert(!fdt_del_node(blob,lookup("/helper")));
 else if(!strcmp(name,"missing-dart")) assert(!fdt_del_node(blob,lookup("/dart")));
 else if(!strcmp(name,"cells")) assert(!fdt_setprop_u32(blob,lookup("/reserved-memory"),"#size-cells",1));
 else if(!strcmp(name,"region-cells")) assert(!fdt_setprop(blob,lookup("/helper"),"memory-region","x",1));
 else if(!strcmp(name,"stream-cells")) assert(!fdt_setprop(blob,lookup("/dart"),"apple,bypass-handoff-streams","x",1));
 else if(!strcmp(name,"foreign-contract")) assert(!fdt_setprop(blob,lookup("/helper"),"apple,rtkit-handoff","other",6));
 else if(!strcmp(name,"collision")) {
  int n=fdt_add_subnode(blob,lookup("/reserved-memory"),"mtp-rtkit@80000000");assert(n>=0);
  assert(!fdt_setprop_u32(blob,n,"phandle",0x50));
  assert(!fdt_setprop(blob,n,"reg","wrong",6));
 } else if(!strcmp(name,"space")) assert(!fdt_pack(blob));
 else assert(0);
 save();assert(board_admit(blob)<0);unchanged(false);
}
int main(int argc,char **argv) {
 (void)scratch_alloc;(void)scratch_free;
 assert(argc==2);init();const char *name=argv[1];
 if(!strcmp(name,"success")) { assert(!board_admit(blob));verify(); }
 else if(!strcmp(name,"repeat")) {
  assert(!board_admit(blob));verify();save();
  for(int i=0;i<5;i++) { assert(!board_admit(blob));verify();unchanged(true); }
 } else if(!strcmp(name,"write-failures")) {
  assert(!board_admit(blob));int total=writes;assert(total>=10);
  for(int i=1;i<=total;i++) { init();fault_at=i;save();assert(board_admit(blob)<0);unchanged(false); }
  printf("injected failure at each of %d publication writes\n",total);
 } else if(!strcmp(name,"published-failure")) {
  assert(!board_admit(blob));verify();save();fail_alloc=true;
  assert(board_admit(blob)==-ENOMEM);unchanged(true);verify();
  fail_alloc=false;writes=0;fault_at=1;
  assert(board_admit(blob)<0);unchanged(true);verify();
  fault_at=0;retain_error=-EINVAL;assert(board_admit(blob)==-EINVAL);unchanged(true);verify();
 } else if(!strcmp(name,"skip")) {
  save();dev.t8140=false;assert(!board_admit(blob));unchanged(false);
  dev.t8140=true;dev.active=false;assert(!board_admit(blob));unchanged(false);
 } else negative(name);
 puts("PASS");return 0;
}
'''
    c = tmp / "publisher.c"
    c.write_text(harness)
    binary = tmp / "publisher"
    libfdt = ROOT / "scripts/dtc/libfdt"
    command = [os.environ.get("CC", "cc"), "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-I" + str(libfdt), str(c)]
    command += [str(p) for p in sorted(libfdt.glob("fdt*.c"))]
    command += ["-o", str(binary)]
    subprocess.run(command, check=True)
    return binary


@pytest.mark.parametrize("case", [
    "success", "repeat", "write-failures", "published-failure", "skip",
    "allocation", "bypass", "cpu", "count", "zero-count", "size", "overflow",
    "alignment", "overlap", "missing-reservations", "missing-helper", "missing-dart",
    "cells", "region-cells", "stream-cells", "foreign-contract", "collision", "space",
])
def test_rtkit_publication(publisher, case):
    subprocess.run([str(publisher), case], check=True)
