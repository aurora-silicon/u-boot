// SPDX-License-Identifier: GPL-2.0+
/*
 * Read the physical window a Linux kernel confines its executable code to, and
 * keep U-Boot's runtime code inside it.
 *
 * The other end of this is ctxr_parse_dt() in the kernel's
 * arch/arm64/mm/ctxr_execmem.c. This accepts what that accepts, so U-Boot only
 * moves a kernel that will look for its image in the window.
 */

#include <ctxr_exec_window.h>
#include <env_internal.h>
#include <fdtdec.h>
#include <asm/global_data.h>
#include <dm/of.h>
#include <dm/ofnode.h>
#include <linux/kernel.h>
#include <linux/libfdt.h>
#include <linux/sizes.h>

DECLARE_GLOBAL_DATA_PTR;

bool ctxr_exec_window_get(struct ctxr_exec_window *win)
{
	const __be32 *prop;
	int len;

	/* <base_hi base_lo size_hi size_lo> */
	prop = ofnode_read_chosen_prop("aurora,ctxr-exec-window", &len);
	if (!prop || len != 4 * sizeof(*prop))
		return false;

	win->base = of_read_number(prop, 2);
	win->size = of_read_number(prop + 2, 2);

	/*
	 * A zero base means the firmware armed no usable window: the kernel
	 * anchors a software-only window on its image, wherever that lands.
	 */
	if (!win->base || !win->size)
		return false;

	prop = ofnode_read_chosen_prop("aurora,ctxr-exec-enforce", &len);
	win->enforce = !prop || len != sizeof(*prop) || *prop;

	return true;
}

/* Runtime services are 64 KiB aligned, and are part of the image */
static phys_size_t ctxr_exec_uboot_size(void)
{
	return ALIGN(gd->mon_len, SZ_64K);
}

static bool ctxr_exec_overlaps(u64 start, u64 size, u64 addr, u64 len)
{
	return addr < start + size && start < addr + len;
}

/* The device tree's reservations are all there is before LMB starts */
static bool ctxr_exec_reserved(const void *fdt, u64 start, u64 size)
{
	struct fdt_resource res;
	int i, node, sub;
	u64 addr, len;

	for (i = 0; i < fdt_num_mem_rsv(fdt); i++) {
		if (!fdt_get_mem_rsv(fdt, i, &addr, &len) &&
		    ctxr_exec_overlaps(start, size, addr, len))
			return true;
	}

	node = fdt_subnode_offset(fdt, 0, "reserved-memory");
	if (node < 0)
		return false;

	fdt_for_each_subnode(sub, fdt, node) {
		if (!fdt_get_resource(fdt, sub, "reg", 0, &res) &&
		    fdtdec_get_is_enabled(fdt, sub) &&
		    ctxr_exec_overlaps(start, size, res.start,
				       fdt_resource_size(&res)))
			return true;
	}

	return false;
}

void ctxr_exec_relocate_uboot(void)
{
	phys_size_t size = ctxr_exec_uboot_size();
	struct ctxr_exec_window win;
	u64 end, image, below;

	gd->ctxr_malloc_end = gd->relocaddr;

	if ((gd->flags & GD_FLG_SKIP_RELOC) ||
	    CONFIG_IS_ENABLED(SKIP_RELOCATE_CODE) ||
	    !ctxr_exec_window_get(&win) || win.size < size)
		return;

	end = win.base + win.size;
	image = ALIGN_DOWN(end - size, SZ_64K);

	/* The rest of U-Boot's memory goes below its usual place, not here */
	below = TOTAL_MALLOC_LEN + CONFIG_STACK_SIZE +
		fdt_totalsize(gd->fdt_blob) + SZ_1M;

	if (image < win.base || win.base < gd->ram_base ||
	    end + below > gd->relocaddr ||
	    ctxr_exec_reserved(gd->fdt_blob, image, size))
		return;

	gd->relocaddr = image;
}

phys_size_t ctxr_exec_uboot_region(phys_addr_t *base)
{
	if (gd->relocaddr == gd->ctxr_malloc_end)
		return 0;

	*base = gd->relocaddr;

	return ctxr_exec_uboot_size();
}
