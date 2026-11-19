# SPDX-License-Identifier: GPL-2.0-or-later OR MIT
"""Check the legacy MTP handoff against real libfdt, including refusal cases."""
from pathlib import Path
import subprocess


def test_helper_reserves_live_buffers(tmp_path):
    repo = Path(__file__).resolve().parents[3]
    text = (repo / 'arch/arm/mach-apple/rtkit_helper.c').read_text()
    start = text.index('static int rtkit_helper_fdt_publish_retained(')
    production = text[start:text.index('/*\n * Describe the running session', start)]
    fixup = text.index('int apple_rtkit_helper_fdt_fixup(')
    production += text[fixup:text.index('static const struct udevice_id rtkit_helper_ids', fixup)]
    harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <errno.h>
#include <string.h>
#include <stdlib.h>
#include "libfdt.h"
typedef uint64_t u64;
typedef uint32_t u32;
#define REG_CPU_CTRL 0x44
#define REG_CPU_CTRL_RUN 0x10
#define RTKIT_HELPER_MAX_BUFS 8
#define RTKIT_HELPER_HANDOFF "mtp-live-v1"
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define DM_FLAG_LEAVE_PD_ON 0x2000
struct udevice { int flags; const char *name; void *driver; };
struct uclass { int unused; };
struct rtkit_helper_buf { u64 dva; size_t size; };
struct apple_rtkit_buffer { void *buffer; u64 dva; size_t size; bool is_mapped; int endpoint; };
struct rtkit_helper_priv {
 void *rtk, *dart, *asc;
 int nbufs;
 bool handoff_published, input_ready;
 const void *published_fdt;
 struct rtkit_helper_buf bufs[8];
};
static struct rtkit_helper_priv priv;
static u32 ctrl;
static int bypass_error;
static struct rtkit_helper_priv *dev_get_priv(struct udevice *dev) { return &priv; }
static u32 readl(void *addr) { return ctrl; }
static int apple_dart_retain_bypass(void *dart) { return bypass_error; }
static int apple_rtkit_get_buffers(void *rtk, struct apple_rtkit_buffer *buffers, size_t count)
{
 assert(count >= 3);
 buffers[0] = (struct apple_rtkit_buffer){.endpoint = 1, .dva = priv.bufs[0].dva, .size = 0x4000};
 buffers[1] = (struct apple_rtkit_buffer){.endpoint = 4, .dva = priv.bufs[1].dva, .size = 0x5000};
 buffers[2] = (struct apple_rtkit_buffer){.endpoint = 8, .dva = 0x394af8000ULL,
                                        .size = 0x4000, .is_mapped = true};
 if (!priv.nbufs) { buffers[0] = buffers[2]; return 1; }
 return 3;
}
static int rtkit_helper_fdt_node(void *blob, struct udevice *dev)
{ return fdt_path_offset(blob, "/mtp"); }
static void dev_or_flags(struct udevice *dev, int flags) { dev->flags |= flags; }
static struct udevice *active_dev;
static char driver_tag;
static int stops;
#define UCLASS_MISC 1
#define DM_DRIVER_GET(x) (&driver_tag)
#define uclass_id_foreach_dev(id, dev, uc) for (dev = active_dev, uc = NULL; dev && !uc; dev = NULL)
#define dev_warn(dev, ...) printf(__VA_ARGS__)
#define debug(...) ((void)0)
static bool device_active(struct udevice *dev) { return true; }
static bool rtkit_helper_live(struct udevice *dev) { return true; }
static bool rtkit_helper_retained(struct udevice *dev) { return true; }
static int rtkit_helper_fdt_publish(struct udevice *dev, void *blob) { assert(false); return -1; }
static void rtkit_helper_stop(struct udevice *dev)
{ stops++; priv.rtk = NULL; priv.input_ready = priv.handoff_published = false; }

