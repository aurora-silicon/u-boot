# SPDX-License-Identifier: GPL-2.0-or-later OR MIT
"""Exercise the production per-UKI importer with real libfdt.

Optional APPLE_J713_RUNTIME_DTB and APPLE_J713_OS_DTBS (colon-separated) run
the same importer against private hardware captures and built PR DTBs. Captures
contain per-machine identifiers and are intentionally not repository fixtures.
"""
import os
from pathlib import Path
import subprocess


def fixture_tree(runtime, graphics=True, peripherals=False, aop=False):
    shift = 0 if runtime else 1000
    aliases = {
        'ans': '/soc/nvme', 'ans-mbox': '/soc/mailbox', 'sart-ans': '/soc/sart',
        'spmi-primary': '/soc/spmi', 'pmu-primary': '/soc/spmi/pmu',
        'pmp': '/soc/pmp', 'wifi0': '/soc/wifi', 'keyboard': '/soc/keyboard',
    }
    if graphics:
        aliases.update(gpu='/soc/gpu', dcp='/soc/dcp', disp0='/soc/display',
                       disp0_piodma='/soc/dcp/piodma')
    if peripherals:
        aliases.update(bluetooth0='/soc/bluetooth', atcphy0='/soc/phy0',
                       atcphy1='/soc/phy1')
    strings = ['asahi,iboot1-version', 'asahi,iboot2-version', 'asahi,system-fw-version',
               'asahi,os-fw-version', 'asahi,m1n1-stage1-version',
               'asahi,m1n1-stage2-version', 'asahi,efi-system-partition',
               'apfs-preboot-uuid', 'stdout-path']
    chosen = ''.join(f'{name} = "live-{name}";' for name in strings) if runtime else ''
    if not runtime:
        chosen += 'u-boot,j713-dtb-source = "0123456789012345678901234567890123456789";'
    fb = ('reg = <0x9 0x10000 0 0x1000000>; width = <2560>; height = <1600>;'
          'stride = <10240>; format = "x2r10g10b10";' if runtime else
          'reg = <0 0 0 0>; status = "disabled";')
    reserved = ''
    for index, name in enumerate(['uat-ttbs', 'uat-pagetables', 'uat-handoff',
                                  'uat-shared-l2', 'gpu-fw-text', 'gpu-fw-data']):
        if graphics:
            reg = f'<0x9 {0x20000 + index * 0x4000} 0 0x4000>' if runtime else '<0 0 0 0>'
            reserved += (f'{name} {{ phandle = <{200 + shift + index}>; reg = {reg};'
                         'no-map;' + ('status = "okay";' if runtime else 'status = "disabled";') + '};')
    if runtime:
        reserved += ('asc-firmware@900000000 { phandle = <300>; reg = <9 0 0 0x4000>;'
                     'compatible = "apple,asc-mem"; no-map;'
                     'iommu-addresses = <10 1 0 0 0x4000>, <11 1 0 0 0x4000>; };'
                     'flash@900080000 { phandle = <301>; reg = <9 0x80000 0 0x4000>;'
                     'compatible = "phram"; label = "adt"; };')
    if runtime and aop:
        reserved += ('asc-firmware@9000c0000 { phandle = <310>; reg = <9 0xc0000 0 0x18000>;'
                     'compatible = "apple,asc-mem"; no-map;'
                     'iommu-addresses = <50 0x100 0 0 0x18000>; };'
                     'asc-mmio@388020000 { phandle = <311>; reg = <3 0x88020000 0 0x8000>;'
                     'compatible = "apple,asc-mmio"; no-map;'
                     'iommu-addresses = <50 3 0x88020000 0 0x8000>; };')
    aop_node = ''
    if aop:
        aop_node = (f'aop@390600000 {{ compatible = "apple,t8132-aop"; phandle = <{50 if runtime else 1050}>;'
                    + ('memory-region = <310 311>;' if runtime else '') + '};')
    graphics_nodes = ''
    if graphics:
        gpu_props = ('apple,g16-handoff-version = <1>; apple,firmware-segment-vas = <1 0 1 0x4000>;'
                     'apple,firmware-segment-flags = <1 0>;' if runtime else '')
        dcp_props = ('memory-region = <300>; apple,firmware-version = <26 6 2>;'
                     'apple,firmware-compat = <26 6 2>;'
                     'apple,firmware-uuid = "81BAF476-679D-3CC0-8C6A-E6B6FA659D5E";'
                     if runtime else '')
        frontend = ('memory-region = <300>; apple,j713-inherited-mappings;' if runtime else '')
        gpu_refs = ' '.join(str(200 + shift + i) for i in range(6))
        graphics_nodes = f'''
            gpu {{ phandle = <{20 + shift}>; memory-region = <{gpu_refs}>; {gpu_props} }};
            dart {{ phandle = <{30 + shift}>; #iommu-cells = <1>; status = "disabled"; }};
            dcp {{ phandle = <{10 + shift}>; #address-cells = <2>; #size-cells = <2>;
                reg = <0 0x1234 0 0x4000>; {dcp_props}
                piodma {{ phandle = <{12 + shift}>; iommus = <{30 + shift} 16>; {frontend} }};
            }};
            display {{ phandle = <{11 + shift}>; iommus = <{30 + shift} 0>; {frontend} }};
        '''
    tunables = 'apple,tunable-test = [01 02 03 04];' if runtime else ''
    peripheral_nodes = ''
    if peripherals:
        bt = ('local-bd-address = [01 02 03 04 05 06];'
              'brcm,taurus-cal-blob = [12 34]; brcm,taurus-bf-cal-blob = [56 78];'
              if runtime else 'local-bd-address = [00 00 00 00 00 00];')
        peripheral_nodes = f'bluetooth {{ compatible = "pci14e4,5f72"; {bt} }};'
        for port in range(2):
            cal = ('apple,tunable-usb3-axi2af = <0x7ffc 0xffffffff 0x12345678>;'
                   'apple,tunable-usb3-lioa = <0x3ffc 0xff 0x12>;'
                   'apple,tunable-usb3-fabric = <0x3ffc 0xff 0x12>;'
                   'apple,tunable-usb3-config = <0x3ffc 0xff 0x12>;'
                   'apple,tunable-usb3-common = <0x23ffc 0xff 0x12>;'
                   'apple,tunable-usb3-dp = <0x16008 0x38 8>, <0x1600c 0x1f 0>;'
                   if runtime else 'apple,tunable-usb3-efuse = <4 0xff 0x99>;'
                   'apple,tunable-usb3-dp = <0x16008 0xff 0x99>;')
            peripheral_nodes += f'''phy{port} {{ compatible = "apple,t8132-atcphy";
              reg = <4 {0x03000000 + port * 0x8000000} 0 0x24000>,
                    <4 {port * 0x8000000} 0 0x8000>,
                    <4 {0x00020000 + port * 0x8000000} 0 0x4000>,
                    <4 {0x03044000 + port * 0x8000000} 0 0x4000>,
                    <4 {0x03050000 + port * 0x8000000} 0 0x4000>;
              reg-names = "core", "axi2af", "lioa", "fabric", "common-config"; {cal}
            }};'''
    return f'''/dts-v1/;
    / {{ compatible = "apple,j713", "apple,t8132"; serial-number = "fixture";
      #address-cells = <2>; #size-cells = <2>;
      aliases {{ {''.join(f'{a} = "{p}";' for a, p in aliases.items())} }};
      panel {{ phandle = <{40 + shift}>; }};
      chosen {{ #address-cells = <2>; #size-cells = <2>; {chosen} asahi,kblang-code = <4>;
        framebuffer@0 {{ compatible = "apple,simple-framebuffer", "simple-framebuffer";
          panel = <{40 + shift}>; {fb} }};
      }};
      cpus {{ #address-cells = <2>; #size-cells = <0>;
        cpu@0 {{ reg = <0 0>; cpu-release-addr = /bits/ 64 <0>; }};
        cpu@1 {{ reg = <0 1>; cpu-release-addr = /bits/ 64 <{0x900040000 if runtime else 0}>; }};
      }};
      reserved-memory {{ #address-cells = <2>; #size-cells = <2>; ranges; {reserved} }};
      soc {{ #address-cells = <2>; #size-cells = <2>; ranges;
        nvme {{ reg = <0 1 0 2>, <0 3 0 4>, <0 5 0 6>; interrupts = <0 1 4>; status = "okay"; }};
        mailbox {{ reg = <0 7 0 8>; interrupts = <0 1 4>, <0 2 4>, <0 3 4>, <0 4 4>; }};
        sart {{ reg = <0 9 0 10>; }};
        spmi {{ #address-cells = <2>; #size-cells = <0>; reg = <0 11 0 12>;
          pmu {{ reg = <14 0>; }}; }};
        pmp {{ apple,board-id = <44>; apple,dram-vendor-id = <6>; {tunables} status = "okay"; }};
        pcie@1cb0000000 {{ compatible = "apple,t8132-pcie"; status = "okay"; }};
        wifi {{ local-mac-address = [00 01 02 03 04 05]; brcm,cal-blob = [01 02]; apple,antenna-sku = "X3"; }};
        keyboard {{ apple,keyboard-layout-id = <4>; hid-country-code = <13>; }};
        mtp {{ compatible = "apple,t8132-rtk-helper-asc4"; }};
        pmgr {{ status = "{'okay' if runtime else 'disabled'}";
           {'apple,firmware-owned;' if not runtime else ''} }};
        {graphics_nodes}
        {peripheral_nodes}
        {aop_node}
      }};
    }};'''


