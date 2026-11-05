/* SPDX-License-Identifier: GPL-2.0+ */
/*
 * The physical window a Linux kernel confines its executable code to, as
 * advertised by /chosen/aurora,ctxr-exec-window.
 */

#ifndef __CTXR_EXEC_WINDOW_H
#define __CTXR_EXEC_WINDOW_H

#include <linux/types.h>

/**
 * struct ctxr_exec_window - physical window for a kernel's executable code
 *
 * @base:	first byte of the window
 * @size:	size of the window in bytes
 * @enforce:	the kernel panics if its image is outside the window
 */
struct ctxr_exec_window {
	u64 base;
	u64 size;
	bool enforce;
};

#if CONFIG_IS_ENABLED(CTXR_EXEC_WINDOW)
/**
 * ctxr_exec_window_get() - read the window a kernel image has to be placed in
 *
 * @win:	filled in with the window if there is one
 * Return:	true if the device tree advertises a physical window
 */
bool ctxr_exec_window_get(struct ctxr_exec_window *win);
#else
static inline bool ctxr_exec_window_get(struct ctxr_exec_window *win)
{
	return false;
}
#endif

#endif /* __CTXR_EXEC_WINDOW_H */