''' + production + r'''
int main(void)
{
 for (int fault = 0; fault < 8; fault++) {
  char blob[4096];
  struct udevice dev = {.driver = &driver_tag};
  active_dev = &dev; stops = 0;
  assert(!fdt_create_empty_tree(blob, sizeof(blob)));
  if (fault != 5) assert(fdt_add_subnode(blob, 0, "mtp") >= 0);
  int resv = fdt_add_subnode(blob, 0, "reserved-memory");
  assert(resv >= 0);
  assert(!fdt_setprop_u32(blob, resv, "#address-cells", 2));
  assert(!fdt_setprop_u32(blob, resv, "#size-cells", fault == 7 ? 1 : 2));
  assert(!fdt_setprop_empty(blob, resv, "ranges"));
  priv = (struct rtkit_helper_priv){.rtk = (void *)1, .dart = (void *)2, .nbufs = 2, .input_ready = fault != 6};
  priv.bufs[0] = (struct rtkit_helper_buf){0x9ffff0000ULL, 0x4000};
  priv.bufs[1] = (struct rtkit_helper_buf){0x9fffe0000ULL, 0x8000};
  ctrl = REG_CPU_CTRL_RUN; bypass_error = 0;
  if (fault == 1) priv.rtk = NULL;
  if (fault == 2) priv.nbufs = 9;
  if (fault == 3) ctrl = 0;
  if (fault == 4) bypass_error = -EPERM;
  int result = rtkit_helper_fdt_publish_retained(&dev, blob);
  if (fault) {
   assert(result < 0 && !priv.handoff_published && !dev.flags);
   assert(!apple_rtkit_helper_fdt_fixup(blob) && stops == 1);
   assert(!priv.rtk && !priv.input_ready && !priv.handoff_published);
   int mtp = fdt_path_offset(blob, "/mtp");
   if (mtp >= 0) {
    assert(!fdt_getprop(blob, mtp, "linux-enablement-mac,rtkit-inherited", NULL));
    const char *status = fdt_getprop(blob, mtp, "status", NULL);
    assert(status && !strcmp(status, "disabled"));
   }
  } else {
   assert(!result && priv.handoff_published && (dev.flags & DM_FLAG_LEAVE_PD_ON));
   assert(fdt_num_mem_rsv(blob) == 0);
   for (int i = 0; i < 2; i++) {
    char path[96];
    snprintf(path, sizeof(path), "/reserved-memory/mtp-rtkit@%llx", (unsigned long long)priv.bufs[i].dva);
    int node = fdt_path_offset(blob, path), len;
    const fdt64_t *reg = fdt_getprop(blob, node, "reg", &len);
    assert(reg && len == 16);
    assert(fdt64_to_cpu(reg[0]) == priv.bufs[i].dva);
    assert(fdt64_to_cpu(reg[1]) == priv.bufs[i].size);
    assert(fdt_getprop(blob, node, "no-map", &len) && !len);
   }
   priv.handoff_published = false;
   assert(!apple_rtkit_helper_fdt_fixup(blob) && stops == 0);
   int len;
   const fdt32_t *v = fdt_getprop(blob, fdt_path_offset(blob, "/mtp"),
      "linux-enablement-mac,rtkit-inherited", &len);
   assert(v && len == 4 && fdt32_to_cpu(*v) == 1);
   int mtp = fdt_path_offset(blob, "/mtp");
   const char *typed = fdt_getprop(blob, mtp, "apple,rtkit-handoff", NULL);
   assert(typed && !strcmp(typed, "mtp-live-v1"));
   const fdt32_t *inventory = fdt_getprop(blob, mtp, "apple,rtkit-buffers", &len);
   assert(inventory && len == 15 * 4);
   assert(fdt32_to_cpu(inventory[0]) == 1 && fdt32_to_cpu(inventory[5]) == 4);
   assert(fdt32_to_cpu(inventory[10]) == 8 && fdt32_to_cpu(inventory[14]) == 1);
   assert(fdt32_to_cpu(inventory[8]) == 0x5000); /* Not rounded reservation size. */
   const fdt32_t *regions = fdt_getprop(blob, mtp, "memory-region", &len);
   assert(regions && len == 2 * 4);
   for (int i = 0; i < 2; i++) {
    int node = fdt_node_offset_by_phandle(blob, fdt32_to_cpu(regions[i]));
    assert(node >= 0 && fdt_getprop(blob, node, "no-map", NULL));
   }
   priv.nbufs = 0; /* A session using SRAM alone needs no DRAM phandle array. */
   assert(!apple_rtkit_helper_fdt_fixup(blob));
   mtp = fdt_path_offset(blob, "/mtp");
   assert(!fdt_getprop(blob, mtp, "memory-region", NULL));
   char failed[4096], saved[4096];
   memcpy(saved, blob, sizeof(blob));
   assert(!fdt_open_into(blob, failed, sizeof(failed)));
   int r = fdt_path_offset(failed, "/reserved-memory");
   assert(!fdt_setprop_u32(failed, r, "#size-cells", 1));
   assert(apple_rtkit_helper_fdt_fixup(failed) < 0 && stops == 0);
   assert(priv.rtk && priv.input_ready && priv.handoff_published);
   assert(!memcmp(saved, blob, sizeof(blob)));
   assert(fdt_getprop(blob, fdt_path_offset(blob, "/mtp"), "apple,rtkit-handoff", NULL));
   assert(!fdt_getprop(failed, fdt_path_offset(failed, "/mtp"), "apple,rtkit-handoff", NULL));
   /* Failure while republishing the original table keeps it unchanged. */
   bypass_error = -EPERM;
   assert(!apple_rtkit_helper_fdt_fixup(blob) && stops == 0);
   assert(!memcmp(saved, blob, sizeof(blob)) && priv.handoff_published);
  }
 }
 return 0;
}
'''
    source = tmp_path / 'helper.c'
    source.write_text(harness)
    binary = tmp_path / 'helper'
    libfdt = repo / 'scripts/dtc/libfdt'
    sources = [libfdt / (n + '.c') for n in ('fdt', 'fdt_ro', 'fdt_rw', 'fdt_sw', 'fdt_wip', 'fdt_empty_tree', 'fdt_strerror', 'fdt_addresses')]
    subprocess.run(['cc', '-D_GNU_SOURCE', '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-Wno-format', '-I', str(libfdt), str(source), *map(str, sources), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)


def test_helper_stops_dma_before_reclaiming(tmp_path):
    repo = Path(__file__).resolve().parents[3]
    text = (repo / 'arch/arm/mach-apple/rtkit_helper.c').read_text()
    start = text.index('static void rtkit_helper_stop(')
    production = text[start:text.index('static int rtkit_helper_remove(', start)]
    harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
typedef uint32_t u32;
#define REG_CPU_CTRL 0x44
#define REG_CPU_CTRL_RUN 0x10
#define APPLE_RTKIT_PWR_STATE_QUIESCED 0x10
struct udevice { int unused; };
struct rtkit_helper_priv {
 void *rtk, *asc, *dart;
 bool handoff_published, input_ready;
 const void *published_fdt;
 int nbufs;
};
static struct rtkit_helper_priv priv;
static u32 ctrl;
static int shutdown_error, quiesces, frees, releases, flushed;
static struct rtkit_helper_priv *dev_get_priv(struct udevice *d) { return &priv; }
static int apple_rtkit_shutdown(void *rtk, int state)
{ assert(ctrl & REG_CPU_CTRL_RUN); quiesces++; return shutdown_error; }
static u32 readl(void *addr) { if (!(ctrl & REG_CPU_CTRL_RUN)) flushed++; return ctrl; }
static void writel(u32 value, void *addr) { ctrl = value; }
static void apple_rtkit_free(void *rtk)
{
 assert(!(ctrl & REG_CPU_CTRL_RUN) && flushed);
 assert(!priv.handoff_published && !priv.input_ready);
 frees++;
}
static void apple_dart_release_bypass(void *dart)
{ assert(!priv.rtk && !(ctrl & REG_CPU_CTRL_RUN)); releases++; }
''' + production + r'''
int main(void)
{
 struct udevice dev;
 for (shutdown_error = -1; shutdown_error <= 0; shutdown_error++) {
  priv = (struct rtkit_helper_priv){.rtk = (void *)1, .asc = (void *)2,
         .dart = (void *)3, .handoff_published = true, .input_ready = true, .nbufs = 2};
  ctrl = REG_CPU_CTRL_RUN | 1;
  quiesces = frees = releases = flushed = 0;
  rtkit_helper_stop(&dev);
  assert(ctrl == 1 && quiesces == 1 && frees == 1 && releases == 1);
  assert(!priv.nbufs && !priv.rtk);
  rtkit_helper_stop(&dev); /* Failed repeated FDT preparation is harmless. */
  assert(quiesces == 1 && frees == 1 && releases == 2);
 }
 return 0;
}
'''
    source = tmp_path / 'stop.c'
    source.write_text(harness)
    binary = tmp_path / 'stop'
    subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                    '-Wno-unused-parameter', str(source), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)