HARNESS = r'''
#define J713_FDT_HOST_TEST
#include "PRODUCTION"
#include <assert.h>
#include <stdio.h>

static void *load(const char *path, size_t capacity)
{
 FILE *f = fopen(path, "rb"); assert(f);
 assert(!fseek(f, 0, SEEK_END)); long size = ftell(f); rewind(f);
 void *original = malloc(size), *blob = malloc(capacity);
 assert(original && blob && fread(original, 1, size, f) == (size_t)size);
 fclose(f); assert(!fdt_open_into(original, blob, capacity)); free(original);
 return blob;
}

static void check_references(const void *fdt)
{
 int node = -1, depth = 0, len;
 while ((node = fdt_next_node(fdt, node, &depth)) >= 0) {
  const fdt32_t *p = fdt_getprop(fdt, node, "memory-region", &len);
  if (p) for (int i = 0; i < len / 4; i++) assert(fdt_node_offset_by_phandle(fdt, fdt32_to_cpu(p[i])) >= 0);
  p = fdt_getprop(fdt, node, "panel", &len);
  if (p) assert(len == 4 && fdt_node_offset_by_phandle(fdt, fdt32_to_cpu(*p)) >= 0);
  p = fdt_getprop(fdt, node, "iommu-addresses", &len);
  if (p) for (int i = 0; i < len / 4; i += 5) {
   assert(len / 4 - i >= 5);
   assert(fdt_node_offset_by_phandle(fdt, fdt32_to_cpu(p[i])) >= 0);
  }
 }
}

static void check_policy(const void *before, const void *after)
{
 int node = -1, depth = 0;
 while ((node = fdt_next_node(before, node, &depth)) >= 0) {
  char path[256]; assert(!fdt_get_path(before, node, path, sizeof(path)));
  if (!strstr(path, "/power-management@") && !strstr(path, "/pinctrl@") &&
      strcmp(path, "/soc/pmgr")) continue;
  int target = fdt_path_offset(after, path), po, len, alen;
  assert(target >= 0);
  fdt_for_each_property_offset(po, before, node) {
   const struct fdt_property *prop = fdt_get_property_by_offset(before, po, &len);
   const char *name = fdt_string(before, fdt32_to_cpu(prop->nameoff));
   const void *value = fdt_getprop(after, target, name, &alen);
   assert(value && len == alen && !memcmp(prop->data, value, len));
  }
 }
}

int main(int argc, char **argv)
{
 assert(argc == 4);
 int fault = atoi(argv[3]), len;
 size_t capacity = fault == 1 ? 65536 : 1024 * 1024;
 void *source = load(argv[1], 128 * 1024), *target = load(argv[2], capacity);
 if (fault == 18 || fault == 19) {
  /* Model EFI_DT_APPLY_FIXUPS allocation from used tree bytes + real pad. */
  size_t used = fdt_off_dt_strings(target) + fdt_size_dt_strings(target);
  size_t pad = fault == 18 ? 0x3000 : 0x40000;
  capacity = (used + pad + 4095) & ~4095;
  fdt_set_totalsize(target, capacity);
 }
 int chosen = fdt_path_offset(target, "/chosen");
 assert(!fdt_setprop_string(target, chosen, J713_FDT_SOURCE, "0123456789012345678901234567890123456789"));
 if (fault == 2) assert(!fdt_setprop_string(source, 0, "compatible", "apple,j715"));
 if (fault == 3) {
  int node = fdt_path_offset(source, "/reserved-memory/asc-firmware@900000000");
  assert(node >= 0); assert(!fdt_setprop_u32(source, node, "iommu-addresses", 0xdead));
 }
 if (fault == 4) {
  int node = fdt_path_offset(source, "pmp");
  assert(!fdt_delprop(source, node, "apple,board-id"));
 }
 if (fault == 5) {
  int node = fdt_path_offset(source, "/cpus/cpu@1"); assert(node >= 0);
  assert(!fdt_del_node(source, node));
 }
 if (fault == 6) {
  int node = fdt_path_offset(source, "/soc/mtp"); assert(node >= 0);
  assert(!fdt_del_node(source, node));
 }
 if (fault == 7) assert(!fdt_setprop_string(target, chosen, J713_FDT_SOURCE, "invalid"));
 if (fault == 8) {
  int node = fdt_path_offset(source, "/reserved-memory/asc-firmware@900000000");
  fdt32_t tuple[] = {cpu_to_fdt32(10), 0, 0, 0};
  assert(!fdt_setprop(source, node, "iommu-addresses", tuple, sizeof(tuple)));
 }
 if (fault == 9) {
  int node = fdt_path_offset(source, "/reserved-memory/asc-firmware@900000000");
  assert(!fdt_setprop_empty(source, node, "unreviewed-reference"));
 }
 if (fault == 10) assert(!fdt_delprop(target, chosen, J713_FDT_SOURCE));
 if (fault == 11) {
  int node = fdt_path_offset(source, "/cpus/cpu@1");
  assert(!fdt_setprop_u64(source, node, "cpu-release-addr", 1));
 }
 if (fault == 13 || fault == 14) {
  int gpu = fdt_path_offset(source, "gpu"), resv = fdt_path_offset(source, "/reserved-memory"), n;
  assert(!fdt_delprop(source, gpu, "apple,g16-handoff-version"));
  assert(!fdt_setprop_string(source, gpu, "status", "disabled"));
  fdt_for_each_subnode(n, source, resv) {
   const char *name = fdt_get_name(source, n, NULL);
   if (!strncmp(name, "uat-", 4) || !strncmp(name, "gpu-fw-", 7)) {
    fdt64_t zero[2] = {0}; assert(!fdt_setprop(source, n, "reg", zero, sizeof(zero)));
   }
  }
  if (fault == 14) {
   int bad = fdt_add_subnode(source, fdt_path_offset(source, "/reserved-memory"), "unknown-enabled");
   fdt64_t zero[2] = {0}; assert(!fdt_setprop(source, bad, "reg", zero, sizeof(zero)));
  }
 }
 if (fault == 15) {
  int display = fdt_path_offset(source, "disp0");
  assert(!fdt_delprop(source, display, "apple,j713-inherited-mappings"));
 }
 if (fault == 17) {
  int n = fdt_path_offset(source, "/soc/pcie@1cb0000000");
  assert(!fdt_setprop_string(source, n, "status", "disabled"));
 }
 if (fault == 16) {
  assert(!fdt_delprop(source, fdt_path_offset(source, "/chosen"), "apfs-preboot-uuid"));
 }
 if (fault == 12) {
  int node = fdt_path_offset(source, "/reserved-memory/asc-firmware@900000000");
  assert(!fdt_add_mem_rsv(source, 0x900000000ULL, 0x4000));
  fdt32_t tuple[] = {cpu_to_fdt32(10), 0, 0, 0, cpu_to_fdt32(0x4000), cpu_to_fdt32(11), 0, 0};
  assert(!fdt_setprop(source, node, "iommu-addresses", tuple, sizeof(tuple)));
 }
  if (fault == 20) {
   int node = fdt_path_offset(source, "bluetooth0");
   assert(!fdt_setprop_u32(source, node, "local-bd-address", 1));
  }
  if (fault == 21) {
   int node = fdt_path_offset(source, "bluetooth0");
   assert(!fdt_delprop(source, node, "brcm,taurus-cal-blob"));
  }
  if (fault == 22) {
   int aliases = fdt_path_offset(source, "/aliases");
   assert(!fdt_delprop(source, aliases, "atcphy0"));
   assert(!fdt_delprop(source, aliases, "bluetooth0"));
  }
  if (fault >= 23 && fault <= 25) {
   int node = fdt_path_offset(source, "atcphy0");
   fdt32_t tuple[] = {cpu_to_fdt32(fault == 23 ? 1 : 0x8000),
                      cpu_to_fdt32(0xff), cpu_to_fdt32(0x12)};
   assert(!fdt_setprop(source, node, "apple,tunable-usb3-axi2af", tuple,
                       fault == 25 ? 8 : sizeof(tuple)));
  }
  if (fault >= 35 && fault <= 39) {
   int node = fdt_node_offset_by_compatible(source, -1, "apple,t8132-aop");
   assert(node >= 0);
   if (fault == 35) assert(!fdt_setprop_string(source, node, "status", "disabled"));
   if (fault == 36) {
    /* m1n1 without the handoff: no AOP in its tree and no reservations. */
    const char *names[] = {"asc-firmware@9000c0000", "asc-mmio@388020000"};
    assert(!fdt_del_node(source, node));
    for (unsigned i = 0; i < 2; i++) {
     int resv = fdt_subnode_offset(source, fdt_path_offset(source, "/reserved-memory"), names[i]);
     assert(resv >= 0 && !fdt_del_node(source, resv));
    }
   }
   if (fault == 37) assert(!fdt_setprop_u32(source, node, "memory-region", 0xdead));
   if (fault == 39) {
    node = fdt_node_offset_by_compatible(target, -1, "apple,t8132-aop");
    assert(!fdt_set_name(target, node, "aop@1"));
   }
   if (fault == 38) {
    node = fdt_node_offset_by_compatible(target, -1, "apple,t8132-aop");
    assert(!fdt_setprop_u32(target, node, "memory-region", 1040));
   }
  }
  if (fault == 26 || fault == 28) {
   int node = fdt_path_offset(target, "atcphy0");
   const char names[] = "usb2phy\0pipehandler";
   fdt32_t regs[] = {cpu_to_fdt32(4), cpu_to_fdt32(0x02a90000), 0, cpu_to_fdt32(0x4000),
                    cpu_to_fdt32(4), cpu_to_fdt32(0x02a84000), 0, cpu_to_fdt32(0x4000)};
   assert(!fdt_setprop(target, node, "reg-names", names, sizeof(names)));
   assert(!fdt_setprop(target, node, "reg", regs, sizeof(regs)));
   if (fault == 28) {
    fdt32_t malformed[2] = {0};
    node = fdt_path_offset(source, "atcphy0");
    assert(!fdt_setprop(source, node, "apple,tunable-usb3-axi2af", malformed, sizeof(malformed)));
   }
  }
  if (fault == 27) {
   int node = fdt_path_offset(target, "atcphy0");
   const char names[] = "core\0axi2af\0lioa\0common-config";
   assert(!fdt_setprop(target, node, "reg-names", names, sizeof(names)));
  }
  if (fault == 29) {
   int node = fdt_path_offset(source, "atcphy0");
   assert(!fdt_delprop(source, node, "apple,tunable-usb3-dp"));
  }
  if (fault >= 30 && fault <= 34) {
   int node = fdt_path_offset(source, "atcphy0");
   fdt32_t tuple[] = {cpu_to_fdt32(fault == 30 ? 0x24000 :
                                 fault == 31 ? 0x16002 : 0x23ffc),
                      cpu_to_fdt32(0xff), cpu_to_fdt32(0x12)};
   assert(!fdt_setprop(source, node, "apple,tunable-usb3-dp", tuple,
                     fault == 32 ? 8 : fault == 33 ? 0 : sizeof(tuple)));
  }
 assert(!fdt_add_mem_rsv(source, 0x900040000ULL, 0x4000));
 void *saved = malloc(capacity), *saved_source = malloc(fdt_totalsize(source));
 memcpy(saved, target, capacity); memcpy(saved_source, source, fdt_totalsize(source));
 int ret = apple_j713_import_fdt(target, source);
 assert(!memcmp(source, saved_source, fdt_totalsize(source)));
  if (fault && fault != 10 && fault != 13 && fault != 15 && fault != 16 && fault != 17 && fault != 19 && fault != 21 && fault != 22 && fault != 26 && fault != 28 && fault != 29 && fault != 34 && fault != 35 && fault != 36 && fault != 38 && fault != 39) {
  assert(ret < 0); assert(!memcmp(saved, target, capacity));
  puts("refused transactionally"); return 0;
 }
 if (fault == 10) { assert(!ret && !memcmp(saved, target, capacity)); return 0; }
 assert(ret == 1); assert(!fdt_check_full(target, capacity));
 if (fault == 13 || fault == 15) {
  int node = fdt_path_offset(target, fault == 13 ? "gpu" : "disp0");
  if (node >= 0) {
   const char *status = fdt_getprop(target, node, "status", NULL);
   assert(status && !strcmp(status, "disabled"));
  }
 }
 if (fault == 17) {
  const char *s = fdt_getprop(target, fdt_path_offset(target, "/soc/pcie@1cb0000000"), "status", NULL);
  assert(s && !strcmp(s, "disabled"));
 }
  int aop = fdt_node_offset_by_compatible(target, -1, "apple,t8132-aop");
  if (aop >= 0) {
   const char *status = fdt_getprop(target, aop, "status", NULL);
   const fdt32_t *region = fdt_getprop(target, aop, "memory-region", &len);
   if (fault == 35 || fault == 36 || fault == 39) {
    assert(status && !strcmp(status, "disabled") && !region);
   } else if (fault == 38) {
    assert(!status && region && len == 4 && fdt32_to_cpu(*region) == 1040);
   } else {
    /* m1n1's reservations, owned by the OS tree's AOP. */
    const char *names[] = {"asc-firmware@9000c0000", "asc-mmio@388020000"};
    u32 phandle = fdt_get_phandle(target, aop);
    assert(!status && region && len == 8 && phandle);
    for (unsigned i = 0; i < 2; i++) {
     int n = fdt_node_offset_by_phandle(target, fdt32_to_cpu(region[i])), ilen;
     assert(n >= 0 && !strcmp(fdt_get_name(target, n, NULL), names[i]));
     const fdt32_t *tuple = fdt_getprop(target, n, "iommu-addresses", &ilen);
     assert(tuple && ilen == 20 && fdt32_to_cpu(tuple[0]) == phandle);
    }
   }
  }
  int bt = fdt_path_offset(target, "bluetooth0");
  if (bt >= 0) {
   const char *status = fdt_getprop(target, bt, "status", NULL);
   if (fault == 21 || fault == 22) {
    assert(status && !strcmp(status, "disabled"));
   } else {
    const char *names[] = {"local-bd-address", "brcm,taurus-cal-blob", "brcm,taurus-bf-cal-blob"};
    int sn = fdt_path_offset(source, "bluetooth0");
    for (unsigned i = 0; i < sizeof(names) / sizeof(names[0]); i++) {
     int expected_len;
     const void *expected = fdt_getprop(source, sn, names[i], &expected_len);
     const void *actual = fdt_getprop(target, bt, names[i], &len);
     assert(expected && actual && len == expected_len && !memcmp(expected, actual, len));
    }
   }
  }
 for (unsigned port = 0; port < 2; port++) {
  const char *alias = port ? "atcphy1" : "atcphy0";
  int phy = fdt_path_offset(target, alias);
  if (phy >= 0) {
   bool usb2_only = fdt_stringlist_count(target, phy, "reg-names") == 2;
   const char *names[] = {"apple,tunable-usb3-axi2af", "apple,tunable-usb3-lioa",
                         "apple,tunable-usb3-fabric", "apple,tunable-usb3-config",
                         "apple,tunable-usb3-common", "apple,tunable-usb3-efuse",
                         "apple,tunable-usb3-dp",
                         "apple,tunable-lane0-usb", "apple,tunable-lane1-usb"};
   int sn = fdt_path_offset(source, alias);
   for (unsigned i = 0; i < sizeof(names) / sizeof(names[0]); i++) {
    int expected_len;
    const void *expected = sn >= 0 ? fdt_getprop(source, sn, names[i], &expected_len) : NULL;
    if (usb2_only) expected = NULL;
    const void *actual = fdt_getprop(target, phy, names[i], &len);
    if (!expected) assert(!actual);
    else assert(actual && len == expected_len && !memcmp(expected, actual, len));
   }
  }
 }
 check_policy(saved, target);
 chosen = fdt_path_offset(target, "/chosen");
 assert(!fdt_getprop(target, chosen, J713_FDT_COMPLETE, NULL));
 assert(!apple_j713_complete_fdt(target));
 const fdt32_t *p = fdt_getprop(target, fdt_path_offset(target, "/chosen"), J713_FDT_COMPLETE, &len);
 assert(p && len == 4 && fdt32_to_cpu(*p) == 1);
 check_references(target);
 const char *enabled[] = {"ans", "ans-mbox", "sart-ans", "pmp"};
 for (unsigned i = 0; i < sizeof(enabled) / sizeof(enabled[0]); i++) {
  int n = fdt_path_offset(target, enabled[i]);
  const char *status = fdt_getprop(target, n, "status", &len);
  assert(status && !strcmp(status, "okay"));
 }
 assert(fdt_num_mem_rsv(target) >= 1);
 int fb = fdt_path_offset(target, "/chosen/framebuffer");
 assert(!fdt_getprop(target, fb, "status", NULL));
 int policy = fdt_path_offset(target, "/soc/pmgr");
 if (policy >= 0) {
  const char *status = fdt_getprop(target, policy, "status", &len);
  assert(status && !strcmp(status, "disabled"));
  assert(fdt_getprop(target, policy, "apple,firmware-owned", NULL));
 }
 /* Repeat against the same live epoch: no duplicate reservations or foreign IDs. */
 int nr = fdt_num_mem_rsv(target);
 assert(apple_j713_import_fdt(target, source) == 1);
 assert(nr == fdt_num_mem_rsv(target)); check_references(target);
 chosen = fdt_path_offset(target, "/chosen");
 assert(!fdt_getprop(target, chosen, J713_FDT_COMPLETE, NULL));
 assert(!apple_j713_complete_fdt(target));
 puts("runtime import and destination phandles verified"); return 0;
}
'''


