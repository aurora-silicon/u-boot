// SPDX-License-Identifier: GPL-2.0-or-later OR MIT
/*
 * Transfer this boot's m1n1 data to an explicitly selected J713 OS tree.
 * Static topology and policy always come from the OS tree. In particular,
 * never import PMGR ownership, pinctrl, power-domain or clock descriptions.
 */
#ifdef J713_FDT_HOST_TEST
#include <errno.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <libfdt.h>
typedef uint32_t u32;
typedef uint64_t u64;
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#else
#include <malloc.h>
#include <linux/errno.h>
#include <linux/kernel.h>
#include <linux/libfdt.h>
#include <linux/string.h>
#endif

#define J713_FDT_SOURCE "u-boot,j713-dtb-source"
#define J713_FDT_COMPLETE "u-boot,j713-dtb-imported"
#include "../include/asm/arch-apple/j713_fdt.h"
#define J713_FDT_MAX_BUFFER (1024 * 1024)
#define J713_FDT_PATH 256

static int j713_copy_prop(void *dst, int dn, const void *src, int sn,
			  const char *name, int exact)
{
	const void *value;
	int len;

	value = fdt_getprop(src, sn, name, &len);
	if (!value)
		return len == -FDT_ERR_NOTFOUND ? -ENOENT : -EINVAL;
	if ((exact >= 0 && len != exact) || len > J713_FDT_MAX_SOURCE)
		return -EINVAL;
	return fdt_setprop(dst, dn, name, value, len);
}

static int j713_copy_optional(void *dst, int dn, const void *src, int sn,
			      const char *name, int exact)
{
	int ret = j713_copy_prop(dst, dn, src, sn, name, exact);

	return ret == -ENOENT ? 0 : ret;
}

static int j713_check_ranges(const void *src, int node, const char *name,
			     int expected)
{
	const fdt64_t *reg;
	int len, i;

	reg = fdt_getprop(src, node, name, &len);
	if (!reg || len != expected || len % 16)
		return -EINVAL;
	for (i = 0; i < len / 8; i += 2) {
		u64 address = fdt64_to_cpu(reg[i]), size = fdt64_to_cpu(reg[i + 1]);

		if (!address || !size || address + size <= address)
			return -EINVAL;
	}
	return 0;
}

static int j713_copy_string(void *dst, int dn, const void *src, int sn,
			    const char *name, int limit, bool required)
{
	const char *value;
	int len;

	value = fdt_getprop(src, sn, name, &len);
	if (!value)
		return len == -FDT_ERR_NOTFOUND && !required ? 0 : -EINVAL;
	if (len <= 0 || len > limit || value[len - 1] ||
	    strnlen(value, len) != len - 1)
		return -EINVAL;
	return fdt_setprop(dst, dn, name, value, len);
}

static int j713_alias_node(const void *fdt, const char *alias)
{
	const char *path = fdt_get_alias(fdt, alias);

	return path ? fdt_path_offset(fdt, path) : -FDT_ERR_NOTFOUND;
}

/* An alias is semantic identity; node offsets and phandle values are not. */
static int j713_map_node(void *dst, const void *src, int sn)
{
	static const char * const aliases[] = {
		"gpu", "dcp", "disp0", "disp0_piodma", "ans", "ans-mbox",
		"sart-ans", "spmi-primary", "pmu-primary", "pmp", "wifi0",
		"keyboard", "bluetooth0", "atcphy0", "atcphy1",
	};
	char path[J713_FDT_PATH];
	unsigned int i;
	int node;

	if (sn == fdt_path_offset(src, "/chosen/framebuffer"))
		return fdt_path_offset(dst, "/chosen/framebuffer");
	for (i = 0; i < ARRAY_SIZE(aliases); i++) {
		node = j713_alias_node(src, aliases[i]);
		if (node >= 0 && node == sn)
			return j713_alias_node(dst, aliases[i]);
	}
	if (fdt_get_path(src, sn, path, sizeof(path)))
		return -FDT_ERR_BADPATH;
	return fdt_path_offset(dst, path);
}

static int j713_translate_phandle(void *dst, const void *src, u32 old,
				  u32 *translated)
{
	int sn = fdt_node_offset_by_phandle(src, old), dn, ret;

	if (sn < 0)
		return -EINVAL;
	dn = j713_map_node(dst, src, sn);
	if (dn < 0)
		return dn;
	*translated = fdt_get_phandle(dst, dn);
	if (*translated)
		return 0;
	ret = fdt_generate_phandle(dst, translated);
	if (ret)
		return ret;
	return fdt_setprop_u32(dst, dn, "phandle", *translated);
}

/* Only known optional native admission may degrade. Unknown enabled memory
 * with an invalid extent remains an import error. A completion marker alone
 * never establishes an MTP session; its separate typed inventory is required.
 */
