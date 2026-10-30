/* SPDX-License-Identifier: GPL-2.0+ */
#include <apple_apif_nvme.h>

static struct mm_region m5_apif_mem_map[CONFIG_NR_DRAM_BANKS + 7];

static void build_m5_apif_mem_map(void)
{
	struct mm_region *map = m5_apif_mem_map;
	u64 queues[4], base, size;
	ofnode fb = ofnode_path("/chosen/framebuffer");
	int n = 0, i, j, node, inherited = 0;

	memset(map, 0, sizeof(m5_apif_mem_map));
	map[n++] = (struct mm_region) {
		.virt = 0x60000000, .phys = 0x60000000, .size = SZ_128M,
		.attrs = PTE_BLOCK_MEMTYPE(MT_DEVICE_NGNRNE) |
			 PTE_BLOCK_NON_SHARE | PTE_BLOCK_PXN | PTE_BLOCK_UXN,
	};
	for (i = 0; i < CONFIG_NR_DRAM_BANKS; i++) {
		base = gd->bd->bi_dram[i].start;
		size = gd->bd->bi_dram[i].size;
		if (!size)
			continue;
		if (!IS_ALIGNED(base, SZ_4K) || !IS_ALIGNED(size, SZ_4K) ||
		    base + size < base)
			panic("Invalid m5_apif RAM bank\n");
		map[n++] = (struct mm_region) {
			.virt = base, .phys = base, .size = size,
			.attrs = PTE_BLOCK_MEMTYPE(MT_NORMAL) | PTE_BLOCK_INNER_SHARE,
		};
	}
	node = fdt_node_offset_by_compatible(gd->fdt_blob, -1, APPLE_APIF_NVME_COMPAT);
	if (node >= 0)
		inherited = apple_apif_queue_pages(gd->fdt_blob, node, queues);
	if (inherited < 0)
		panic("Invalid inherited APIF NVMe queues\n");
	for (i = 0; inherited && i < 4; i++) {
		bool mapped = false;

		base = queues[i];
		size = APPLE_APIF_QUEUE_PAGE_SIZE;
		for (j = 0; j < n; j++) {
			if (base >= map[j].phys + map[j].size || base + size <= map[j].phys)
				continue;
			if (!j || base < map[j].phys || base + size > map[j].phys + map[j].size)
				panic("APIF queue overlaps guest mapping\n");
			mapped = true;
			break;
		}
		if (!mapped)
			map[n++] = (struct mm_region) {
				.virt = base, .phys = base, .size = size,
				.attrs = PTE_BLOCK_MEMTYPE(MT_NORMAL) | PTE_BLOCK_INNER_SHARE |
					 PTE_BLOCK_PXN | PTE_BLOCK_UXN,
			};
	}
	if (ofnode_valid(fb)) {
		fdt_size_t fb_size;

		base = ofnode_get_addr_size(fb, "reg", &fb_size);
		if (base != FDT_ADDR_T_NONE && fb_size) {
			size = ALIGN(fb_size + (base & (SZ_4K - 1)), SZ_4K);
			base = ALIGN_DOWN(base, SZ_4K);
			for (i = 0; inherited && i < 4; i++)
				if (base < queues[i] + APPLE_APIF_QUEUE_PAGE_SIZE &&
				    queues[i] < base + size)
					panic("APIF queue overlaps framebuffer\n");
			map[n++] = (struct mm_region) {
				.virt = base, .phys = base, .size = size,
				.attrs = PTE_BLOCK_MEMTYPE(MT_NORMAL_NC) | PTE_BLOCK_INNER_SHARE |
					 PTE_BLOCK_PXN | PTE_BLOCK_UXN,
			};
		}
	}
	mem_map = map;
}
