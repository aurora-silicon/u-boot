#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0+
# Usage: python3 tools/apple_apif_handoff_test.py OUTPUT_DIRECTORY
from pathlib import Path
import subprocess,re,json,hashlib,sys
T=Path(__file__).resolve().parents[1];O=Path(sys.argv[1]).resolve();O.mkdir(parents=True,exist_ok=True)
def function(s,name):
 pos=s.index(name+'(');a=s.rfind('\n',0,pos)+1;b=s.index('{',pos);n=1;i=b+1
 while n:n+=(s[i]=='{')-(s[i]=='}');i+=1
 return s[a:i]
header=(T/'include/apple_apif_nvme.h').read_text();header='\n'.join(l for l in header.splitlines() if not l.startswith('#include'))
maptext=(T/'arch/arm/mach-apple/m5_apif_memmap.h').read_text();maptext='\n'.join(l for l in maptext.splitlines() if not l.startswith('#include'))
s=(T/'drivers/nvme/nvme_apple_apif.c').read_text();bodies='\n'.join(function(s,n) for n in ['apple_nvme_apif_handoff_fdt','apple_nvme_apif_quiesce'])
prefix=r'''
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
#include <errno.h>
#include <setjmp.h>
#include <libfdt.h>
typedef uint64_t u64;typedef uint32_t u32;typedef uint64_t fdt_addr_t;typedef uint64_t fdt_size_t;
#define CONFIG_NR_DRAM_BANKS 32
#define SZ_4K 0x1000ULL
#define SZ_128M 0x8000000ULL
#define ARRAY_SIZE(x) (sizeof(x)/sizeof(x[0]))
#define IS_ALIGNED(x,a) (((x)&((a)-1))==0)
#define ALIGN(x,a) (((x)+(a)-1)&~((a)-1))
#define ALIGN_DOWN(x,a) ((x)&~((a)-1))
#define PTE_BLOCK_MEMTYPE(x) ((x)<<2)
#define MT_DEVICE_NGNRNE 1ULL
#define MT_NORMAL 2ULL
#define MT_NORMAL_NC 3ULL
#define PTE_BLOCK_NON_SHARE 0ULL
#define PTE_BLOCK_PXN (1ULL<<53)
#define PTE_BLOCK_UXN (1ULL<<54)
#define PTE_BLOCK_INNER_SHARE (3ULL<<8)
#define FDT_ADDR_T_NONE (~0ULL)
struct mm_region {u64 virt,phys,size,attrs;};static struct mm_region *mem_map;
struct bd {struct {u64 start,size;} bi_dram[CONFIG_NR_DRAM_BANKS];};
struct gd {struct bd *bd;void *fdt_blob;};static struct bd bd;static struct gd global;static struct gd *gd=&global;
typedef int ofnode;
static ofnode ofnode_path(const char*p){return fdt_path_offset(gd->fdt_blob,p);}
static bool ofnode_valid(ofnode n){return n>=0;}
static fdt_addr_t ofnode_get_addr_size(ofnode n,const char*p,fdt_size_t*z){int len;const fdt64_t*r=fdt_getprop(gd->fdt_blob,n,p,&len);if(!r||len!=16)return FDT_ADDR_T_NONE;*z=fdt64_to_cpu(r[1]);return fdt64_to_cpu(r[0]);}
static jmp_buf escape;static unsigned panics;
static void panic(const char *fmt,...){(void)fmt;panics++;longjmp(escape,1);}
#define UCLASS_NVME 7
#define NVME_Q_NUM 2
#define DM_REMOVE_NORMAL 1
struct apple_nvme_apif {struct {unsigned queue_count;} ndev;u64 queue[4];bool failed;};
struct driver {const char *name;};struct udevice {struct driver *driver;bool active;int remove_result;struct apple_nvme_apif *priv;struct udevice *next;};
struct uclass {struct udevice *head;};static struct uclass uc;static int get_error;static unsigned removes;static bool ans_probe_failed;
static int uclass_get(int type,struct uclass **out){assert(type==UCLASS_NVME);*out=&uc;return get_error;}
#define uclass_foreach_dev(d,u) for(d=(u)->head;d;d=d->next)
static bool device_active(struct udevice*d){return d->active;}
static struct apple_nvme_apif *dev_get_priv(struct udevice*d){return d->priv;}
static int device_remove(struct udevice*d,int flags){assert(flags==DM_REMOVE_NORMAL);removes++;if(!d->remove_result)d->active=false;return d->remove_result;}
'''
tests=r'''
static unsigned char blob[65536],saved[65536];static u64 queues[4];
static int nvme(void){return fdt_path_offset(blob,"/ans-dma/nvme@60400000");}
static void fixture(void){
 assert(!fdt_create_empty_tree(blob,sizeof(blob)));assert(!fdt_setprop_u32(blob,0,"#address-cells",2));assert(!fdt_setprop_u32(blob,0,"#size-cells",2));
 int reserved=fdt_add_subnode(blob,0,"reserved-memory");assert(reserved>=0);assert(!fdt_setprop_u32(blob,reserved,"#address-cells",2));assert(!fdt_setprop_u32(blob,reserved,"#size-cells",2));assert(!fdt_setprop_empty(blob,reserved,"ranges"));
 for(unsigned i=0;i<4;i++){reserved=fdt_path_offset(blob,"/reserved-memory");char name[32];snprintf(name,sizeof(name),"queue%u",i);int q=fdt_add_subnode(blob,reserved,name);assert(q>=0);queues[i]=0x10020000000ULL+i*0x4000;fdt64_t reg[]={cpu_to_fdt64(queues[i]),cpu_to_fdt64(0x4000)};assert(!fdt_setprop(blob,q,"reg",reg,sizeof(reg)));assert(!fdt_setprop_empty(blob,q,"no-map"));assert(!fdt_setprop_u32(blob,q,"phandle",0x900+i));}
 int ans=fdt_add_subnode(blob,0,"ans-dma"),n=fdt_add_subnode(blob,ans,"nvme@60400000");assert(n>=0);assert(!fdt_setprop_string(blob,n,"compatible",APPLE_APIF_NVME_COMPAT));
 fdt32_t handles[4];for(unsigned i=0;i<4;i++)handles[i]=cpu_to_fdt32(0x900+i);assert(!fdt_setprop(blob,n,"memory-region",handles,sizeof(handles)));
 const char names[]="admin-sq\0admin-cq\0io-sq\0io-cq";assert(!fdt_setprop(blob,n,"memory-region-names",names,sizeof(names)));assert(!fdt_setprop_empty(blob,n,"dma-coherent"));assert(!fdt_setprop_empty(blob,n,"asahi,rtkit-quiesced"));
 int chosen=fdt_add_subnode(blob,0,"chosen"),fb=fdt_add_subnode(blob,chosen,"framebuffer");fdt64_t reg[]={cpu_to_fdt64(0x10060000100ULL),cpu_to_fdt64(0x10000)};assert(!fdt_setprop(blob,fb,"reg",reg,sizeof(reg)));
 memset(&bd,0,sizeof(bd));bd.bi_dram[0].start=0x10000000000ULL;bd.bi_dram[0].size=0x200000;bd.bi_dram[1].start=0x10040000000ULL;bd.bi_dram[1].size=0x400000;global=(struct gd){&bd,blob};get_error=0;ans_probe_failed=false;removes=panics=0;
}
int main(void){
 fixture();u64 out[4];assert(apple_apif_queue_pages(blob,nvme(),out)==1&&!memcmp(out,queues,sizeof(out)));
 unsigned char unaligned[9]={0,1,2,3,4,5,6,7,8};assert(apple_apif_fdt64(unaligned+1)==0x0102030405060708ULL);
 assert(!fdt_delprop(blob,nvme(),"asahi,rtkit-quiesced"));assert(apple_apif_queue_pages(blob,nvme(),out)==-EINVAL);
 fixture();fdt32_t duplicate[4]={cpu_to_fdt32(0x900),cpu_to_fdt32(0x900),cpu_to_fdt32(0x902),cpu_to_fdt32(0x903)};assert(!fdt_setprop(blob,nvme(),"memory-region",duplicate,sizeof(duplicate)));assert(apple_apif_queue_pages(blob,nvme(),out)==-EINVAL);
 fixture();int q=fdt_node_offset_by_phandle(blob,0x900);assert(!fdt_delprop(blob,q,"no-map"));assert(apple_apif_queue_pages(blob,nvme(),out)==-EINVAL);
 fixture();build_m5_apif_mem_map();assert(mem_map[0].phys==0x60000000&&mem_map[0].size==SZ_128M);assert(mem_map[1].phys==bd.bi_dram[0].start&&mem_map[2].phys==bd.bi_dram[1].start);for(unsigned i=0;i<4;i++)assert(mem_map[3+i].phys==queues[i]&&mem_map[3+i].size==0x4000);assert(mem_map[7].phys==0x10060000000ULL&&mem_map[7].size==0x11000&&mem_map[8].size==0);
 fixture();bd.bi_dram[0].size++;if(!setjmp(escape)){build_m5_apif_mem_map();assert(!"bad RAM admitted");}assert(panics==1);
 fixture();struct driver driver={"apple_nvme_apif"};struct apple_nvme_apif priv={.ndev={2}};memcpy(priv.queue,queues,sizeof(queues));struct udevice dev={&driver,true,0,&priv,NULL};uc.head=&dev;memcpy(saved,blob,sizeof(blob));assert(!apple_nvme_apif_handoff_fdt(blob));assert(!memcmp(saved,blob,sizeof(blob)));assert(fdt_getprop(blob,nvme(),"asahi,rtkit-quiesced",NULL));
 priv.queue[0]+=0x4000;assert(apple_nvme_apif_handoff_fdt(blob)==-EINVAL);priv.queue[0]-=0x4000;
 ans_probe_failed=true;assert(apple_nvme_apif_handoff_fdt(blob)==-EIO);if(!setjmp(escape)){apple_nvme_apif_quiesce();assert(!"partial probe handed off");}assert(panics==1&&!removes);ans_probe_failed=false;
 dev.remove_result=-ETIMEDOUT;if(!setjmp(escape)){apple_nvme_apif_quiesce();assert(!"failed stop handed off");}assert(panics==2&&removes==1&&dev.active);
 dev.remove_result=0;apple_nvme_apif_quiesce();assert(removes==2&&!dev.active);apple_nvme_apif_quiesce();assert(removes==2);assert(!memcmp(saved,blob,sizeof(blob)));
 puts("queue parser, unaligned FDT, high-bank/queue/framebuffer mappings, immutable EFI DT and final stop guard: PASS");return 0;
}
'''
path=O/'host-handoff.c';path.write_text(prefix+header+'\n'+maptext+'\n'+bodies+'\n'+tests)
subprocess.run(['cc','-O1','-g','-std=gnu11','-Wall','-Wextra','-Werror','-Wno-unused-parameter','-fsanitize=address,undefined',str(path),'-lfdt','-o',str(O/'host-handoff')],check=True)
subprocess.run([str(O/'host-handoff')],check=True)
(O/'host-test-source.json').write_text(json.dumps({r:hashlib.sha256((T/r).read_bytes()).hexdigest() for r in ['include/apple_apif_nvme.h','arch/arm/mach-apple/m5_apif_memmap.h','drivers/nvme/nvme_apple_apif.c']},indent=2)+'\n')
