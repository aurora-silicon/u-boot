// SPDX-License-Identifier: GPL-2.0+
/* ANS queues and DMA ownership through Aura's native SPTM interface. */
#include <cpu_func.h>
#include <apple_apif_nvme.h>
#include <asm/global_data.h>

DECLARE_GLOBAL_DATA_PTR;
#include <dm.h>
#include <dm/device_compat.h>
#include <dm/device-internal.h>
#include <dm/uclass.h>
#include <dm/ofnode.h>
#include <malloc.h>
#include <mailbox.h>
#include <memalign.h>
#include <asm/arch/apif.h>
#include <asm/arch/rtkit.h>
#include <linux/ioport.h>
#include <linux/iopoll.h>
#include <linux/sizes.h>
#include "nvme.h"

#define ANS_ASQ_DB 0x2490c
#define ANS_IOSQ_DB 0x24910
#define ANS_QUEUE_DEPTH 64
#define ANS_PAGE_LIST_ENTRIES (SZ_4K / sizeof(u64))

struct ans_apif_tcb {
	u8 opcode, flags, slot, reserved0;
	__le16 length;
	u8 reserved1[18];
	__le64 prp1, prp2;
	u8 reserved2[88];
};
static_assert(sizeof(struct ans_apif_tcb) == 128);

struct ans_apif_pending {
	bool registered;
	unsigned int page_count;
	void *bounce_target;
	size_t bounce_bytes;
	bool from_device;
};

struct apple_nvme_apif {
	struct nvme_dev ndev;
	u64 queue[4];
	void *scratch;
	void *bounce;
	u64 scratch_pa;
	u32 firmware_entries;
	bool grants[5];
	bool failed;
	struct ans_apif_pending pending[NVME_Q_NUM];
	struct mbox_chan chan;
	struct apple_rtkit *rtkit;
	bool rtkit_release_failed;
};

/* A partial native setup must not be retried with fresh DMA allocations. */
static bool ans_probe_failed;

static struct apple_nvme_apif *ans_priv(struct nvme_queue *q)
{
	return container_of(q->dev, struct apple_nvme_apif, ndev);
}

static int ans_call(u32 table, u32 endpoint, u64 a, u64 b, u64 c,
		    u64 d, u64 e, u64 *result)
{
	u64 args[6] = { a, b, c, d, e };
	int ret = apple_apif_call(table, endpoint, args, result);

	if (ret)
		printf("ANS APIF %u:%u failed: %d\n", table, endpoint, ret);
	return ret;
}

static int ans_identity(u64 address, size_t size)
{
	u64 physical;
	int ret = apple_apif_translate(address, size, &physical);

	if (ret)
		return ret;
	return physical == address ? 0 : -ERANGE;
}

static int ans_sart_region(u64 address, size_t size, bool map)
{
	u64 result;
	int ret;

	ret = ans_identity(address, size);
	if (ret)
		return ret;
	ret = ans_call(5, map ? 1 : 2, address, size, map, map, 0, &result);
	return ret ?: (!map && result ? -EIO : 0);
}

static int ans_sart(u64 address, bool map)
{
	return ans_sart_region(address, SZ_16K, map);
}

static int ans_rtkit_alloc(void *cookie, struct apple_rtkit_buffer *buf)
{
	size_t size = ALIGN(buf->size, SZ_16K);
	int ret;

	if (buf->dva || !size || size > SZ_1M)
		return -EINVAL;
	buf->buffer = memalign(SZ_16K, size);
	if (!buf->buffer)
		return -ENOMEM;
	memset(buf->buffer, 0, size);
	flush_dcache_range((ulong)buf->buffer, (ulong)buf->buffer + size);
	ret = ans_sart_region((ulong)buf->buffer, size, true);
	if (ret)
		return ret;
	buf->dva = (ulong)buf->buffer;
	return 0;
}

static void ans_rtkit_free(void *cookie, struct apple_rtkit_buffer *buf)
{
	struct apple_nvme_apif *priv = cookie;

	if (ans_sart_region(buf->dva, ALIGN(buf->size, SZ_16K), false)) {
		priv->rtkit_release_failed = true;
		return;
	}
	free(buf->buffer);
	buf->buffer = NULL;
}