static bool j713_gpu_ready(const void *src)
{
	const fdt32_t *version;
	const char *status;
	int node = j713_alias_node(src, "gpu"), len;

	if (node < 0)
		return false;
	status = fdt_getprop(src, node, "status", NULL);
	if (status && strcmp(status, "okay") && strcmp(status, "ok"))
		return false;
	version = fdt_getprop(src, node, "apple,g16-handoff-version", &len);
	return version && len == 4 && fdt32_to_cpu(*version) == 1 &&
	       fdt_getprop(src, node, "apple,firmware-segment-vas", &len) && len == 16 &&
	       fdt_getprop(src, node, "apple,firmware-segment-flags", &len) && len == 8;
}

static bool j713_display_ready(const void *src)
{
	int dcp = j713_alias_node(src, "dcp"), disp = j713_alias_node(src, "disp0");
	int piodma = j713_alias_node(src, "disp0_piodma"), len;
	const char *uuid;

	if (dcp < 0 || disp < 0 || piodma < 0)
		return false;
	uuid = fdt_getprop(src, dcp, "apple,firmware-uuid", &len);
	if (!uuid || len != 37 || uuid[36] || strnlen(uuid, len) != 36)
		return false;
	return fdt_getprop(src, dcp, "apple,firmware-version", &len) && len == 12 &&
	       fdt_getprop(src, dcp, "apple,firmware-compat", &len) && len == 12 &&
	       fdt_getprop(src, disp, "apple,j713-inherited-mappings", &len) && !len &&
	       fdt_getprop(src, piodma, "apple,j713-inherited-mappings", &len) && !len;
}

static bool j713_optional_disabled(const void *src, const char *alias)
{
	if (!strcmp(alias, "gpu"))
		return !j713_gpu_ready(src);
	if (!strcmp(alias, "dcp") || !strcmp(alias, "disp0") || !strcmp(alias, "disp0_piodma"))
		return !j713_display_ready(src);
	return false;
}

static int j713_disable_optional(void *dst, int dn, const char *alias)
{
	const char *marker = !strcmp(alias, "gpu") ? "apple,g16-handoff-version" :
						  "apple,j713-inherited-mappings";
	int ret = fdt_delprop(dst, dn, marker);

	if (ret && ret != -FDT_ERR_NOTFOUND)
		return ret;
	return fdt_setprop_string(dst, dn, "status", "disabled");
}

static bool j713_zero_gpu_placeholder(const void *src, int node)
{
	static const char *const names[] = {"uat-ttbs", "uat-pagetables", "uat-handoff",
		"uat-shared-l2", "gpu-fw-text", "gpu-fw-data"};
	const char *name = fdt_get_name(src, node, NULL);
	const fdt64_t *reg;
	int len;

	if (j713_gpu_ready(src))
		return false;
	for (unsigned int i = 0; i < ARRAY_SIZE(names); i++) {
		if (strcmp(name, names[i]))
			continue;
		reg = fdt_getprop(src, node, "reg", &len);
		return reg && len == 16 && !fdt64_to_cpu(reg[0]) && !fdt64_to_cpu(reg[1]);
	}
	return false;
}

static bool j713_omitted_display(const void *dst, const void *src, int sn)
{
	static const char * const aliases[] = { "dcp", "disp0", "disp0_piodma" };
	unsigned int i;

	/* A basic bring-up tree deliberately has no display coprocessor. */
	for (i = 0; i < ARRAY_SIZE(aliases); i++)
		if (j713_alias_node(src, aliases[i]) == sn &&
		    (j713_alias_node(dst, aliases[i]) == -FDT_ERR_NOTFOUND ||
		     !j713_display_ready(src)))
			return true;
	return false;
}

/* m1n1 reserves the AOP's firmware and DAPF windows for the AOP of its control
 * tree. An OS tree without that AOP owns none of them.
 */
static bool j713_omitted_aop(const void *dst, const void *src, int sn)
{
	return !fdt_node_check_compatible(src, sn, "apple,t8132-aop") &&
	       j713_map_node((void *)dst, src, sn) == -FDT_ERR_NOTFOUND;
}

static int j713_iommu_addresses(void *dst, const void *src, const char *path)
{
	const fdt32_t *value;
	fdt32_t *out;
	int sn, dn, len, pos = 0, used = 0, ret = 0;

	sn = fdt_path_offset(src, path);
	value = fdt_getprop(src, sn, "iommu-addresses", &len);
	if (!value)
		return len == -FDT_ERR_NOTFOUND ? 0 : -EINVAL;
	if (len <= 0 || len % sizeof(*value))
		return -EINVAL;
	out = malloc(len);
	if (!out)
		return -ENOMEM;
	while (pos < len / sizeof(*value)) {
		u32 old = fdt32_to_cpu(value[pos]), phandle;
		int owner = fdt_node_offset_by_phandle(src, old);
		int na, ns, cells;

		if (owner < 0) {
			ret = -EINVAL;
			break;
		}
		/* iommu-addresses uses the owner's DMA parent address space. The
		 * qualified J713 profile has no dma-mem interconnect indirection.
		 */
		if (fdt_getprop(src, owner, "interconnects", NULL)) {
			ret = -EINVAL;
			break;
		}
		na = fdt_address_cells(src, fdt_parent_offset(src, owner));
		ns = fdt_size_cells(src, fdt_parent_offset(src, owner));
		if (na != 2 || ns != 2 || len / sizeof(*value) - pos < 1 + na + ns) {
			ret = -EINVAL;
			break;
		}
		cells = 1 + na + ns;
		if (!j713_omitted_display(dst, src, owner) && !j713_omitted_aop(dst, src, owner)) {
			int target = j713_map_node(dst, src, owner);
			int parent = target < 0 ? target : fdt_parent_offset(dst, target);

			if (parent < 0 || fdt_address_cells(dst, parent) != na ||
			    fdt_size_cells(dst, parent) != ns ||
			    fdt_getprop(dst, target, "interconnects", NULL)) {
				ret = -EINVAL;
				break;
			}
			ret = j713_translate_phandle(dst, src, old, &phandle);
			if (ret)
				break;
			out[used++] = cpu_to_fdt32(phandle);
			memcpy(out + used, value + pos + 1, (cells - 1) * sizeof(*out));
			used += cells - 1;
		}
		pos += cells;
	}
	if (!ret) {
		dn = fdt_path_offset(dst, path);
		if (used) {
			ret = fdt_setprop(dst, dn, "iommu-addresses", out, used * sizeof(*out));
		} else {
			ret = fdt_delprop(dst, dn, "iommu-addresses");
			if (ret == -FDT_ERR_NOTFOUND)
				ret = 0;
		}
	}
	free(out);
	return ret;
}

