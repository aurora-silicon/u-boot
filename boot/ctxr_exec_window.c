// SPDX-License-Identifier: GPL-2.0+
/*
 * Read the physical window a Linux kernel confines its executable code to, and
 * keep U-Boot's runtime code inside it.
 *
 * The other end of this is ctxr_parse_dt() and ctxr_window_valid() in the
 * kernel's arch/arm64/mm/ctxr_execmem.c. This takes the windows a 16K-page
 * kernel takes, so U-Boot only moves a kernel that will look for its image in
 * the window. A kernel that rejects the window, or finds its image outside it,
 * keeps its secondary CPUs offline and boots on the boot CPU.
 */

#include <ctxr_exec_window.h>
#include <env_internal.h>
#include <fdtdec.h>
#include <asm/global_data.h>
#include <asm/sections.h>
#include <dm/of.h>
#include <dm/ofnode.h>
#include <lmb.h>
#include <linux/errno.h>
#include <linux/kernel.h>
#include <linux/libfdt.h>
#include <linux/sizes.h>

DECLARE_GLOBAL_DATA_PTR;

/* Where booti or bootm last put a kernel in the window, if it didn't start */
static phys_addr_t ctxr_booti_base;
static phys_size_t ctxr_booti_size;

/* The kernel wants its window page aligned, and Apple kernels use 16K pages */
#define CTXR_EXEC_PAGE	SZ_16K

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

	return win->base && win->size && IS_ALIGNED(win->base, CTXR_EXEC_PAGE) &&
	       IS_ALIGNED(win->size, CTXR_EXEC_PAGE) &&
	       win->base + win->size > win->base;
}

void ctxr_exec_warn_outside(const struct ctxr_exec_window *win,
			    const char *why)
{
	printf("WARNING: CTXR exec window [%#llx, %#llx): %s\n", win->base,
	       win->base + win->size, why);
	printf("WARNING: Linux will run outside it, on the boot CPU only\n");
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

/* What relocate_code() reads while it copies, and the device tree */
static bool ctxr_exec_in_use(u64 start, u64 size)
{
	ulong image = (ulong)__image_copy_start;
	ulong end = max((ulong)__bss_end, (ulong)_end);

	return ctxr_exec_overlaps(image, end - image, start, size) ||
	       ctxr_exec_overlaps((ulong)gd->fdt_blob,
				  fdt_totalsize(gd->fdt_blob), start, size);
}

int ctxr_exec_relocate_uboot(void)
{
	phys_size_t size = ctxr_exec_uboot_size();
	struct ctxr_exec_window win;
	u64 end, image, bottom;

	gd->ctxr_malloc_end = gd->relocaddr;

	if ((gd->flags & GD_FLG_SKIP_RELOC) ||
	    CONFIG_IS_ENABLED(SKIP_RELOCATE_CODE) ||
	    !ctxr_exec_window_get(&win) || win.size < size)
		return 0;

	end = win.base + win.size;
	image = ALIGN_DOWN(end - size, SZ_64K);
	/* All of U-Boot's other memory is reserved by now, down to its stack */
	bottom = ALIGN_DOWN(gd->start_addr_sp - CONFIG_STACK_SIZE, SZ_4K);

	if (image < win.base || win.base < gd->ram_base || end > bottom ||
	    ctxr_exec_in_use(image, size) ||
	    ctxr_exec_reserved(gd->fdt_blob, image, size))
		return 0;

	gd->relocaddr = image;

	return 0;
}

phys_size_t ctxr_exec_uboot_region(phys_addr_t *base)
{
	if (gd->relocaddr == gd->ctxr_malloc_end)
		return 0;

	*base = gd->relocaddr;

	return ctxr_exec_uboot_size();
}

int ctxr_exec_place_booti(const struct ctxr_exec_window *win, u64 size,
			  u64 *slot)
{
	u64 addr;

	ctxr_exec_unplace_booti();

	for (addr = ALIGN(win->base, SZ_2M);
	     addr + size <= win->base + win->size; addr += SZ_2M) {
		phys_addr_t base = addr;

		if (lmb_release_ctxr_window(addr, size))
			continue;
		/* booti reserves the image's part again, which merges */
		if (lmb_alloc_mem(LMB_MEM_ALLOC_ADDR, 0, &base, size, LMB_NONE)) {
			lmb_hold_ctxr_window(addr, size);
			continue;
		}

		ctxr_booti_base = addr;
		ctxr_booti_size = size;
		*slot = addr;
		return 0;
	}

	return -ENOSPC;
}

void ctxr_exec_unplace_booti(void)
{
	if (!ctxr_booti_size)
		return;

	if (!lmb_free(ctxr_booti_base, ctxr_booti_size, LMB_NONE))
		lmb_hold_ctxr_window(ctxr_booti_base, ctxr_booti_size);
	ctxr_booti_size = 0;
}
