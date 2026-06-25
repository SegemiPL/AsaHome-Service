#!/usr/bin/env python3
"""
Complete voice.xp3 decryption toolkit.

STATUS:
  - Cx LFSR algorithm: VERIFIED (matches x64dbg runtime)
  - XP3 structure: PARSED (14468 entries, 1.68GB)
  - Key seed location: FOUND (after adlr section, before "File" marker)
  - Key-to-entry mapping: 1-to-1 by index order (VERIFIED for data.xp3)
  - Decryption: NEEDS FINAL VERIFICATION

USAGE:
  python3 extract_keys.py voice.xp3 [--decrypt output_dir]

The script extracts keys and attempts decryption.
If the keys are correct, OggS files will be found.
"""

import sys, os

def generate_key(seed):
    state = (seed & 0x7FFFFFFF) | ((seed << 31) & 0xFFFFFFFF)
    state &= 0xFFFFFFFF
    key = bytearray(31)
    for i in range(31):
        key[i] = state & 0xFF
        state = ((state >> 8) | ((state & 0xFFFFFFFE) << 23)) & 0xFFFFFFFF
    return bytes(key)

def parse_archive(filepath):
    with open(filepath, 'rb') as f:
        f.seek(0, 2); fsize = f.tell()
        f.seek(max(0, fsize - 2_000_000))
        data = f.read(min(2_000_000, fsize))

    # Parse segms (file sizes for offset calculation)
    pos = 0; sizes = []
    while True:
        p = data.find(b'segm\x1c\x00\x00\x00', pos)
        if p < 0: break
        sizes.append(int.from_bytes(data[p+0x20: p+0x24], 'little'))
        pos = p + 4

    # Compute cumulative offsets from data start (0x28)
    off = 0x28; offsets = [off]
    for sz in sizes: off += sz; offsets.append(off)

    # Parse keys from after adlr section
    pos = 0; keys = []; filenames = []
    while True:
        apos = data.find(b'adlr', pos)
        if apos < 0: break
        asize = int.from_bytes(data[apos+4:apos+8], 'little')
        dend = apos + 8 + asize
        fpos = data.find(b'File', dend)
        if fpos < 0 or fpos - dend < 4:
            pos = apos + 4; continue
        keys.append(int.from_bytes(data[dend:dend+4], 'little'))

        # Get filename from info section after File
        ipos = data.find(b'info', fpos)
        fname = "?"
        if ipos >= 0 and ipos < fpos + 200:
            isize = int.from_bytes(data[ipos+4:ipos+8], 'little')
            info = data[ipos+8:ipos+8+isize]
            if len(info) >= 0x1C:
                flen = int.from_bytes(info[0x18:0x1A], 'little')
                fraw = info[0x1A:0x1A+flen*2]
                try: fname = fraw.decode('utf-16-le')
                except: pass
        filenames.append(fname)
        pos = apos + 4

    return sizes, offsets, keys, filenames

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    path = sys.argv[1]
    sizes, offsets, keys, filenames = parse_archive(path)

    print(f"File: {path}")
    print(f"Entries: {len(sizes)} segms, {len(keys)} keys, {len(filenames)} names")
    print()

    # Show entry mapping
    voice_entries = []
    with open(path, 'rb') as f:
        for i in range(len(keys)):
            if i >= len(offsets): break
            o = offsets[i]; sz = sizes[i]; k = keys[i]; fn = filenames[i]

            # Only process entries with data in the data section (< index start)
            if o > 0x69000000: continue

            is_voice = '.opus' in fn or '.ogg' in fn
            if is_voice:
                voice_entries.append(i)

            if i < 20 or is_voice:
                f.seek(o); header = f.read(4)
                print(f"[{i}] offset=0x{o:010X} size=0x{sz:06X} "
                      f"key=0x{k:08X} enc_hdr={header.hex()} name={fn}")

    print(f"\nVoice entries in data section: {len(voice_entries)}")

    # Try decryption on voice entries
    if '--decrypt' in sys.argv:
        outdir = sys.argv[sys.argv.index('--decrypt') + 1]
        os.makedirs(outdir, exist_ok=True)
        with open(path, 'rb') as f:
            for i in voice_entries[:5]:
                o = offsets[i]; sz = sizes[i]; k = keys[i]; fn = filenames[i]
                f.seek(o); enc = f.read(sz)
                key = generate_key(k)
                dec = bytes(enc[j] ^ key[j % 31] for j in range(sz))
                outname = fn.replace('/', '_')
                with open(f"{outdir}/{outname}", 'wb') as of:
                    of.write(dec)
                print(f"Saved: {outname} ({sz} bytes)")

if __name__ == '__main__':
    main()
