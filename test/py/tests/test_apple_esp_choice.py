# SPDX-License-Identifier: GPL-2.0-or-later OR MIT
"""Host-check which ESP the Apple boot flow picks, and when it tells the OS."""
from pathlib import Path
import subprocess

import pytest

HARNESS = r'''
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#define UUID_STR_LEN 36
#define MAX_SEARCH_PARTITIONS 128
#define PART_EFI_SYSTEM_PARTITION (1 << 2)
#define UCLASS_NVME 1
typedef int ofnode;
struct udevice { int unused; };
struct blk_desc { struct udevice *bdev; };
struct disk_partition { char uuid[UUID_STR_LEN + 1]; int bootable; };
static struct { int uclass_id, devnum, part; } efi_system_partition;

/* Test disk: argv[2..] are "uuid:flags", E = ESP, R = readable,
 * L = EFI/BOOT/BOOTAA64.EFI, V = vendorfw/firmware.cpio. */
static int nparts;
static char *pid[16], *pflags[16];
static const char *named;
static int cur;
static struct udevice nvme;
static struct blk_desc disk = { .bdev = &nvme };

static size_t test_strlcpy(char *d, const char *s, size_t n)
{ snprintf(d, n, "%s", s); return strlen(s); }
#define strlcpy test_strlcpy
static bool has(int p, char c) { return strchr(pflags[p - 1], c); }
static bool of_machine_is_compatible(const char *s) { return false; }
static ofnode ofnode_path(const char *s) { return 1; }
static bool ofnode_valid(ofnode n) { return n; }
static const char *ofnode_get_property(ofnode n, const char *s, int *len)
{ return named; }
static void nvme_scan_namespace(void) {}
static struct blk_desc *blk_get_devnum_by_uclass_id(int id, int devnum)
{ return devnum ? NULL : &disk; }
static struct udevice *dev_get_parent(struct udevice *d) { return d; }
static bool device_is_compatible(struct udevice *d, const char *c) { return true; }
static int part_get_info(struct blk_desc *d, int p, struct disk_partition *info)
{
	if (p > nparts)
		return -1;
	strlcpy(info->uuid, pid[p - 1], sizeof(info->uuid));
	info->bootable = has(p, 'E') ? PART_EFI_SYSTEM_PARTITION : 0;
	return 0;
}
static int fs_set_blk_dev_with_part(struct blk_desc *d, int p)
{ cur = has(p, 'R') ? p : 0; return cur ? 0 : -1; }
static void fs_close(void) { cur = 0; }
static int fs_exists(const char *path)
{
	int p = cur;

	fs_close();
	if (!p)
		return 0;
	if (!strcmp(path, "EFI/BOOT/BOOTAA64.EFI"))
		return has(p, 'L');
	if (!strcmp(path, "vendorfw/firmware.cpio"))
		return has(p, 'V');
	return 0;
}
''' + '@PRODUCTION@' + r'''
int main(int argc, char **argv)
{
	const char *devpart;

	named = strcmp(argv[1], "-") ? argv[1] : NULL;
	for (nparts = 0; nparts + 2 < argc; nparts++) {
		pid[nparts] = argv[nparts + 2];
		pflags[nparts] = strchr(pid[nparts], ':');
		*pflags[nparts]++ = '\0';
	}
	devpart = asahi_esp_devpart();
	printf("RESULT devpart=%s moved=%d uuid=%s\n", devpart[0] ? devpart : "-",
	       asahi_esp_moved, asahi_esp_moved ? asahi_esp_uuid : "-");
	return 0;
}
'''

# UUIDs for the partitions of the test disks.
STUB = '11111111-1111-1111-1111-111111111111'
OS_A = '22222222-2222-2222-2222-222222222222'
OS_B = '33333333-3333-3333-3333-333333333333'
GONE = '44444444-4444-4444-4444-444444444444'
ROOT = '55555555-5555-5555-5555-555555555555'

