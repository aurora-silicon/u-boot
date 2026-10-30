# SPDX-License-Identifier: GPL-2.0-only OR MIT
"""Production pre-M4 ANS start from every state a previous stage leaves.

The driver's apple_nvme_boot() and the RTKit boot code in rtkit.c are built
against a host model of the ANS with a virtual clock.  The model follows what
the M2 (T8112) showed: a sleep request makes the firmware run its own NVMe
shutdown and saveCtx first, it answers INIT with HELLO only once it is asleep,
a PMGR reset does not stop a running CPU, and a CPU stopped before it has
acknowledged sleep never boots again.  This does not establish behaviour on
hardware.
"""
from pathlib import Path
import re
import subprocess


def function(text, name):
    match = re.search(r'^(?:static )?[^;{}()\n]*\b' + name + r'\([^;{}]*\)\s*\{',
                      text, re.M)
    assert match, name
    start, end, depth = match.start(), match.end(), 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end] + '\n'


def define(text, name):
    match = re.search(r'^#define\s+' + name + r'\b.*$', text, re.M)
    assert match, name
    return match.group(0) + '\n'


HARNESS = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uint8_t u8; typedef uint16_t u16; typedef uint32_t u32; typedef uint64_t u64;
typedef unsigned long ulong;
#define BIT(n) (1UL << (n))
#define GENMASK(h, l) (((~0UL) << (l)) & (~0UL >> (63 - (h))))
#define GENMASK_ULL(h, l) GENMASK(h, l)
#define FIELD_PREP(m, v) (((u64)(v) << __builtin_ctzll(m)) & (m))
#define FIELD_GET(m, v) (((u64)(v) & (m)) >> __builtin_ctzll(m))
#define min(a, b) ((a) < (b) ? (a) : (b))
#define min_t(t, a, b) ((t)(a) < (t)(b) ? (t)(a) : (t)(b))
#define ALIGN(x, a) (((x) + (a) - 1) & ~((a) - 1))
#define SZ_16K 0x4000
#define USEC_PER_SEC 1000000L
#define ENOTSUPP 524
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define memalign(a, s) aligned_alloc(a, s)
#define NVME_CC_ENABLE (1 << 0)
#define NVME_CC_SHN_NORMAL (1 << 14)
#define NVME_CC_SHN_MASK (3 << 14)
#define NVME_CSTS_RDY (1 << 0)
#define NVME_CSTS_SHST_CMPLT (2 << 2)
#define NVME_CSTS_SHST_MASK (3 << 2)

struct apple_mbox_msg { u64 msg0; u32 msg1; };
struct mbox_chan { int unused; };
'''

MODEL = r'''
struct nvme_bar { u64 cap; u32 vs, intms, intmc, cc, rsvd1, csts; };
struct nvme_dev { struct nvme_bar *bar; struct udevice *udev; };
struct udevice { void *priv; const char *name; };
struct reset_ctl_bulk { int unused; };
struct apple_sart { int unused; };
typedef struct { int unused; } ofnode;
struct apple_nvme_priv {
    struct nvme_dev ndev;
    char *base, *asc, *nvmmu;
    struct reset_ctl_bulk resets;
    struct mbox_chan chan;
    struct apple_sart *sart;
    struct apple_rtkit *rtk;
};
static void *dev_get_priv(struct udevice *d) { return d->priv; }

/* ---- The model ANS ---- */
enum fw { STOPPED, SAVING, ASLEEP, HANDSHAKE, ON, LOST };
static const char *fw_name[] = { "stopped", "saving", "asleep", "handshake", "on", "lost" };
static enum fw fw;
static bool late_ack;          /* the sleep that finishes owes its requester an ACK */
static u64 now, save_done;     /* virtual clock, us */
static int resets, run_clears, run_sets, sleeps_asked;
static struct apple_mbox_msg i2a[16];
static int i2a_len;
static struct apple_nvme_priv *model;

#define MGMT_TYPE(t) ((u64)(t) << 52)
#define T_HELLO 1
#define T_HELLO_REPLY 2
#define T_STARTEP 5
#define T_IOP_PWR 6
#define T_IOP_ACK 7
#define T_EPMAP 8
#define T_AP_PWR 11
#define PS_SLEEP 0x01
#define PS_QUIESCED 0x10
#define PS_ON 0x20
#define PS_INIT 0x220