static int ans_alloc_queue(struct nvme_queue *q)
{
	struct apple_nvme_apif *priv = ans_priv(q);

	if (q->qid >= NVME_Q_NUM || q->q_depth > ANS_QUEUE_DEPTH)
		return -EINVAL;
	q->sq_cmds = (void *)(uintptr_t)priv->queue[q->qid * 2];
	q->cqes = (void *)(uintptr_t)priv->queue[q->qid * 2 + 1];
	q->external_buffers = true;
	memset(q->sq_cmds, 0, SZ_16K);
	memset((void *)q->cqes, 0, SZ_16K);
	flush_dcache_range((ulong)q->sq_cmds, (ulong)q->sq_cmds + SZ_16K);
	flush_dcache_range((ulong)q->cqes, (ulong)q->cqes + SZ_16K);
	return 0;
}

static int ans_configure_admin(struct nvme_queue *q)
{
	struct apple_nvme_apif *priv = ans_priv(q);
	int ret;

	ret = ans_call(6, 3, priv->firmware_entries, 2, 0, 0, 0, NULL);
	if (!ret)
		ret = ans_call(6, 0, 0, 0, 0, 0, 0, NULL);
	if (!ret)
		ret = ans_call(6, 4, priv->queue[0], q->q_depth - 1,
			       priv->queue[1], q->q_depth - 1, 0, NULL);
	if (!ret)
		ret = ans_call(6, 5, ANS_QUEUE_DEPTH - 1, ANS_QUEUE_DEPTH - 1,
			       0, 0, 0, NULL);
	if (!ret)
		ret = ans_call(6, 7, priv->queue[3], 0, 0, 0, 0, NULL);
	if (!ret)
		ret = ans_call(6, 6, priv->queue[2], 0, 0, 0, 0, NULL);
	if (!ret) {
		/* Match the native driver's two-step CC configuration: program
		 * queue entry sizes while disabled, then let the core set EN.
		 */
		writel(q->dev->ctrl_config, &q->dev->bar->cc);
		readl(&q->dev->bar->cc);
	}
	return ret;
}

static int ans_prepare_cmd(struct nvme_queue *q, struct nvme_command *cmd,
			   size_t payload_len)
{
	struct apple_nvme_apif *priv = ans_priv(q);
	struct ans_apif_pending *pending = &priv->pending[q->qid];
	u64 *list = priv->scratch;
	struct ans_apif_tcb *tcb = priv->scratch + SZ_4K;
	u64 first = le64_to_cpu(cmd->common.prp1);
	u64 second = le64_to_cpu(cmd->common.prp2);
	u64 *chain = (void *)(uintptr_t)second;
	unsigned int count, index = 0;
	int ret;

	if (priv->failed)
		return -EIO;
	for (unsigned int i = 0; i < NVME_Q_NUM; i++)
		if (priv->pending[i].registered)
			return -EBUSY;
	memset(pending, 0, sizeof(*pending));
	count = payload_len ? DIV_ROUND_UP((first & (SZ_4K - 1)) +
					  payload_len, SZ_4K) : !!first;
	/* Native ANS cannot encode a partial, single-page transfer. Only
	 * that case needs an aligned bounce buffer; larger PRP chains retain
	 * their normal direct DMA and transfer size.
	 */
	if (count == 1 && !IS_ALIGNED(first, SZ_4K)) {
		if (!payload_len || payload_len > SZ_4K)
			return -EINVAL;
		if (!priv->bounce) {
			priv->bounce = memalign(SZ_16K, SZ_16K);
			if (!priv->bounce)
				return -ENOMEM;
		}
		pending->bounce_target = (void *)(uintptr_t)first;
		pending->bounce_bytes = payload_len;
		pending->from_device = !(cmd->common.opcode & 1);
		if (!pending->from_device)
			memcpy(priv->bounce, pending->bounce_target, payload_len);
		flush_dcache_range((ulong)priv->bounce, (ulong)priv->bounce + SZ_16K);
		first = (ulong)priv->bounce;
		second = 0;
		cmd->common.prp1 = cpu_to_le64(first);
		cmd->common.prp2 = 0;
	}
	if (count > ANS_PAGE_LIST_ENTRIES)
		return -E2BIG;
	if (count)
		list[0] = first;
	if (count == 2)
		list[1] = second;
	for (unsigned int i = 1; count > 2 && i < count; i++) {
		if (index == ANS_PAGE_LIST_ENTRIES - 1 && i < count - 1) {
			chain = (void *)(uintptr_t)le64_to_cpu(chain[index]);
			index = 0;
		}
		list[i] = le64_to_cpu(chain[index++]);
	}
	for (unsigned int i = 0; i < count; i++) {
		if (i && !IS_ALIGNED(list[i], SZ_4K))
			return -EINVAL;
		ret = ans_identity(list[i] & ~(SZ_4K - 1), SZ_4K);
		if (ret)
			return ret;
	}
	memset(tcb, 0, sizeof(*tcb));
	tcb->slot = q->sq_tail;
	tcb->flags = first ? (cmd->common.opcode & 1 ? BIT(1) : BIT(0)) : 0;
	tcb->length = cpu_to_le16(count ? count - ((first & (SZ_4K - 1)) ? 2 : 1) : 0);
	tcb->prp1 = cpu_to_le64(first);
	tcb->prp2 = cpu_to_le64(second);
	cmd->common.command_id = cpu_to_le16(q->sq_tail);
	flush_dcache_range((ulong)priv->scratch, (ulong)priv->scratch + SZ_16K);
	pending->page_count = count;
	return 0;
}

