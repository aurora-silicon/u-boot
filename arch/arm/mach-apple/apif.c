// SPDX-License-Identifier: GPL-2.0+
/* Generic Aura APIF transport. Native results are opaque to this layer. */
#include <cpu_func.h>
#include <command.h>
#include <dm/ofnode.h>
#include <malloc.h>
#include <asm/io.h>
#include <asm/cache.h>
#include <asm/arch/apif.h>
#include <linux/errno.h>
#include <linux/sizes.h>
#include <vsprintf.h>

#define APIF_MAGIC 0x4f494d4d46495041ULL

struct apif_packet {
	u32 count, flags;
	s32 status;
	u32 done;
	u64 selector, args[6], result;
};

static void __iomem *apif_regs;
static struct apif_packet *packet;

#ifdef APPLE_APIF_TRACE
#define apif_trace(fmt, ...) printf("APIF: " fmt, ##__VA_ARGS__)
#else
#define apif_trace(fmt, ...) do { } while (0)
#endif

bool apple_apif_present(void)
{
	ofnode node;
	fdt_addr_t address;
	fdt_size_t size;

	if (apif_regs)
		return true;
	node = ofnode_by_compatible(ofnode_null(), "aurora,apif-mmio");
	if (!ofnode_valid(node) || !ofnode_is_enabled(node))
		return false;
	address = ofnode_get_addr_size(node, "reg", &size);
	if (address == FDT_ADDR_T_NONE || size < 0x108)
		return false;
	apif_regs = (void *)(uintptr_t)address;
	apif_trace("provider=%#lx before identity read\n", (ulong)address);
	if (readq(apif_regs) != APIF_MAGIC || readq(apif_regs + 8) != 2) {
		apif_regs = NULL;
		return false;
	}
	apif_trace("provider identity verified\n");
	return true;
}

int apple_apif_call(u32 table, u32 endpoint, const u64 args[6], u64 *result)
{
	const ulong bytes = ALIGN(sizeof(*packet), ARCH_DMA_MINALIGN);
	apif_trace("call %u:%u begin\n", table, endpoint);
	if (!apple_apif_present())
		return -ENODEV;
	if (!packet) {
		packet = memalign(SZ_16K, SZ_16K);
		if (!packet)
			return -ENOMEM;
	}
	memset(packet, 0, sizeof(*packet));
	packet->count = 1;
	packet->selector = (u64)table << 32 | endpoint;
	if (args)
		memcpy(packet->args, args, sizeof(packet->args));
	flush_dcache_range((ulong)packet, (ulong)packet + bytes);
	dsb();
	apif_trace("dispatch packet=%#lx selector=%#llx\n", (ulong)packet, packet->selector);
	/* SPTM redispatch restores its exception-stack SP_EL1. Preserve the
	 * caller's stack across the trapping store, as the Linux client does.
	 */
	asm volatile("mov x9, sp\n"
		     "str %x[pa], [%[db]]\n"
		     "mov sp, x9\n"
		     :
		     : [pa] "r"((ulong)packet), [db] "r"(apif_regs + 0x100)
		     : "x9", "memory");
	apif_trace("dispatch returned\n");
	dsb();
	invalidate_dcache_range((ulong)packet, (ulong)packet + bytes);
	apif_trace("status=%d done=%u result=%#llx\n", packet->status, packet->done, packet->result);
	if (packet->status || packet->done != 1)
		return packet->status ?: -EIO;
	if (result)
		*result = packet->result;
	return 0;
}

int apple_apif_translate(u64 address, u64 size, u64 *physical)
{
	u64 args[6] = { address, size };

	return apple_apif_call(APPLE_APIF_SERVICE, APPLE_APIF_TRANSLATE,
			       args, physical);
}

static int do_apple_apif(struct cmd_tbl *cmdtp, int flag, int argc,
			 char *const argv[])
{
	u64 physical;
	int ret;

	if (argc != 4 || strcmp(argv[1], "translate"))
		return CMD_RET_USAGE;
	ret = apple_apif_translate(hextoul(argv[2], NULL),
				hextoul(argv[3], NULL), &physical);
	if (ret) {
		printf("APIF translation failed: %d\n", ret);
		return CMD_RET_FAILURE;
	}
	printf("APIF physical address: %#llx\n", physical);
	return CMD_RET_SUCCESS;
}

U_BOOT_CMD(apif, 4, 0, do_apple_apif,
	   "query the Aura platform interface",
	   "translate <guest-address> <size> - resolve a guest RAM range");