def build_importer(tmp_path):
    repo = Path(__file__).resolve().parents[3]
    source = tmp_path / 'import.c'
    source.write_text(HARNESS.replace('PRODUCTION', str(repo / 'arch/arm/mach-apple/j713_fdt.c')))
    libfdt = repo / 'scripts/dtc/libfdt'
    names = ['fdt', 'fdt_ro', 'fdt_rw', 'fdt_sw', 'fdt_wip', 'fdt_empty_tree',
             'fdt_strerror', 'fdt_addresses']
    binary = tmp_path / 'import'
    subprocess.run(['cc', '-D_GNU_SOURCE', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                    '-Wno-sign-compare', '-Wno-missing-field-initializers',
                    '-I', str(libfdt), str(source),
                    *[str(libfdt / (name + '.c')) for name in names],
                    '-o', str(binary)], check=True)
    return binary


def compile_tree(tmp_path, name, text):
    source = tmp_path / (name + '.dts')
    dtb = tmp_path / (name + '.dtb')
    source.write_text(text)
    subprocess.run(['dtc', '-q', '-I', 'dts', '-O', 'dtb', '-o', str(dtb), str(source)], check=True)
    return dtb


def test_j713_runtime_import(tmp_path):
    binary = build_importer(tmp_path)
    source = compile_tree(tmp_path, 'runtime', fixture_tree(True))
    graphics = compile_tree(tmp_path, 'graphics', fixture_tree(False))
    basic = compile_tree(tmp_path, 'basic', fixture_tree(False, graphics=False))
    for target in [graphics, basic]:
        for fault in range(20):
            subprocess.run([str(binary), str(source), str(target), str(fault)], check=True)


