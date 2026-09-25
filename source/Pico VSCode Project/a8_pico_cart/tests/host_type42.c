/* Exercise the production loader with the real upstream FatFs and a
 * fragmented simulated flash disk. This does not model Atari bus timing. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
#include "type42_image.h"
#include "diskio.h"

#define DISK_SECTORS 16384u
static uint8_t disk_bytes[DISK_SECTORS * 512];
static uint8_t cache[128 * 1024];
static unsigned permutation = 1, pointer_calls, fail_pointer_call;
static unsigned verified_files, verified_bytes;
static FATFS fs;

static uint8_t *disk_sector(unsigned sector) {
    assert(sector < DISK_SECTORS);
    return disk_bytes + ((sector * permutation) & (DISK_SECTORS - 1)) * 512;
}
const uint8_t *flash_fs_sector_pointer(uint32_t sector) {
    if (++pointer_calls == fail_pointer_call) return NULL;
    return sector < DISK_SECTORS ? disk_sector(sector) : NULL;
}
DSTATUS disk_initialize(BYTE p) { return p ? STA_NOINIT : 0; }
DSTATUS disk_status(BYTE p) { return p ? STA_NOINIT : 0; }
DRESULT disk_read(BYTE p, BYTE *buf, LBA_t s, UINT n) {
    if (p || s >= DISK_SECTORS || n > DISK_SECTORS - s) return RES_PARERR;
    for (UINT i = 0; i < n; ++i) memcpy(buf + i * 512, disk_sector(s + i), 512);
    return RES_OK;
}
DRESULT disk_write(BYTE p, const BYTE *buf, LBA_t s, UINT n) {
    if (p || s >= DISK_SECTORS || n > DISK_SECTORS - s) return RES_PARERR;
    for (UINT i = 0; i < n; ++i) memcpy(disk_sector(s + i), buf + i * 512, 512);
    return RES_OK;
}
DRESULT disk_ioctl(BYTE p, BYTE cmd, void *out) {
    if (p) return RES_PARERR;
    switch (cmd) {
        case CTRL_SYNC: return RES_OK;
        case GET_SECTOR_COUNT: *(LBA_t *)out = DISK_SECTORS; return RES_OK;
        case GET_SECTOR_SIZE: *(WORD *)out = 512; return RES_OK;
        case GET_BLOCK_SIZE: *(DWORD *)out = 1; return RES_OK;
        default: return RES_PARERR;
    }
}
static void make_disk(const uint8_t *data, size_t size, int fragmented) {
    uint8_t work[4096], fill[8192] = {0};
    FIL game, filler;
    MKFS_PARM options = { FM_FAT | FM_SFD, 1, 0, 0, 512 };
    assert(f_mount(NULL, "", 0) == FR_OK);
    memset(disk_bytes, 0, sizeof disk_bytes);
    permutation = fragmented ? 97 : 1;
    pointer_calls = fail_pointer_call = 0;
    assert(f_mkfs("", &options, work, sizeof work) == FR_OK);
    assert(f_mount(&fs, "", 1) == FR_OK);
    assert(f_open(&game, "GAME.CAR", FA_WRITE | FA_CREATE_ALWAYS) == FR_OK);
    assert(f_open(&filler, "FILLER.BIN", FA_WRITE | FA_CREATE_ALWAYS) == FR_OK);
    for (size_t offset = 0; offset < size;) {
        UINT wrote, amount = size - offset > 8192 ? 8192 : (UINT)(size - offset);
        assert(f_write(&game, data + offset, amount, &wrote) == FR_OK && wrote == amount);
        offset += amount;
        if (fragmented)
            assert(f_write(&filler, fill, sizeof fill, &wrote) == FR_OK && wrote == sizeof fill);
    }
    assert(f_close(&game) == FR_OK);
    assert(f_close(&filler) == FR_OK);
}
static void check_image(const uint8_t *data, size_t size, int fragmented) {
    FIL game;
    char error[40] = {0};
    make_disk(data, size, fragmented);
    assert(f_open(&game, "GAME.CAR", FA_READ) == FR_OK);
    if (!type42_load_image(&game, cache, error)) {
        fprintf(stderr, "Unexpected load failure: %s\n", error);
        abort();
    }
    assert(f_close(&game) == FR_OK);
    unsigned cache_pages = 0, direct_banks = 0;
    for (unsigned page = 0; page < TYPE42_FILE_PAGES; ++page) {
        uintptr_t p = (uintptr_t)type42_pages[page];
        if (p >= (uintptr_t)cache && p < (uintptr_t)(cache + sizeof cache)) ++cache_pages;
    }
    assert(cache_pages == TYPE42_CACHE_PAGES);
    for (unsigned bank = 0; bank < 128; ++bank) {
        if (type42_bank_base[bank]) ++direct_banks;
        for (unsigned address = 0; address < 8192; ++address) {
            unsigned file_offset = 16 + bank * 8192 + address;
            assert(type42_pages[file_offset >> 9][file_offset & 511] == data[file_offset]);
            if (type42_bank_base[bank])
                assert(type42_bank_base[bank][address] == data[file_offset]);
            ++verified_bytes;
        }
    }
    assert(direct_banks >= 15); /* pinned tail, including initial bank 127 */
    assert(fragmented ? direct_banks < 128 : direct_banks > 100);
    ++verified_files;
}
static void check_rejected(const uint8_t *data, size_t size, unsigned fail_page) {
    FIL game;
    char error[40] = {0};
    make_disk(data, size, 1);
    fail_pointer_call = fail_page;
    assert(f_open(&game, "GAME.CAR", FA_READ) == FR_OK);
    assert(!type42_load_image(&game, cache, error));
    assert(error[0]);
    assert(f_close(&game) == FR_OK);
    for (unsigned i = 0; i < TYPE42_FILE_PAGES; ++i) assert(type42_pages[i] == NULL);
    for (unsigned i = 0; i < 128; ++i) assert(type42_bank_base[i] == NULL);
}
static void put_be32(uint8_t *p, uint32_t value) {
    p[0] = value >> 24; p[1] = value >> 16; p[2] = value >> 8; p[3] = value;
}
int main(int argc, char **argv) {
    const size_t size = TYPE42_ROM_BYTES + 16;
    uint8_t *image = malloc(size + 1);
    assert(image);
    memset(image, 0, 16);
    memcpy(image, "CART", 4);
    put_be32(image + 4, 42);
    uint32_t sum = 0;
    for (unsigned i = 0; i < TYPE42_ROM_BYTES; ++i) {
        image[i + 16] = (uint8_t)((i * 37) ^ (i >> 8) ^ (i >> 13));
        sum += image[i + 16];
    }
    put_be32(image + 8, sum);
    check_image(image, size, 0);
    check_image(image, size, 1);
    check_rejected(image, size - 1, 0);
    image[size] = 0xff;
    check_rejected(image, size + 1, 0);
    image[0] = 'X'; check_rejected(image, size, 0); image[0] = 'C';
    image[7] = 61; check_rejected(image, size, 0); image[7] = 42;
    image[4] = 1; check_rejected(image, size, 0); image[4] = 0;
    image[8] ^= 1; check_rejected(image, size, 0); image[8] ^= 1;
    image[600] ^= 1; check_rejected(image, size, 0); image[600] ^= 1;
    check_rejected(image, size, 17);
    for (int arg = 1; arg < argc; ++arg) {
        FILE *input = fopen(argv[arg], "rb");
        assert(input);
        assert(fread(image, 1, size + 1, input) == size);
        fclose(input);
        check_image(image, size, 1);
        printf("PASS unchanged image: %s\n", argv[arg]);
    }
    printf("PASS %u images, %u byte comparisons; 8 rejection cases; real FatFs fragmentation\n",
           verified_files, verified_bytes);
    free(image);
    return 0;
}
