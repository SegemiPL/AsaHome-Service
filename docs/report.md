# 视觉小说游戏 XP3 资源加密与解密技术报告

## 前言

如果你玩过日本的 Galgame（视觉小说），可能会好奇：游戏的图片、音乐、语音和剧本都藏在哪儿？答案是 **XP3 归档文件**——一种把成千上万个文件打包成几个大文件的技术。为了保护游戏资源不被随意提取，厂商会给这些文件加密。

本文以某款基于 Krkr2 引擎的 Galgame 为例，完整讲解它的加密方式以及如何解密。不需要你是密码学专家——只要会写简单的 Python 代码就能理解。

---

## 一、XP3 文件是什么

XP3 就像一个**带目录的 ZIP 压缩包**：文件内容放在前面，文件目录放在末尾。目录里记录了每个文件叫什么、在哪个位置、有多大。

```
┌─────────────────────────────────────┐
│  [文件1数据] [文件2数据] ... [文件N数据]  │   ← 文件体
├─────────────────────────────────────┤
│  [File1] [File2] ... [FileN]        │   ← 索引区（末尾 ~2MB）
└─────────────────────────────────────┘
```

每个 `File` 条目包含三个关键信息块：

| 字段名 | 作用 | 举个例子 |
|--------|------|---------|
| `info` | 文件名、原始大小、加密标志 | "vos/vo01_0001.opus"，加密标志 = 0x80000000 |
| `segm` | 文件在 XP3 里的位置 (sf) 和数据大小 | 从第 951 个字节开始，大小 99309 字节 |
| `adlr` | **密钥种子**（4 字节藏在 adlr 数据后面） | 0x9C5F8288 |

密钥种子是加密的核心——没有它，即使知道算法也算不出正确的密钥。

---

## 二、两种加密方式

这个游戏用了**两种不同场景的加密**：

### 2.1 加密一：Cx LFSR（用于语音/图片）

**一句话概括**：用一个 31 字节的密码本，把文件每一字节轮流"加锁"。

#### 2.1.1 什么样的数据会走这种加密？

- 语音文件（`.opus`）
- 图片文件（`.png`）
- 部分脚本源码（`.tjs` 文本文件）

这些文件的**原始内容直接加密后存入 XP3**，解密后即可使用（语音可直接播放，图片可直接显示）。

#### 2.1.2 加密流程（从开发者视角）

```
步骤1: 准备好原始文件 → OGG Opus 音频数据
步骤2: 生成31字节密钥 → 用密钥种子烧出一串密码
步骤3: 逐字节加密      → data[i] XOR key[i % 31]
步骤4: 塞入 XP3        → 搞定
```

#### 2.1.3 解密流程（从逆向者视角）

```
步骤1: 解析 XP3 末尾索引 → 拿到密钥种子
步骤2: 生成31字节密钥    → 和加密时一模一样
步骤3: 逐字节 XOR        → 恢复原始数据
步骤4: 验证文件头        → OggS 开头就对了
```

### 2.2 加密二：Zlib + Cx LFSR（用于脚本文件）

**一句话概括**：先把脚本压缩成 ZIP 格式，再用同样的 31 字节密码本加密。

#### 2.2.1 为什么要先压缩？

游戏剧本动辄几十万字，每个 `.ks` 场景文件可能有 100KB+。直接加密太占空间。先 zlib 压缩（类似 ZIP 压缩）可以把体积缩小 40%~60%，然后再加密。

#### 2.2.2 加密流程

```
步骤1: 剧本源文件           → UTF-8 或 UTF-16LE 编码的纯文本
步骤2: zlib 压缩            → 压缩率约 40%~60%
步骤3: Cx LFSR 加密         → 对压缩后的数据逐字节 XOR
步骤4: 塞入 XP3             → 文件头是 0x78（zlib 的"我是压缩数据"标志）
```

#### 2.2.3 解密流程

