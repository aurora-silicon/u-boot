// SPDX-License-Identifier: GPL-2.0+
/*
 * Record every EFI image U-Boot loads in /chosen/aurora,boot-security of the
 * device tree it hands to the OS, next to the entries the aura stages before
 * it wrote (measurement@N: stage, policy, component, verdict, sha256).
 *
 * There is no TPM on these machines, so this is a log, not a quote: it is
 * as trustworthy as the stages that wrote it, and only says "verified" for
 * a chain where every stage enforced its signature checks.
 */

#define LOG_CATEGORY LOGC_EFI

#include <efi_loader.h>
#include <log.h>
#include <linux/libfdt.h>
#include <u-boot/sha256.h>

struct aurora_measurement {
	u8 digest[SHA256_SUM_LEN];
	bool enforcing;
	bool audit;
	bool authenticated;
};

#define AURORA_MAX_IMAGES 8
static struct aurora_measurement pending[AURORA_MAX_IMAGES];
static int pending_cnt;
static void *flushed_fdt;
static int flushed_cnt;

/* Under audit nothing was refused, so this link never counts as verified. */
static const char *chain_state(const char *prev, bool enforcing, bool passed)
{
	if (!enforcing || !passed)
		return prev && (!strcmp(prev, "verified") ||
				!strcmp(prev, "partial")) ?
		       "partial" : "unverified";
	return prev && !strcmp(prev, "verified") ? "verified" : "partial";
}

static int add_one(void *fdt, const struct aurora_measurement *m)
{
	char name[24];
	int chosen, node, e, idx = 0;
	const char *prev;

	chosen = fdt_path_offset(fdt, "/chosen");
	if (chosen < 0)
		return chosen;
	node = fdt_subnode_offset(fdt, chosen, "aurora,boot-security");
	if (node < 0) {
		node = fdt_add_subnode(fdt, chosen, "aurora,boot-security");
		if (node < 0)
			return node;
		fdt_setprop_string(fdt, node, "compatible",
				   "aurora,boot-security-v1");
		fdt_setprop_u32(fdt, node, "#address-cells", 1);
		fdt_setprop_u32(fdt, node, "#size-cells", 0);
	}
	for (e = fdt_first_subnode(fdt, node); e >= 0;
	     e = fdt_next_subnode(fdt, e))
		idx++;

	snprintf(name, sizeof(name), "measurement@%d", idx);
	e = fdt_add_subnode(fdt, node, name);
	if (e < 0)
		return e;
	fdt_setprop_u32(fdt, e, "reg", idx);
	fdt_setprop_string(fdt, e, "stage", "u-boot");
	fdt_setprop_string(fdt, e, "policy", m->enforcing ? "enforce" :
			   m->audit ? "audit" : "off");
	fdt_setprop_string(fdt, e, "component", "efi-image");
	fdt_setprop_string(fdt, e, "verdict",
			   !m->enforcing && !m->audit ? "measured" :
			   m->authenticated ? "verified" : "bad-signature");
	fdt_setprop(fdt, e, "sha256", m->digest, sizeof(m->digest));

	node = fdt_subnode_offset(fdt, fdt_path_offset(fdt, "/chosen"),
				  "aurora,boot-security");
	prev = fdt_getprop(fdt, node, "state", NULL);
	fdt_setprop_string(fdt, node, "state",
			   chain_state(prev, m->enforcing, m->authenticated));
	fdt_setprop_string(fdt, node, "uefi-secure-boot",
			   m->enforcing ? "enforcing" :
			   m->audit ? "audit" : "off");
	return 0;
}

/*
 * Write what is not in this device tree yet. Called when an image is loaded
 * and again when efi_install_fdt() installs a (new) copy for the OS.
 */
void efi_aurora_flush(void *fdt)
{
	if (!fdt || fdt_check_header(fdt))
		return;
	if (fdt != flushed_fdt) {
		flushed_fdt = fdt;
		flushed_cnt = 0;
	}
	while (flushed_cnt < pending_cnt) {
		int ret = add_one(fdt, &pending[flushed_cnt]);

		if (ret) {
			log_warning("aurora: no room for EFI image record %d (%d)\n",
				    flushed_cnt, ret);
			return;
		}
		flushed_cnt++;
	}
}

void efi_aurora_measure(const void *efi, size_t efi_size, bool authenticated)
{
	struct aurora_measurement *m;

	if (pending_cnt >= AURORA_MAX_IMAGES) {
		log_warning("aurora: EFI image record full\n");
		return;
	}
	m = &pending[pending_cnt++];
	m->enforcing = efi_secure_boot_enabled();
	m->audit = efi_secure_boot_audit();
	m->authenticated = authenticated;
	sha256_csum_wd(efi, efi_size, m->digest, CHUNKSZ_SHA256);
	log_info("aurora: EFI image %d sha256 %02x%02x%02x%02x... %s\n",
		 pending_cnt - 1, m->digest[0], m->digest[1], m->digest[2],
		 m->digest[3], !m->enforcing && !m->audit ? "measured" :
		 authenticated ? "verified" :
		 m->audit ? "NOT verified (audit, started)" : "NOT verified");
	efi_aurora_flush(efi_get_configuration_table(&efi_guid_fdt));
}
