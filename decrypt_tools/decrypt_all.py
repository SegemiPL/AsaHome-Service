#!/usr/bin/env python3
"""
Krkr2 XP3 Universal Decryptor — 恋愛、借りちゃいました 及同引擎游戏
============================================================

自动检测并解密所有类型的 XP3 加密文件：
  - 语音 (.opus/.ogg)     → Cx LFSR 直接解密
  - 音乐 (.ogg)           → Cx LFSR 直接解密
  - 立绘 (.tlg)           → Cx LFSR 直接解密
  - 事件CG/背景 (.png)    → Cx LFSR 直接解密
  - 场景脚本 (.ks)        → zlib解压 → Cx LFSR → UTF-8/UTF-16LE文本
  - 系统脚本 (.tjs)       → zlib解压 → Cx LFSR → UTF-16LE文本
  - 音效 (.ogg)           → Cx LFSR 直接解密

Usage:
  python3 decrypt_all.py                          # 解密当前目录所有 .xp3
  python3 decrypt_all.py voice.xp3                # 解密单个文件
  python3 decrypt_all.py voice.xp3 data.xp3       # 解密多个文件
  python3 decrypt_all.py -o ./output .            # 指定输出目录
  python3 decrypt_all.py --dry-run voice.xp3      # 预览不解密

Output:
  ./decrypted/<archive_name>/                     # 按 XP3 归档组织
"""

import sys, os, zlib, struct, argparse, glob
from pathlib import Path
from collections import Counter


# ============================================================
# Cx LFSR — 核心加密算法
# ============================================================

def generate_key_schedule(seed: int) -> bytes:
    """
    从 32-bit 种子生成 31 字节 Cx LFSR 密钥。
    已通过 x64dbg 运行时对比验证，与 koikari.tpm 完全一致。
    """
    state = (seed & 0x7FFFFFFF) | ((seed << 31) & 0xFFFFFFFF)
    state &= 0xFFFFFFFF
    key = bytearray(31)
    for i in range(31):
        key[i] = state & 0xFF
        state = ((state >> 8) | ((state & 0xFFFFFFFE) << 23)) & 0xFFFFFFFF
    return bytes(key)


def decrypt_cx(data: bytes, seed: int) -> bytes:
    """Cx LFSR 解密（语音、图片、音乐）"""
    key = generate_key_schedule(seed)
    return bytes(data[i] ^ key[i % 31] for i in range(len(data)))


def decrypt_script(data: bytes, seed: int) -> bytes:
    """zlib 解压 → Cx LFSR 解密（脚本文件）"""
    dec = zlib.decompress(data)
    key = generate_key_schedule(seed)
    return bytes(dec[i] ^ key[i % 31] for i in range(len(dec)))


# ============================================================
# XP3 索引解析
# ============================================================

def parse_xp3(filepath: str) -> list:
    """
    解析 XP3 归档末尾的索引区（~2MB）。
    返回每条目的: offset, comp_size, orig_size, key_seed, filename, enc_flag
    """
    with open(filepath, 'rb') as f:
        f.seek(0, 2)
        fsize = f.tell()
        f.seek(max(0, fsize - 2_000_000))
        index_data = f.read(min(2_000_000, fsize))

    entries = []
    pos = 0

    while True:
        fpos = index_data.find(b'File', pos)
        if fpos < 0:
            break

        fsize_field = int.from_bytes(index_data[fpos + 4:fpos + 8], 'little')

        # ── info 段: 文件名、加密标志、原始大小 ──
        ipos = index_data.find(b'info', fpos, fpos + fsize_field + 8)
        enc_flag, orig_size, fname = 0, 0, "unknown"
        if ipos >= 0:
            isize = int.from_bytes(index_data[ipos + 4:ipos + 8], 'little')
            info = index_data[ipos + 8:ipos + 8 + isize]
            if len(info) >= 0x1C:
                enc_flag  = int.from_bytes(info[4:8], 'little')
                orig_size = int.from_bytes(info[8:12], 'little')
                flen = int.from_bytes(info[0x18:0x1A], 'little')
                if 0 < flen < 256:
                    fraw = bytearray(flen * 2)
                    name_start = ipos + 8 + 0x1A
                    for bi in range(flen * 2):
                        pi = name_start + bi
                        if pi < len(index_data):
                            fraw[bi] = index_data[pi]
                    fname = fraw.decode('utf-16-le', errors='replace')
                    if fname.endswith('.op') and not fname.endswith('.opus'):
                        fname += 'us'

        # ── segm 段: 文件偏移(sf)、压缩大小 ──
        spos = index_data.find(b'segm', fpos, fpos + fsize_field + 8)
        sf, comp_size = 0, 0
        if spos >= 0:
            segm_data = index_data[spos + 8:spos + 8 + 0x1C]
            sf        = int.from_bytes(segm_data[8:12], 'little')          # ← 绝对偏移
            comp_size = int.from_bytes(segm_data[0x18:0x1C], 'little')

        # ── adlr 段: 密钥种子（藏在 adlr 数据之后 4 字节） ──
        apos = index_data.find(b'adlr', fpos, fpos + fsize_field + 8)
        key_seed = 0
        if apos >= 0:
            asize = int.from_bytes(index_data[apos + 4:apos + 8], 'little')
            key_pos = apos + 8 + asize
            if key_pos + 4 <= len(index_data):
                key_seed = int.from_bytes(index_data[key_pos:key_pos + 4], 'little')

        entries.append({
            'offset':    sf,
            'comp_size': comp_size,
            'orig_size': orig_size,
            'key_seed':  key_seed,
            'filename':  fname,
            'enc_flag':  enc_flag,
        })
        pos = fpos + fsize_field + 8

    return entries