def test_retained_input_finishes_late_startup_requests(tmp_path):
    repo = Path(__file__).resolve().parents[3]
    text = (repo / 'arch/arm/mach-apple/board.c').read_text()
    start = text.index('static int apple_mtp_prepare_retained_input(')
    production = text[start:text.index('\n}', start) + 2]
    harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
typedef int ofnode;
typedef uint64_t fdt_addr_t;
typedef unsigned long ulong;
#define UCLASS_MISC 1
#define FDT_ADDR_T_NONE UINT64_MAX
#define debug(...) ((void)0)
struct udevice { int unused; };
static struct udevice helper;
static int polls, fault, ready;
static ulong ticks;
static int uclass_get_device_by_ofnode(int cls, ofnode n, struct udevice **dev)
{ assert(n == 1); *dev = &helper; return fault == 1 ? -EIO : 0; }
static int ofnode_stringlist_search(ofnode n, const char *p, const char *s)
{ assert(n == 2 && !strcmp(s, "data")); return 3; }
static fdt_addr_t ofnode_get_addr_index(ofnode n, int index) { return 0x394b34000; }
static uint32_t readl(void *addr)
{ assert((uintptr_t)addr == 0x394b3402c); return fault == 0 && polls >= 3 ? 8 : 0; }
static ulong get_timer(ulong base) { return ticks - base; }
static int apple_rtkit_helper_poll(struct udevice *dev, ulong timeout)
{ assert(!timeout); polls++; return fault == 3 ? -EIO : 0; }
static void udelay(unsigned usec) { ticks++; }
static void apple_rtkit_helper_input_ready(struct udevice *dev) { ready++; }
''' + production + r'''
int main(void)
{
 for (fault = 0; fault < 4; fault++) {
  polls = ticks = ready = 0;
  assert(apple_mtp_prepare_retained_input(1, 2) == (fault == 0 ? 0 : fault == 2 ? -ETIMEDOUT : -EIO));
  assert(ready == (fault == 0));
  if (fault == 0) assert(polls == 3);
  if (fault == 1) assert(!polls);
  if (fault == 2) assert(polls == 2000);
  if (fault == 3) assert(polls == 1);
 }
 return 0;
}
'''
    source = tmp_path / 'startup.c'
    source.write_text(harness)
    binary = tmp_path / 'startup'
    subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', str(source), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)


def test_helper_stopped_poll_and_frozen_inventory(tmp_path):
    """A published handoff cannot acquire new firmware-visible buffers."""
    import re
    root = Path(__file__).resolve().parents[3]
    text = (root / 'arch/arm/mach-apple/rtkit_helper.c').read_text()
    def function(name):
        match = re.search(r'^(?:static )?(?:int|bool|void) ' + name + r'\([^;{}]*\)\s*\{', text, re.M)
        assert match, name
        start, end, depth = match.start(), match.end(), 1
        while depth:
            depth += (text[end] == '{') - (text[end] == '}')
            end += 1
        return text[start:end] + '\n'
    unit = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <errno.h>
typedef uint64_t u64;typedef uint32_t u32;typedef unsigned long ulong;
typedef uint64_t fdt_size_t;typedef uint64_t fdt_addr_t;
#define SZ_16K 16384
#define ALIGN(v,a) (((v)+(a)-1)&~((a)-1))
#define ALIGN_DOWN(v,a) ((v)&~((a)-1))
#define FDT_ADDR_T_NONE UINT64_MAX
#define APPLE_RTKIT_EP_OSLOG 8
#define RTKIT_HELPER_MAX_BUFS 8
#define DM_FLAG_LEAVE_PD_ON 0x2000
#define dev_err(...) ((void)0)
struct rtkit_helper_buf {u64 dva;size_t size;};
struct rtkit_helper_priv {
 void *rtk;bool handoff_published,input_ready,sram_stolen;int nbufs;
 struct rtkit_helper_buf bufs[8];
};
struct udevice {struct rtkit_helper_priv *priv;bool active;u32 flags;};
struct apple_rtkit_buffer {void *buffer;u64 dva;size_t size;bool is_mapped;int endpoint;};
static unsigned allocs,polls,sram_reads;static unsigned char arena[65536];
static void *dev_get_priv(struct udevice *d){return d->priv;}
static bool device_active(struct udevice *d){return d->active;}
static u32 dev_get_flags(struct udevice *d){return d->flags;}
static int apple_rtkit_poll(void *rtk,ulong timeout){assert(rtk==(void *)1&&timeout==0);polls++;return 0;}
static bool rtkit_helper_dram_oslog(struct udevice *d){(void)d;return true;}
static void *rtkit_helper_alloc(struct rtkit_helper_priv *p,size_t size){(void)p;assert(size<=sizeof(arena));allocs++;return arena;}
static fdt_addr_t dev_read_addr_size_name(struct udevice *d,const char *n,fdt_size_t *size){
 (void)d;assert(!strcmp(n,"sram"));sram_reads++;*size=65536;return 0x100000;
}
static void flush_dcache_range(ulong start,ulong end){assert(end>=start);}
static u64 map_to_sysmem(void *p){assert(p==arena);return 0x200000;}
'''
    for name in ('shmem_setup', 'apple_rtkit_helper_poll', 'apple_rtkit_helper_input_prepared',
                 'apple_rtkit_helper_input_retained', 'apple_rtkit_helper_input_failed'):
        unit += function(name)
    unit += r'''
int main(void){
 struct rtkit_helper_priv p={.rtk=(void *)1,.input_ready=true};
 struct udevice d={.priv=&p,.active=true};
 assert(apple_rtkit_helper_input_prepared(&d));assert(!apple_rtkit_helper_poll(&d,0)&&polls==1);
 p.rtk=NULL;assert(!apple_rtkit_helper_input_prepared(&d));
 assert(apple_rtkit_helper_poll(&d,0)==-ENODEV&&polls==1);
 d.priv=NULL;assert(apple_rtkit_helper_poll(&d,0)==-ENODEV&&polls==1);
 assert(!apple_rtkit_helper_input_prepared(&d));
 d.flags=DM_FLAG_LEAVE_PD_ON;assert(apple_rtkit_helper_input_retained(&d));
 d.priv=&p;p.rtk=(void *)1;
 struct apple_rtkit_buffer b={.size=0x2000,.endpoint=1};
 assert(!shmem_setup(&d,&b)&&allocs==1&&p.nbufs==1&&b.dva==0x200000);
 p.handoff_published=true;
 struct apple_rtkit_buffer late={.size=0x4000,.endpoint=4};
 assert(shmem_setup(&d,&late)==-EBUSY&&allocs==1&&p.nbufs==1&&!late.buffer&&!late.dva);
 late.is_mapped=true;late.dva=0x100000;
 assert(shmem_setup(&d,&late)==-EBUSY&&!sram_reads&&allocs==1&&p.nbufs==1);
 apple_rtkit_helper_input_failed(&d);assert(p.input_ready);
 p.handoff_published=false;apple_rtkit_helper_input_failed(&d);assert(!p.input_ready);
 return 0;
}
'''
    c, binary = tmp_path / 'frozen.c', tmp_path / 'frozen'
    c.write_text(unit)
    subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=undefined', '-fno-sanitize-recover=all',
                    str(c), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)