static int j713_phandles(void *dst, const void *src, int sn,
			 const char *name, int exact)
{
	const fdt32_t *value;
	fdt32_t *out;
	char path[J713_FDT_PATH];
	int len, i, ret = 0, dn;

	value = fdt_getprop(src, sn, name, &len);
	if (!value || len <= 0 || len % sizeof(*value) ||
	    (exact >= 0 && len != exact) ||
	    fdt_get_path(dst, j713_map_node(dst, src, sn), path, sizeof(path)))
		return -EINVAL;
	out = malloc(len);
	if (!out)
		return -ENOMEM;
	for (i = 0; i < len / sizeof(*value); i++) {
		u32 translated;

		ret = j713_translate_phandle(dst, src, fdt32_to_cpu(value[i]), &translated);
		if (ret)
			break;
		out[i] = cpu_to_fdt32(translated);
	}
	if (!ret) {
		dn = fdt_path_offset(dst, path);
		ret = fdt_setprop(dst, dn, name, out, len);
	}
	free(out);
	return ret;
}

static int j713_reservations(void *dst, const void *src)
{
	int sr = fdt_path_offset(src, "/reserved-memory"), sn, ret, i, count;
	int dr = fdt_path_offset(dst, "/reserved-memory");

	if (sr < 0 || dr < 0 || fdt_address_cells(src, sr) != 2 ||
	    fdt_size_cells(src, sr) != 2 || fdt_address_cells(dst, dr) != 2 ||
	    fdt_size_cells(dst, dr) != 2)
		return -EINVAL;
	count = fdt_num_mem_rsv(src);
	if (count < 0 || count > 128)
		return -EINVAL;
	for (i = 0; i < count; i++) {
		u64 address, size;
		int n, found = 0;

		ret = fdt_get_mem_rsv(src, i, &address, &size);
		if (ret || !size || address + size <= address)
			return -EINVAL;
		for (n = 0; n < fdt_num_mem_rsv(dst); n++) {
			u64 da, ds;

			if (fdt_get_mem_rsv(dst, n, &da, &ds))
				return -EINVAL;
			if (da == address && ds == size)
				found = 1;
		}
		if (!found) {
			ret = fdt_add_mem_rsv(dst, address, size);
			if (ret)
				return ret;
		}
	}
	/* First create every memory node. References are translated in a second pass. */
	fdt_for_each_subnode(sn, src, sr) {
		const char *name = fdt_get_name(src, sn, NULL);
		const fdt64_t *reg;
		int po, dn, len;

		/* The live U-Boot helper republishes its own current session below. */
		if (!strncmp(name, "mtp-rtkit@", 10) || j713_zero_gpu_placeholder(src, sn))
			continue;
		reg = fdt_getprop(src, sn, "reg", &len);
		if (!reg || len != 16 || !fdt64_to_cpu(reg[1]) ||
		    fdt64_to_cpu(reg[0]) + fdt64_to_cpu(reg[1]) <= fdt64_to_cpu(reg[0]))
			return -EINVAL;
		dr = fdt_path_offset(dst, "/reserved-memory");
		dn = fdt_subnode_offset(dst, dr, name);
		if (dn == -FDT_ERR_NOTFOUND)
			dn = fdt_add_subnode(dst, dr, name);
		if (dn < 0)
			return dn;
		fdt_for_each_property_offset(po, src, sn) {
			const struct fdt_property *prop = fdt_get_property_by_offset(src, po, &len);
			const char *pn;

			if (!prop)
				return -EINVAL;
			pn = fdt_string(src, fdt32_to_cpu(prop->nameoff));
			if (!strcmp(pn, "phandle") || !strcmp(pn, "linux,phandle") ||
			    !strcmp(pn, "iommu-addresses"))
				continue;
			if (strcmp(pn, "reg") && strcmp(pn, "no-map") &&
			    strcmp(pn, "status") && strcmp(pn, "compatible") &&
			    strcmp(pn, "label") && strcmp(pn, "apple,dcp-os-log"))
				return -EINVAL;
			ret = fdt_setprop(dst, dn, pn, prop->data, len);
			if (ret)
				return ret;
		}
	}
	fdt_for_each_subnode(sn, src, sr) {
		char path[J713_FDT_PATH];
		const char *name = fdt_get_name(src, sn, NULL);

		if (!strncmp(name, "mtp-rtkit@", 10) || j713_zero_gpu_placeholder(src, sn))
			continue;
		if (fdt_get_path(src, sn, path, sizeof(path)))
			return -EINVAL;
		ret = j713_iommu_addresses(dst, src, path);
		if (ret)
			return ret;
	}
	return 0;
}

