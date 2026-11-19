// SPDX-License-Identifier: GPL-2.0+
/*
 * (C) Copyright 2021 Mark Kettenis <kettenis@openbsd.org>
 */

#define APPLE_RTKIT_PWR_STATE_SLEEP	0x01
#define APPLE_RTKIT_PWR_STATE_QUIESCED	0x10
#define APPLE_RTKIT_PWR_STATE_ON	0x20
#define APPLE_RTKIT_PWR_STATE_INIT	0x220

struct apple_rtkit_buffer {
	void *buffer;
	u64 dva;
	size_t size;
	bool is_mapped;
	int endpoint;
};

typedef int (*apple_rtkit_shmem_setup)(void *cookie,
				       struct apple_rtkit_buffer *buf);
typedef void (*apple_rtkit_shmem_destroy)(void *cookie,
					  struct apple_rtkit_buffer *buf);

#include <dm/ofnode.h>

struct apple_rtkit;
struct mbox_chan;

struct apple_rtkit *apple_rtkit_init(struct mbox_chan *chan, void *cookie,
				     apple_rtkit_shmem_setup shmem_setup,
				     apple_rtkit_shmem_destroy shmem_destroy);
void apple_rtkit_free(struct apple_rtkit *rtk);
int apple_rtkit_get_buffers(struct apple_rtkit *rtk,
			   struct apple_rtkit_buffer *buffers, size_t count);
int apple_rtkit_boot(struct apple_rtkit *rtk);
int apple_rtkit_boot_with_sys_ep_mask(struct apple_rtkit *rtk, u32 system_eps);
int apple_rtkit_set_ap_power(struct apple_rtkit *rtk, int pwrstate);
int apple_rtkit_poll(struct apple_rtkit *rtk, ulong timeout);
int apple_rtkit_shutdown(struct apple_rtkit *rtk, int pwrstate);

int apple_rtkit_helper_poll(struct udevice *dev, ulong timeout);
/* Only publish a live session once the keyboard's startup is complete. */
void apple_rtkit_helper_input_ready(struct udevice *dev);
bool apple_rtkit_helper_input_prepared(struct udevice *dev);
bool apple_rtkit_helper_input_retained(struct udevice *dev);
void apple_rtkit_helper_input_failed(struct udevice *dev);

/* What U-Boot does with a helper; see rtkit_helper.c. */
enum apple_rtkit_helper_handoff {
	APPLE_RTKIT_HELPER_RESTART,	/* stopped at OS entry, the OS restarts it */
	APPLE_RTKIT_HELPER_LIVE,	/* handed to the OS running (mtp-live-v1) */
	APPLE_RTKIT_HELPER_OS,		/* never touched: the OS owns it */
};

enum apple_rtkit_helper_handoff apple_rtkit_helper_handoff(ofnode node);
/* A live handoff in the retained form (see rtkit_helper.c). */
bool apple_rtkit_helper_retained(ofnode node);
bool apple_rtkit_helper_allowed(ofnode node);
/* Describe a helper the OS must adopt while it runs (OS device tree fixup). */
int apple_rtkit_helper_fdt_fixup(void *blob);
