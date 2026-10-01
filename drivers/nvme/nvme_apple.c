// SPDX-License-Identifier: GPL-2.0+
/*
 * (C) Copyright 2021 Mark Kettenis <kettenis@openbsd.org>
 */

#include <dm.h>
#include <mailbox.h>
#include <mapmem.h>
#include "nvme.h"
#include <reset.h>
#include <dm/device_compat.h>

#include <asm/io.h>
#include <asm/arch/rtkit.h>
#include <asm/arch/sart.h>
#include <linux/iopoll.h>
#include <linux/sizes.h>

/* ASC registers */
#define REG_CPU_CTRL		0x0044
#define  REG_CPU_CTRL_RUN	BIT(4)

/* Apple NVMe registers */
#define ANS_IOSQ_BASE		0x01200	/* post-M4 */
#define ANS_IOCQ_BASE		0x01208	/* post-M4 */
#define ANS_MAX_PEND_CMDS_CTRL	0x01210	/* IOQA on post-M4 */
#define  ANS_MAX_QUEUE_DEPTH	64
#define ANS_BOOT_STATUS		0x01300
#define  ANS_BOOT_STATUS_OK	0xde71ce55
#define ANS_MODESEL		0x01304
#define ANS_UNKNOWN_CTRL	0x24008
#define  ANS_PRP_NULL_CHECK	(1 << 11)
#define ANS_LINEAR_SQ_CTRL	0x24908
#define  ANS_LINEAR_SQ_CTRL_EN	(1 << 0)
#define ANS_ASQ_DB		0x2490c
#define ANS_IOSQ_DB		0x24910
#define ANS_NVMMU_NUM		0x28100
#define ANS_NVMMU_BASE_ASQ	0x28108
#define ANS_NVMMU_BASE_IOSQ	0x28110
#define ANS_NVMMU_TCB_INVAL	0x28118
#define ANS_NVMMU_TCB_STAT	0x28120

#define ANS_NVMMU_TCB_SIZE	0x4000
#define ANS_NVMMU_TCB_PITCH	0x80

/*
 * The Apple NVMe controller includes an IOMMU known as NVMMU.  The
 * NVMMU is programmed through an array of TCBs. These TCBs are paired
 * with the corresponding slot in the submission queues and need to be
 * configured with the command details before a command is allowed to
 * execute. This is necessary even for commands that don't do DMA.
 */
struct ans_nvmmu_tcb {
	u8 opcode;
	u8 flags;
	u8 slot;
	u8 pad0;
	u32 prpl_len;
	u8 pad1[16];
	u64 prp1;
	u64 prp2;
};

/* DMA direction, named from the device's point of view */
#define ANS_NVMMU_TCB_WRITE	BIT(0)
#define ANS_NVMMU_TCB_READ	BIT(1)

/*
 * On post-M4 controllers (A18 Pro/T8140 and M4/T8132 onwards) the
 * NVMMU has its own register window, the I/O queue addresses are also
 * programmed through dedicated registers, and the ANS coprocessor cannot
 * be reset or restarted once running: clearing CPU_CTRL.RUN is one-way
 * and asserting its PMGR reset raises an SError.  The boot loader that
 * started the firmware (m1n1) hands it over with its RTKit session live,
 * and every later stage adopts that session instead of booting it.
 */
struct apple_nvme_hw {
	bool post_m4;
};

static const struct apple_nvme_hw apple_nvme_t8103_hw = {
	.post_m4 = false,
};

/*
 * The post-M4 (T8132 and later) register layout and I/O queue setup follow
 * Yureka Lilian's T8132 support in m1n1 (commit 53f8ee9b54ba, "nvme: support
 * T8132").
 */
static const struct apple_nvme_hw apple_nvme_t8132_hw = {
	.post_m4 = true,
};

struct apple_nvme_priv {
	struct nvme_dev ndev;
	const struct apple_nvme_hw *hw;
	void *base;		/* NVMe registers */
	void *nvmmu;		/* NVMMU registers */
	void *asc;		/* ASC registers */
	struct reset_ctl_bulk resets; /* ASC reset */
	struct mbox_chan chan;
	struct apple_sart *sart;
	struct apple_rtkit *rtk;
	struct ans_nvmmu_tcb *tcbs[NVME_Q_NUM]; /* Submission queue TCBs */
	u32 __iomem *q_db[NVME_Q_NUM]; /* Submission queue doorbell */
};

