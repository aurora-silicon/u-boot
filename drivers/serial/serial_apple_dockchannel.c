// SPDX-License-Identifier: GPL-2.0+ OR MIT
/*
 * Apple DockChannel UART
 *
 * DockChannel is a pair of byte FIFOs between the application processor
 * and the USB-C port controller, which forwards them to a debug host.
 * Newer Apple SoCs (A18 Pro/T8140) have no Samsung-style UART exposed
 * and use it for the serial console instead.
 *
 * Copyright The Aurora Silicon Contributors
 *
 * The "apple,dockchannel-uart" binding and DockChannel FIFO layout follow Yureka
 * Lilian's Linux driver and the Asahi MTP keyboard driver (apple_mtp_kbd.c).
 */

#include <dm.h>
#include <errno.h>
#include <serial.h>
#include <asm/io.h>
#include <linux/delay.h>

#define DATA_TX8		0x04
#define DATA_TX_FREE		0x14
#define DATA_RX8		0x1c
#define DATA_RX_COUNT		0x2c

/*
 * With no debug host attached the TX FIFO fills up and never drains.  Wait
 * this long for space before declaring the link stalled; while stalled,
 * output is dropped instead of blocking the boot.
 */
#define TX_STALL_US		2000

struct apple_dockchannel_serial_priv {
	void __iomem *data;
	bool stalled;
};

static int apple_dockchannel_serial_putc(struct udevice *dev, const char ch)
{
	struct apple_dockchannel_serial_priv *priv = dev_get_priv(dev);
	unsigned int us;

	for (us = 0; !readl(priv->data + DATA_TX_FREE); us++) {
		if (priv->stalled || us >= TX_STALL_US) {
			priv->stalled = true;
			return 0;
		}
		udelay(1);
	}

	priv->stalled = false;
	writel(ch, priv->data + DATA_TX8);

	return 0;
}

static int apple_dockchannel_serial_getc(struct udevice *dev)
{
	struct apple_dockchannel_serial_priv *priv = dev_get_priv(dev);

	if (!readl(priv->data + DATA_RX_COUNT))
		return -EAGAIN;

	return (readl(priv->data + DATA_RX8) >> 8) & 0xff;
}

static int apple_dockchannel_serial_pending(struct udevice *dev, bool input)
{
	struct apple_dockchannel_serial_priv *priv = dev_get_priv(dev);

	if (input)
		return readl(priv->data + DATA_RX_COUNT) ? 1 : 0;

	/* The FIFO drains on its own; there is no transmit queue to wait for. */
	return 0;
}

static int apple_dockchannel_serial_setbrg(struct udevice *dev, int baudrate)
{
	/* The link is packetised by the port controller; there is no baud rate. */
	return 0;
}

static int apple_dockchannel_serial_probe(struct udevice *dev)
{
	struct apple_dockchannel_serial_priv *priv = dev_get_priv(dev);
	fdt_addr_t addr;

	addr = dev_read_addr_name(dev, "data");
	if (addr == FDT_ADDR_T_NONE)
		return -EINVAL;

	priv->data = (void __iomem *)addr;

	return 0;
}

static const struct dm_serial_ops apple_dockchannel_serial_ops = {
	.putc = apple_dockchannel_serial_putc,
	.getc = apple_dockchannel_serial_getc,
	.pending = apple_dockchannel_serial_pending,
	.setbrg = apple_dockchannel_serial_setbrg,
};

static const struct udevice_id apple_dockchannel_serial_ids[] = {
	{ .compatible = "apple,dockchannel-uart" },
	{ }
};

U_BOOT_DRIVER(serial_apple_dockchannel) = {
	.name = "serial_apple_dockchannel",
	.id = UCLASS_SERIAL,
	.of_match = apple_dockchannel_serial_ids,
	.probe = apple_dockchannel_serial_probe,
	.priv_auto = sizeof(struct apple_dockchannel_serial_priv),
	.ops = &apple_dockchannel_serial_ops,
	.flags = DM_FLAG_PRE_RELOC,
};