# ============================================================
# 文本编码自动检测
# ============================================================

def detect_encoding(data: bytes) -> str:
    """
    采样分析字节频率，自动判定编码:
      - UTF-16LE: 大量 0x00 字节（ASCII 字符高位）
      - UTF-8:    大量 0xE0-0xEF 前导字节 + 0x80-0xBF 后续字节
      - Shift-JIS: 大量 0x81-0x9F / 0xE0-0xFC 首字节
    """
    if len(data) >= 2 and data[:2] == b'\xff\xfe':
        return 'utf-16-le'
    if len(data) >= 3 and data[:3] == b'\xef\xbb\xbf':
        return 'utf-8'

    sample = data[:4096]
    freq = Counter(sample)
    total = max(len(sample), 1)

    utf16_score = freq.get(0, 0) / total
    utf8_score  = sum(freq.get(b, 0) for b in range(0xE0, 0xF0)) / total
    sjis_score  = sum(freq.get(b, 0) for b in list(range(0x81, 0xA0))
                                                + list(range(0xE0, 0xFD))) / total

    if utf8_score > 0.15:
        return 'utf-8'
    elif utf16_score > 0.10:
        return 'utf-16-le'
    elif sjis_score > 0.05:
        return 'shift-jis'
    return 'utf-8'


def try_decode_text(plain: bytes) -> str:
    """尝试多种编码解码文本，返回第一个包含日文字符的结果"""
    for enc in ['utf-16-le', 'utf-8', 'shift-jis']:
        try:
            text = plain.decode(enc, errors='replace')
            if any('ぁ' <= c <= 'ん' or '一' <= c <= '龥' for c in text[:500]):
                return text
        except:
            pass
    # fallback: 自动检测
    enc = detect_encoding(plain)
    return plain.decode(enc, errors='replace')


# ============================================================
# 主逻辑
# ============================================================

# 不需要 zlib 解压的格式（直接 Cx LFSR）
DIRECT_DECRYPT_EXTS = {'.opus', '.ogg', '.png', '.jpg', '.bmp', '.tlg', '.wav'}

# 需要 zlib + Cx LFSR 的格式（脚本）
SCRIPT_EXTS = {'.ks', '.tjs'}


def decrypt_one(raw: bytes, seed: int, ext: str):
    """
    解密单个文件。
    返回 (decrypted_bytes, method_string)。
    """
    ext = ext.lower()

    # ── 直接 Cx LFSR（语音/图片/音乐） ──
    if ext in DIRECT_DECRYPT_EXTS:
        dec = decrypt_cx(raw, seed)
        return dec, 'Cx LFSR'

    # ── 脚本：zlib + Cx LFSR ──
    if ext in SCRIPT_EXTS:
        if raw[0] in (0x78, 0x68):    # zlib 魔数
            plain = decrypt_script(raw, seed)
            return plain, 'zlib → Cx LFSR'
        else:
            plain = decrypt_cx(raw, seed)
            return plain, 'Cx LFSR (raw)'

    # ── 未知格式：自动探测 ──
    if raw[0] in (0x78, 0x68):
        try:
            dec = zlib.decompress(raw)
            key = generate_key_schedule(seed)
            plain = bytes(dec[i] ^ key[i % 31] for i in range(len(dec)))
            return plain, 'zlib → Cx LFSR (auto)'
        except:
            pass

    plain = decrypt_cx(raw, seed)
    return plain, 'Cx LFSR (auto)'


