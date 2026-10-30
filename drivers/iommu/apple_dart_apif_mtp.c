// SPDX-License-Identifier: GPL-2.0+ OR MIT
/*
 * MTP's pre-boot console uses DockChannel and the standard RTKit buffers.
 * The native DART calls mirror Linux's APIF backend. Firmware-visible
 * allocations and tables stay live across the OS handoff.
 */
#include <dm.h>
#include <dm/device_compat.h>
#include <iommu.h>
#include <malloc.h>
#include <cpu_func.h>
#include <asm/arch/apif.h>
#include <asm/arch/rtkit.h>
#include <asm/io.h>
#include <linux/sizes.h>

#define MTP_PAGE SZ_16K
#define MTP_BLOCK SZ_32M

struct apple_apif_mtp {
	u32 id, sid;
	bool connected;
	u64 next, end;
};

static int apple_apif_mtp_table(struct apple_apif_mtp *p, u64 dva, u32 level)
{
	u64 base = ALIGN_DOWN(dva, level == 1 ? BIT_ULL(36) : MTP_BLOCK);
	u64 owner = 0x4441525400000000ULL | ((u64)p->id << 16) |
		    ((u64)p->sid << 8) | level;
	u64 args[6] = { owner, base }, pa, result;
	int ret;

	ret = apple_apif_call(APPLE_APIF_SERVICE, 8, args, &pa);
	if (ret || pa)
		return ret;
	memset(args, 0, sizeof(args));
	ret = apple_apif_call(APPLE_APIF_SERVICE, 2, args, &pa);
	if (ret)
		return ret;
	args[0] = pa; args[1] = 11; args[2] = 24;
	args[3] = (((u64)level << 16 | p->sid << 8 | p->id) << 32) | 5;
	ret = apple_apif_call(0, 1, args, &result);
	if (ret || !result)
		return ret ?: -EIO;
	memset(args, 0, sizeof(args));
	args[0] = p->id; args[1] = p->sid; args[2] = dva;
	args[3] = level; args[4] = pa;
	ret = apple_apif_call(3, 0, args, &result);
	if (ret)
		return ret;
	memset(args, 0, sizeof(args));
	args[0] = pa; args[1] = owner; args[2] = base;
	return apple_apif_call(APPLE_APIF_SERVICE, 9, args, &result);
}

static int apple_apif_sram_connect(struct udevice *dev)
{
	struct apple_apif_mtp *p = dev_get_priv(dev->iommu);
	struct ofnode_phandle_args args;
	u32 phandle;
	ofnode helper;
	int ret;

	if (!device_is_compatible(dev, "apple,mtp")) {
		if (!device_is_compatible(dev, "apple,dockchannel-hid") ||
		    dev_read_u32(dev, "apple,helper-cpu", &phandle))
			return -EOPNOTSUPP;
		helper = ofnode_get_by_phandle(phandle);
		if (!apple_rtkit_helper_needs_live_handoff(helper))
			return -EOPNOTSUPP;
	} else if (!apple_rtkit_helper_needs_live_handoff(dev_ofnode(dev))) {
		return -EOPNOTSUPP;
	}
	ret = dev_read_phandle_with_args(dev, "iommus", "#iommu-cells", 0, 0, &args);
	if (ret || args.args_count != 1 || args.args[0] > 255)
		return -EINVAL;
	if (p->connected && p->sid != args.args[0])
		return -EINVAL;
	p->sid = args.args[0];
	p->connected = true;
	return 0;
}

static dma_addr_t apple_apif_sram_map(struct udevice *dev, void *addr, size_t size)
{
	struct apple_apif_mtp *p = dev_get_priv(dev);
	u64 *pages, pa, list_pa, result, dva = p->next, args[6] = { 0 };
	int ret;

	if (!p->connected || !size || size > MTP_BLOCK ||
	    ((ulong)addr | size) & (MTP_PAGE - 1) ||
	    dva > p->end || size - 1 > p->end - dva)
		return (dma_addr_t)-1;
	if (ALIGN_DOWN(dva, MTP_BLOCK) != ALIGN_DOWN(dva + size - 1, MTP_BLOCK))
		dva = ALIGN(dva, MTP_BLOCK);
	if (dva > p->end || size - 1 > p->end - dva)
		return (dma_addr_t)-1;
	/* Never reuse an address after an uncertain native-call completion. */
	p->next = dva + size;
	pages = memalign(MTP_PAGE, MTP_PAGE);
	if (!pages)
		return (dma_addr_t)-1;
	ret = apple_apif_translate((ulong)addr, size, &pa);
	if (ret)
		goto fail;
	for (size_t i = 0; i < size / MTP_PAGE; i++)
		pages[i] = pa + i * MTP_PAGE;
	flush_dcache_range((ulong)pages, (ulong)pages + MTP_PAGE);
	ret = apple_apif_translate((ulong)pages, MTP_PAGE, &list_pa);
	if (ret)
		goto fail;
	ret = apple_apif_mtp_table(p, dva, 1);
	if (!ret)
		ret = apple_apif_mtp_table(p, dva, 2);
	if (ret)
		goto fail;
	args[0] = p->id; args[1] = p->sid; args[2] = dva;
	args[3] = list_pa; args[4] = size; args[5] = 4; /* read/write, no execute */
	ret = apple_apif_call(3, 2, args, &result);
	if (ret)
		goto fail;
	memset(args, 0, sizeof(args));
	args[0] = p->id; args[1] = p->sid;
	ret = apple_apif_call(3, 8, args, &result);
	if (ret)
		goto fail;
	free(pages);
	return dva;
fail:
	dev_err(dev, "MTP DMA setup failed (%d); retaining uncertain resources\n", ret);
	return (dma_addr_t)-1;
}

static int apple_apif_sram_probe(struct udevice *dev)
{
	struct apple_apif_mtp *p = dev_get_priv(dev);
	u64 args[6] = { 0 }, result;
	u64 range[2];
	u32 id;
	int ret;

	if (!apple_apif_present())
		return -ENODEV;
	ret = dev_read_u32(dev, "apple,apif-dart-id", &id);
	if (ret || id > 255 || ofnode_read_u64_array(dev_ofnode(dev), "apple,dma-range", range, 2) ||
	    !range[1] || range[0] + range[1] < range[0] ||
	    (range[0] | range[1]) & (MTP_PAGE - 1))
		return -EINVAL;
	p->id = id; p->next = range[0]; p->end = range[0] + range[1] - 1;
	args[0] = id;
	/* Same native INIT and POWERUP sequence used by the Linux backend. */
	ret = apple_apif_call(3, 6, args, &result);
	if (!ret)
		ret = apple_apif_call(3, 5, args, &result);
	return ret;
}

static const struct iommu_ops apple_apif_sram_ops = {
	.connect = apple_apif_sram_connect,
	.map = apple_apif_sram_map,
};

static const struct udevice_id apple_apif_sram_ids[] = {
	{ .compatible = "apple,dart-apif" },
	{ }
};

U_BOOT_DRIVER(apple_dart_apif_mtp) = {
	.name = "apple_dart_apif_mtp",
	.id = UCLASS_IOMMU,
	.of_match = apple_apif_sram_ids,
	.probe = apple_apif_sram_probe,
	.priv_auto = sizeof(struct apple_apif_mtp),
	.ops = &apple_apif_sram_ops,
};
