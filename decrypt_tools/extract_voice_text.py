#!/usr/bin/env python3
"""
从已解密的场景脚本中提取语音-文本配对
=====================================

需要先运行 decrypt_all.py 解密 data.xp3。

Usage:
  python3 extract_voice_text.py [decrypted_scripts_dir] [voice_dir]
  python3 extract_voice_text.py ../decrypted/data ../decrypted/voice
"""

import sys, os, re, json
from pathlib import Path
from collections import defaultdict


def normalize_voice_id(fname: str) -> str:
    """从语音文件名提取标准 ID: vo1_vo1_0001.opus → vo1_0001"""
    base = fname.replace('.opus', '').replace('.ogg', '')
    # vos_vo8_0001 → vo8_0001
    m = re.match(r'^(?:vos_)?vo0*(\d+)_(\d+)$', base)
    if m:
        return f"vo{int(m.group(1))}_{int(m.group(2)):04d}"
    # vo1_vo1_0001 → vo1_0001
    m = re.match(r'^vo(\d+)_vo\d+_(\d+)$', base)
    if m:
        return f"vo{int(m.group(1))}_{int(m.group(2)):04d}"
    return base


def extract_from_script(text: str, filename: str = '') -> list:
    """
    从单个解密后的 KAG 脚本中提取语音-文本对。
    格式: [角色名 vo=voXX_YYYY] \n [>>]对话文本
    """
    pairs = []
    current_char = None
    current_voice = None

    for line in text.split('\n'):
        # 语音标签: [角色名 vo=vo1_0001] 或 [角色名 text="..." vo=...]
        vo = re.search(r'\[([^\s\]=]+)(?:\s+[^v]\S*="[^"]*")*\s+vo=vo(\d+_\d+)[^\]]*\]', line)
        if vo:
            current_char = vo.group(1).strip()
            current_voice = f"vo{vo.group(2)}"
            continue

        # 对话文本: [>>]文本内容
        dialog = re.search(r'\[>>\](.*?)(?:<<\]|$)', line)
        if dialog and current_voice:
            dtext = dialog.group(1).strip()
            dtext = re.sub(r'\[.*?\]', '', dtext)   # 去掉格式标签
            dtext = dtext.replace('[r]', '\n').replace('[p]', '')
            if dtext:
                pairs.append({
                    'voice': current_voice,
                    'char': current_char,
                    'text': dtext,
                    'file': filename,
                })
            current_voice = None

    return pairs


def main():
    script_dir = sys.argv[1] if len(sys.argv) > 1 else '../decrypted/data'
    voice_dir = sys.argv[2] if len(sys.argv) > 2 else '../decrypted/voice'
    out_dir = sys.argv[3] if len(sys.argv) > 3 else './voice_text_output'

    if not os.path.isdir(script_dir):
        print(f"[!] 脚本目录不存在: {script_dir}")
        print(f"    请先运行 decrypt_all.py 解密 data.xp3")
        return

    # 1. 索引语音文件
    voice_index = {}
    if os.path.isdir(voice_dir):
        for root, _, files in os.walk(voice_dir):
            for f in files:
                if f.endswith(('.opus', '.ogg')):
                    vid = normalize_voice_id(f)
                    rel_path = os.path.relpath(os.path.join(root, f), voice_dir)
                    voice_index[vid] = rel_path
        print(f"[*] 索引了 {len(voice_index)} 个语音文件")
    else:
        print(f"[!] 语音目录不存在: {voice_dir} (仅提取文本)")

    # 2. 扫描所有脚本
    all_pairs = []
    total_files = 0

    for root, _, files in os.walk(script_dir):
        for f in files:
            if not f.endswith(('.ks', '.tjs')):
                continue
            fpath = os.path.join(root, f)
            try:
                # 尝试 UTF-8 和 UTF-16LE
                with open(fpath, 'rb') as fh:
                    raw = fh.read()
                for enc in ['utf-8', 'utf-16-le', 'shift-jis']:
                    try:
                        text = raw.decode(enc)
                        if any('ぁ' <= c <= 'ん' or 'StartProcess' in text for c in [text[:500]]):
                            break
                    except:
                        continue
            except:
                continue

            pairs = extract_from_script(text, os.path.relpath(fpath, script_dir))
            if pairs:
                all_pairs.extend(pairs)
                total_files += 1

    print(f"[*] 从 {total_files} 个脚本中提取了 {len(all_pairs)} 个语音-文本对")

    # 3. 匹配语音文件
    matched = 0
    voice_to_text = defaultdict(list)
    for p in all_pairs:
        if p['voice'] in voice_index:
            p['voice_file'] = voice_index[p['voice']]
            voice_to_text[p['voice_file']].append({
                'text': p['text'],
                'char': p['char'],
                'scene': p['file'],
            })
            matched += 1

    print(f"[*] 匹配到语音文件: {matched}/{len(all_pairs)}")

    # 4. 输出
    Path(out_dir).mkdir(parents=True, exist_ok=True)

    # JSON 完整数据
    json_path = os.path.join(out_dir, 'voice_text_pairs.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump([p for p in all_pairs if 'voice_file' in p], f, ensure_ascii=False, indent=2)
    print(f"[*] JSON: {json_path}")

    # TSV (适合 TTS 训练)
    tsv_path = os.path.join(out_dir, 'tts_training.tsv')
    with open(tsv_path, 'w', encoding='utf-8') as f:
        f.write("voice_file\tcharacter\ttext\n")
        for p in all_pairs:
            if 'voice_file' in p:
                text = p['text'].replace('\n', ' ').replace('\t', ' ')
                f.write(f"{p['voice_file']}\t{p.get('char', '')}\t{text}\n")
    print(f"[*] TSV: {tsv_path}")

    # 纯文本语料
    txt_path = os.path.join(out_dir, 'corpus.txt')
    with open(txt_path, 'w', encoding='utf-8') as f:
        for p in all_pairs:
            f.write(p['text'] + '\n')
    print(f"[*] 语料: {txt_path}")

    # 统计
    if voice_to_text:
        chars = Counter(p['char'] for v in voice_to_text.values() for p in v)
        print(f"\n  角色统计:")
        for char, cnt in chars.most_common():
            print(f"    {char}: {cnt} 句")

    print(f"\n  输出目录: {os.path.abspath(out_dir)}")


from collections import Counter

if __name__ == '__main__':
    main()
