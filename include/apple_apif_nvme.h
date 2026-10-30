/* SPDX-License-Identifier: GPL-2.0+ */
#ifndef APPLE_APIF_NVME_H
#define APPLE_APIF_NVME_H

#include <linux/types.h>
#include <linux/errno.h>
#include <linux/string.h>
#include <linux/libfdt.h>

/* Same named reserved-memory contract consumed by Linux nvme-apple. */
#define APPLE_APIF_QUEUE_PAGE_SIZE 0x4000UL
#define APPLE_APIF_NVME_COMPAT "apple,nvme-apif"

/* FDT cells are only four-byte aligned. This also runs before enable_caches,
 * where a compiler-coalesced unaligned 64-bit load raises an alignment fault.
 */
static inline u64 apple_apif_fdt64(const void *value)
{
	const volatile unsigned char *p = value;
	u64 result = 0;
	int i;

	for (i = 0; i < 8; i++)
		result = (result << 8) | p[i];
	return result;
}

/* 0: no inherited queues, 1: complete valid handoff, negative: malformed. */
static inline int apple_apif_queue_pages(const void *fdt, int nvme, u64 pages[4])
{
	static const char * const names[] = { "admin-sq", "admin-cq", "io-sq", "io-cq" };
	const fdt32_t *handles;
	u64 found[4];
	int len, reserved, i, j;

	memset(pages, 0, sizeof(found));
	handles = fdt_getprop(fdt, nvme, "memory-region", &len);
	if (!handles) {
		if (len != -FDT_ERR_NOTFOUND ||
		    fdt_getprop(fdt, nvme, "memory-region-names", NULL))
			return -EINVAL;
		return 0;
	}
	if (len != 4 * sizeof(*handles) ||
	    fdt_stringlist_count(fdt, nvme, "memory-region-names") != 4 ||
	    !fdt_getprop(fdt, nvme, "asahi,rtkit-quiesced", NULL) ||
	    !fdt_getprop(fdt, nvme, "dma-coherent", NULL))
		return -EINVAL;
	reserved = fdt_path_offset(fdt, "/reserved-memory");
	if (reserved < 0 || fdt_address_cells(fdt, reserved) != 2 ||
	    fdt_size_cells(fdt, reserved) != 2)
		return -EINVAL;
	for (i = 0; i < 4; i++) {
		const fdt64_t *reg;
		int node, index = fdt_stringlist_search(fdt, nvme, "memory-region-names", names[i]);
		u32 handle;

		if (index < 0 || index >= 4)
			return -EINVAL;
		handle = fdt32_to_cpu(handles[index]);
		if (!handle || handle == ~0U)
			return -EINVAL;
		node = fdt_node_offset_by_phandle(fdt, handle);
		if (node < 0 || fdt_parent_offset(fdt, node) != reserved ||
		    !fdt_getprop(fdt, node, "no-map", NULL))
			return -EINVAL;
		reg = fdt_getprop(fdt, node, "reg", &len);
		if (!reg || len != 2 * sizeof(*reg) ||
		    apple_apif_fdt64(reg + 1) != APPLE_APIF_QUEUE_PAGE_SIZE)
			return -EINVAL;
		found[i] = apple_apif_fdt64(reg);
		if (!found[i] || found[i] & (APPLE_APIF_QUEUE_PAGE_SIZE - 1) ||
		    found[i] > (1ULL << 42) - APPLE_APIF_QUEUE_PAGE_SIZE)
			return -EINVAL;
		for (j = 0; j < i; j++)
			if (found[i] == found[j])
				return -EINVAL;
	}
	memcpy(pages, found, sizeof(found));
	return 1;
}

int apple_nvme_apif_handoff_fdt(void *blob);
void apple_nvme_apif_quiesce(void);

#endif