def test_j713_peripheral_calibration(tmp_path):
    binary = build_importer(tmp_path)
    source = compile_tree(tmp_path, 'runtime', fixture_tree(True, peripherals=True))
    target = compile_tree(tmp_path, 'os', fixture_tree(False, peripherals=True))
    for fault in [0, *range(20, 29)]:
        subprocess.run([str(binary), str(source), str(target), str(fault)], check=True)


def test_j713_optional_multifunction_calibration(tmp_path):
    binary = build_importer(tmp_path)
    source = compile_tree(tmp_path, 'runtime', fixture_tree(True, peripherals=True))
    target = compile_tree(tmp_path, 'os', fixture_tree(False, peripherals=True))
    # Missing AUX preserves USB; malformed AUX refuses without publishing changes.
    for fault in range(29, 35):
        subprocess.run([str(binary), str(source), str(target), str(fault)], check=True)


def test_j713_aop_handoff(tmp_path):
    binary = build_importer(tmp_path)
    source = compile_tree(tmp_path, 'runtime', fixture_tree(True, aop=True))
    target = compile_tree(tmp_path, 'os', fixture_tree(False, aop=True))
    # Handed off, refused by m1n1, absent from the control tree, a malformed
    # reference (refused transactionally), an OS tree with its own regions and
    # an OS tree whose AOP is not the control tree's.
    for fault in [0, 35, 36, 37, 38, 39]:
        subprocess.run([str(binary), str(source), str(target), str(fault)], check=True)
    # An OS tree without an AOP is untouched by a control tree with one.
    plain = compile_tree(tmp_path, 'plain', fixture_tree(False))
    subprocess.run([str(binary), str(source), str(plain), '0'], check=True)