def test_published_remove_never_stops_firmware_on_dart_failure(tmp_path):
    repo = Path(__file__).resolve().parents[3]
    text = (repo / 'arch/arm/mach-apple/rtkit_helper.c').read_text()
    start = text.index('static int rtkit_helper_remove(')
    production = text[start:text.index('int apple_rtkit_helper_poll(', start)]
    unit = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <errno.h>
#include <setjmp.h>
#define debug(...) ((void)0)
struct rtkit_helper_priv { bool handoff_published;void *rtk,*dart; };
struct udevice { const char *name;struct rtkit_helper_priv *priv; };
static int retention_error,stops,frees,panics;
static jmp_buf boundary;
static void *dev_get_priv(struct udevice *d) { return d->priv; }
static int apple_dart_retain_bypass(void *d) { assert(d);return retention_error; }
static void apple_rtkit_free(void *r) { assert(r);frees++; }
static void rtkit_helper_stop(struct udevice *d) { d->priv->rtk=NULL;stops++; }
static void __attribute__((noreturn)) panic(const char *fmt,...) {
 assert(fmt);panics++;longjmp(boundary,1);
}
''' + production + r'''
int main(void) {
 struct rtkit_helper_priv p={.handoff_published=true,.rtk=(void *)1,.dart=(void *)2};
 struct udevice d={.name="mtp",.priv=&p};retention_error=-ENODEV;
 if(!setjmp(boundary)){rtkit_helper_remove(&d);assert(false);}
 assert(panics==1&&!stops&&!frees&&p.rtk&&p.handoff_published);
 retention_error=0;assert(!rtkit_helper_remove(&d));assert(frees==1&&!stops&&!p.rtk);
 p.rtk=(void *)1;p.handoff_published=false;
 assert(!rtkit_helper_remove(&d)&&stops==1&&!p.rtk);
 /* Every published session, whatever its form, refuses OS entry. */
 p.rtk=(void *)1;p.handoff_published=true;retention_error=-EINVAL;
 if(!setjmp(boundary)){rtkit_helper_remove(&d);assert(false);}
 assert(panics==2&&stops==1&&p.rtk&&p.handoff_published);
 return 0;
}
'''
    c, binary = tmp_path / 'remove.c', tmp_path / 'remove'
    c.write_text(unit)
    subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=undefined', '-fno-sanitize-recover=all',
                    str(c), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)


def test_helper_handoff_follows_the_mtp(tmp_path):
    """The handoff is chosen from the MTP compatible, never from the board."""
    root = Path(__file__).resolve().parents[3]
    text = (root / 'arch/arm/mach-apple/rtkit_helper.c').read_text()
    start = text.index('enum apple_rtkit_helper_handoff apple_rtkit_helper_handoff(')
    end = text.index('static bool rtkit_helper_live(', start)
    allowed = text.index('bool apple_rtkit_helper_allowed(')
    allowed = text[allowed:text.index('\n}', allowed) + 2]
    unit = r'''
