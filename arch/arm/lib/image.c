// SPDX-License-Identifier: GPL-2.0+
/*
 * (C) Copyright 2000-2009
 * Wolfgang Denk, DENX Software Engineering, wd@denx.de.
 */

#include <ctxr_exec_window.h>
#include <image.h>
#include <lmb.h>
#include <mapmem.h>
#include <asm/global_data.h>
#include <linux/bitops.h>
#include <linux/errno.h>
#include <linux/sizes.h>

DECLARE_GLOBAL_DATA_PTR;

#define LINUX_ARM64_IMAGE_MAGIC 0x644d5241

/* See Documentation/arm64/booting.txt in the Linux kernel */
struct Image_header {
	uint32_t	code0;		/* Executable code */
	uint32_t	code1;		/* Executable code */
	uint64_t	text_offset;	/* Image load offset, LE */
	uint64_t	image_size;	/* Effective Image size, LE */
	uint64_t	flags;		/* Kernel flags, LE */
	uint64_t	res2;		/* reserved */
	uint64_t	res3;		/* reserved */
	uint64_t	res4;		/* reserved */
	uint32_t	magic;		/* Magic number */
	uint32_t	res5;
};

/**
 * ctxr_exec_window_slot() - find where an image fits in the window
 *
 * The image is moved before booti reserves its memory, so check here that the
 * range is free RAM.
 *
 * @win:	window to search
 * @size:	bytes from the 2 MiB boundary to the end of the image
 * @slot:	set to the lowest free 2 MiB boundary that fits
 * Return:	0 if found, -ENOSPC if not
 */
static int ctxr_exec_window_slot(const struct ctxr_exec_window *win,
				 uint64_t size, uint64_t *slot)
{
	uint64_t addr;

	for (addr = ALIGN(win->base, SZ_2M);
	     addr + size <= win->base + win->size; addr += SZ_2M) {
		if (lmb_get_free_size(addr) >= size) {
			*slot = addr;
			return 0;
		}
	}

	return -ENOSPC;
}

int booti_setup(ulong image, ulong *relocated_addr, ulong *size,
		bool force_reloc)
{
	struct ctxr_exec_window win;
	struct Image_header *ih;
	uint64_t dst;
	uint64_t image_size, text_offset;

	*relocated_addr = image;

	ih = (struct Image_header *)map_sysmem(image, 0);

	if (ih->magic != le32_to_cpu(LINUX_ARM64_IMAGE_MAGIC)) {
		puts("Bad Linux ARM64 Image magic!\n");
		return 1;
	}

	/*
	 * Prior to Linux commit a2c1d73b94ed, the text_offset field
	 * is of unknown endianness.  In these cases, the image_size
	 * field is zero, and we can assume a fixed value of 0x80000.
	 */
	if (ih->image_size == 0) {
		puts("Image lacks image_size field, assuming 16MiB\n");
		image_size = 16 << 20;
		text_offset = 0x80000;
	} else {
		image_size = le64_to_cpu(ih->image_size);
		text_offset = le64_to_cpu(ih->text_offset);
	}

	*size = image_size;

	/*
	 * If bit 3 of the flags field is set, the 2MB aligned base of the
	 * kernel image can be anywhere in physical memory, so respect
	 * images->ep.  Otherwise, relocate the image to the base of RAM
	 * since memory below it is not accessible via the linear mapping.
	 */
	if (!force_reloc && (le64_to_cpu(ih->flags) & BIT(3)))
		dst = image - text_offset;
	else
		dst = gd->bd->bi_dram[0].start;

	/* A kernel confined to a window panics if its image is outside it */
	if (ctxr_exec_window_get(&win) &&
	    ctxr_exec_window_slot(&win, text_offset + image_size, &dst)) {
		printf("%sCTXR exec window [%#llx, %#llx): no free 2 MiB aligned range fits the %#llx byte Linux image\n",
		       win.enforce ? "ERROR: " : "", win.base,
		       win.base + win.size, image_size);
		if (win.enforce) {
			unmap_sysmem(ih);
			return 1;
		}
	}

	*relocated_addr = ALIGN(dst, SZ_2M) + text_offset;

	unmap_sysmem(ih);

	return 0;
}