static int ans_register_cmd(struct nvme_queue *q, struct nvme_command *cmd)
{
	struct apple_nvme_apif *priv = ans_priv(q);
	struct ans_apif_pending *pending = &priv->pending[q->qid];
	int ret;

	pending->registered = true;
	ret = ans_call(6, 1, q->qid, q->sq_tail, priv->scratch_pa + SZ_4K,
		       priv->scratch_pa, pending->page_count, NULL);
	if (ret)
		priv->failed = true;
	return ret;
}

static void ans_submit_cmd(struct nvme_queue *q, struct nvme_command *cmd)
{
	writel(q->sq_tail, (void *)q->dev->bar +
	       (q->qid == NVME_ADMIN_Q ? ANS_ASQ_DB : ANS_IOSQ_DB));
}

static int ans_complete_cmd(struct nvme_queue *q, struct nvme_command *cmd)
{
	struct apple_nvme_apif *priv = ans_priv(q);
	struct ans_apif_pending *pending = &priv->pending[q->qid];
	u64 result;
	int ret;

	if (!pending->registered)
		return -EINVAL;
	ret = ans_call(6, 2, q->qid, q->sq_tail, 0, 0, 0, &result);
	if (ret || result != 1) {
		priv->failed = true;
		return ret ?: -EIO;
	}
	pending->registered = false;
	if (pending->bounce_target && pending->from_device) {
		ulong start = (ulong)pending->bounce_target & ~(ARCH_DMA_MINALIGN - 1);
		ulong end = ALIGN((ulong)pending->bounce_target + pending->bounce_bytes,
				  ARCH_DMA_MINALIGN);

		invalidate_dcache_range((ulong)priv->bounce, (ulong)priv->bounce + SZ_16K);
		memcpy(pending->bounce_target, priv->bounce, pending->bounce_bytes);
		/* Callers may invalidate the original DMA buffer after return. */
		flush_dcache_range(start, end);
	}
	memset(pending, 0, sizeof(*pending));
	if (++q->sq_tail == q->q_depth)
		q->sq_tail = 0;
	return 0;
}