CASES = [
    # Usual Mac: the named ESP has the EFI loader.
    ('named-loader', OS_A, [f'{OS_A}:ERLV', f'{ROOT}:R'],
     '0:1', False, None),
    # Two installs; the named one has the loader.
    ('named-loader-side-by-side', OS_B, [f'{OS_A}:ERLV', f'{OS_B}:ERLV'],
     '0:2', False, None),
    # The named install has no EFI loader (say an extlinux install) next
    # to an EFI install: never boot the other install.
    ('named-no-loader', OS_A, [f'{OS_A}:ERV', f'{OS_B}:ERLV'],
     '0:1', False, None),
    # J616s: Stage 1 names the AURORA stub ESP, which holds only Stage 2;
    # Fedora has its own ESP.
    ('j616s-stub', STUB, [f'{STUB}:ER', f'{OS_A}:ERLV', f'{ROOT}:R'],
     '0:2', True, OS_A),
    # A stub next to two installs: refuse to guess.
    ('stub-two-installs', STUB, [f'{STUB}:ER', f'{OS_A}:ERLV', f'{OS_B}:ERLV'],
     '0:1', False, None),
    # A stub and nothing with a loader: the stub, as before.
    ('stub-alone', STUB, [f'{STUB}:ER', f'{ROOT}:R'],
     '0:1', False, None),
    # The named ESP is not on the disk and one ESP has a loader.
    ('named-absent-one', GONE, [f'{STUB}:ER', f'{OS_A}:ERLV'],
     '0:2', True, OS_A),
    # The named ESP is not on the disk and two have a loader.
    ('named-absent-two', GONE, [f'{OS_A}:ERLV', f'{OS_B}:ERLV'],
     '0:1', False, None),
    # The named ESP cannot be read: treated as absent.
    ('named-unreadable-one', OS_A, [f'{OS_A}:ELV', f'{OS_B}:ERLV'],
     '0:2', True, OS_B),
    ('named-unreadable-two', OS_A,
     [f'{OS_A}:ELV', f'{OS_B}:ERLV', f'{STUB}:ERLV'],
     '0:1', False, None),
    # Nothing named (m1n1 kboot over the tether): as above.
    ('none-named-one', '-', [f'{STUB}:ER', f'{OS_A}:ERLV'],
     '0:2', True, OS_A),
    ('none-named-two', '-', [f'{OS_A}:ERLV', f'{OS_B}:ERLV'],
     '0:1', False, None),
    # Matching ignores UUID case, as before.
    ('named-upper-case', OS_B.upper(), [f'{OS_A}:ERLV', f'{OS_B}:ERLV'],
     '0:2', False, None),
    # No ESP at all.
    ('no-esp', OS_A, [f'{ROOT}:R'], '-', False, None),
]


@pytest.fixture(scope='module')
def esp_binary(tmp_path_factory):
    repo = Path(__file__).resolve().parents[3]
    source = (repo / 'arch/arm/mach-apple/board.c').read_text()
    start = source.index('#define ASAHI_EFI_LOADER')
    production = source[start:source.index('const char *env_fat_get_intf(void)', start)]
    tmp = tmp_path_factory.mktemp('apple-esp')
    c = tmp / 'apple-esp.c'
    c.write_text(HARNESS.replace('@PRODUCTION@', production))
    binary = tmp / 'apple-esp'
    subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                    '-Wno-unused-parameter', '-Wno-unused-function',
                    str(c), '-o', str(binary)], check=True)
    return binary


@pytest.mark.parametrize('name,named,parts,devpart,moved,uuid', CASES,
                         ids=[c[0] for c in CASES])
def test_apple_esp_choice(esp_binary, name, named, parts, devpart, moved, uuid):
    out = subprocess.run([str(esp_binary), named] + parts, check=True,
                         capture_output=True, text=True).stdout
    result = out.splitlines()[-1]
    assert result == (f'RESULT devpart={devpart} moved={int(moved)} '
                      f'uuid={uuid if moved else "-"}'), out
    # Every choice other than the plain named ESP says what it saw.
    if devpart != '-' and not (named != '-' and name.startswith('named-loader')):
        assert out.startswith('ESP: named ESP '), out
    if moved:
        assert 'telling the OS' in out, out
