/* SPDX-License-Identifier: GPL-2.0+ */
/*
 * The physical window a Linux kernel confines its executable code to, as
 * advertised by /chosen/aurora,ctxr-exec-window.
 */

#ifndef __CTXR_EXEC_WINDOW_H
#define __CTXR_EXEC_WINDOW_H

#include <linux/errno.h>
#include <linux/types.h>

/**
 * struct ctxr_exec_window - physical window for a kernel's executable code
 *
 * @base:	first byte of the window
 * @size:	size of the window in bytes
 */
struct ctxr_exec_window {
	u64 base;
	u64 size;
};

#if CONFIG_IS_ENABLED(CTXR_EXEC_WINDOW)
/**
 * ctxr_exec_window_get() - read the window a kernel image has to be placed in
 *
 * A window the kernel accepts has a non-zero base and size, both page aligned,
 * and doesn't wrap. Anything else doesn't count as one.
 *
 * @win:	filled in with the window if there is one
 * Return:	true if the device tree advertises a usable window
 */
bool ctxr_exec_window_get(struct ctxr_exec_window *win);

/**
 * ctxr_exec_warn_outside() - warn that Linux will start outside the window
 *
 * Such a kernel keeps its secondary CPUs offline and boots on the boot CPU, so
 * this warns rather than refusing it.
 *
 * @win:	the window
 * @why:	what kept the kernel out of it
 */
void ctxr_exec_warn_outside(const struct ctxr_exec_window *win,
			    const char *why);

/**
 * ctxr_exec_relocate_uboot() - relocate U-Boot's image into the window
 *
 * Call this once all of U-Boot's memory is reserved, before setup_reloc(). Only
 * the image moves, which holds runtime services. The rest of U-Boot's memory is
 * free once the OS runs, so it stays where it would be without a window, and
 * nothing changes if the window can't take the image.
 *
 * Return:	0
 */
int ctxr_exec_relocate_uboot(void);

/**
 * ctxr_exec_uboot_region() - find U-Boot's image if it moved into the window
 *
 * The rest of U-Boot's memory doesn't cover the image then, so it needs
 * reserving as well.
 *
 * @base:	set to the start of the image if it moved
 * Return:	size of the image's memory, or 0 if it did not move
 */
phys_size_t ctxr_exec_uboot_region(phys_addr_t *base);

/**
 * ctxr_exec_place_booti() - reserve a place in the window for booti's image
 *
 * The image is moved there before booti reserves its memory, so this checks
 * that the place is free RAM. It first gives back the place an earlier booti or
 * bootm took, since that boot didn't start.
 *
 * @win:	window to search
 * @size:	bytes from the 2 MiB boundary to the end of the image
 * @slot:	set to the lowest free 2 MiB boundary that fits
 * Return:	0 if found, -ENOSPC if not
 */
int ctxr_exec_place_booti(const struct ctxr_exec_window *win, u64 size,
			  u64 *slot);

/**
 * ctxr_exec_booti_placed() - check whether booti's image is in a place it took
 *
 * @addr:	address the image was moved to
 * Return:	true if that is inside the place ctxr_exec_place_booti() took
 */
bool ctxr_exec_booti_placed(phys_addr_t addr);

/**
 * ctxr_exec_unplace_booti() - give back the place booti or bootm took
 *
 * Placing another kernel calls this: the boot that took the place didn't start.
 */
void ctxr_exec_unplace_booti(void);

/* Where the malloc() region ends */
#define ctxr_exec_malloc_end()	gd->ctxr_malloc_end
#else
static inline bool ctxr_exec_window_get(struct ctxr_exec_window *win)
{
	return false;
}

static inline void ctxr_exec_warn_outside(const struct ctxr_exec_window *win,
					  const char *why)
{
}

static inline int ctxr_exec_relocate_uboot(void)
{
	return 0;
}

static inline phys_size_t ctxr_exec_uboot_region(phys_addr_t *base)
{
	return 0;
}

static inline int ctxr_exec_place_booti(const struct ctxr_exec_window *win,
					u64 size, u64 *slot)
{
	return -ENOSPC;
}

static inline bool ctxr_exec_booti_placed(phys_addr_t addr)
{
	return false;
}

static inline void ctxr_exec_unplace_booti(void)
{
}

#define ctxr_exec_malloc_end()	gd->relocaddr
#endif

#endif /* __CTXR_EXEC_WINDOW_H */
