/* SPDX-License-Identifier: GPL-2.0+ */
#ifndef __ASM_ARCH_APPLE_NVME_APPLE_H
#define __ASM_ARCH_APPLE_NVME_APPLE_H

/* Reserve, in the OS devicetree, RTKit buffers of an ANS session handed on live. */
#if IS_ENABLED(CONFIG_NVME_APPLE)
int apple_nvme_fdt_fixup(void *blob);
#else
static inline int apple_nvme_fdt_fixup(void *blob) { return 0; }
#endif

#endif