static int j713_cpus(void *dst, const void *src)
{
	int dc = fdt_path_offset(dst, "/cpus"), sc = fdt_path_offset(src, "/cpus");
	int dn, count = 0, missing_release = 0;

	if (dc < 0 || sc < 0)
		return -EINVAL;
	fdt_for_each_subnode(dn, dst, dc) {
		const char *name = fdt_get_name(dst, dn, NULL);
		const fdt64_t *reg, *release;
		u64 mpidr;
		int sn, len, found = -1, ret;

		if (strncmp(name, "cpu@", 4))
			continue;
		reg = fdt_getprop(dst, dn, "reg", &len);
		if (!reg || len != 8 || ++count > 32)
			return -EINVAL;
		mpidr = fdt64_to_cpu(*reg);
		fdt_for_each_subnode(sn, src, sc) {
			reg = fdt_getprop(src, sn, "reg", &len);
			if (reg && len == 8 && fdt64_to_cpu(*reg) == mpidr) {
				if (found >= 0)
					return -EINVAL;
				found = sn;
			}
		}
		/* Never invent release slots or re-enable CPUs pruned by m1n1. */
		if (found < 0)
			return -EINVAL;
		release = fdt_getprop(src, found, "cpu-release-addr", &len);
		if (!release || (len == 8 && !fdt64_to_cpu(*release))) {
			if ((!release && len != -FDT_ERR_NOTFOUND) || ++missing_release > 1)
				return -EINVAL;
			continue; /* The already-running boot CPU needs no release slot. */
		}
		if (len != 8 || !fdt64_to_cpu(*release) || (fdt64_to_cpu(*release) & 7))
			return -EINVAL;
		ret = fdt_setprop(dst, dn, "cpu-release-addr", release, len);
		if (ret)
			return ret;
	}
	return count ? 0 : -EINVAL;
}

struct j713_property {
	const char *alias;
	const char *property;
	int size;
	bool optional;
};

/* Runtime admission belongs to this boot; an optional failed radio port
 * must stay disabled when switching to the OS's static topology. */
static int j713_pcie_admission(void *dst, const void *src)
{
	const char *path = "/soc/pcie@1cb0000000";
	int sn = fdt_path_offset(src, path), dn;
	const char *status;

	if (sn == -FDT_ERR_NOTFOUND)
		return 0;
	if (sn < 0 || fdt_node_check_compatible(src, sn, "apple,t8132-pcie"))
		return -EINVAL;
	status = fdt_getprop(src, sn, "status", NULL);
	if (!status || !strcmp(status, "okay") || !strcmp(status, "ok"))
		return 0;
	dn = fdt_path_offset(dst, path);
	if (dn < 0 || fdt_node_check_compatible(dst, dn, "apple,t8132-pcie"))
		return -EINVAL;
	return fdt_setprop_string(dst, dn, "status", "disabled");
}

static int j713_bluetooth(void *dst, const void *src)
{
	static const struct j713_property props[] = {
		{ "bluetooth0", "local-bd-address", 6 },
		{ "bluetooth0", "brcm,taurus-cal-blob", -1 },
		{ "bluetooth0", "brcm,taurus-bf-cal-blob", -1 },
	};
	int dn = j713_alias_node(dst, "bluetooth0");
	int sn = j713_alias_node(src, "bluetooth0");
	unsigned int i;
	int len, ret;

	/* Older OS trees have no Bluetooth node. Do not extend their topology. */
	if (dn == -FDT_ERR_NOTFOUND)
		return 0;
	if (dn < 0 || fdt_node_check_compatible(dst, dn, "pci14e4,5f72"))
		return -EINVAL;
	/* A bootloader without this device's calibration cannot admit it. */
	if (sn == -FDT_ERR_NOTFOUND)
		return fdt_setprop_string(dst, dn, "status", "disabled");
	if (sn < 0 || fdt_node_check_compatible(src, sn, "pci14e4,5f72"))
		return -EINVAL;
	for (i = 0; i < ARRAY_SIZE(props); i++) {
		if (!fdt_getprop(src, sn, props[i].property, &len) || !len)
			return fdt_setprop_string(dst, dn, "status", "disabled");
		ret = j713_copy_prop(dst, dn, src, sn, props[i].property, props[i].size);
		if (ret)
			return ret;
	}
	return 0;
}

