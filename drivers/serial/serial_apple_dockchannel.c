// SPDX-License-Identifier: GPL-2.0+ OR MIT
/*
 * Apple DockChannel UART
 *
 * Copyright 2026 Aurora Silicon
 */

#include <dm.h>
#include <errno.h>
#include <serial.h>
#include <asm/io.h>

#define APPLE_DC_CHANNEL_STRIDE	0x10000

#define APPLE_DC_DATA_OFFSET	0x4000
#define APPLE_DC_DATA_TX8	0x04
#define APPLE_DC_DATA_TX_FREE	0x14
#define APPLE_DC_DATA_RX8	0x1c
#define APPLE_DC_DATA_RX_COUNT	0x2c

struct apple_dc_serial_plat {
	void __iomem *base;
};

static int apple_dc_serial_putc(struct udevice *dev, const char ch)
{
	struct apple_dc_serial_plat *plat = dev_get_plat(dev);

	/* A disconnected Type-C serial peer must never stall the boot path. */
	if (!readl(plat->base + APPLE_DC_DATA_TX_FREE))
		return 0;

	writel(ch, plat->base + APPLE_DC_DATA_TX8);
	return 0;
}

static int apple_dc_serial_getc(struct udevice *dev)
{
	struct apple_dc_serial_plat *plat = dev_get_plat(dev);

	if (!readl(plat->base + APPLE_DC_DATA_RX_COUNT))
		return -EAGAIN;

	return readl(plat->base + APPLE_DC_DATA_RX8) >> 8;
}

static int apple_dc_serial_pending(struct udevice *dev, bool input)
{
	struct apple_dc_serial_plat *plat = dev_get_plat(dev);

	if (input)
		return readl(plat->base + APPLE_DC_DATA_RX_COUNT);

	/* Writes are best-effort, so there is no software queue to drain. */
	return 0;
}

static int apple_dc_serial_setbrg(struct udevice *dev, int baudrate)
{
	/* DockChannel is packet-clocked; the host baud setting is immaterial. */
	return 0;
}

static int apple_dc_serial_of_to_plat(struct udevice *dev)
{
	struct apple_dc_serial_plat *plat = dev_get_plat(dev);
	fdt_addr_t addr;
	u32 channel;

	/*
	 * Linux-style DTs describe the channel's data window explicitly.  The
	 * compact m1n1 smoke DT instead supplies the shared config window and an
	 * apple,channel selector.  Normalize both forms to the data window so the
	 * handoff can stay on the firmware-selected DockChannel.
	 */
	addr = dev_read_addr_name(dev, "data");
	if (addr != FDT_ADDR_T_NONE) {
		channel = dev_read_u32_default(dev, "apple,channel", 0);
	} else {
		addr = dev_read_addr(dev);
		if (addr == FDT_ADDR_T_NONE)
			return -EINVAL;
		channel = dev_read_u32_default(dev, "apple,channel", 1);
		addr += APPLE_DC_DATA_OFFSET;
	}
	if (channel > 15)
		return -EINVAL;

	plat->base = (void __iomem *)(uintptr_t)
		(addr + channel * APPLE_DC_CHANNEL_STRIDE);
	return 0;
}

static const struct dm_serial_ops apple_dc_serial_ops = {
	.putc = apple_dc_serial_putc,
	.getc = apple_dc_serial_getc,
	.pending = apple_dc_serial_pending,
	.setbrg = apple_dc_serial_setbrg,
};

static const struct udevice_id apple_dc_serial_ids[] = {
	{ .compatible = "apple,dockchannel-uart" },
	{ }
};

U_BOOT_DRIVER(serial_apple_dockchannel) = {
	.name = "serial_apple_dockchannel",
	.id = UCLASS_SERIAL,
	.of_match = apple_dc_serial_ids,
	.of_to_plat = apple_dc_serial_of_to_plat,
	.plat_auto = sizeof(struct apple_dc_serial_plat),
	.ops = &apple_dc_serial_ops,
};
