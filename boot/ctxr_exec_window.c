// SPDX-License-Identifier: GPL-2.0+
/*
 * Read the physical window a Linux kernel confines its executable code to.
 *
 * The other end of this is ctxr_parse_dt() in the kernel's
 * arch/arm64/mm/ctxr_execmem.c. This accepts what that accepts, so U-Boot only
 * moves a kernel that will look for its image in the window.
 */

#include <ctxr_exec_window.h>
#include <dm/of.h>
#include <dm/ofnode.h>

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