static int j713_atc_tunables(void *dst, const void *src, const char *alias)
{
	static const struct {
		const char *name;
		const char *bank;
	} props[] = {
		{ "apple,tunable-usb3-axi2af", "axi2af" },
		{ "apple,tunable-usb3-lioa", "lioa" },
		{ "apple,tunable-usb3-fabric", "fabric" },
		{ "apple,tunable-usb3-config", "common-config" },
		{ "apple,tunable-usb3-common", "core" },
		{ "apple,tunable-usb3-efuse", "core" },
		{ "apple,tunable-usb3-dp", "core" },
		{ "apple,tunable-lane0-usb", "core" },
		{ "apple,tunable-lane1-usb", "core" },
	};
	int dn = j713_alias_node(dst, alias), sn = j713_alias_node(src, alias);
	unsigned int i;
	int ret, reg_len;
	bool usb2_only;

	if (dn == -FDT_ERR_NOTFOUND)
		return 0;
	if (dn < 0 || fdt_node_check_compatible(dst, dn, "apple,t8132-atcphy"))
		return -EINVAL;
	if (sn >= 0 && fdt_node_check_compatible(src, sn, "apple,t8132-atcphy"))
		return -EINVAL;
	if (sn < 0 && sn != -FDT_ERR_NOTFOUND)
		return -EINVAL;
	/* An OS may retain USB2 on a port whose common bank is not admitted.
	 * Calibration does not authorize importing the missing USB3 topology.
	 */
	usb2_only = fdt_stringlist_count(dst, dn, "reg-names") == 2 &&
		fdt_stringlist_search(dst, dn, "reg-names", "usb2phy") == 0 &&
		fdt_stringlist_search(dst, dn, "reg-names", "pipehandler") == 1 &&
		fdt_getprop(dst, dn, "reg", &reg_len) && reg_len == 32;
	for (i = 0; i < ARRAY_SIZE(props); i++) {
		const fdt32_t *value = NULL, *reg;
		int len, bank, parent, reg_len, j;
		u64 size;

		if (!usb2_only && sn >= 0)
			value = fdt_getprop(src, sn, props[i].name, &len);
		if (!value) {
			/* Never reuse calibration left in an OS tree from another boot. */
			ret = fdt_delprop(dst, dn, props[i].name);
			if (ret && ret != -FDT_ERR_NOTFOUND)
				return ret;
			continue;
		}
		if (len <= 0 || len > J713_FDT_MAX_SOURCE || len % 12)
			return -EINVAL;
		parent = fdt_parent_offset(dst, dn);
		bank = fdt_stringlist_search(dst, dn, "reg-names", props[i].bank);
		reg = fdt_getprop(dst, dn, "reg", &reg_len);
		if (parent < 0 || fdt_address_cells(dst, parent) != 2 ||
		    fdt_size_cells(dst, parent) != 2 || bank < 0 || !reg ||
		    reg_len % 16 || bank >= reg_len / 16)
			return -EINVAL;
		size = (u64)fdt32_to_cpu(reg[bank * 4 + 2]) << 32 |
		       fdt32_to_cpu(reg[bank * 4 + 3]);
		for (j = 0; j < len / 4; j += 3) {
			u32 offset = fdt32_to_cpu(value[j]);

			if (offset % 4 || (u64)offset + 4 > size)
				return -EINVAL;
		}
		ret = fdt_setprop(dst, dn, props[i].name, value, len);
		if (ret)
			return ret;
	}
	return 0;
}