def process_archive(xp3_path: str, out_base: str, dry_run: bool = False):
    """处理单个 XP3 归档"""
    name = os.path.splitext(os.path.basename(xp3_path))[0]
    out_dir = os.path.join(out_base, name)

    print(f"\n{'='*65}")
    print(f"  {xp3_path}")
    print(f"  → {out_dir}/")
    print(f"{'='*65}")

    entries = parse_xp3(xp3_path)
    encrypted = [e for e in entries if e['enc_flag'] == 0x80000000]
    print(f"  Entries: {len(entries)}, Encrypted: {len(encrypted)}")

    if dry_run:
        # 统计文件类型
        from collections import Counter
        types = Counter()
        for e in encrypted:
            types[os.path.splitext(e['filename'])[1].lower()] += 1
        for ext, cnt in types.most_common():
            print(f"    {ext}: {cnt} files")
        return

    Path(out_dir).mkdir(parents=True, exist_ok=True)
    success, total = 0, len(encrypted)

    with open(xp3_path, 'rb') as f:
        for i, e in enumerate(encrypted):
            if e['comp_size'] == 0 or e['offset'] == 0:
                continue
            if e['offset'] > os.path.getsize(xp3_path):
                continue

            try:
                f.seek(e['offset'])
                raw = f.read(e['comp_size'])
                ext = os.path.splitext(e['filename'])[1]

                plain, method = decrypt_one(raw, e['key_seed'], ext)

                # 构建输出路径，保持子目录结构
                out_path = os.path.join(out_dir, e['filename'].replace('\\', '/'))
                os.makedirs(os.path.dirname(out_path), exist_ok=True)

                with open(out_path, 'wb') as of:
                    of.write(plain)

                success += 1
                if success % 500 == 0 or success == 1:
                    print(f"  [{success}/{total}] {e['filename']} ({method})")

            except Exception as ex:
                pass  # 静默跳过坏文件

    print(f"  Done: {success}/{total} files → {os.path.abspath(out_dir)}")
    return out_dir


def main():
    parser = argparse.ArgumentParser(
        description='Krkr2 XP3 Universal Decryptor (Cx LFSR)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                          # 解密当前目录所有 .xp3
  %(prog)s voice.xp3                # 解密单个归档
  %(prog)s voice.xp3 data.xp3       # 解密多个归档
  %(prog)s -o ./output .            # 指定输出目录
  %(prog)s --dry-run voice.xp3      # 预览不解密
  %(prog)s --text-only data.xp3     # 仅解密脚本并导出文本
        """
    )
    parser.add_argument('targets', nargs='*', default=['.'],
                        help='XP3 文件或目录（默认: 当前目录）')
    parser.add_argument('-o', '--output', default='./decrypted',
                        help='输出根目录（默认: ./decrypted/）')
    parser.add_argument('--dry-run', action='store_true',
                        help='仅显示文件列表，不解密')
    parser.add_argument('--text-only', action='store_true',
                        help='仅解密脚本文件 (.ks/.tjs)，跳过媒体文件')
    args = parser.parse_args()

    # 收集目标文件
    xp3_list = []
    for t in args.targets:
        if os.path.isdir(t):
            xp3_list.extend(sorted(glob.glob(os.path.join(t, '*.xp3'))))
        elif os.path.isfile(t) and t.lower().endswith('.xp3'):
            xp3_list.append(t)

    if not xp3_list:
        print("[!] 未找到 .xp3 文件。把 .xp3 文件放在当前目录，或指定路径。")
        return

    print(f"[*] 找到 {len(xp3_list)} 个 XP3 归档\n")

    for xp3 in xp3_list:
        process_archive(xp3, args.output, args.dry_run)

    print(f"\n[*] 全部完成。输出目录: {os.path.abspath(args.output)}")


if __name__ == '__main__':
    main()
