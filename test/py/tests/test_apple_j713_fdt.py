# SPDX-License-Identifier: GPL-2.0-or-later OR MIT
"""Exercise the production per-UKI importer with real libfdt.

Optional APPLE_J713_RUNTIME_DTB and APPLE_J713_OS_DTBS (colon-separated) run
the same importer against private hardware captures and built PR DTBs. Captures
contain per-machine identifiers and are intentionally not repository fixtures.
"""
import os
from pathlib import Path
import subprocess


def fixture_tree(runtime, graphics=True):
    shift = 0 if runtime else 1000
    aliases = {
        'ans': '/soc/nvme', 'ans-mbox': '/soc/mailbox', 'sart-ans': '/soc/sart',
        'spmi-primary': '/soc/spmi', 'pmu-primary': '/soc/spmi/pmu',
        'pmp': '/soc/pmp', 'wifi0': '/soc/wifi', 'keyboard': '/soc/keyboard',
    }
    if graphics:
        aliases.update(gpu='/soc/gpu', dcp='/soc/dcp', disp0='/soc/display',
                       disp0_piodma='/soc/dcp/piodma')
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
 assert(!fdt_add_mem_rsv(source, 0x900040000ULL, 0x4000));
 void *saved = malloc(capacity), *saved_source = malloc(fdt_totalsize(source));
 memcpy(saved, target, capacity); memcpy(saved_source, source, fdt_totalsize(source));
 int ret = apple_j713_import_fdt(target, source);
 assert(!memcmp(source, saved_source, fdt_totalsize(source)));
 if (fault && fault != 10 && fault != 13 && fault != 15 && fault != 16 && fault != 17 && fault != 19) {
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