static int j713_devices(void *dst, const void *src)
{
	static const struct j713_property props[] = {
		{ "ans", "reg", 48 }, { "ans", "interrupts", 12 },
		{ "ans-mbox", "reg", 16 }, { "ans-mbox", "interrupts", 48 },
		{ "sart-ans", "reg", 16 }, { "spmi-primary", "reg", 16 },
		{ "pmu-primary", "reg", 8 }, { "pmp", "apple,board-id", 4 },
		{ "pmp", "apple,dram-vendor-id", 4 },
		{ "pmp", "apple,dram-capacity", 4, true },
		{ "wifi0", "local-mac-address", 6 },
		{ "wifi0", "brcm,cal-blob", -1 },
		{ "keyboard", "apple,keyboard-layout-id", 4 },
		{ "keyboard", "hid-country-code", 4, true },
		{ "gpu", "apple,g16-handoff-version", 4 },
		{ "gpu", "apple,firmware-segment-vas", 16 },
		{ "gpu", "apple,firmware-segment-flags", 8 },
		{ "dcp", "apple,firmware-version", 12 },
		{ "dcp", "apple,firmware-compat", 12 },
		{ "dcp", "apple,notch-height", 4, true },
		{ "disp0", "apple,j713-inherited-mappings", 0 },
		{ "disp0_piodma", "apple,j713-inherited-mappings", 0 },
	};
	static const char * const enabled[] = {
		"ans", "ans-mbox", "sart-ans", "pmp", "gpu", "disp0", "disp0_piodma",
	};
	unsigned int i;
	int dn, sn, po, len, ret, tunables = 0;

	for (i = 0; i < ARRAY_SIZE(props); i++) {
		dn = j713_alias_node(dst, props[i].alias);
		if (dn == -FDT_ERR_NOTFOUND &&
		    (!strcmp(props[i].alias, "gpu") || !strcmp(props[i].alias, "dcp") ||
		     !strcmp(props[i].alias, "disp0") || !strcmp(props[i].alias, "disp0_piodma")))
			continue;
		if (dn >= 0 && j713_optional_disabled(src, props[i].alias)) {
			ret = j713_disable_optional(dst, dn, props[i].alias);
			if (ret)
				return ret;
			continue;
		}
		sn = j713_alias_node(src, props[i].alias);
		if (dn < 0 || sn < 0)
			return -EINVAL;
		if (!strcmp(props[i].property, "reg")) {
			int sp = fdt_parent_offset(src, sn), dp = fdt_parent_offset(dst, dn);

			if (sp < 0 || dp < 0 ||
			    fdt_address_cells(src, sp) != fdt_address_cells(dst, dp) ||
			    fdt_size_cells(src, sp) != fdt_size_cells(dst, dp))
				return -EINVAL;
		}
		if (!strcmp(props[i].property, "reg") && props[i].size % 16 == 0 &&
		    j713_check_ranges(src, sn, "reg", props[i].size))
			return -EINVAL;
		ret = props[i].optional ?
			j713_copy_optional(dst, dn, src, sn, props[i].property, props[i].size) :
			j713_copy_prop(dst, dn, src, sn, props[i].property, props[i].size);
		if (ret)
			return ret;
	}
	for (i = 0; i < ARRAY_SIZE(enabled); i++) {
		dn = j713_alias_node(dst, enabled[i]);
		if (dn == -FDT_ERR_NOTFOUND)
			continue;
		if (j713_optional_disabled(src, enabled[i])) {
			ret = j713_disable_optional(dst, dn, enabled[i]);
			if (ret)
				return ret;
			continue;
		}
		sn = j713_alias_node(src, enabled[i]);
		/* An absent status is enabled. Older m1n1 input trees did not
		 * need disabled placeholders for these now-qualified resources.
		 */
		if (!fdt_getprop(src, sn, "status", NULL))
			ret = fdt_setprop_string(dst, dn, "status", "okay");
		else
			ret = j713_copy_string(dst, dn, src, sn, "status", 16, true);
		if (ret)
			return ret;
	}
	sn = j713_alias_node(src, "pmp");
	fdt_for_each_property_offset(po, src, sn) {
		const struct fdt_property *prop = fdt_get_property_by_offset(src, po, &len);
		const char *name;

		if (!prop)
			return -EINVAL;
		name = fdt_string(src, fdt32_to_cpu(prop->nameoff));
		if (strncmp(name, "apple,tunable-", 13))
			continue;
		dn = j713_alias_node(dst, "pmp");
		ret = fdt_setprop(dst, dn, name, prop->data, len);
		if (ret)
			return ret;
		tunables++;
	}
	if (!tunables)
		return -EINVAL;
	dn = j713_alias_node(dst, "wifi0");
	sn = j713_alias_node(src, "wifi0");
	ret = j713_copy_string(dst, dn, src, sn, "apple,antenna-sku", 32, true);
	if (ret)
		return ret;
	dn = j713_alias_node(dst, "dcp");
	if (dn >= 0 && j713_display_ready(src)) {
		sn = j713_alias_node(src, "dcp");
		ret = j713_copy_string(dst, dn, src, sn, "apple,firmware-uuid", 37, true);
		if (ret)
			return ret;
	}
	dn = j713_alias_node(dst, "gpu");
	if (dn >= 0 && j713_gpu_ready(src)) {
		const fdt32_t *version;

		sn = j713_alias_node(src, "gpu");
		version = fdt_getprop(src, sn, "apple,g16-handoff-version", &len);
		if (!version || len != 4 || fdt32_to_cpu(*version) != 1)
			return -EINVAL;
	}
	return 0;
}

static int j713_display_refs(void *dst, const void *src)
{
	static const char * const aliases[] = { "dcp", "disp0", "disp0_piodma" };
	unsigned int i;
	int sn, dn, ret;

	for (i = 0; i < ARRAY_SIZE(aliases); i++) {
		if (!j713_display_ready(src))
			return 0;
		dn = j713_alias_node(dst, aliases[i]);
		if (dn == -FDT_ERR_NOTFOUND)
			continue;
		sn = j713_alias_node(src, aliases[i]);
		if (sn < 0 || dn < 0)
			return -EINVAL;
		ret = j713_phandles(dst, src, sn, "memory-region", -1);
		if (ret)
			return ret;
		/* The loader qualifies the frontend DART along with SID0/SID16. */
		if (i == 1) {
			const fdt32_t *iommu;
			int len, dart;

			dn = j713_alias_node(dst, aliases[i]);
			iommu = fdt_getprop(dst, dn, "iommus", &len);
			if (!iommu || len != 8)
				return -EINVAL;
			dart = fdt_node_offset_by_phandle(dst, fdt32_to_cpu(iommu[0]));
			if (dart < 0)
				return -EINVAL;
			ret = fdt_setprop_string(dst, dart, "status", "okay");
			if (ret)
				return ret;
		}
	}
	return 0;
}