static u32 *cpu_ctrl(void) { return (u32 *)(model->asc + REG_CPU_CTRL); }
static u32 *boot_status(void) { return (u32 *)(model->base + ANS_BOOT_STATUS); }
static void push(u64 msg0, u32 ep)
{
    assert(i2a_len < 16);
    i2a[i2a_len].msg0 = msg0;
    i2a[i2a_len].msg1 = ep;
    i2a_len++;
}
/* AP to IOP: a message waits in the FIFO until the firmware takes it. */
static struct apple_mbox_msg a2i[16];
static int a2i_len;
static void fw_rx(const struct apple_mbox_msg *m);
static void fw_tick(void)
{
    if (fw == SAVING && now >= save_done) {
        fw = ASLEEP;
        *boot_status() = 0;
        if (late_ack)
            push(MGMT_TYPE(T_IOP_ACK) | PS_SLEEP, 0);
        late_ack = false;
    }
    /* Busy saving, it does not read its mailbox. */
    while (a2i_len && fw != SAVING) {
        struct apple_mbox_msg m = a2i[0];

        memmove(a2i, a2i + 1, --a2i_len * sizeof(*a2i));
        fw_rx(&m);
    }
}
static void advance(u64 us) { now += us; fw_tick(); }
static void fw_rx(const struct apple_mbox_msg *m)
{
    u64 type = (m->msg0 >> 52) & 0xff, ps = m->msg0 & 0xffff;

    if (m->msg1 != 0 || fw == STOPPED || fw == LOST)
        return;     /* endpoint acks, or nobody is listening */
    switch (type) {
    case T_IOP_PWR:
        if (ps == PS_INIT && fw == ASLEEP) {
            fw = HANDSHAKE;
            push(MGMT_TYPE(T_HELLO) | (12 << 16) | 11, 0);
        } else if (ps == PS_INIT && fw == ON) {
            /* An awake session just acknowledges the state. */
            push(MGMT_TYPE(T_IOP_ACK) | PS_ON, 0);
        } else if (ps == PS_SLEEP && fw == ON) {
            sleeps_asked++;
            fw = SAVING;
            save_done = now + 300000;
            late_ack = true;
        }
        break;
    case T_HELLO_REPLY:
        assert(fw == HANDSHAKE);
        push(MGMT_TYPE(T_EPMAP) | BIT(51) | BIT(0), 0);
        break;
    case T_EPMAP:
        assert(fw == HANDSHAKE);
        fw = ON;
        *boot_status() = ANS_BOOT_STATUS_OK;
        push(MGMT_TYPE(T_IOP_ACK) | PS_ON, 0);
        break;
    case T_AP_PWR:
        push(MGMT_TYPE(T_AP_PWR) | ps, 0);
        break;
    }
}
static void run_changed(bool was)
{
    bool run = *cpu_ctrl() & REG_CPU_CTRL_RUN;

    if (was && !run) {
        run_clears++;
        /* Stopped before it acknowledged sleep: its context is gone. */
        fw = fw == ASLEEP ? STOPPED : LOST;
    } else if (!was && run) {
        run_sets++;
        if (fw == STOPPED)
            fw = ASLEEP;    /* boots and waits for INIT */
    }
}
static u32 readl(const volatile void *a) { advance(1); return *(const volatile u32 *)a; }
static void writel(u32 v, volatile void *a)
{
    struct nvme_bar *bar = model->ndev.bar;
    bool was = *cpu_ctrl() & REG_CPU_CTRL_RUN;

    *(volatile u32 *)a = v;
    if (a == (void *)&bar->cc) {
        if ((v & NVME_CC_SHN_MASK) == NVME_CC_SHN_NORMAL)
            bar->csts = (bar->csts & ~NVME_CSTS_SHST_MASK) | NVME_CSTS_SHST_CMPLT;
        if (!(v & NVME_CC_ENABLE))
            bar->csts &= ~NVME_CSTS_RDY;
    }
    if (a == (void *)cpu_ctrl())
        run_changed(was);
}
static void setbits_le32(volatile void *a, u32 v) { writel(readl(a) | v, a); }
static void clrbits_le32(volatile void *a, u32 v) { writel(readl(a) & ~v, a); }
static void udelay(ulong us) { advance(us); }
static void mdelay(ulong ms) { advance(ms * 1000); }
static ulong timer_get_us(void) { return now; }
#define readl_poll_sleep_timeout(addr, val, cond, sleep_us, timeout_us) ({ \
    int __ret = -ETIMEDOUT; u64 __end = now + (timeout_us);           \
    for (;;) {                                                          \
        (val) = readl(addr);                                            \
        if (cond) { __ret = 0; break; }                                 \
        if (now >= __end) break;                                        \
        advance(sleep_us);                                              \
    }                                                                   \
    __ret; })

