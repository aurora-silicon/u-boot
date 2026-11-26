# SPDX-License-Identifier: GPL-2.0-or-later OR MIT
"""Host-check the Apple MMU map used before EFI memory allocation."""
from pathlib import Path
import subprocess


def test_apple_dram_maps(tmp_path):
    repo = Path(__file__).resolve().parents[3]
    source = (repo / 'arch/arm/mach-apple/board.c').read_text()
    start = source.index('static const struct mm_region t8103_io_map')
    end = source.index('struct mm_region *mem_map = apple_mem_map;\n')
    maps = source[start:end + len('struct mm_region *mem_map = apple_mem_map;\n')]
    start = source.index('#define APPLE_IO_MAP(map)')
    build = source[start:source.index('void enable_caches(void)', start)]
    harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uint64_t u64, fdt_addr_t, fdt_size_t, phys_addr_t, phys_size_t;
typedef int ofnode;
#define CONFIG_NR_DRAM_BANKS 16
#define SZ_4K 0x1000ULL
#define SZ_2M 0x200000ULL
#define SZ_256M 0x10000000ULL
#define SZ_512M 0x20000000ULL
#define SZ_1G 0x40000000ULL
#define SZ_2G 0x80000000ULL
#define MT_NORMAL 1
#define MT_NORMAL_NC 2
#define MT_DEVICE_NGNRE 3
#define MT_DEVICE_NGNRNE 4
#define PTE_BLOCK_MEMTYPE(x) (x)
#define PTE_BLOCK_NON_SHARE (1ULL << 10)
#define PTE_BLOCK_INNER_SHARE (1ULL << 11)
#define PTE_BLOCK_PXN (1ULL << 12)
#define PTE_BLOCK_UXN (1ULL << 13)
#define ALIGN_DOWN(x, n) ((x) & ~((n) - 1))
#define ALIGN(x, n) ALIGN_DOWN((x) + (n) - 1, (n))
#define FDT_ADDR_T_NONE UINT64_MAX
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define BUILD_BUG_ON(c) _Static_assert(!(c), #c)
struct mm_region { u64 virt, phys, size, attrs; };
static struct { struct { u64 start, size; } bi_dram[CONFIG_NR_DRAM_BANKS]; } board;
static struct { void *unused; typeof(board) *bd; } global = {.bd = &board}, *gd = &global;
static const char *soc;
static bool have_fb;
static bool of_machine_is_compatible(const char *s)
{ return !strcmp(s, soc) || !strcmp(s, "apple,j873g"); }
static ofnode ofnode_path(const char *s) { return have_fb; }
static bool ofnode_valid(ofnode n) { return n; }
static u64 ofnode_get_addr_size(ofnode n, const char *s, u64 *size)
{ *size = 0x12345; return 0x800001234; }
#define panic(...) abort()
''' + maps + build + r'''
static void check_banks(const char *name, unsigned banks, bool framebuffer)
{
    soc = name; have_fb = framebuffer;
    memset(&board, 0, sizeof(board));
    for (unsigned i = 0; i < banks; i++) {
        board.bi_dram[i].start = 0x800000000ULL + i * 0x10000000ULL + 0x123;
        board.bi_dram[i].size = 0x100000 + i * 0x1000;
    }
    build_mem_map();
    /* Every SoC: its I/O regions, then every bank, then the framebuffer. */
    unsigned slot = 0;
    while ((mem_map[slot].attrs & 7) == MT_DEVICE_NGNRE ||
           (mem_map[slot].attrs & 7) == MT_DEVICE_NGNRNE)
        slot++;
    assert(slot);
    if (!strcmp(soc, "apple,t8132"))
        assert(slot == 8);
    for (unsigned i = 0; i < banks; i++) {
        assert(mem_map[slot + i].phys == ALIGN_DOWN(board.bi_dram[i].start, SZ_4K));
        assert(mem_map[slot + i].virt == mem_map[slot + i].phys);
        assert(mem_map[slot + i].size >= board.bi_dram[i].size + 0x123);
        assert((mem_map[slot + i].attrs & 7) == MT_NORMAL);
    }
    if (framebuffer) {
        assert(mem_map[slot + banks].phys == 0x800001000);
        assert(mem_map[slot + banks].size == 0x13000);
        assert((mem_map[slot + banks].attrs & 7) == MT_NORMAL_NC);
    }
    unsigned end = slot + banks + framebuffer;
    assert(!mem_map[end].size && !mem_map[end].attrs);
    if (!strcmp(soc, "apple,t8132")) {
        /* Both split ANS BARs and PCIe ECAM must remain mapped as I/O. */
        assert(mem_map[2].phys <= 0x485cc0000 && mem_map[2].phys + mem_map[2].size > 0x4c5ce4910);
        /* The J713 DT config window and both endpoint ranges must be
         * covered, independently of how the map splits its entries. */
        const u64 windows[][2] = {{0x1cb0000000ULL, 0x10000000ULL},
                                {0xb80000000ULL, 0x40000000ULL},
                                {0xbc0000000ULL, 0x20000000ULL},
                                {0x494000000ULL, 0x4000ULL}};
        for (unsigned w = 0; w < sizeof(windows) / sizeof(windows[0]); w++) {
            bool covered = false;
            for (unsigned m = 0; m < slot; m++)
                if (mem_map[m].phys <= windows[w][0] &&
                    mem_map[m].phys + mem_map[m].size >= windows[w][0] + windows[w][1] &&
                    ((mem_map[m].attrs & 7) == MT_DEVICE_NGNRE ||
                     (mem_map[m].attrs & 7) == MT_DEVICE_NGNRNE))
                    covered = true;
            assert(covered);
        }
    }
}
int main(int argc, char **argv)
{
    assert(argc == 4);
    check_banks(argv[1], atoi(argv[2]), atoi(argv[3]));
    return 0;
}
'''
    c = tmp_path / 'apple-map.c'
    c.write_text(harness)
    binary = tmp_path / 'apple-map'
    subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', str(c), '-o', str(binary)], check=True)
    for soc in ('apple,t8103', 'apple,t8112', 'apple,t6000', 'apple,t6002', 'apple,t6020',
                'apple,t6022', 'apple,t8122', 'apple,t6030', 'apple,t8132', 'apple,t8140',
                'apple,t6040', 'apple,t6050', 'apple,t8142', 'apple,t8152'):
        for banks in (1, 2, 16):
            for fb in (0, 1):
                subprocess.run([str(binary), soc, str(banks), str(fb)], check=True)