static int apple_nvme_setup_queue(struct nvme_queue *nvmeq)
{
	struct apple_nvme_priv *priv =
		container_of(nvmeq->dev, struct apple_nvme_priv, ndev);
	struct nvme_dev *dev = nvmeq->dev;

	switch (nvmeq->qid) {
	case NVME_ADMIN_Q:
	case NVME_IO_Q:
		break;
	default:
		return -EINVAL;
	}

	priv->tcbs[nvmeq->qid] = (void *)memalign(4096, ANS_NVMMU_TCB_SIZE);
	if (!priv->tcbs[nvmeq->qid])
		return -ENOMEM;

	memset((void *)priv->tcbs[nvmeq->qid], 0, ANS_NVMMU_TCB_SIZE);

	switch (nvmeq->qid) {
	case NVME_ADMIN_Q:
		priv->q_db[nvmeq->qid] =
			((void __iomem *)dev->bar) + ANS_ASQ_DB;
		nvme_writeq((ulong)priv->tcbs[nvmeq->qid],
			    priv->nvmmu + ANS_NVMMU_BASE_ASQ);
		break;
	case NVME_IO_Q:
		priv->q_db[nvmeq->qid] =
			((void __iomem *)dev->bar) + ANS_IOSQ_DB;
		nvme_writeq((ulong)priv->tcbs[nvmeq->qid],
			    priv->nvmmu + ANS_NVMMU_BASE_IOSQ);
		break;
	}

	return 0;
}

static void apple_nvme_submit_cmd(struct nvme_queue *nvmeq,
				  struct nvme_command *cmd)
{
	struct apple_nvme_priv *priv =
		container_of(nvmeq->dev, struct apple_nvme_priv, ndev);
	struct ans_nvmmu_tcb *tcb;
	u16 tail = nvmeq->sq_tail;

	tcb = ((void *)priv->tcbs[nvmeq->qid]) + tail * ANS_NVMMU_TCB_PITCH;
	memset(tcb, 0, sizeof(*tcb));
	if (priv->hw->post_m4) {
		/* Post-M4 firmware checks the DMA direction against the command. */
		tcb->opcode = 0;
		if (!cmd->common.prp1)
			tcb->flags = 0;
		else if (cmd->common.opcode & 1)
			tcb->flags = ANS_NVMMU_TCB_READ;
		else
			tcb->flags = ANS_NVMMU_TCB_WRITE;
	} else {
		tcb->opcode = cmd->common.opcode;
		tcb->flags = ANS_NVMMU_TCB_WRITE | ANS_NVMMU_TCB_READ;
	}
	tcb->slot = tail;
	tcb->prpl_len = cmd->rw.length;
	tcb->prp1 = cmd->common.prp1;
	tcb->prp2 = cmd->common.prp2;
	flush_dcache_range((ulong)tcb, (ulong)tcb + ANS_NVMMU_TCB_PITCH);

	writel(tail, priv->q_db[nvmeq->qid]);
}

static void apple_nvme_complete_cmd(struct nvme_queue *nvmeq,
				    struct nvme_command *cmd)
{
	struct apple_nvme_priv *priv =
		container_of(nvmeq->dev, struct apple_nvme_priv, ndev);
	struct ans_nvmmu_tcb *tcb;
	u16 tail = nvmeq->sq_tail;

	tcb = ((void *)priv->tcbs[nvmeq->qid]) + tail * ANS_NVMMU_TCB_PITCH;
	memset(tcb, 0, sizeof(*tcb));
	writel(tail, priv->nvmmu + ANS_NVMMU_TCB_INVAL);
	readl(priv->nvmmu + ANS_NVMMU_TCB_STAT);

	if (++tail == nvmeq->q_depth)
		tail = 0;
	nvmeq->sq_tail = tail;
}