```
步骤1: 解析 XP3 末尾索引 → 拿到密钥种子
步骤2: zlib 解压         → 因为加密的是"压缩后的数据"
步骤3: Cx LFSR 解密       → 恢复压缩前的剧本原文
步骤4: 自动检测编码       → UTF-8 还是 UTF-16LE？
步骤5: 得到可读文本       → 日文剧本！
```

#### 2.2.4 注意：编码的坑

同一个游戏的脚本文件，可能使用**不同的字符编码**：
- 系统脚本 (`scenario/avan/`) → **UTF-16LE**（每个字符 2 字节）
- 主线场景 (`scenario/main/`) → **UTF-8**（英文 1 字节，日文 3 字节）

如果用错编码，看到的日文就会变成乱码——但这不是密钥错了，只是"翻译方式"错了。

---

## 三、核心算法：Cx LFSR 详解

### 3.1 什么是 LFSR

LFSR（Linear Feedback Shift Register，线性反馈移位寄存器）是一种快速生成伪随机数的方法。想象一个 32 位的"转盘"，每次转动都产生一个新的数字，而这个数字的某一部分就被用作密钥。

### 3.2 密钥生成过程

```
输入：一个 32 位整数（密钥种子），例如 0x9C5F8288
输出：31 个字节的密钥

算法（Python）:
    state = (seed & 0x7FFFFFFF) | ((seed & 1) << 31)   # 初始化"转盘"
    
    for i in range(31):
        key[i] = state & 0xFF                          # 取最后 8 位作为密钥
        state = ((state >> 8) |                        # 右移 8 位
                 ((state & 0xFFFFFFFE) << 23))         # 最低位转到最高位

    return key    # 31 字节密钥
```

### 3.3 加密/解密（XOR 运算）

XOR（异或）运算有个神奇的特性：**用同样的密钥 XOR 两次，就回到了原文**。

```
原文 ⊕ 密钥 = 密文
密文 ⊕ 密钥 = 原文
```

所以加密和解密用的是**同一个算法**，只是应用的对象不同。

```python
# 加密和解密是同一个函数！
def xor_decrypt(data, key):
    return bytes(data[i] ^ key[i % 31] for i in range(len(data)))
    #               │       │        │
    #               │       │        └─ 第 i 个字节
    #               │       └─ 循环使用密钥（0~30 周而复始）
    #               └─ XOR: 相同为0，不同为1
```

### 3.4 为什么是 31 字节

31 是个质数。如果密钥长度是 2 的幂（如 32），某些简单的文件格式（如全部是相同字节）可能被部分破解。质数长度让密钥的循环周期和文件长度不太可能有公约数，增加了破解难度。

---

## 四、完整解密流程（一步步来）

### Step 1：解析 XP3 索引

```python
# 打开 XP3 文件，跳到末尾 2MB 区域（索引在这里）
with open('voice.xp3', 'rb') as f:
    f.seek(0, 2)                       # 跳到文件末尾
    fsize = f.tell()
    f.seek(max(0, fsize - 2_000_000))  # 回退 2MB
    index_data = f.read(min(2_000_000, fsize))
```

### Step 2：找到每个 File 条目

```python
# 在索引区搜索 "File" 标记
pos = index_data.find(b'File')  # 找到第一个 "File" 标记

# 然后在这个 File 块内搜索三个关键标记:
info_pos = index_data.find(b'info', pos)  # ← 文件名信息
segm_pos = index_data.find(b'segm', pos)  # ← 文件位置和大小
adlr_pos = index_data.find(b'adlr', pos)  # ← 密钥种子藏在这后面
```

### Step 3：提取密钥种子

```python
# adlr 的结构: "adlr" + 4字节大小 + adlr数据(4字节0) + 4字节密钥种子
asize = int.from_bytes(index_data[adlr_pos+4:adlr_pos+8], 'little')
key_seed_bytes = index_data[adlr_pos+8+asize : adlr_pos+8+asize+4]
key_seed = int.from_bytes(key_seed_bytes, 'little')
# 例如: 0x9C5F8288
```

### Step 4：读取加密数据

