#!/usr/bin/env python3
"""
Voice.xp3 Complete Decryptor
=============================
Verified: sf field = absolute file offset, adlr key = Cx key seed
First successful decryption: "other/TestVo_1.opus" -> valid OGG Opus file!

Usage:
  python3 decrypt_voice.py <voice.xp3> [output_dir] [--dry-run]
"""

import sys, os
from pathlib import Path

# ============================================================
# Cx Decryption Algorithm (VERIFIED)
# ============================================================
def generate_key_schedule(seed: int) -> bytes:
    """Generate 31-byte key schedule from 32-bit Cx key seed."""
    state = (seed & 0x7FFFFFFF) | ((seed << 31) & 0xFFFFFFFF)
    state &= 0xFFFFFFFF
    key = bytearray(31)
    for i in range(31):
        key[i] = state & 0xFF
        state = ((state >> 8) | ((state & 0xFFFFFFFE) << 23)) & 0xFFFFFFFF
    return bytes(key)


def decrypt(data: bytes, seed: int) -> bytes:
    """Decrypt data using Cx algorithm with simple sequential key."""
    key = generate_key_schedule(seed)
    return bytes(data[i] ^ key[i % 31] for i in range(len(data)))


# ============================================================
# XP3 Index Parser
# ============================================================
def parse_xp3(filepath: str) -> list:
    """
    Parse XP3 archive index.
    Returns list of dicts: {offset, size, key, filename, enc_flag}
    """
    with open(filepath, 'rb') as f:
        f.seek(0, 2)
        fsize = f.tell()
        # Read last 2MB for the index
        f.seek(max(0, fsize - 2_000_000))
        index_data = f.read(min(2_000_000, fsize))

    entries = []
    pos = 0

    while True:
        # Find next "File" marker
        fpos = index_data.find(b'File', pos)
        if fpos < 0:
            break

        fsize = int.from_bytes(index_data[fpos + 4:fpos + 8], 'little')

        # Find info section
        ipos = index_data.find(b'info', fpos, fpos + fsize + 8)
        if ipos < 0:
            pos = fpos + 4
            continue

        isize = int.from_bytes(index_data[ipos + 4:ipos + 8], 'little')
        info = index_data[ipos + 8:ipos + 8 + isize]

        # Extract encryption flag (0x80000000 = encrypted)
        enc_flag = int.from_bytes(info[4:8], 'little')

        # Extract original size (may be larger than compressed)
        orig_size = int.from_bytes(info[8:12], 'little')

        # Extract filename (wide string at info[0x18], may overflow info)
        fname = "unknown"
        if len(info) >= 0x1C:
            try:
                flen = int.from_bytes(info[0x18:0x1A], 'little')
                if flen > 0 and flen < 256:
                    # Read from info + overflow into subsequent data
                    fraw = bytearray(flen * 2)
                    name_start = ipos + 8 + 0x1A
                    for bi in range(flen * 2):
                        pos_in_chunk = name_start + bi
                        if pos_in_chunk < len(index_data):
                            fraw[bi] = index_data[pos_in_chunk]
                    fname = fraw.decode('utf-16-le', errors='replace')
            except:
                pass
        # Fix truncated .opus filenames
        if fname.endswith('.op') and not fname.endswith('.opus'):
            fname = fname + 'us'

        # Find segm section
        spos = index_data.find(b'segm', fpos, fpos + fsize + 8)
        sf = 0
        comp_size = 0
        if spos >= 0:
            segm_data = index_data[spos + 8:spos + 8 + 0x1C]
            sf = int.from_bytes(segm_data[8:12], 'little')
            comp_size = int.from_bytes(segm_data[0x18:0x1C], 'little')

        # Find adlr section and extract key (4 bytes after adlr data)
        apos = index_data.find(b'adlr', fpos, fpos + fsize + 8)
        key_seed = 0
        if apos >= 0:
            asize = int.from_bytes(index_data[apos + 4:apos + 8], 'little')
            key_pos = apos + 8 + asize
            if key_pos + 4 <= len(index_data):
                key_seed = int.from_bytes(index_data[key_pos:key_pos + 4], 'little')

        entries.append({
            'offset': sf,           # sf = absolute offset in XP3 file
            'comp_size': comp_size,
            'orig_size': orig_size,
            'key_seed': key_seed,
            'filename': fname,
            'enc_flag': enc_flag,
        })

        pos = fpos + fsize + 8

    return entries


# ============================================================
# Main
# ============================================================
def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    xp3_path = sys.argv[1]
    out_dir = sys.argv[2] if len(sys.argv) > 2 else './decrypted'
    dry_run = '--dry-run' in sys.argv

    print(f"[*] Parsing {xp3_path}...")
    entries = parse_xp3(xp3_path)
    print(f"[*] Found {len(entries)} files")

    # Stats
    encrypted = [e for e in entries if e['enc_flag'] == 0x80000000]
    voice = [e for e in encrypted if '.opus' in e.get('filename', '')]
    print(f"[*] Encrypted: {len(encrypted)}, Voice (.opus): {len(voice)}")

    if dry_run:
        print("\n[*] Dry run - listing voice files:")
        for e in voice[:20]:
            print(f"  {e['filename']}: offset=0x{e['offset']:X} size={e['comp_size']} key=0x{e['key_seed']:08X}")
        print(f"  ... and {len(voice) - 20} more")
        return

    # Decrypt
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    success = 0
    total = len(voice)

    print(f"\n[*] Decrypting {total} voice files to {out_dir}/ ...")

    with open(xp3_path, 'rb') as f:
        for i, e in enumerate(voice):
            # Skip entries without valid offsets
            if e['offset'] == 0 or e['comp_size'] == 0:
                continue

            try:
                f.seek(e['offset'])
                enc = f.read(e['comp_size'])
                dec = decrypt(enc, e['key_seed'])

                # Verify OGG header
                if dec[:4] != b'OggS':
                    print(f"  [!] {e['filename']}: bad header {dec[:4].hex()} (key=0x{e['key_seed']:08X})")
                    continue

                # Save
                safe_name = e['filename'].replace('/', '_').replace('\\', '_')
                out_path = os.path.join(out_dir, safe_name)
                with open(out_path, 'wb') as of:
                    of.write(dec)

                success += 1
                if success <= 5 or success % 50 == 0:
                    print(f"  [{success}/{total}] {e['filename']} -> {safe_name} ({len(dec)} bytes)")

            except Exception as ex:
                print(f"  [!] {e['filename']}: {ex}")

    print(f"\n[*] Done: {success}/{total} files decrypted successfully")
    if success > 0:
        print(f"[*] Output: {os.path.abspath(out_dir)}")


if __name__ == '__main__':
    main()