static int nvme_shmem_setup(void *cookie, struct apple_rtkit_buffer *buf)
{
	struct apple_nvme_priv *priv = (struct apple_nvme_priv *)cookie;

	if (!buf || buf->dva || !buf->size)
		return -1;

	buf->buffer = memalign(SZ_16K, ALIGN(buf->size, SZ_16K));
	if (!buf->buffer)
		return -ENOMEM;

	if (!sart_add_allowed_region(priv->sart, buf->buffer, buf->size)) {
		free(buf->buffer);
		buf->buffer = NULL;
		buf->size = 0;
		return -1;
	}

	buf->dva = (u64)buf->buffer;

	return 0;
}

static void nvme_shmem_destroy(void *cookie, struct apple_rtkit_buffer *buf)
{
	struct apple_nvme_priv *priv = (struct apple_nvme_priv *)cookie;

	if (!buf)
		return;

	if (buf->buffer) {
		sart_remove_allowed_region(priv->sart, buf->buffer, buf->size);
		free(buf->buffer);
		buf->buffer = NULL;
		buf->size = 0;
		buf->dva = 0;
	}
}

static int apple_nvme_queue_created(struct nvme_queue *nvmeq)
{
	struct apple_nvme_priv *priv =
		container_of(nvmeq->dev, struct apple_nvme_priv, ndev);

	if (!priv->hw->post_m4 || nvmeq->qid != NVME_IO_Q)
		return 0;

	/*
	 * Post-M4 firmware also takes the I/O queue addresses through
	 * dedicated registers, after the queues have been created.  Without
	 * them it crashes on the first I/O command.
	 */
	nvme_writeq((ulong)nvmeq->cqes, priv->base + ANS_IOCQ_BASE);
	nvme_writeq((ulong)nvmeq->sq_cmds, priv->base + ANS_IOSQ_BASE);

	return 0;
}

static bool apple_nvme_ans_running(struct apple_nvme_priv *priv)
{
	return (readl(priv->asc + REG_CPU_CTRL) & REG_CPU_CTRL_RUN) &&
	       readl(priv->base + ANS_BOOT_STATUS) == ANS_BOOT_STATUS_OK;
}

/*
 * Post-M4: take over the ANS session the previous boot stage left running.
 * HELLO and the endpoint map are one-time messages of an RTKit session, so
 * the firmware is left alone entirely -- no RTKit, no SART, no reset -- and
 * only the NVMe controller and its queues are reprogrammed.  An enabled
 * controller is disabled first, exactly as Linux does when it adopts the
 * session.
 */
static int apple_nvme_adopt(struct udevice *dev)
{
	struct apple_nvme_priv *priv = dev_get_priv(dev);
	u32 ioqa, csts, cc, timeout;

	if (!apple_nvme_ans_running(priv)) {
		dev_err(dev, "ANS is not running (CPU_CTRL %#x, boot status %#x): post-M4 ANS can only be adopted from the boot loader\n",
			readl(priv->asc + REG_CPU_CTRL),
			readl(priv->base + ANS_BOOT_STATUS));
		return -ENODEV;
	}

	csts = readl(&priv->ndev.bar->csts);
	if (csts & NVME_CSTS_CFS) {
		dev_err(dev, "inherited controller is in a fatal state (CSTS %#x)\n",
			csts);
		return -EIO;
	}

	cc = readl(&priv->ndev.bar->cc);
	printf("%s: adopting the live ANS session (CC %#x CSTS %#x)\n",
	       dev->name, cc, csts);

	/*
	 * Like Linux, disable an enabled controller (the previous owner's
	 * queues may still be live) before touching any queue register.
	 */
	if (cc & NVME_CC_ENABLE) {
		/*
		 * Retire the previous owner's I/O queue addresses first, as our
		 * own handoff does: the disable must not meet a controller that
		 * still advertises live I/O queues.
		 */
		nvme_writeq(0, priv->base + ANS_IOSQ_BASE);
		nvme_writeq(0, priv->base + ANS_IOCQ_BASE);
		writel(cc & ~NVME_CC_ENABLE, &priv->ndev.bar->cc);
		timeout = NVME_CAP_TIMEOUT(nvme_readq(&priv->ndev.bar->cap)) * 500;
		if (readl_poll_sleep_timeout(&priv->ndev.bar->csts, csts,
					     !(csts & NVME_CSTS_RDY), 100,
					     (timeout ?: 500) * 1000)) {
			dev_err(dev, "inherited controller did not disable (CSTS %#x)\n",
				csts);
			return -ETIMEDOUT;
		}
	}

	writel(ANS_LINEAR_SQ_CTRL_EN, priv->base + ANS_LINEAR_SQ_CTRL);
	if (!(readl(priv->base + ANS_LINEAR_SQ_CTRL) & ANS_LINEAR_SQ_CTRL_EN)) {
		dev_err(dev, "failed to enable linear submission queues\n");
		return -EIO;
	}

	/*
	 * On post-M4 this register is IOQA: both fields hold the zero-based
	 * I/O queue size, like AQA.  Programming the entry count instead makes
	 * the firmware reject the CQ head once the queue wraps.
	 */
	ioqa = (ANS_MAX_QUEUE_DEPTH - 1) | ((ANS_MAX_QUEUE_DEPTH - 1) << 16);
	writel(ioqa, priv->base + ANS_MAX_PEND_CMDS_CTRL);
	if (readl(priv->base + ANS_MAX_PEND_CMDS_CTRL) != ioqa) {
		dev_err(dev, "failed to program the I/O queue aperture\n");
		return -EIO;
	}

	writel(ANS_MAX_QUEUE_DEPTH - 1, priv->nvmmu + ANS_NVMMU_NUM);
	priv->ndev.max_q_depth = ANS_MAX_QUEUE_DEPTH;

	return 0;
}