```python
# segm 数据中 [8:12] 位置的 sf 字段就是文件在 XP3 中的绝对偏移
sf = int.from_bytes(segm_data[8:12], 'little')
comp_size = int.from_bytes(segm_data[24:28], 'little')

f.seek(sf)                              # 跳到文件数据位置
raw_data = f.read(comp_size)             # 读取加密数据
```

### Step 5：解密（分两种情况）

**情况 A：语音/图片（直接加密）**

```python
key = generate_key_schedule(key_seed)    # 生成 31 字节密钥
decrypted = bytes(raw_data[i] ^ key[i % 31] for i in range(len(raw_data)))
# 验证: decrypted[:4] 应该是 b'OggS' 或 b'\x89PNG'
```

**情况 B：脚本文件（压缩+加密）**

```python
# 先判断: raw_data[0] == 0x78 说明用了 zlib 压缩
import zlib
decompressed = zlib.decompress(raw_data)  # 步骤1: 解压

key = generate_key_schedule(key_seed)     # 步骤2: 生成密钥
decrypted = bytes(decompressed[i] ^ key[i % 31] for i in range(len(decompressed)))
```

### Step 6：确定文本编码并解码

```python
# 采样分析
sample = decrypted[:4096]
byte_freq = Counter(sample)

# UTF-8 特征: 大量 0xE0-0xEF 和 0x80-0xBF 字节
# UTF-16LE 特征: 大量 \x00 字节（英文字符的高位）
# Shift-JIS 特征: 大量 0x81-0x9F 和 0xE0-0xFC 字节

if byte_freq[0] / len(sample) > 0.10:     # 10% 以上是空字节
    encoding = 'utf-16-le'
elif sum(byte_freq[b] for b in range(0xE0, 0xF0)) / len(sample) > 0.15:
    encoding = 'utf-8'
else:
    encoding = 'shift-jis'

text = decrypted.decode(encoding)
```

### Step 7：验证结果

```python
# 语音文件：检查文件头
assert decrypted[:4] == b'OggS', "语音解密失败！"

# 脚本文件：检查是否有日语字符
hiragana = sum(1 for c in text[:500] if 'ぁ' <= c <= 'ん')
assert hiragana > 0 or 'StartProcess' in text, "脚本文本异常！"
```

---

## 五、完整 Python 代码

把上面所有步骤整合成一个完整的解密脚本：

```python
import zlib, os
from pathlib import Path
from collections import Counter

# ── 密钥生成 ──
def generate_key_schedule(seed):
    state = (seed & 0x7FFFFFFF) | ((seed << 31) & 0xFFFFFFFF)
    state &= 0xFFFFFFFF
    key = bytearray(31)
    for i in range(31):
        key[i] = state & 0xFF
        state = ((state >> 8) | ((state & 0xFFFFFFFE) << 23)) & 0xFFFFFFFF
    return bytes(key)

# ── XP3 解析 ──
def parse_xp3(filepath):
    with open(filepath, 'rb') as f:
        f.seek(0, 2)
        f.seek(max(0, f.tell() - 2_000_000))
        index = f.read(2_000_000)

    entries = []
    pos = 0
    while True:
        fpos = index.find(b'File', pos)
        if fpos < 0: break
        # ... (完整的 info/segm/adlr 解析代码见 universal_decrypt.py)
        pos = fpos + 8
    return entries

# ── 解密 ──
def decrypt_voice(raw, seed):
    key = generate_key_schedule(seed)
    return bytes(raw[i] ^ key[i % 31] for i in range(len(raw)))

def decrypt_script(raw, seed):
    dec = zlib.decompress(raw)
    key = generate_key_schedule(seed)
    return bytes(dec[i] ^ key[i % 31] for i in range(len(dec)))

# ── 编码检测 ──
def detect_encoding(data):
    sample = data[:4096]
    freq = Counter(sample)
    if freq[0] / len(sample) > 0.10:
        return 'utf-16-le'
    if sum(freq[b] for b in range(0xE0, 0xF0)) / len(sample) > 0.15:
        return 'utf-8'
    return 'shift-jis'

# ── 主流程 ──
def decrypt_xp3(xp3_path, output_dir):
    entries = parse_xp3(xp3_path)
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    with open(xp3_path, 'rb') as f:
        for e in entries:
            f.seek(e['offset'])
            raw = f.read(e['comp_size'])

            # 判断文件类型
            ext = os.path.splitext(e['filename'])[1].lower()

            if ext in ('.opus', '.ogg', '.png'):
                dec = decrypt_voice(raw, e['key_seed'])     # 直接解密
            elif raw[0] == 0x78:
                dec = decrypt_script(raw, e['key_seed'])    # 解压+解密
            else:
                dec = raw                                    # 未加密

            # 保存
            with open(os.path.join(output_dir,
                      e['filename'].replace('/', '_')), 'wb') as of:
                of.write(dec)
```