static int reset_get_bulk(struct udevice *d, struct reset_ctl_bulk *b) { return 0; }
static int reset_assert_bulk(struct reset_ctl_bulk *b)
{
    /* A PMGR reset leaves a running CPU running (M2 syslog). */
    resets++;
    if (!(*cpu_ctrl() & REG_CPU_CTRL_RUN))
        *boot_status() = 0;
    return 0;
}
static int reset_deassert_bulk(struct reset_ctl_bulk *b) { return 0; }
static int mbox_get_by_index(struct udevice *d, int i, struct mbox_chan *c) { return 0; }
static int mbox_send(struct mbox_chan *c, const void *data)
{
    assert(a2i_len < 16);
    a2i[a2i_len++] = *(const struct apple_mbox_msg *)data;
    advance(1);
    return 0;
}
static int mbox_recv(struct mbox_chan *c, void *data, ulong timeout_us)
{
    u64 end = now + timeout_us;

    for (;;) {
        fw_tick();
        if (i2a_len) {
            *(struct apple_mbox_msg *)data = i2a[0];
            memmove(i2a, i2a + 1, --i2a_len * sizeof(*i2a));
            return 0;
        }
        if (now >= end)
            return -ETIMEDOUT;
        advance(100);
    }
}
static int dev_read_u32(struct udevice *d, const char *p, u32 *v) { *v = 1; return 0; }
static ofnode ofnode_get_by_phandle(u32 p) { ofnode n = {0}; return n; }
static struct apple_sart sart;
static struct apple_sart *sart_init(ofnode n) { return &sart; }
static int nvme_shmem_setup(void *c, struct apple_rtkit_buffer *b) { return -1; }
static void nvme_shmem_destroy(void *c, struct apple_rtkit_buffer *b) {}
static int apple_nvme_wait_disabled(struct apple_nvme_priv *priv, u32 *csts)
{
    *csts = readl(&priv->ndev.bar->csts);
    return *csts & NVME_CSTS_RDY ? -ETIMEDOUT : 0;
}
'''

MAIN = r'''
int main(int argc, char **argv)
{
    static struct apple_nvme_priv priv;
    static struct nvme_bar bar;
    struct udevice dev = { &priv, "nvme" };
    int scenario = atoi(argv[1]), ret;
    u64 t0;

    model = &priv;
    priv.base = calloc(1, 0x30000);
    priv.asc = calloc(1, 0x100);
    priv.nvmmu = priv.base;
    priv.ndev.bar = &bar;
    priv.ndev.udev = &dev;

    switch (scenario) {
    case 0: /* Previous stage completed its sleep: stopped, a stale ACK queued. */
        fw = STOPPED;
        push(MGMT_TYPE(T_IOP_ACK) | PS_SLEEP, 0);
        break;
    case 1: /* iBoot style: asleep with RUN set. */
        fw = ASLEEP;
        *cpu_ctrl() = REG_CPU_CTRL_RUN;
        break;
    case 2: /* aura Stage 1: its sleep request timed out, RUN left set and
             * the PMGR reset hit a running CPU; saveCtx still has 1.2 s to go
             * and the late ACK lands first. */
        fw = SAVING;
        save_done = 1200000;
        late_ack = true;
        *cpu_ctrl() = REG_CPU_CTRL_RUN;
        reset_assert_bulk(NULL);
        reset_deassert_bulk(NULL);
        resets = 0;
        break;
    case 3: /* A previous stage left its session awake, controller enabled. */
        fw = ON;
        *cpu_ctrl() = REG_CPU_CTRL_RUN;
        *boot_status() = ANS_BOOT_STATUS_OK;
        bar.cc = NVME_CC_ENABLE;
        bar.csts = NVME_CSTS_RDY;
        break;
    case 4: /* A firmware that says nothing at all. */
        fw = LOST;
        *cpu_ctrl() = REG_CPU_CTRL_RUN;
        break;
    }

    t0 = now;
    ret = apple_nvme_boot(&dev);
    fprintf(stderr, "scenario %d: ret %d fw %s after %llu ms, resets %d, RUN cleared %d\n",
            scenario, ret, fw_name[fw], (unsigned long long)(now - t0) / 1000,
            resets, run_clears);

    switch (scenario) {
    case 0:
    case 1:
        assert(!ret && fw == ON && resets == 0 && run_clears == 0);
        break;
    case 2:
        /* Never stopped mid-saveCtx; waited it out instead. */
        assert(!ret && fw == ON && resets == 0 && run_clears == 0);
        assert(now - t0 < 5 * USEC_PER_SEC);
        break;
    case 3:
        /* Shut down, slept, then the one restart Linux would do. */
        assert(!ret && fw == ON && sleeps_asked == 1);
        assert(resets == 1 && run_clears == 1);
        assert(!(bar.cc & NVME_CC_ENABLE));
        break;
    case 4:
        /* Gives up in bounded time and does not stop a CPU it cannot restart. */
        assert(ret < 0 && run_clears == 0 && resets == 0);
        assert(now - t0 <= 6 * USEC_PER_SEC);
        break;
    }
    assert(i2a_len == 0 || scenario == 4);
    return 0;
}
'''


def test_apple_nvme_starts_an_inherited_ans(tmp_path):
    root = Path(__file__).resolve().parents[3]
    nvme = (root / 'drivers/nvme/nvme_apple.c').read_text()
    rtkit = (root / 'arch/arm/mach-apple/rtkit.c').read_text()
    header = (root / 'arch/arm/include/asm/arch-apple/rtkit.h').read_text()

    hdr = header[:header.index('#include <dm/ofnode.h>')]
    rtk_defs = rtkit[rtkit.index('#define APPLE_RTKIT_EP_MGMT'):
                     rtkit.index('struct apple_rtkit *apple_rtkit_init(')]
    rtk_code = ''.join(function(rtkit, name) for name in (
        'apple_rtkit_init', 'apple_rtkit_free', 'rtkit_handle_buf_req',
        'apple_rtkit_poll', 'rtkit_wait_inherited_hello', 'rtkit_boot',
        'apple_rtkit_boot_inherited', 'apple_rtkit_sleep_inherited'))
    regs = nvme[nvme.index('/* ASC registers */'):nvme.index('/*\n * RTKit messages')]
    nvme_code = define(nvme, 'ANS_MAX_QUEUE_DEPTH') + \
        define(nvme, 'ANS_NVMMU_TCB_SIZE') + define(nvme, 'ANS_NVMMU_TCB_PITCH') + \
        define(nvme, 'ANS_PWR_TIMEOUT_US') + ''.join(function(nvme, name) for name in (
            'apple_nvme_drain', 'apple_nvme_restart_awake', 'apple_nvme_boot'))

    unit = HARNESS + hdr + regs + MODEL + rtk_defs + rtk_code + nvme_code + MAIN
    binary = tmp_path / 'apple-nvme-inherited'
    (tmp_path / 'unit.c').write_text(unit)
    subprocess.run(['cc', '-std=gnu11', '-Wall', '-Werror', '-Wno-unused-function',
                    '-Wno-unused-parameter', '-Wno-unused-variable',
                    '-Wno-unused-but-set-variable', str(tmp_path / 'unit.c'),
                    '-o', str(binary)], check=True)
    for scenario in range(5):
        subprocess.run([str(binary), str(scenario)], check=True)
