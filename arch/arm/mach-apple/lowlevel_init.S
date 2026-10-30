/* SPDX-License-Identifier: GPL-2.0+ */
/*
 * (C) Copyright 2021 Mark Kettenis <kettenis@openbsd.org>
 */

#include <asm/macro.h>
#include <asm/system.h>

.align 8
.global fw_dtb_pointer
fw_dtb_pointer:
	.quad	0

.global save_boot_params
save_boot_params:
	/* Stash DTB pointer passed by m1n1 */
	adr	x1, fw_dtb_pointer
	str	x0, [x1]

	/*
	 * m1n1 hands over with the MMU and both caches off, and U-Boot
	 * turns them on only in initr_caches(), after relocation. Until
	 * then every instruction is fetched from DRAM, which stretches
	 * board_init_f() to seconds on these cores. With the MMU off an
	 * enabled I-cache needs no page tables, so turn it on now, after
	 * dropping any lines the previous stage left behind.
	 */
	ic	iallu
	dsb	sy
	isb
	switch_el x1, 3f, 2f, 1f
3:	mrs	x1, sctlr_el3
	orr	x1, x1, #CR_I
	msr	sctlr_el3, x1
	b	0f
2:	mrs	x1, sctlr_el2
	orr	x1, x1, #CR_I
	msr	sctlr_el2, x1
	b	0f
1:	mrs	x1, sctlr_el1
	orr	x1, x1, #CR_I
	msr	sctlr_el1, x1
0:	isb

	b	save_boot_params_ret
