// SPDX-License-Identifier: GPL-2.0-only
/*
 * Safe UFS 4K-sector GPT splitter for Lenovo Xiaoxin Pad Pro GT 13 (TB378FC).
 * Shrinks stock Android `userdata` (entry [17] / sda18) and creates `ubuntu`
 * (entry [18] / sda19) with primary + backup GPT CRC32 verification and
 * backup-first atomic write ordering.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <linux/fs.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

#define BSIZE 4096ULL
#define BLOCKS_PER_GIB 262144ULL
#define MIN_UBUNTU_GIB 40ULL
#define MAX_UBUNTU_GIB 180ULL
#define MIN_USERDATA_GIB 48ULL
#define EXPECTED_USERDATA_FIRST 6365608ULL

static uint32_t crc32_calc(const void *data, size_t len) {
    const uint8_t *p = (const uint8_t *)data;
    uint32_t crc = 0xFFFFFFFFU;
    for (size_t i = 0; i < len; i++) {
        crc ^= p[i];
        for (int b = 0; b < 8; b++)
            crc = (crc >> 1) ^ (0xEDB88320U & (-(int32_t)(crc & 1U)));
    }
    return ~crc;
}

static void encode_utf16le(uint8_t *dst, size_t max_bytes, const char *ascii) {
    memset(dst, 0, max_bytes);
    for (size_t i = 0; ascii[i] && (i + 1) * 2 <= max_bytes; i++) {
        dst[i * 2] = (uint8_t)ascii[i];
        dst[i * 2 + 1] = 0;
    }
}

static int has_utf16le_name(const uint8_t *src, const char *ascii) {
    uint8_t expected[72];
    encode_utf16le(expected, sizeof(expected), ascii);
    return memcmp(src, expected, sizeof(expected)) == 0;
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "Usage: %s <block-dev-or-img> [--ubuntu-gib <40..180>] [--apply]\n", argv[0]);
        return 2;
    }
    const char *dev = argv[1];
    uint64_t ubuntu_gib = 120ULL;
    int apply = 0;

    for (int i = 2; i < argc; i++) {
        if (strcmp(argv[i], "--apply") == 0) {
            apply = 1;
        } else if (strcmp(argv[i], "--ubuntu-gib") == 0 && i + 1 < argc) {
            char *endp = NULL;
            unsigned long long v = strtoull(argv[++i], &endp, 10);
            if (!endp || *endp != '\0' || v < MIN_UBUNTU_GIB || v > MAX_UBUNTU_GIB) {
                fprintf(stderr, "ERROR: --ubuntu-gib must be in [%llu..%llu]\n",
                        (unsigned long long)MIN_UBUNTU_GIB, (unsigned long long)MAX_UBUNTU_GIB);
                return 2;
            }
            ubuntu_gib = (uint64_t)v;
        } else {
            fprintf(stderr, "ERROR: Unknown argument: %s\n", argv[i]);
            return 2;
        }
    }

    int fd = open(dev, apply ? O_RDWR : O_RDONLY);
    if (fd < 0) {
        perror("open");
        return 1;
    }

    uint8_t pri_hdr[BSIZE];
    if (pread(fd, pri_hdr, BSIZE, BSIZE) != (ssize_t)BSIZE) {
        perror("pread pri_hdr");
        close(fd);
        return 1;
    }
    if (memcmp(pri_hdr, "EFI PART", 8) != 0) {
        fprintf(stderr, "ERROR: Missing EFI PART at LBA 1 (expected 4096-byte UFS sectors)\n");
        close(fd);
        return 1;
    }

    uint32_t hdr_sz, orig_hdr_crc, num_entries, entry_sz, orig_ent_crc;
    uint64_t my_lba, alt_lba, first_usable, last_usable, part_lba;
    memcpy(&hdr_sz, pri_hdr + 12, 4);
    memcpy(&orig_hdr_crc, pri_hdr + 16, 4);
    memcpy(&my_lba, pri_hdr + 24, 8);
    memcpy(&alt_lba, pri_hdr + 32, 8);
    memcpy(&first_usable, pri_hdr + 40, 8);
    memcpy(&last_usable, pri_hdr + 48, 8);
    memcpy(&part_lba, pri_hdr + 72, 8);
    memcpy(&num_entries, pri_hdr + 80, 4);
    memcpy(&entry_sz, pri_hdr + 84, 4);
    memcpy(&orig_ent_crc, pri_hdr + 88, 4);

    if (hdr_sz != 92 || my_lba != 1 || part_lba != 2 || num_entries < 20 || num_entries > 128 || entry_sz != 128) {
        fprintf(stderr, "ERROR: Unexpected GPT geometry hdr=%u my=%llu part=%llu n=%u esz=%u\n",
                hdr_sz, (unsigned long long)my_lba, (unsigned long long)part_lba, num_entries, entry_sz);
        close(fd);
        return 1;
    }

    uint8_t tmp_hdr[BSIZE];
    memcpy(tmp_hdr, pri_hdr, BSIZE);
    memset(tmp_hdr + 16, 0, 4);
    if (crc32_calc(tmp_hdr, hdr_sz) != orig_hdr_crc) {
        fprintf(stderr, "ERROR: Primary GPT header CRC mismatch\n");
        close(fd);
        return 1;
    }

    size_t tbl_bytes = (size_t)num_entries * entry_sz;
    uint8_t *entries = calloc(1, tbl_bytes);
    if (!entries) {
        close(fd);
        return 1;
    }
    if (pread(fd, entries, tbl_bytes, part_lba * BSIZE) != (ssize_t)tbl_bytes) {
        perror("pread entries");
        free(entries);
        close(fd);
        return 1;
    }
    if (crc32_calc(entries, tbl_bytes) != orig_ent_crc) {
        fprintf(stderr, "ERROR: Primary GPT entries CRC mismatch\n");
        free(entries);
        close(fd);
        return 1;
    }

    uint8_t *e17 = entries + 17 * 128;
    uint8_t *e18 = entries + 18 * 128;
    uint64_t u_first, u_last, b_first, b_last;
    memcpy(&u_first, e17 + 32, 8);
    memcpy(&u_last, e17 + 40, 8);
    memcpy(&b_first, e18 + 32, 8);
    memcpy(&b_last, e18 + 40, 8);

    printf("Current [17]: LBA %llu..%llu, [18]: LBA %llu..%llu (first_usable=%llu last_usable=%llu alt_lba=%llu)\n",
           (unsigned long long)u_first, (unsigned long long)u_last,
           (unsigned long long)b_first, (unsigned long long)b_last,
           (unsigned long long)first_usable, (unsigned long long)last_usable,
           (unsigned long long)alt_lba);

    if (!has_utf16le_name(e17 + 56, "userdata") || u_first != EXPECTED_USERDATA_FIRST) {
        fprintf(stderr, "ERROR: Entry [17] is not TB378FC stock userdata at LBA %llu\n",
                (unsigned long long)EXPECTED_USERDATA_FIRST);
        free(entries);
        close(fd);
        return 1;
    }

    if (has_utf16le_name(e18 + 56, "ubuntu") && b_first == u_last + 1 && b_last > b_first && b_last <= last_usable) {
        double ugib = (double)(u_last - u_first + 1) / (double)BLOCKS_PER_GIB;
        double lgib = (double)(b_last - b_first + 1) / (double)BLOCKS_PER_GIB;
        printf("ALREADY_SPLIT_OK: userdata=%.2fGiB + ubuntu=%.2fGiB\n", ugib, lgib);
        free(entries);
        close(fd);
        return 0;
    }

    for (int i = 0; i < 16; i++) {
        if (e18[i] != 0) {
            fprintf(stderr, "ERROR: GPT entry [18] is already occupied by a non-ubuntu partition\n");
            free(entries);
            close(fd);
            return 1;
        }
    }

    uint64_t want_blocks = ubuntu_gib * BLOCKS_PER_GIB;
    if (u_last <= u_first + want_blocks + MIN_USERDATA_GIB * BLOCKS_PER_GIB) {
        fprintf(stderr, "ERROR: Not enough space in userdata (%llu blocks) for %llu GiB ubuntu + >=%llu GiB userdata\n",
                (unsigned long long)(u_last - u_first + 1),
                (unsigned long long)ubuntu_gib,
                (unsigned long long)MIN_USERDATA_GIB);
        free(entries);
        close(fd);
        return 1;
    }

    /* Align new ubuntu start LBA to 1 MiB (256 * 4096-byte blocks) */
    uint64_t new_ubuntu_first = ((u_last + 1ULL - want_blocks + 128ULL) & ~255ULL);
    uint64_t new_ubuntu_last = u_last;
    uint64_t new_userdata_last = new_ubuntu_first - 1ULL;

    memcpy(e17 + 40, &new_userdata_last, 8);

    static const uint8_t linux_fs_guid[16] = {
        0xAF, 0x3D, 0xC6, 0x0F, 0x83, 0x84, 0x72, 0x47,
        0x8E, 0x79, 0x3D, 0x69, 0xD8, 0x47, 0x7D, 0xE4
    };
    static const uint8_t ubuntu_uniq_guid[16] = {
        0x78, 0x37, 0xB3, 0x54, 0x12, 0x30, 0x90, 0x4A,
        0x9C, 0x55, 0x01, 0x20, 0x26, 0x04, 0x01, 0x20
    };
    memset(e18, 0, 128);
    memcpy(e18 + 0, linux_fs_guid, 16);
    memcpy(e18 + 16, ubuntu_uniq_guid, 16);
    uint64_t nb_flags = 0;
    memcpy(e18 + 32, &new_ubuntu_first, 8);
    memcpy(e18 + 40, &new_ubuntu_last, 8);
    memcpy(e18 + 48, &nb_flags, 8);
    encode_utf16le(e18 + 56, 72, "ubuntu");

    uint32_t new_ent_crc = crc32_calc(entries, tbl_bytes);

    uint8_t new_pri[BSIZE];
    memcpy(new_pri, pri_hdr, BSIZE);
    memcpy(new_pri + 88, &new_ent_crc, 4);
    memset(new_pri + 16, 0, 4);
    uint32_t new_pri_crc = crc32_calc(new_pri, hdr_sz);
    memcpy(new_pri + 16, &new_pri_crc, 4);

    uint8_t bak_hdr[BSIZE];
    if (pread(fd, bak_hdr, BSIZE, alt_lba * BSIZE) != (ssize_t)BSIZE ||
        memcmp(bak_hdr, "EFI PART", 8) != 0) {
        fprintf(stderr, "ERROR: Missing backup EFI PART at alt_lba %llu\n", (unsigned long long)alt_lba);
        free(entries);
        close(fd);
        return 1;
    }
    uint64_t bak_part_lba;
    memcpy(&bak_part_lba, bak_hdr + 72, 8);
    memcpy(bak_hdr + 88, &new_ent_crc, 4);
    memset(bak_hdr + 16, 0, 4);
    uint32_t new_bak_crc = crc32_calc(bak_hdr, hdr_sz);
    memcpy(bak_hdr + 16, &new_bak_crc, 4);

    printf("Plan OK: [17] userdata -> %llu..%llu (%.2f GiB), [18] ubuntu -> %llu..%llu (%.2f GiB)\n",
           (unsigned long long)u_first, (unsigned long long)new_userdata_last,
           (double)(new_userdata_last - u_first + 1) / (double)BLOCKS_PER_GIB,
           (unsigned long long)new_ubuntu_first, (unsigned long long)new_ubuntu_last,
           (double)(new_ubuntu_last - new_ubuntu_first + 1) / (double)BLOCKS_PER_GIB);

    if (!apply) {
        printf("DRY_RUN_COMPLETE (pass --apply to write)\n");
        free(entries);
        close(fd);
        return 0;
    }

    if (pwrite(fd, entries, tbl_bytes, bak_part_lba * BSIZE) != (ssize_t)tbl_bytes) { perror("pwrite bak entries"); free(entries); close(fd); return 1; }
    if (pwrite(fd, bak_hdr, BSIZE, alt_lba * BSIZE) != (ssize_t)BSIZE) { perror("pwrite bak hdr"); free(entries); close(fd); return 1; }
    if (fsync(fd) != 0) { perror("fsync bak"); free(entries); close(fd); return 1; }

    if (pwrite(fd, entries, tbl_bytes, part_lba * BSIZE) != (ssize_t)tbl_bytes) { perror("pwrite pri entries"); free(entries); close(fd); return 1; }
    if (pwrite(fd, new_pri, BSIZE, BSIZE) != (ssize_t)BSIZE) { perror("pwrite pri hdr"); free(entries); close(fd); return 1; }
    if (fsync(fd) != 0) { perror("fsync pri"); free(entries); close(fd); return 1; }

    ioctl(fd, BLKRRPART, 0);
    free(entries);
    close(fd);
    printf("APPLIED_AND_SYNCED_OK\n");
    return 0;
}