static int apple_nvme_boot(struct udevice *dev)
{
	struct apple_nvme_priv *priv = dev_get_priv(dev);
	ofnode of_sart;
	u32 ctrl, stat, phandle;
	int ret;

	ret = reset_get_bulk(dev, &priv->resets);
	if (ret < 0)
		return ret;

	ret = mbox_get_by_index(dev, 0, &priv->chan);
	if (ret < 0)
		return ret;

	ret = dev_read_u32(dev, "apple,sart", &phandle);
	if (ret < 0)
		return ret;

	of_sart = ofnode_get_by_phandle(phandle);
	priv->sart = sart_init(of_sart);
	if (!priv->sart)
		return -EINVAL;

	ctrl = readl(priv->asc + REG_CPU_CTRL);
	writel(ctrl | REG_CPU_CTRL_RUN, priv->asc + REG_CPU_CTRL);

	priv->rtk = apple_rtkit_init(&priv->chan, priv, nvme_shmem_setup, nvme_shmem_destroy);
	if (!priv->rtk)
		return -ENOMEM;

	ret = apple_rtkit_boot(priv->rtk);
	if (ret < 0) {
		printf("%s: NVMe apple_rtkit_boot returned: %d\n", __func__, ret);
		return ret;
	}

	ret = readl_poll_sleep_timeout(priv->base + ANS_BOOT_STATUS, stat,
				       (stat == ANS_BOOT_STATUS_OK), 100,
				       500000);
	if (ret < 0) {
		printf("%s: NVMe firmware didn't boot\n", __func__);
		return -ETIMEDOUT;
	}

	writel(ANS_LINEAR_SQ_CTRL_EN, priv->base + ANS_LINEAR_SQ_CTRL);
	writel(((ANS_MAX_QUEUE_DEPTH << 16) | ANS_MAX_QUEUE_DEPTH),
	       priv->base + ANS_MAX_PEND_CMDS_CTRL);

	writel(readl(priv->base + ANS_UNKNOWN_CTRL) & ~ANS_PRP_NULL_CHECK,
	       priv->base + ANS_UNKNOWN_CTRL);

	writel((ANS_NVMMU_TCB_SIZE / ANS_NVMMU_TCB_PITCH) - 1,
	       priv->nvmmu + ANS_NVMMU_NUM);
	writel(0, priv->base + ANS_MODESEL);

	return 0;
}

/*
 * A post-M4 session that could not be adopted will not become adoptable by
 * trying again, and each attempt can wait for controller timeouts.  Every
 * block device scan probes again, so remember the failure.
 */
static bool apple_nvme_adopt_failed;