static int ans_read_queues(struct udevice *dev, struct apple_nvme_apif *priv)
{
	int ret = apple_apif_queue_pages(gd->fdt_blob, dev_of_offset(dev), priv->queue);

	if (ret != 1)
		return ret < 0 ? ret : -EINVAL;
	priv->firmware_entries = dev_read_u32_default(dev, "apple,nvme-queue-entries", 253);
	if (priv->firmware_entries < ANS_QUEUE_DEPTH || priv->firmware_entries > 0xfff)
		return -EINVAL;
	for (unsigned int i = 0; i < ARRAY_SIZE(priv->queue); i++) {
		ret = ans_identity(priv->queue[i], SZ_16K);
		if (ret)
			return ret;
	}
	return 0;
}

static int ans_probe(struct udevice *dev)
{
	struct apple_nvme_apif *priv = dev_get_priv(dev);
	int ret;

	if (ans_probe_failed)
		return -EIO;
	if (!apple_apif_present())
		return -ENODEV;
	priv->ndev.bar = dev_read_addr_name_ptr(dev, "nvme");
	if (!priv->ndev.bar)
		return -EINVAL;
	ret = ans_read_queues(dev, priv);
	if (ret)
		return ret;
	ans_probe_failed = true;
	priv->scratch = memalign(SZ_16K, SZ_16K);
	if (!priv->scratch)
		return -ENOMEM;
	priv->scratch_pa = (ulong)priv->scratch;
	ret = ans_identity(priv->scratch_pa, SZ_16K);
	if (ret)
		return ret;
	ret = ans_call(5, 0, 1, 0, 0, 0, 0, NULL);
	for (unsigned int i = 0; !ret && i < ARRAY_SIZE(priv->grants); i++) {
		ret = ans_sart(i < 4 ? priv->queue[i] : priv->scratch_pa, true);
		if (!ret)
			priv->grants[i] = true;
	}
	if (ret)
		return ret;
	ret = mbox_get_by_index(dev, 0, &priv->chan);
	if (ret)
		return ret;
	priv->rtkit = apple_rtkit_init(&priv->chan, priv,
				       ans_rtkit_alloc, ans_rtkit_free);
	if (!priv->rtkit)
		return -ENOMEM;
	ret = apple_rtkit_boot(priv->rtkit);
	if (!ret)
		ret = apple_rtkit_set_ap_power(priv->rtkit, APPLE_RTKIT_PWR_STATE_ON);
	if (ret) {
		dev_err(dev, "RTKit wake failed: %d\n", ret);
		return ret;
	}
	printf("%s: native SPTM RTKit wake complete\n", dev->name);
	priv->ndev.max_q_depth = ANS_QUEUE_DEPTH;
	/* Preserve the inherited disabled configuration until native reset. */
	priv->ndev.ctrl_config = readl(&priv->ndev.bar->cc);
	strcpy(priv->ndev.vendor, "Apple");
	ret = nvme_init(dev);
	if (ret)
		priv->failed = true;
	else {
		ans_probe_failed = false;
		printf("%s: native SPTM ANS queues ready\n", dev->name);
	}
	return ret;
}

static int ans_remove(struct udevice *dev)
{
	struct apple_nvme_apif *priv = dev_get_priv(dev);
	ulong start;
	int ret;

	if (priv->failed)
		return -EIO;
	for (unsigned int i = 0; i < NVME_Q_NUM; i++)
		if (priv->pending[i].registered)
			return -EBUSY;
	/* Native queue-address setters require valid pinned pages, including
	 * at shutdown. Match Linux's post-M4 fence: clear EN, wait for RDY=0,
	 * then quiesce RTKit and release DMA grants. Never program address 0.
	 */
	writel(priv->ndev.ctrl_config & ~NVME_CC_ENABLE, &priv->ndev.bar->cc);
	start = get_timer(0);
	while (readl(&priv->ndev.bar->csts) & NVME_CSTS_RDY) {
		ret = apple_rtkit_poll(priv->rtkit, 0);
		if (ret && ret != -ETIMEDOUT && ret != -ENODATA)
			return ret;
		if (get_timer(start) >= 5000)
			return -ETIMEDOUT;
	}
	ret = apple_rtkit_shutdown(priv->rtkit, APPLE_RTKIT_PWR_STATE_QUIESCED);
	if (ret)
		return ret;
	apple_rtkit_free(priv->rtkit);
	priv->rtkit = NULL;
	if (priv->rtkit_release_failed)
		return -EIO;
	for (unsigned int i = 0; i < ARRAY_SIZE(priv->grants); i++) {
		if (!priv->grants[i])
			continue;
		ret = ans_sart(i < 4 ? priv->queue[i] : priv->scratch_pa, false);
		if (ret)
			return ret;
		priv->grants[i] = false;
	}
	printf("%s: native SPTM ANS quiesced for Linux\n", dev->name);
	return 0;
}

