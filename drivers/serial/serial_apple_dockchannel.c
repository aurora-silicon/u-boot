// SPDX-License-Identifier: GPL-2.0+ OR MIT
/* Apple DockChannel UART, Copyright 2026 Aurora Silicon */

#include <dm.h>
#include <errno.h>
#include <serial.h>
#include <asm/io.h>

#define DC_TX8		0x04
#define DC_TX_FREE	0x14
#define DC_RX8		0x1c
#define DC_RX_COUNT	0x2c

struct apple_dc_plat {
	void __iomem *data;
};

static int apple_dc_putc(struct udevice *dev, const char ch)
{
	struct apple_dc_plat *plat = dev_get_plat(dev);

	if (!readl(plat->data + DC_TX_FREE))
		return -EAGAIN;
	writel(ch, plat->data + DC_TX8);
	return 0;
}

static int apple_dc_getc(struct udevice *dev)
{
	struct apple_dc_plat *plat = dev_get_plat(dev);

	if (!readl(plat->data + DC_RX_COUNT))
		return -EAGAIN;
	return readl(plat->data + DC_RX8) >> 8;
}

static int apple_dc_pending(struct udevice *dev, bool input)
{
	struct apple_dc_plat *plat = dev_get_plat(dev);

	return input ? readl(plat->data + DC_RX_COUNT) : 0;
}

static int apple_dc_setbrg(struct udevice *dev, int baudrate)
{
	/* DockChannel is packet-clocked; there is no baud divider. */
	return 0;
}

static int apple_dc_of_to_plat(struct udevice *dev)
{
	struct apple_dc_plat *plat = dev_get_plat(dev);
	fdt_addr_t addr = dev_read_addr_name(dev, "data");

	if (addr == FDT_ADDR_T_NONE)
		return -EINVAL;
	plat->data = (void __iomem *)(uintptr_t)addr;
	return 0;
}

static const struct dm_serial_ops apple_dc_ops = {
	.putc = apple_dc_putc,
	.getc = apple_dc_getc,
	.pending = apple_dc_pending,
	.setbrg = apple_dc_setbrg,
};

static const struct udevice_id apple_dc_ids[] = {
	{ .compatible = "apple,dockchannel-uart" },
	{ }
};

U_BOOT_DRIVER(serial_apple_dockchannel) = {
	.name = "serial_apple_dockchannel",
	.id = UCLASS_SERIAL,
	.of_match = apple_dc_ids,
	.of_to_plat = apple_dc_of_to_plat,
	.plat_auto = sizeof(struct apple_dc_plat),
	.ops = &apple_dc_ops,
};
