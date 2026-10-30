# SPDX-License-Identifier: GPL-2.0-only OR MIT
"""Production pre-M4 ANS start, including an ANS a previous stage left running.

MMIO, the mailbox, PMGR reset and RTKit are host models: the model ANS boots
its firmware and sends HELLO only when RUN is set on a stopped CPU after a
reset, and keeps whatever it last sent in its outbound FIFO.  This does not
establish behaviour on hardware.
"""
from pathlib import Path
import re
import subprocess


def function(text, name):
    match = re.search(r'^static [^;{}]*\b' + name + r'\([^;{}]*\)\s*\{', text, re.M)
    assert match, name
    start, end, depth = match.start(), match.end(), 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end] + '\n'


def test_apple_nvme_restarts_a_running_ans(tmp_path):
    root = Path(__file__).resolve().parents[3]
    text = (root / 'drivers/nvme/nvme_apple.c').read_text()
    # The driver's own register layout.
    regs = text[text.index('/* ASC registers */'):text.index('/*\n * RTKit messages')]
    code = ''.join(function(text, name) for name in (
        'apple_nvme_drain', 'apple_nvme_stop_ans', 'apple_nvme_start_ans',
        'apple_nvme_boot'))
    unit = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uint32_t u32; typedef uint64_t u64;
#define BIT(n) (1U << (n))
''' + regs + r'''
#define HELLO 1
#define IOP_PWR_ACK 7
struct udevice { void *priv; const char *name; };
struct mbox_chan { int unused; };
struct reset_ctl_bulk { int unused; };
struct apple_mbox_msg { u64 msg0; u32 msg1; };
struct apple_sart { int unused; };
struct apple_rtkit { int unused; };
typedef struct { int unused; } ofnode;
struct apple_nvme_priv {
    char base[0x30000], asc[0x100];
    void *nvmmu;
    struct reset_ctl_bulk resets;
    struct mbox_chan chan;
    struct apple_sart *sart;
    struct apple_rtkit *rtk;
};
static void *dev_get_priv(struct udevice *d) { return d->priv; }

/* The model ANS */
static bool cpu_running, firmware_dead;
static int fifo[8], fifo_len, resets, boots, broken_boots;
static struct apple_nvme_priv *model;
static u32 *cpu_ctrl(void) { return (u32 *)(model->asc + REG_CPU_CTRL); }
static u32 *boot_status(void) { return (u32 *)(model->base + ANS_BOOT_STATUS); }
static void push(int type) { assert(fifo_len < 8); fifo[fifo_len++] = type; }
static void run_changed(void)
{
    bool run = *cpu_ctrl() & REG_CPU_CTRL_RUN;
    if (run && !cpu_running) {
        /* A fresh start boots the firmware, which says HELLO. */
        cpu_running = true;
        if (broken_boots) {
            broken_boots--;
        } else if (!firmware_dead) {
            push(HELLO);
            *boot_status() = ANS_BOOT_STATUS_OK;
        }
    } else if (!run) {
        cpu_running = false;
    }
}
static u32 readl(const void *a) { return *(const volatile u32 *)a; }
static void writel(u32 v, void *a) { *(volatile u32 *)a = v; }
static void setbits_le32(void *a, u32 v) { *(u32 *)a |= v; run_changed(); }
static void clrbits_le32(void *a, u32 v) { *(u32 *)a &= ~v; run_changed(); }
static void mdelay(int ms) { (void)ms; }
static void udelay(int us) { (void)us; }
static int reset_get_bulk(struct udevice *d, struct reset_ctl_bulk *b) { return 0; }
static int reset_assert_bulk(struct reset_ctl_bulk *b)
{
    /* The block reset only takes on a stopped CPU in this model. */
    assert(!cpu_running);
    resets++;
    *boot_status() = 0;
    return 0;
}
static int reset_deassert_bulk(struct reset_ctl_bulk *b) { return 0; }
static int mbox_get_by_index(struct udevice *d, int i, struct mbox_chan *c) { return 0; }
static int mbox_recv(struct mbox_chan *c, void *data, unsigned long timeout)
{
    struct apple_mbox_msg *msg = data;
    if (!fifo_len)
        return -ETIMEDOUT;
    msg->msg0 = fifo[0];
    msg->msg1 = 0;
    memmove(fifo, fifo + 1, --fifo_len * sizeof(*fifo));
    return 0;
}
static int dev_read_u32(struct udevice *d, const char *p, u32 *v) { *v = 1; return 0; }
static ofnode ofnode_get_by_phandle(u32 p) { ofnode n = {0}; return n; }
static struct apple_sart sart;
static struct apple_sart *sart_init(ofnode n) { return &sart; }
static int nvme_shmem_setup(void *c, void *b) { return 0; }
static void nvme_shmem_destroy(void *c, void *b) {}
static struct apple_rtkit rtkit;
static int rtkits;
static struct apple_rtkit *apple_rtkit_init(struct mbox_chan *c, void *cookie, void *s, void *d)
{ rtkits++; return &rtkit; }
static void apple_rtkit_free(struct apple_rtkit *r) { rtkits--; }
/* Like the real one, the first message must be HELLO. */
static int apple_rtkit_boot(struct apple_rtkit *r)
{
    struct apple_mbox_msg msg;
    boots++;
    if (mbox_recv(NULL, &msg, 1000000))
        return -ETIMEDOUT;
    return msg.msg0 == HELLO ? 0 : -EINVAL;
}
#define readl_poll_sleep_timeout(addr, val, cond, sleep_us, timeout_us) \
    ((val) = readl(addr), (cond) ? 0 : -ETIMEDOUT)
''' + code + r'''
int main(int argc, char **argv)
{
    static struct apple_nvme_priv priv;
    struct udevice dev = { &priv, "nvme" };
    int scenario = atoi(argv[1]);

    model = &priv;
    priv.nvmmu = priv.base;
    switch (scenario) {
    case 0: /* A completed sleep: stopped and reset. Nothing to recover. */
        assert(!apple_nvme_boot(&dev));
        assert(resets == 0 && boots == 1);
        break;
    case 1: /* A sleep that timed out: RUN still set, its late answer queued. */
        *cpu_ctrl() = REG_CPU_CTRL_RUN;
        cpu_running = true;
        push(IOP_PWR_ACK);
        assert(!apple_nvme_boot(&dev));
        assert(resets == 1 && boots == 1);
        break;
    case 2: /* Stopped, but a stale message is still queued. */
        push(IOP_PWR_ACK);
        assert(!apple_nvme_boot(&dev));
        assert(resets == 0 && boots == 1);
        break;
    case 3: /* The first start fails once; a clean restart succeeds. */
        broken_boots = 1;
        assert(!apple_nvme_boot(&dev));
        assert(resets == 1 && boots == 2 && rtkits == 1);
        break;
    case 4: /* A firmware that never starts fails after one retry. */
        firmware_dead = true;
        assert(apple_nvme_boot(&dev) < 0);
        assert(resets == 1 && boots == 2);
        break;
    }
    assert(fifo_len == 0);
    assert(*cpu_ctrl() & REG_CPU_CTRL_RUN);
    return 0;
}
'''
    binary = tmp_path / 'apple-nvme-restart'
    subprocess.run(['cc', '-std=gnu11', '-Wall', '-Werror', '-Wno-unused-function',
                    '-Wno-unused-parameter', '-x', 'c', '-', '-o', str(binary)],
                   input=unit, text=True, check=True)
    for scenario in range(5):
        subprocess.run([str(binary), str(scenario)], check=True)