/* Validate the retained queue contract in the outgoing DT. The EFI stub
 * copies this DT before ExitBootServices, so do not defer a property update
 * until after that copy. The final board guard makes quiescence a condition
 * of successful handoff, even if GRUB performs more disk I/O after fixup. */
int apple_nvme_apif_handoff_fdt(void *blob)
{
	struct uclass *uc;
	struct udevice *dev;
	int ret = uclass_get(UCLASS_NVME, &uc);

	if (ret || ans_probe_failed)
		return ret ?: -EIO;
	uclass_foreach_dev(dev, uc) {
		struct apple_nvme_apif *priv;
		u64 pages[4];
		int node;

		if (!device_active(dev) || strcmp(dev->driver->name, "apple_nvme_apif"))
			continue;
		priv = dev_get_priv(dev);
		if (priv->failed || priv->ndev.queue_count != NVME_Q_NUM)
			return -EIO;
		node = fdt_node_offset_by_compatible(blob, -1, APPLE_APIF_NVME_COMPAT);
		if (node < 0 || apple_apif_queue_pages(blob, node, pages) != 1 ||
		    memcmp(pages, priv->queue, sizeof(pages)))
			return -EINVAL;
	}
	return 0;
}

/* Generic DM teardown discards remove errors. The M5 board's OS-boundary
 * hook must not hand Linux a live/uncertain DMA owner as quiesced. */
void apple_nvme_apif_quiesce(void)
{
	struct uclass *uc;
	struct udevice *dev;
	int ret;

	if (ans_probe_failed)
		panic("ANS APIF: incomplete probe prevents OS handoff\n");
	ret = uclass_get(UCLASS_NVME, &uc);
	if (ret)
		panic("ANS APIF: cannot inspect OS handoff ownership\n");
	uclass_foreach_dev(dev, uc) {
		if (!device_active(dev) || strcmp(dev->driver->name, "apple_nvme_apif"))
			continue;
		ret = device_remove(dev, DM_REMOVE_NORMAL);
		if (ret)
			panic("ANS APIF: quiesce failed (%d); OS handoff stopped\n", ret);
	}

}

static int ans_poll(struct nvme_dev *dev)
{
	struct apple_nvme_apif *priv = container_of(dev, struct apple_nvme_apif, ndev);
	int ret = apple_rtkit_poll(priv->rtkit, 0);

	return ret == -ENODATA || ret == -ETIMEDOUT ? 0 : ret;
}

static const struct nvme_ops ans_ops = {
	.poll = ans_poll,
	.alloc_queue = ans_alloc_queue,
	.configure_admin_queue = ans_configure_admin,
	.prepare_cmd = ans_prepare_cmd,
	.register_cmd = ans_register_cmd,
	.submit_cmd = ans_submit_cmd,
	.complete_cmd = ans_complete_cmd,
};
static const struct udevice_id ans_ids[] = {
	{ .compatible = "apple,nvme-apif" },
	{}
};
U_BOOT_DRIVER(apple_nvme_apif) = {
	.name = "apple_nvme_apif",
	.id = UCLASS_NVME,
	.of_match = ans_ids,
	.priv_auto = sizeof(struct apple_nvme_apif),
	.probe = ans_probe,
	.remove = ans_remove,
	.ops = &ans_ops,
	.flags = DM_FLAG_OS_PREPARE,
};