def test_j713_fdt_padding_guard():
    repo = Path(__file__).resolve().parents[3]
    header = repo / 'arch/arm/include/asm/arch-apple/j713_fdt.h'
    for enabled, pad, valid in [(True, 0x3000, False), (True, 144 * 1024 - 1, False),
                                (True, 144 * 1024, True), (True, 0x40000, True),
                                (False, 0x3000, True)]:
        command = ['cc', '-x', 'c', '-fsyntax-only', '-include', str(header),
                   '-DCONFIG_SYS_FDT_PAD=' + str(pad)]
        if enabled:
            command.append('-DCONFIG_APPLE_J713_FDT=1')
        result = subprocess.run(command + ['-'], input='', text=True, capture_output=True)
        assert (result.returncode == 0) == valid, result.stderr
        if not valid:
            assert 'J713 per-UKI FDT import requires' in result.stderr


def test_j713_private_captures(tmp_path):
    source = os.environ.get('APPLE_J713_RUNTIME_DTB')
    targets = os.environ.get('APPLE_J713_OS_DTBS')
    if not source or not targets:
        import pytest
        pytest.skip('private runtime capture and built candidate DTBs were not supplied')
    binary = build_importer(tmp_path)
    for target in targets.split(':'):
        subprocess.run([str(binary), source, target, '0'], check=True)
