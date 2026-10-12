/* SPDX-License-Identifier: GPL-2.0+ */

#ifndef __ASM_ARCH_APPLE_DART_H
#define __ASM_ARCH_APPLE_DART_H

struct udevice;

/*
 * Keep a bypass-mode DART in bypass when it is removed for the OS handoff,
 * so a coprocessor left running can still reach its buffers.  Fails unless
 * the DART is active and in bypass.
 */
int apple_dart_retain_bypass(struct udevice *dev);
void apple_dart_release_bypass(struct udevice *dev);

#endif
