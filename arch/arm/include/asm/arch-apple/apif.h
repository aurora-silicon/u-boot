/* SPDX-License-Identifier: GPL-2.0+ */
#ifndef __APPLE_APIF_H
#define __APPLE_APIF_H

#include <linux/types.h>

#define APPLE_APIF_SERVICE 0xffffffffU
#define APPLE_APIF_TRANSLATE 1

bool apple_apif_present(void);
int apple_apif_call(u32 table, u32 endpoint, const u64 args[6], u64 *result);
int apple_apif_translate(u64 address, u64 size, u64 *physical);

#endif
