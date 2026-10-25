/* SPDX-License-Identifier: GPL-2.0-or-later OR MIT */
#ifndef _APPLE_J713_FDT_H
#define _APPLE_J713_FDT_H

#define J713_FDT_MAX_SOURCE (192 * 1024)
#define J713_FDT_EXTRA_PAD (16 * 1024)
#define J713_FDT_MIN_PAD (J713_FDT_MAX_SOURCE + J713_FDT_EXTRA_PAD)

#if !defined(J713_FDT_HOST_TEST) && defined(CONFIG_APPLE_J713_FDT)
#if CONFIG_SYS_FDT_PAD < J713_FDT_MIN_PAD
#error "J713 per-UKI FDT import requires CONFIG_SYS_FDT_PAD >= 208 KiB"
#endif
#endif

int apple_j713_import_fdt(void *blob, const void *source);
int apple_j713_complete_fdt(void *blob);

#endif