完整可运行的通用解密器见 `universal_decrypt.py`。

---

## 六、逆向过程简述

如果你好奇这个加密算法是怎么被搞清楚的，这里简单说一下：

### 6.1 工具链

| 工具 | 干什么用 |
|------|---------|
| **x64dbg** | 动态调试器，在游戏运行时下断点、看内存 |
| **Ghidra** | 反编译工具，把机器码翻译回类 C 代码 |
| **Python** | 写脚本验证算法、批量处理 |

### 6.2 关键步骤

1. **找到加密代码**：用 x64dbg 的 Memory Map 发现 `koikari.tpm` 插件，它就是加密模块。但磁盘上的文件是加壳的，dump 运行时内存才能分析。

2. **反编译分析算法**：在 Ghidra 中看到 `sub esp, 0x24` → 初始化；`and eax, 0x7FFFFFFF` → 位运算；循环 31 次生成密钥表 → LFSR。

3. **动态验证**：在 x64dbg 中设断点，捕获加密前/解密后的数据，XOR 两者验证密钥是否正确。

4. **找到密钥种子**：发现解密用的种子藏在 XP3 索引区的 `adlr` 字段后面 4 字节。这个位置是在对比几个 XP3 文件的相同偏移后发现的。

5. **解开所有文件**：有了种子和算法，批量解密 → 验证文件头（OggS、TJS2）→ 成功！

### 6.3 踩过的坑

- 以为加密代码是运行时动态生成的 → 实际在静态 .tpm 插件里
- 以为密钥在 segm 字段里 → 实际 segm 存的是文件偏移
- 以为文件偏移需要逐个累加 → 实际 sf 字段就是绝对偏移
- 以为脚本都是同一种编码 → 实际系统脚本和场景脚本用了不同编码
- 以为所有 .tjs 文件都能解开 → 编译后的字节码文件用了另一种加密

---

## 七、总结

### 技术要点

| 项目 | 说明 |
|------|------|
| 加密算法 | Cx LFSR（31 字节密钥 + XOR） |
| 密钥来源 | XP3 索引 adlr 段后的 4 字节种子 |
| 语音加密 | 原始数据 → Cx LFSR 直接加密 |
| 脚本加密 | 原始文本 → zlib 压缩 → Cx LFSR 加密 |
| 文本编码 | 系统脚本 UTF-16LE，主场景 UTF-8 |
| 核心运算 | XOR（异或）：相同为 0，不同为 1 |

### 为什么 XOR 能用于加密

XOR 是计算机世界里最简单的"锁"：

```
原文:   01001111  (字母 'O')
密钥:   11001010
        ────────
密文:   10000101  (看上去是乱码)

密文:   10000101
密钥:   11001010  (同一个密钥)
        ────────
原文:   01001111  ('O' 又回来了！)
```

这就是"把同一个锁锁两次，门就开了"的数学原理。

### 文件清单

| 文件名 | 用途 |
|--------|------|
| `universal_decrypt.py` | 通用解密器，可复用到同公司其他游戏 |
| `decrypt_voice.py` | 专门解密语音文件 |
| `decrypt_all.py` | 解密全部 XP3（语音+脚本） |
| `extract_voice_text.py` | 提取语音-文本对用于 TTS 训练 |
| `experience.md` | 详细逆向过程记录（含失败尝试） |
| `report.md` | 本文，科普向技术报告 |
