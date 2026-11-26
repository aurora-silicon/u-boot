# SPDX-License-Identifier: GPL-2.0-or-later OR MIT
"""Host-check the Apple DRAM bank list against the generic fdtdec readers.

dram_init() and dram_init_banksize() in arch/arm/mach-apple/board.c read the
memory nodes in one walk of the tree. They must give exactly what
fdtdec_setup_mem_size_base() and the CONFIG_NR_DRAM_BANKS check followed by
fdtdec_setup_memory_banksize() gave: the same banks in the same order, the
same RAM base and size, the same errors. The harness builds both from the
sources in this tree against real libfdt and compares them, and counts the
structure tags each one reads, since the old way walked the tree twice per
range before relocation with the data cache off.

APPLE_DRAM_DTBS (colon-separated) adds device trees captured from machines.
"""
import os
from pathlib import Path
import re
import subprocess

# The twelve /memory ranges the J700 (A18 Pro) gets from aura Stage 2.
J700_RANGES = [
    (0x1000342c000, 0x1e39e0000), (0x101ffe50000, 0x4000), (0x101ffe08000, 0x4000),
    (0x101ffe30000, 0xc000), (0x101ffe70000, 0x8000), (0x101ffef8000, 0x14000),
    (0x101ffed8000, 0x18000), (0x101e6e2c000, 0x4000), (0x101e6e10000, 0x4000),
    (0x101fff30000, 0x4000), (0x101fff34000, 0x4000), (0x101fff38000, 0x80000),
]

HARNESS = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <libfdt.h>

typedef uint64_t u64, phys_addr_t, phys_size_t, fdt_addr_t, fdt_size_t, resource_size_t;
typedef int ofnode;
#define CONFIG_NR_DRAM_BANKS 16
#define CONFIG_IS_ENABLED(x) 1	/* OF_TRANSLATE, as in apple_m1_defconfig */
#define __section(s)
#define debug(...) do { } while (0)
#define log_err(...) fprintf(stderr, __VA_ARGS__)
#define OF_BAD_ADDR ((u64)-1)
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define be32_to_cpup(p) fdt32_to_cpu(*(p))
static inline u64 fdt_read_number(const fdt32_t *cell, int size)
{
	u64 r = 0;

	while (size--)
		r = (r << 32) | fdt32_to_cpu(*(cell++));
	return r;
}
struct resource { resource_size_t start, end; };
struct fdt_resource { fdt_addr_t start, end; };
struct bd_info { struct { phys_addr_t start; phys_size_t size; } bi_dram[CONFIG_NR_DRAM_BANKS]; };
static struct bd_info board;
static struct {
	const void *fdt_blob;
	unsigned long ram_base;
	phys_size_t ram_size;
	struct bd_info *bd;
} global = { .bd = &board }, *gd = &global;

/* Flat-tree ofnode, as drivers/core/ofnode.c has it without OF_LIVE. */
static bool ofnode_valid(ofnode n) { return n >= 0; }
static ofnode ofnode_null(void) { return -1; }
static ofnode offset_to_ofnode(int o) { return o; }
static ofnode ofnode_path(const char *p) { return fdt_path_offset(gd->fdt_blob, p); }
static ofnode ofnode_by_prop_value(ofnode from, const char *name, const void *val, int len)
{ return fdt_node_offset_by_prop_value(gd->fdt_blob, from, name, val, len); }
int fdtdec_get_is_enabled(const void *blob, int node);
static bool ofnode_is_enabled(ofnode n) { return fdtdec_get_is_enabled(gd->fdt_blob, n); }
int fdt_get_resource(const void *fdt, int node, const char *property,
		     unsigned int index, struct fdt_resource *res);
static int ofnode_read_resource(ofnode node, unsigned int index, struct resource *res)
{
	struct fdt_resource fres;

	if (fdt_get_resource(gd->fdt_blob, node, "reg", index, &fres) < 0)
		return -EINVAL;
	memset(res, 0, sizeof(*res));
	res->start = fres.start;
	res->end = fres.end;
	return 0;
}

/* Every structure tag libfdt reads goes through here. */
uint32_t fdt_next_tag_counted(const void *fdt, int startoffset, int *nextoffset);
static unsigned long tags;
uint32_t fdt_next_tag(const void *fdt, int startoffset, int *nextoffset)
{
	tags++;
	return fdt_next_tag_counted(fdt, startoffset, nextoffset);
}

@TRANSLATE@
@FDTDEC@

/* The Apple code this replaces (097a4347017). */
static int old_dram_init(void)
{
	return fdtdec_setup_mem_size_base();
}

static int old_dram_init_banksize(void)
{
	ofnode mem = ofnode_null();
	struct resource res;
	int bank = 0, reg;

	while (ofnode_valid(mem = fdtdec_get_next_memory_node(mem))) {
		for (reg = 0; !ofnode_read_resource(mem, reg, &res); reg++) {
			if (++bank > CONFIG_NR_DRAM_BANKS)
				return -E2BIG;
		}
	}

	return fdtdec_setup_memory_banksize();
}

@APPLE@