static int apple_nvme_probe(struct udevice *dev)
{
	struct apple_nvme_priv *priv = dev_get_priv(dev);
	fdt_addr_t addr;
	int ret;

	if (apple_nvme_adopt_failed)
		return -ENODEV;

	priv->hw = (const struct apple_nvme_hw *)dev_get_driver_data(dev);

	if (priv->hw->post_m4) {
		priv->base = dev_read_addr_name_ptr(dev, "nvme");
		priv->asc = dev_read_addr_name_ptr(dev, "ans");
		priv->nvmmu = dev_read_addr_name_ptr(dev, "nvmmu");
		if (!priv->base || !priv->asc || !priv->nvmmu)
			return -EINVAL;
	} else {
		priv->base = dev_read_addr_ptr(dev);
		if (!priv->base)
			return -EINVAL;

		addr = dev_read_addr_index(dev, 1);
		if (addr == FDT_ADDR_T_NONE)
			return -EINVAL;
		priv->asc = map_sysmem(addr, 0);
		priv->nvmmu = priv->base;
	}
	priv->ndev.bar = priv->base;

	if (priv->hw->post_m4)
		ret = apple_nvme_adopt(dev);
	else
		ret = apple_nvme_boot(dev);
	if (ret < 0) {
		if (priv->hw->post_m4)
			apple_nvme_adopt_failed = true;
		return ret;
	}

	strcpy(priv->ndev.vendor, "Apple");

	ret = nvme_init(dev);
	if (ret && priv->hw->post_m4)
		apple_nvme_adopt_failed = true;

	return ret;
}

static int apple_nvme_remove(struct udevice *dev)
{
	struct apple_nvme_priv *priv = dev_get_priv(dev);
	u32 ctrl;

	if (priv->hw->post_m4) {
		/*
		 * Hand the session over with RTKit live and the controller
		 * enabled and ready, which is what Linux's nvme-apple adopts:
		 * it disables the controller and builds its own queues.
		 * Delete our I/O queues first so that disable never hits a
		 * controller with live I/O queues, and clear their addresses.
		 * Shutting the firmware down instead would leave the machine
		 * without storage until the next reset.
		 */
		if (nvme_retire_io_queues(dev))
			printf("%s: could not delete the I/O queues\n", dev->name);
		nvme_writeq(0, priv->base + ANS_IOSQ_BASE);
		nvme_writeq(0, priv->base + ANS_IOCQ_BASE);
		printf("%s: handing over the live ANS session (CC %#x CSTS %#x)\n",
		       dev->name, readl(&priv->ndev.bar->cc),
		       readl(&priv->ndev.bar->csts));
		return 0;
	}

	nvme_shutdown(dev);

	apple_rtkit_shutdown(priv->rtk, APPLE_RTKIT_PWR_STATE_SLEEP);

	ctrl = readl(priv->asc + REG_CPU_CTRL);
	writel(ctrl & ~REG_CPU_CTRL_RUN, priv->asc + REG_CPU_CTRL);

	apple_rtkit_free(priv->rtk);
	priv->rtk = NULL;

	sart_free(priv->sart);
	priv->sart = NULL;

	reset_assert_bulk(&priv->resets);
	reset_deassert_bulk(&priv->resets);

	return 0;
}

static const struct nvme_ops apple_nvme_ops = {
	.setup_queue = apple_nvme_setup_queue,
	.submit_cmd = apple_nvme_submit_cmd,
	.complete_cmd = apple_nvme_complete_cmd,
	.queue_created = apple_nvme_queue_created,
};

static const struct udevice_id apple_nvme_ids[] = {
	{ .compatible = "apple,t8103-nvme-ans2",
	  .data = (ulong)&apple_nvme_t8103_hw },
	{ .compatible = "apple,t8132-nvme-ans2",
	  .data = (ulong)&apple_nvme_t8132_hw },
	{ .compatible = "apple,nvme-ans2",
	  .data = (ulong)&apple_nvme_t8103_hw },
	{ /* sentinel */ }
};

U_BOOT_DRIVER(apple_nvme) = {
	.name = "apple_nvme",
	.id = UCLASS_NVME,
	.of_match = apple_nvme_ids,
	.priv_auto = sizeof(struct apple_nvme_priv),
	.probe = apple_nvme_probe,
	.remove = apple_nvme_remove,
	.ops = &apple_nvme_ops,
	.flags = DM_FLAG_OS_PREPARE,
};