static bool j713_enabled(const void *fdt, int node)
{
	const char *status = fdt_getprop(fdt, node, "status", NULL);

	return !status || !strcmp(status, "okay") || !strcmp(status, "ok");
}

/* m1n1 programs the AOP DART's DAPF and reserves the AOP's firmware segments
 * and DAPF windows for this boot, or disables the AOP. Reference those
 * reservations from the OS tree's AOP, or disable it there too. An OS tree
 * whose AOP describes its own reservations (an older kernel) keeps them.
 */
static int j713_aop(void *dst, const void *src)
{
	static const char compatible[] = "apple,t8132-aop";
	int dn = fdt_node_offset_by_compatible(dst, -1, compatible), sn;

	if (dn < 0)
		return dn == -FDT_ERR_NOTFOUND ? 0 : -EINVAL;
	if (!j713_enabled(dst, dn) || fdt_getprop(dst, dn, "memory-region", NULL))
		return 0;
	sn = fdt_node_offset_by_compatible(src, -1, compatible);
	if (sn < 0 && sn != -FDT_ERR_NOTFOUND)
		return -EINVAL;
	if (sn < 0 || !j713_enabled(src, sn) ||
	    !fdt_getprop(src, sn, "memory-region", NULL) ||
	    j713_map_node(dst, src, sn) != dn)
		return fdt_setprop_string(dst, dn, "status", "disabled");
	return j713_phandles(dst, src, sn, "memory-region", -1);
}

static int j713_chosen(void *dst, const void *src)
{
	static const char * const strings[] = {
		"asahi,iboot1-version", "asahi,iboot2-version", "asahi,system-fw-version",
		"asahi,os-fw-version", "asahi,m1n1-stage1-version", "asahi,m1n1-stage2-version",
		"asahi,efi-system-partition", "apfs-preboot-uuid", "stdout-path",
	};
	static const char * const framebuffer[] = {
		"reg", "width", "height", "stride", "format", "compatible",
	};
	unsigned int i;
	int dn, sn, ret;
	const fdt32_t *dimension;
	const fdt64_t *fbreg;
	const char *format;
	u32 dimensions[3], bytes_per_pixel;
	int len;

	sn = fdt_path_offset(src, "/chosen/framebuffer");
	if (sn < 0 || fdt_node_check_compatible(src, sn, "simple-framebuffer"))
		return -EINVAL;
	for (i = 0; i < 3; i++) {
		dimension = fdt_getprop(src, sn, framebuffer[i + 1], &len);
		if (!dimension || len != 4)
			return -EINVAL;
		dimensions[i] = fdt32_to_cpu(*dimension);
		if (!dimensions[i])
			return -EINVAL;
	}
	format = fdt_getprop(src, sn, "format", &len);
	if (!format || len <= 0 || format[len - 1])
		return -EINVAL;
	if (!strcmp(format, "x2r10g10b10") || !strcmp(format, "x8r8g8b8"))
		bytes_per_pixel = 4;
	else if (!strcmp(format, "r5g6b5"))
		bytes_per_pixel = 2;
	else
		return -EINVAL;
	fbreg = fdt_getprop(src, sn, "reg", &len);
	if (!fbreg || len != 16 ||
	    (u64)dimensions[0] * bytes_per_pixel > dimensions[2] ||
	    (u64)dimensions[1] * dimensions[2] > fdt64_to_cpu(fbreg[1]))
		return -EINVAL;

	for (i = 0; i < ARRAY_SIZE(strings); i++) {
		dn = fdt_path_offset(dst, "/chosen");
		sn = fdt_path_offset(src, "/chosen");
		ret = j713_copy_string(dst, dn, src, sn, strings[i], 256,
				       strcmp(strings[i], "apfs-preboot-uuid") != 0);
		if (ret)
			return ret;
	}
	dn = fdt_path_offset(dst, "/chosen");
	sn = fdt_path_offset(src, "/chosen");
	ret = j713_copy_prop(dst, dn, src, sn, "asahi,kblang-code", 4);
	if (ret)
		return ret;
	/* These are from this m1n1 boot, never from a saved Linux FDT. Generic
	 * EFI fixups discard them if EFI_RNG_PROTOCOL can provide fresh entropy.
	 */
	dn = fdt_path_offset(dst, "/chosen");
	ret = j713_copy_optional(dst, dn, src, sn, "kaslr-seed", 8);
	if (ret)
		return ret;
	dn = fdt_path_offset(dst, "/chosen");
	if (fdt_getprop(src, sn, "rng-seed", &len) && (len < 16 || len > 4096))
		return -EINVAL;
	ret = j713_copy_optional(dst, dn, src, sn, "rng-seed", -1);
	if (ret)
		return ret;
	for (i = 0; i < ARRAY_SIZE(framebuffer); i++) {
		dn = fdt_path_offset(dst, "/chosen/framebuffer");
		sn = fdt_path_offset(src, "/chosen/framebuffer");
		if (dn < 0 || sn < 0)
			return -EINVAL;
		if (i == 0 && j713_check_ranges(src, sn, "reg", 16))
			return -EINVAL;
		ret = j713_copy_prop(dst, dn, src, sn, framebuffer[i], i == 0 ? 16 :
				     i < 4 ? 4 : -1);
		if (ret)
			return ret;
	}
	dn = fdt_path_offset(dst, "/chosen/framebuffer");
	sn = fdt_path_offset(src, "/chosen/framebuffer");
	if (fdt_getprop(src, sn, "status", NULL)) {
		ret = j713_copy_string(dst, dn, src, sn, "status", 16, true);
	} else {
		ret = fdt_delprop(dst, dn, "status");
		if (ret == -FDT_ERR_NOTFOUND)
			ret = 0;
	}
	if (ret)
		return ret;
	sn = fdt_path_offset(src, "/chosen/framebuffer");
	if (fdt_getprop(src, sn, "panel", NULL)) {
		ret = j713_phandles(dst, src, sn, "panel", 4);
		if (ret)
			return ret;
	}
	return j713_copy_string(dst, 0, src, 0, "serial-number", 128, true);
}