#include <assert.h>
#include <stdbool.h>
#include <string.h>
typedef const char *const *ofnode;
enum apple_rtkit_helper_handoff {
 APPLE_RTKIT_HELPER_RESTART, APPLE_RTKIT_HELPER_LIVE, APPLE_RTKIT_HELPER_OS,
};
static bool ofnode_device_is_compatible(ofnode node, const char *compat) {
 for (; *node; node++) if (!strcmp(*node, compat)) return true;
 return false;
}
''' + text[start:end] + allowed + r'''
#define MTP(soc) (const char *const[]){"apple," soc "-mtp", "apple," soc "-rtk-helper-asc4", "apple,mtp", "apple,rtk-helper-asc4", 0}
int main(void) {
 const struct { ofnode node; enum apple_rtkit_helper_handoff want; bool retained; } rows[] = {
  { MTP("t8112"), APPLE_RTKIT_HELPER_RESTART, false },
  { MTP("t6020"), APPLE_RTKIT_HELPER_RESTART, false },
  { (const char *const[]){"apple,rtk-helper-asc4", 0}, APPLE_RTKIT_HELPER_RESTART, false },
  { MTP("t8122"), APPLE_RTKIT_HELPER_LIVE, false },
  { MTP("t6030"), APPLE_RTKIT_HELPER_LIVE, false },
  { (const char *const[]){"apple,t6031-mtp", "apple,t8122-rtk-helper-asc4", "apple,mtp", "apple,rtk-helper-asc4", 0}, APPLE_RTKIT_HELPER_LIVE, false },
  { MTP("t8140"), APPLE_RTKIT_HELPER_LIVE, false },
  { MTP("t8132"), APPLE_RTKIT_HELPER_LIVE, true },
  { MTP("t6041"), APPLE_RTKIT_HELPER_LIVE, true },
  { MTP("t8142"), APPLE_RTKIT_HELPER_OS, false },
  { MTP("t6050"), APPLE_RTKIT_HELPER_OS, false },
 };
 for (unsigned i = 0; i < sizeof(rows) / sizeof(rows[0]); i++) {
  assert(apple_rtkit_helper_handoff(rows[i].node) == rows[i].want);
  assert(apple_rtkit_helper_retained(rows[i].node) == rows[i].retained);
  assert(apple_rtkit_helper_allowed(rows[i].node) == (rows[i].want != APPLE_RTKIT_HELPER_OS));
 }
 return 0;
}
'''
    c, binary = tmp_path / 'policy.c', tmp_path / 'policy'
    c.write_text(unit)
    subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                    str(c), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)


def test_live_publish_needs_ready_input_and_exports_inventory(tmp_path):
    """The generic live form: no publication before the keyboard's READY,
    reservations mapped 1:1 for the DART, and the buffer inventory."""
    repo = Path(__file__).resolve().parents[3]
    text = (repo / 'arch/arm/mach-apple/rtkit_helper.c').read_text()
    start = text.index('/*\n * Describe the running session')
    production = text[start:text.index('int apple_rtkit_helper_fdt_fixup(', start)]
    harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <errno.h>
#include <string.h>
#include "libfdt.h"
typedef uint64_t u64;
typedef uint32_t u32;
#define REG_CPU_CTRL 0x44
#define REG_CPU_CTRL_RUN 0x10
#define RTKIT_HELPER_MAX_BUFS 8
#define RTKIT_HELPER_HANDOFF "mtp-live-v1"
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define debug(...) ((void)0)
struct udevice { const char *path; };
struct rtkit_helper_buf { u64 dva; size_t size; };
struct apple_rtkit_buffer { void *buffer; u64 dva; size_t size; bool is_mapped; int endpoint; };
struct rtkit_helper_priv {
 void *rtk, *asc; struct udevice *dart; u32 dart_sid;
 int nbufs; bool handoff_published, input_ready;
 struct rtkit_helper_buf bufs[8];
};
static struct rtkit_helper_priv priv;
static int bypass_error;
static struct rtkit_helper_priv *dev_get_priv(struct udevice *dev) { return &priv; }
static u32 readl(void *addr) { return REG_CPU_CTRL_RUN; }
static int apple_dart_retain_bypass(struct udevice *dart) { return bypass_error; }
static int rtkit_helper_fdt_node(void *blob, struct udevice *dev)
{ return fdt_path_offset(blob, dev->path); }
static int apple_rtkit_get_buffers(void *rtk, struct apple_rtkit_buffer *b, size_t count)
{
 assert(count == 4);
 b[0] = (struct apple_rtkit_buffer){.endpoint = 2, .dva = 0x10100000000ULL, .size = 0x4000};
 b[1] = (struct apple_rtkit_buffer){.endpoint = 4, .dva = 0x10100010000ULL, .size = 0x6000};
 b[2] = (struct apple_rtkit_buffer){.endpoint = 8, .dva = 0x314cf0000ULL, .size = 0x4000, .is_mapped = true};
 return 3;
}
''' + production + r'''
static void tree(char *blob)
{
 assert(!fdt_create_empty_tree(blob, 8192));
 int soc;
 assert(fdt_add_subnode(blob, 0, "soc") >= 0);
 assert(fdt_add_subnode(blob, 0, "reserved-memory") >= 0);
 /* Offsets move as the tree grows: look every node up by path. */
 static const char *const paths[] = {"/", "/soc", "/reserved-memory"};
 for (int n = 0; n < 3; n++) {
  assert(!fdt_setprop_u32(blob, fdt_path_offset(blob, paths[n]), "#address-cells", 2));
  assert(!fdt_setprop_u32(blob, fdt_path_offset(blob, paths[n]), "#size-cells", 2));
 }
 soc = fdt_path_offset(blob, "/soc");
 assert(fdt_add_subnode(blob, soc, "mtp") >= 0);
 soc = fdt_path_offset(blob, "/soc");
 assert(fdt_add_subnode(blob, soc, "dart") >= 0);
}
int main(void)
{
 static char blob[8192];
 struct udevice dart = {"/soc/dart"}, dev = {"/soc/mtp"};
 for (int nbufs = 0; nbufs <= 2; nbufs += 2) {
  tree(blob);
  priv = (struct rtkit_helper_priv){.rtk = (void *)1, .dart = &dart, .dart_sid = 1, .nbufs = nbufs};
  priv.bufs[0] = (struct rtkit_helper_buf){0x10100000000ULL, 0x4000};
  priv.bufs[1] = (struct rtkit_helper_buf){0x10100010000ULL, 0x8000};
  /* No publication before the keyboard's READY: the OS would miss discovery. */
  assert(rtkit_helper_fdt_publish(&dev, blob) == -ENODEV && !priv.handoff_published);
  priv.input_ready = true;
  bypass_error = -EINVAL;
  assert(rtkit_helper_fdt_publish(&dev, blob) == -EINVAL && !priv.handoff_published);
  bypass_error = 0;
  assert(!rtkit_helper_fdt_publish(&dev, blob) && priv.handoff_published);
  int mtp = fdt_path_offset(blob, "/soc/mtp"), len;
  const char *h = fdt_getprop(blob, mtp, "apple,rtkit-handoff", NULL);
  assert(h && !strcmp(h, "mtp-live-v1"));
  const fdt32_t *inv = fdt_getprop(blob, mtp, "apple,rtkit-buffers", &len);
  assert(inv && len == 15 * 4);
  assert(fdt32_to_cpu(inv[0]) == 2 && fdt32_to_cpu(inv[5]) == 4 && fdt32_to_cpu(inv[10]) == 8);
  assert(fdt32_to_cpu(inv[8]) == 0x6000 && fdt32_to_cpu(inv[14]) == 1);
  const fdt32_t *regions = fdt_getprop(blob, mtp, "memory-region", &len);
  assert(nbufs ? regions && len == nbufs * 4 : !regions);
  for (int i = 0; i < nbufs; i++) {
   int r = fdt_node_offset_by_phandle(blob, fdt32_to_cpu(regions[i]));
   const fdt32_t *ia = fdt_getprop(blob, r, "iommu-addresses", &len);
   assert(r >= 0 && fdt_getprop(blob, r, "no-map", NULL) && ia && len == 5 * 4);
   assert(fdt32_to_cpu(ia[0]) == fdt_get_phandle(blob, mtp));
  }
  const fdt32_t *sid = fdt_getprop(blob, fdt_path_offset(blob, "/soc/dart"),
                                   "apple,bypass-handoff-streams", &len);
  assert(sid && len == 4 && fdt32_to_cpu(*sid) == 1);
 }
 return 0;
}
'''
    source = tmp_path / 'live.c'
    source.write_text(harness)
    binary = tmp_path / 'live'
    libfdt = repo / 'scripts/dtc/libfdt'
    sources = [libfdt / (n + '.c') for n in ('fdt', 'fdt_ro', 'fdt_rw', 'fdt_sw', 'fdt_wip', 'fdt_empty_tree', 'fdt_strerror', 'fdt_addresses')]
    subprocess.run(['cc', '-D_GNU_SOURCE', '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-Wno-format',
                    '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                    '-I', str(libfdt), str(source), *map(str, sources), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