int main(int argc, char **argv)
{
	static char blob[1 << 20];
	struct bd_info old_board;
	unsigned long old_base, walk = 0, old_tags, new_tags;
	phys_size_t old_size;
	int old_init, old_banks, new_init, new_banks, node, depth = -1, i;
	FILE *f;

	assert(argc == 2);
	f = fopen(argv[1], "rb");
	assert(f && fread(blob, 1, sizeof(blob), f) > 0);
	fclose(f);
	assert(!fdt_check_header(blob));
	gd->fdt_blob = blob;

	tags = 0;
	for (node = fdt_next_node(blob, -1, &depth); node >= 0 && depth >= 0;
	     node = fdt_next_node(blob, node, &depth))
		;
	walk = tags;

	tags = 0;
	old_init = old_dram_init();
	old_banks = old_dram_init_banksize();
	old_tags = tags;
	old_board = board;
	old_base = gd->ram_base;
	old_size = gd->ram_size;

	memset(&board, 0, sizeof(board));
	gd->ram_base = 0x5a5a;
	gd->ram_size = 0xa5a5;
	tags = 0;
	new_init = dram_init();
	new_banks = dram_init_banksize();
	new_tags = tags;

	printf("init %d banksize %d walk %lu old %lu new %lu\n",
	       old_init, old_banks, walk, old_tags, new_tags);
	for (i = 0; i < CONFIG_NR_DRAM_BANKS && old_board.bi_dram[i].start | old_board.bi_dram[i].size; i++)
		printf("bank %#llx %#llx\n", (unsigned long long)old_board.bi_dram[i].start,
		       (unsigned long long)old_board.bi_dram[i].size);
	if (!old_init)
		printf("ram %#lx %#llx\n", old_base, (unsigned long long)old_size);

	assert(new_init == old_init);
	assert(new_banks == old_banks);
	if (!old_init)
		assert(gd->ram_base == old_base && gd->ram_size == old_size);
	assert(!memcmp(&board, &old_board, sizeof(board)));
	return 0;
}
'''


def function(source, signature):
    start = source.index(signature)
    return source[start:source.index('\n}\n', start) + 3]


def build(tmp_path):
    repo = Path(__file__).resolve().parents[3]
    board = (repo / 'arch/arm/mach-apple/board.c').read_text()
    apple = board[board.index('/*\n * dram_init() and dram_init_banksize()'):
                  board.index('extern long fw_dtb_pointer;')]
    support = (repo / 'boot/fdt_support.c').read_text()
    translate = support[support.index('#define OF_MAX_ADDR_CELLS'):]
    translate = translate[:translate.index('\n}\n', translate.index(
        'u64 fdt_translate_address(')) + 3]
    fdtdec = (repo / 'lib/fdtdec.c').read_text()
    fdtdec = ''.join(function(fdtdec, s) for s in (
        'u64 fdtdec_get_number(', 'int fdtdec_get_is_enabled(', 'int fdt_get_resource(',
        'int fdtdec_setup_mem_size_base(void)', 'static ofnode get_next_memory_node(',
        'ofnode fdtdec_get_next_memory_node(', 'int fdtdec_setup_memory_banksize(void)'))
    harness = tmp_path / 'dram.c'
    harness.write_text(HARNESS.replace('@TRANSLATE@', translate).replace('@FDTDEC@', fdtdec)
                       .replace('@APPLE@', apple))

    libfdt = repo / 'scripts/dtc/libfdt'
    fdt_c = tmp_path / 'fdt.c'
    text, count = re.subn(r'\buint32_t fdt_next_tag\(', 'uint32_t fdt_next_tag_counted(',
                          (libfdt / 'fdt.c').read_text())
    assert count == 1
    fdt_c.write_text(text)
    binary = tmp_path / 'dram'
    subprocess.run(['cc', '-D_GNU_SOURCE', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                    '-Wno-sign-compare', '-Wno-unused-function', '-Wno-unused-parameter',
                    '-I', str(libfdt), str(harness), str(fdt_c),
                    *[str(libfdt / (name + '.c')) for name in ('fdt_ro', 'fdt_rw', 'fdt_wip', 'fdt_addresses', 'fdt_strerror')],
                    '-o', str(binary)], check=True)
    return binary


def reg(ranges, na=2, ns=2):
    cells = []
    for start, size in ranges:
        cells += [(start >> (32 * i)) & 0xffffffff for i in reversed(range(na))]
        cells += [(size >> (32 * i)) & 0xffffffff for i in reversed(range(ns))]
    return 'reg = <' + ' '.join(hex(c) for c in cells) + '>;'


def memory(name, ranges, extra='', na=2, ns=2):
    body = reg(ranges, na, ns) if ranges is not None else ''
    return f'{name} {{ device_type = "memory"; {body} {extra} }};'


def tree(body, na=2, ns=2, filler=400):
    # A soc node big enough that the memory node sits far into the tree, as on the J700.
    soc = ''.join(f'dev@{i:x} {{ compatible = "apple,dev"; reg = <0 {i:#x} 0 0x1000>;'
                  f' interrupts = <0 {i} 4>; power-domains = <1>; status = "okay"; }};'
                  for i in range(filler))
    return (f'/dts-v1/; / {{ #address-cells = <{na}>; #size-cells = <{ns}>;'
            f' compatible = "apple,j700", "apple,t8140";'
            f' soc {{ #address-cells = <2>; #size-cells = <2>; ranges; {soc} }};'
            f' {body} thermal-zones {{ }}; }};')


CASES = {
    'j700': tree(memory('memory@10000000000', J700_RANGES)),
    'one-range': tree(memory('memory@800000000', [(0x800000000, 0x200000000)])),
    'limit': tree(memory('memory@0', [(0x10000 * i, 0x1000) for i in range(16)])),
    'over-limit': tree(memory('memory@0', [(0x10000 * i, 0x1000) for i in range(17)])),
    'over-limit-split': tree(memory('memory@0', [(0x10000 * i, 0x1000) for i in range(10)]) +
                             memory('memory@1', [(0x100000 * i, 0x1000) for i in range(7)])),
    'two-nodes': tree(memory('memory@800000000', [(0x800000000, 0x1000), (0x900000000, 0x2000)]) +
                      memory('memory@a00000000', [(0xa00000000, 0x3000)])),
    'first-empty': tree(memory('memory@0', None) + memory('memory@1', [(0x1000, 0x1000)])),
    'middle-empty': tree(memory('memory@0', [(0x1000, 0x1000)]) + memory('memory@1', None) +
                         memory('memory@2', [(0x3000, 0x1000)])),
    'disabled': tree(memory('memory@0', [(0x1000, 0x1000)], 'status = "disabled";') +
                     memory('memory@1', [(0x2000, 0x2000)])),
    'zero-size': tree(memory('memory@0', [(0x1000, 0x1000), (0x5000, 0), (0x9000, 0x1000)])),
    'partial-entry': tree('memory@0 { device_type = "memory"; reg = <0 0x1000 0 0x2000 0x1>; };'),
    'size-cells-1': tree(memory('memory@0', [(0x800000000, 0x10000000)], na=2, ns=1), ns=1),
    'address-cells-3': tree(memory('memory@0', [(0x800000000, 0x1000)], na=3, ns=2), na=3),
    'no-unit-name': tree(memory('memory', [(0x800000000, 0x1000)])),
    'path-not-memory': tree('memory@1 { reg = <0 0x1000 0 0x1000>; };' +
                            memory('memory@2', [(0x2000, 0x2000)])),
    'nested': tree('bus { #address-cells = <1>; #size-cells = <1>; ranges = <0 8 0 0x10000000>;' +
                   memory('memory@1000', [(0x1000, 0x2000)], na=1, ns=1) + '};'),
    'no-memory': tree(''),
}


def run(binary, dtb):
    out = subprocess.run([str(binary), str(dtb)], check=True, capture_output=True,
                         text=True).stdout
    first = out.splitlines()[0].split()
    stats = dict(zip(first[::2], map(int, first[1::2])))
    banks = [tuple(int(v, 16) for v in line.split()[1:]) for line in out.splitlines()
             if line.startswith('bank ')]
    return stats, banks


def test_apple_dram_banks(tmp_path):
    binary = build(tmp_path)
    results = {}
    for name, text in CASES.items():
        source = tmp_path / (name + '.dts')
        dtb = tmp_path / (name + '.dtb')
        source.write_text(text)
        subprocess.run(['dtc', '-q', '-I', 'dts', '-O', 'dtb', '-o', str(dtb), str(source)],
                       check=True)
        results[name] = run(binary, dtb)

    stats, banks = results['j700']
    assert stats['init'] == 0 and stats['banksize'] == 0
    assert banks == J700_RANGES
    # The old code walked most of the tree twice per range. Now it is one pass,
    # which reads each node's properties about as often as
    # fdtdec_get_next_memory_node() does.
    assert stats['old'] > 20 * stats['new']
    assert stats['new'] < 4 * stats['walk']

    assert results['limit'][0]['banksize'] == 0 and len(results['limit'][1]) == 16
    assert results['over-limit'][0]['banksize'] == -7
    assert results['over-limit-split'][0]['banksize'] == -7
    assert results['two-nodes'][1] == [(0x800000000, 0x1000), (0x900000000, 0x2000),
                                       (0xa00000000, 0x3000)]
    assert results['first-empty'][1] == [(0x1000, 0x1000)]
    assert results['middle-empty'][1] == [(0x1000, 0x1000)]
    assert results['disabled'][1] == [(0x2000, 0x2000)]
    assert results['nested'][1] == [(0x800001000, 0x2000)]
    assert results['nested'][0]['init'] == -22
    assert results['no-memory'][0]['banksize'] == -22


def test_apple_dram_banks_captures(tmp_path):
    captures = [Path(p) for p in os.environ.get('APPLE_DRAM_DTBS', '').split(':') if p]
    if not captures:
        import pytest
        pytest.skip('APPLE_DRAM_DTBS not set')
    binary = build(tmp_path)
    for dtb in captures:
        stats, banks = run(binary, dtb)
        print(dtb, stats, len(banks))
        assert stats['new'] < 4 * stats['walk']