/* Return 1 only for an opted-in, fully imported tree, 0 for the legacy path.
 * Failure never changes either the caller's OS tree or m1n1's control tree.
 * The completion marker records import, not input readiness. MTP additionally
 * requires its typed handoff marker, validated inventory, and running ASC.
 */
int apple_j713_import_fdt(void *blob, const void *source)
{
	const char *identity;
	void *scratch;
	int chosen, len, ret, i;
	size_t capacity = fdt_totalsize(blob), used;

	chosen = fdt_path_offset(blob, "/chosen");
	identity = chosen < 0 ? NULL : fdt_getprop(blob, chosen, J713_FDT_SOURCE, &len);
	if (!identity)
		return 0;
	if (len != 41 || identity[40])
		return -EINVAL;
	for (i = 0; i < 40; i++)
		if (!((identity[i] >= '0' && identity[i] <= '9') ||
		      (identity[i] >= 'a' && identity[i] <= 'f')))
			return -EINVAL;
	if (blob == source || !source || fdt_check_header(source) ||
	    fdt_totalsize(source) > J713_FDT_MAX_SOURCE ||
	    capacity > J713_FDT_MAX_BUFFER || fdt_check_full(source, fdt_totalsize(source)) ||
	    fdt_check_full(blob, capacity) ||
	    fdt_node_check_compatible(source, 0, "apple,j713") ||
	    fdt_node_check_compatible(source, 0, "apple,t8132") ||
	    fdt_node_check_compatible(blob, 0, "apple,j713") ||
	    fdt_node_check_compatible(blob, 0, "apple,t8132"))
		return -EINVAL;
	used = fdt_off_dt_strings(blob) + fdt_size_dt_strings(blob);
	/* Bound all possible copied property bytes, node names, phandles and MTP
	 * publication before touching the live helper's ownership state.
	 */
	if (used > J713_FDT_MAX_SOURCE ||
	    capacity < used + fdt_totalsize(source) + J713_FDT_EXTRA_PAD)
		return -ENOSPC;
	scratch = malloc(capacity);
	if (!scratch)
		return -ENOMEM;
	ret = fdt_open_into(blob, scratch, capacity);
	if (ret)
		goto out;
	chosen = fdt_path_offset(scratch, "/chosen");
	ret = fdt_delprop(scratch, chosen, J713_FDT_COMPLETE);
	if (ret && ret != -FDT_ERR_NOTFOUND)
		goto out;
	ret = j713_reservations(scratch, source);
	ret = ret ?: j713_cpus(scratch, source);
	ret = ret ?: j713_devices(scratch, source);
	ret = ret ?: j713_bluetooth(scratch, source);
	ret = ret ?: j713_atc_tunables(scratch, source, "atcphy0");
	ret = ret ?: j713_atc_tunables(scratch, source, "atcphy1");
	ret = ret ?: j713_pcie_admission(scratch, source);
	ret = ret ?: j713_display_refs(scratch, source);
	ret = ret ?: j713_aop(scratch, source);
	ret = ret ?: j713_chosen(scratch, source);
	if (!ret) {
		/* All helper paths must resolve before its real hardware fixup runs. */
		const char *compatible = "apple,t8132-rtk-helper-asc4";
		int helper = fdt_node_offset_by_compatible(source, -1, compatible);
		char path[J713_FDT_PATH];

		if (helper < 0 || fdt_get_path(source, helper, path, sizeof(path)) ||
		    fdt_path_offset(scratch, path) < 0 ||
		    fdt_node_check_compatible(scratch, fdt_path_offset(scratch, path), compatible))
			ret = -EINVAL;
	}
	if (!ret)
		memcpy(blob, scratch, capacity);
out:
	free(scratch);
	return ret ? ret : 1;
}

int apple_j713_complete_fdt(void *blob)
{
	int chosen = fdt_path_offset(blob, "/chosen");

	return chosen < 0 ? chosen : fdt_setprop_u32(blob, chosen, J713_FDT_COMPLETE, 1);
}
