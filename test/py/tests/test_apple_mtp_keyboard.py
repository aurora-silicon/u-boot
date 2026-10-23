# SPDX-License-Identifier: GPL-2.0-only OR MIT
"""Production DockChannel discovery, key delivery and live-session replay.

MMIO, timing and helper removal are host boundaries. This does not establish
physical keyboard operation, EFI input delivery or Linux input acceptance.
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


def test_mtp_keyboard_discovery_and_replay(tmp_path):
    root = Path(__file__).resolve().parents[3]
    text = (root / 'drivers/input/apple_mtp_kbd.c').read_text()
    keyboard = (root / 'drivers/input/apple_kbd.c').read_text()
    header = (root / 'drivers/input/apple_kbd.h').read_text()
    header = '\n'.join(line for line in header.splitlines() if not line.startswith('#include'))
    keyboard = '\n'.join(line for line in keyboard.splitlines() if not line.startswith('#include'))
    unit = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <linux/input.h>
typedef uint8_t u8; typedef uint16_t u16; typedef uint32_t u32;
typedef uint16_t __le16; typedef uint32_t __le32; typedef unsigned long ulong;
#define __packed __attribute__((packed))
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define BIT(n) (1U << (n))
#define ALIGN(v,a) (((v)+(a)-1)&~((a)-1))
#define IS_ALIGNED(v,a) (!((v)&((a)-1)))
#define cpu_to_le16(v) (v)
#define le16_to_cpu(v) (v)
#define DM_REMOVE_NORMAL 0
#define dev_err(...) ((void)0)
struct udevice { void *priv, *uc; bool retained; };
struct input_config { struct udevice *dev; };
struct keyboard_priv { struct input_config input; };
static void *dev_get_priv(struct udevice *d) { return d->priv; }
static void *dev_get_uclass_priv(struct udevice *d) { return d->uc; }
static unsigned key_count; static int keys[32], releases[32];
static void input_add_keycode(struct input_config *input, int key, int released) {
    (void)input;assert(key_count<32);keys[key_count]=key;releases[key_count++]=released;
}
static void *memscan(const void *p, int c, size_t n) {
    void *found=memchr(p,c,n);return found ? found : (u8 *)p+n;
}
static u32 get_unaligned_le32(const void *p) { u32 v;memcpy(&v,p,4);return v; }
''' + header + '\n' + keyboard + '\n'
    start = text.index('struct apple_mtp_kbd_priv {')
    end = text.index('static int dockchannel_read(', start)
    unit += text[start:end]
    unit += r'''
static u8 local[64], config[64], remote[64], irq[64];
static u8 wire[65540];static size_t wire_size;
static u8 tx[256], replay[2048];static size_t tx_size,replay_size;
static ulong ticks;static int remove_error,expected_error;static unsigned helper_removes,stalled_reads;
static u32 pending_bytes,producer_extra,producer_at,lag_reads;static bool inject_lag;static u32 remote_space=2048;
static ulong get_timer(ulong base) { return ++ticks-base; }
static u32 readl(const void *p) {
    if(p==local+DATA_TX_FREE)return 64;
    if(p==local+DATA_RX_COUNT){if(lag_reads&&!producer_extra){lag_reads--;return replay_size-4;}return replay_size+pending_bytes+producer_extra;}
    if(p==remote+DATA_TX_FREE)return remote_space-replay_size;
    assert(false);return 0;
}
static void writel(u32 v, void *p) {
    if(p==local+DATA_TX32) { assert(tx_size+4<=sizeof(tx));memcpy(tx+tx_size,&v,4);tx_size+=4; }
    else if(p==remote+DATA_TX32) { assert(replay_size+4<=sizeof(replay));memcpy(replay+replay_size,&v,4);replay_size+=4;if(inject_lag){lag_reads=2;inject_lag=false;}if(producer_at&&replay_size>=producer_at)producer_extra=24; }
    else assert(p==irq+DCHID_IRQ_MASK || p==irq+DCHID_IRQ_FLAG);
}
static int dockchannel_read(struct udevice *dev,void *p,size_t size) {
    (void)dev;assert(size==wire_size);memcpy(p,wire,size);return size;
}
static unsigned invalidations;
static void apple_rtkit_helper_input_failed(struct udevice *d) { assert(d);invalidations++; }
static bool apple_rtkit_helper_input_retained(struct udevice *d) { return d->retained; }
static int device_remove(struct udevice *d,int flags) {
    (void)d;assert(flags==DM_REMOVE_NORMAL);helper_removes++;return remove_error;
}
static int apple_mtp_kbd_check(struct input_config *input) {
    (void)input;stalled_reads++;return 0; /* Inject a drain consumer with no progress. */
}
'''
    for name in ('dockchannel_write', 'dchid_sum', 'dchid_comm_set_report',
                 'dchid_handle_event', 'apple_mtp_kbd_handoff_error', 'apple_mtp_kbd_check_announce', 'apple_mtp_kbd_replay_count', 'apple_mtp_kbd_replay_j713', 'apple_mtp_kbd_handoff'):
        unit += function(text, name)
    unit += r'''
static u8 expected[2048];static size_t expected_size;
static void deliver(struct input_config *input,u8 iface,const void *data,size_t size,bool preserve,bool corrupt) {
    struct dchid_hdr hdr={.hdr_len=8,.channel=DCHID_CHANNEL_REPORT,.iface=iface,
                          .length=sizeof(struct dchid_subhdr)+ALIGN(size,4)};
    struct dchid_subhdr sub={.length=size};
    memset(wire,0,sizeof(wire));memcpy(wire,&sub,sizeof(sub));memcpy(wire+sizeof(sub),data,size);
    wire_size=hdr.length+4;
    u32 checksum=0xffffffff-dchid_sum(&hdr,sizeof(hdr))-dchid_sum(wire,hdr.length);
    memcpy(wire+hdr.length,&checksum,4);
    if(corrupt)wire[wire_size-1]^=1;
    if(preserve&&!corrupt) {
        assert(expected_size+sizeof(hdr)+wire_size<=sizeof(expected));
        memcpy(expected+expected_size,&hdr,sizeof(hdr));expected_size+=sizeof(hdr);
        memcpy(expected+expected_size,wire,wire_size);expected_size+=wire_size;
    }
    int ret=apple_mtp_kbd_check_announce(input,&hdr);
    assert(expected_error ? ret==expected_error : ret>=0);expected_error=0;
}
static void announce(struct input_config *input,const char *name,u8 iface,bool more,bool corrupt) {
    struct dchid_init_hdr init={.type=DCHID_EVENT_INIT,.iface=iface,.more_packets=more};
    strncpy(init.name,name,sizeof(init.name));deliver(input,0,&init,sizeof(init),true,corrupt);
}
int main(void) {
    struct apple_mtp_kbd_priv priv={.local=local,.config=config,.rmt=remote,.irq_base=irq,
        .fifo_size=sizeof(expected),.kbd_iface=-1,.announce=true,.j713=true,.rx_irq_mask=2};
    struct keyboard_priv uc={0};struct udevice helper={.retained=true},dev={.priv=&priv,.uc=&uc};
    priv.helper=&helper;priv.init_data=malloc(priv.fifo_size);priv.pkt=malloc(DCHID_MAX_PKT);
    assert(priv.init_data&&priv.pkt);uc.input.dev=&dev;
    announce(&uc.input,"stm",1,false,false);assert(tx_size==0);
    announce(&uc.input,"multi-touch",2,false,false);assert(tx_size==0);
    announce(&uc.input,"keyboard",3,true,false);assert(tx_size==0);
    announce(&uc.input,"keyboard",3,false,false);
    static const u8 enable[]={8,0x11,12,0,0,0,0,0,0x80,0,2,0,0,0,0,0,0xb4,3,0,0,0xc3,0xea,0xf1,0xff};
    assert(tx_size==sizeof(enable)&&!memcmp(tx,enable,sizeof(enable)));
    assert(priv.kbd_iface==3&&!priv.ready);
    u8 ready[]={DCHID_EVENT_READY,2};deliver(&uc.input,0,ready,2,false,false);assert(!priv.ready);
    ready[1]=3;deliver(&uc.input,0,ready,2,true,false);assert(priv.ready);
    struct apple_kbd_report key={.reportid=1,.keycode={4}};
    deliver(&uc.input,2,&key,sizeof(key),false,false);assert(!key_count);
    deliver(&uc.input,3,&key,sizeof(key),false,false);assert(key_count==1&&keys[0]==KEY_A&&!releases[0]);
    key.keycode[0]=0;deliver(&uc.input,3,&key,sizeof(key),false,false);assert(key_count==2&&keys[1]==KEY_A&&releases[1]);
    key.keycode[0]=0x52;deliver(&uc.input,3,&key,sizeof(key),false,false);assert(key_count==3&&keys[2]==KEY_UP&&!releases[2]);
    key.keycode[0]=0;deliver(&uc.input,3,&key,sizeof(key),false,false);assert(key_count==4&&releases[3]);
    /* A valid short command ACK is not a truncated REPORT subheader. */
    struct dchid_hdr ack={.hdr_len=8,.channel=DCHID_CHANNEL_CMD,.length=4};
    memset(wire,0,8);wire_size=8;
    u32 acksum=0xffffffff-dchid_sum(&ack,sizeof(ack));memcpy(wire+4,&acksum,4);
    assert(!apple_mtp_kbd_check_announce(&uc.input,&ack)&&!priv.handoff_error);
    size_t saved=priv.init_size;
    /* Other announcing profiles keep their original INIT-only replay. */
    priv.j713=false;deliver(&uc.input,0,ready,2,false,false);
    assert(priv.init_size==saved);priv.j713=true;
    struct apple_mtp_kbd_priv broken=priv;dev.priv=&broken;expected_error=-EBADMSG;
    announce(&uc.input,"keyboard",7,false,true);
    assert(broken.handoff_error==-EBADMSG&&apple_mtp_kbd_handoff(&dev)==-EBADMSG);
    assert(helper_removes==1&&!replay_size);
    broken=priv;broken.fifo_size=broken.init_size;expected_error=-ENOSPC;
    struct dchid_init_hdr extra={.type=DCHID_EVENT_INIT,.iface=7};
    deliver(&uc.input,0,&extra,sizeof(extra),false,false);
    assert(broken.handoff_error==-ENOSPC&&apple_mtp_kbd_handoff(&dev)==-ENOSPC);
    assert(helper_removes==2&&invalidations==2&&!replay_size);helper_removes=0;dev.priv=&priv;
    assert(priv.init_size==saved&&priv.kbd_iface==3&&tx_size==sizeof(enable));
    assert(priv.init_size==expected_size&&!memcmp(priv.init_data,expected,expected_size));
    /* A paused producer leaves partial bytes: removal must terminate. */
    broken=priv;broken.helper=NULL;broken.retained_handoff=true;dev.priv=&broken;pending_bytes=4;
    assert(apple_mtp_kbd_handoff(&dev)==-ETIME&&stalled_reads);
    pending_bytes=0;dev.priv=&priv;
    remote_space=priv.init_size-4;
    assert(apple_mtp_kbd_replay_j713(&priv)==-ETIME&&!replay_size);
    remote_space=2048;producer_at=4;
    assert(apple_mtp_kbd_replay_j713(&priv)==-EAGAIN&&replay_size==4);
    producer_at=producer_extra=0;replay_size=0;
    remove_error=-EIO;assert(apple_mtp_kbd_handoff(&dev)==-EIO);
    assert(priv.helper==&helper&&!replay_size);
    remove_error=0;inject_lag=true;assert(!apple_mtp_kbd_handoff(&dev));
    assert(!priv.helper&&helper_removes==2);
    assert(replay_size==expected_size&&!memcmp(replay,expected,expected_size));
    /* First-publication failure disabled input: skip stale partial bytes. */
    struct udevice cold_helper={0};
    broken=(struct apple_mtp_kbd_priv){.j713=true,.helper=&cold_helper,.handoff_error=-EBADMSG,
        .irq_base=irq,.local=local,.pkt=malloc(16),.init_data=malloc(16)};
    dev.priv=&broken;pending_bytes=4;unsigned before=stalled_reads;
    assert(!apple_mtp_kbd_handoff(&dev)&&stalled_reads==before);
    return 0;
}
'''
    c, binary = tmp_path / 'keyboard.c', tmp_path / 'keyboard'
    c.write_text(unit)
    subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                    '-Wno-unused-function', '-Wno-sign-compare', '-fsanitize=address,undefined',
                    '-fno-sanitize-recover=all', str(c), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)


def test_j713_keyboard_never_reboots_unprepared_helper(tmp_path):
    root = Path(__file__).resolve().parents[3]
    text = (root / 'drivers/input/apple_mtp_kbd.c').read_text()
    unit = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <errno.h>
#include <string.h>
typedef uint32_t u32;typedef int ofnode;
#define UCLASS_MISC 1
struct udevice { int unused; };
struct apple_mtp_kbd_priv { struct udevice *helper; };
static struct apple_mtp_kbd_priv priv;
static struct udevice helper;
static bool j713=true,prepared;
static int finds,gets,find_error,property_error;
static void *dev_get_priv(struct udevice *d){(void)d;return &priv;}
static int dev_read_u32(struct udevice *d,const char *name,u32 *v){
 (void)d;assert(!strcmp(name,"apple,helper-cpu"));*v=123;return property_error;
}
static ofnode ofnode_get_by_phandle(u32 phandle){assert(phandle==123);return 123;}
static bool of_machine_is_compatible(const char *name){assert(!strcmp(name,"apple,j713"));return j713;}
static int uclass_find_device_by_ofnode(int cls,ofnode node,struct udevice **d){
 assert(cls==1&&node==123);finds++;*d=&helper;return find_error;
}
static int uclass_get_device_by_ofnode(int cls,ofnode node,struct udevice **d){
 assert(cls==1&&node==123);gets++;*d=&helper;return 0;
}
static bool apple_rtkit_helper_input_prepared(struct udevice *d){assert(d==&helper);return prepared;}
''' + function(text, 'get_rtkit_helper') + r'''
int main(void){
 struct udevice keyboard={0};
 assert(get_rtkit_helper(&keyboard)==-ENODEV&&finds==1&&!gets);
 prepared=true;assert(!get_rtkit_helper(&keyboard)&&finds==2&&!gets);
 find_error=-ENODEV;assert(get_rtkit_helper(&keyboard)==-ENODEV&&!gets);
 property_error=-EINVAL;assert(get_rtkit_helper(&keyboard)==-EINVAL&&finds==3&&!gets);
 property_error=0;j713=false;assert(!get_rtkit_helper(&keyboard)&&gets==1);
 return 0;
}
'''
    c, binary = tmp_path / 'startup.c', tmp_path / 'startup'
    c.write_text(unit)
    subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=undefined', '-fno-sanitize-recover=all',
                    str(c), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)


def test_j713_remove_errors_cannot_be_ignored(tmp_path):
    """EFI/booti ignore DM removal errors: the J713 callback must not return."""
    root = Path(__file__).resolve().parents[3]
    text = (root / 'drivers/input/apple_mtp_kbd.c').read_text()
    unit = r'''
#include <assert.h>
#include <stdbool.h>
#include <errno.h>
#include <setjmp.h>
struct apple_mtp_kbd_priv { bool j713; };
struct udevice { struct apple_mtp_kbd_priv *priv; };
static int handoff_result,panics;
static jmp_buf boundary;
static void *dev_get_priv(struct udevice *dev) { return dev->priv; }
static int apple_mtp_kbd_handoff(struct udevice *dev) { (void)dev;return handoff_result; }
static void __attribute__((noreturn)) panic(const char *fmt,...) {
    assert(fmt);panics++;longjmp(boundary,1);
}
''' + function(text, 'apple_mtp_kbd_remove') + r'''
int main(void) {
    struct apple_mtp_kbd_priv priv={.j713=true};struct udevice dev={.priv=&priv};
    assert(!apple_mtp_kbd_remove(&dev)&&!panics);
    handoff_result=-EIO;
    if (!setjmp(boundary)) { apple_mtp_kbd_remove(&dev);assert(false); }
    assert(panics==1);
    priv.j713=false;assert(apple_mtp_kbd_remove(&dev)==-EIO&&panics==1);
    return 0;
}
'''
    c, binary = tmp_path / 'remove.c', tmp_path / 'remove'
    c.write_text(unit)
    subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=undefined', '-fno-sanitize-recover=all',
                    str(c), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
