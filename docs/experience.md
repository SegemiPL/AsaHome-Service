# Krkr2 XP3 Cx 解密实战经验总结

## 游戏信息

- **游戏**: 恋愛、借りちゃいました (jielian.exe)，Krkr2/KiriKiriZ 引擎 v1.3.3.7
- **加密插件**: `plugin/koikari.tpm` — 自定义 Cx 解密插件
- **目标**: 解密 `voice.xp3`（1.7GB, 14467 个 Opus 语音文件）和 `data.xp3`（264 个脚本文件）

---

## 最终正确算法

### 数据位置

XP3 文件尾部 ~2MB 为索引区（未压缩），包含 `File` 分组：

```
File 分组结构:
  "File" + size(4)
  ├── "info" + size(4) + info_data       ← 含文件名、加密标志、原始大小
  ├── "segm" + 0x1C(4) + segm_data(28B)  ← 含文件偏移、压缩大小
  └── "adlr" + 0x04(4) + adlr_data(4B)   ← 4 字节 0
       └── [adlr 之后 4 字节] = 密钥种子 ← **核心发现**
```

### 关键字段

| 字段 | 来源 | 说明 |
|------|------|------|
| 文件偏移 | `segm` 数据 `[0x08:0x0C]`（sf 字段） | XP3 文件内的绝对偏移量 |
| 加密大小 | `segm` 数据 `[0x18:0x1C]` | 加密数据的字节数 |
| 密钥种子 | `adlr` 数据结束后 4 字节 | 32-bit little-endian |
| 加密标志 | `info` 数据 `[0x04:0x08]` | `0x80000000` = 已加密 |
| 原始大小 | `info` 数据 `[0x08:0x0C]` | 解密后（可能已压缩）的大小 |
| 文件名 | `info` 数据 `[0x18:]` | UTF-16LE 宽字符串，可能溢出 info 边界 |

### 解密算法（Cx LFSR）

```python
def generate_key_schedule(seed: int) -> bytes:
    """Generate 31-byte Cx LFSR key from 32-bit seed."""
    state = (seed & 0x7FFFFFFF) | ((seed << 31) & 0xFFFFFFFF)
    state &= 0xFFFFFFFF
    key = bytearray(31)
    for i in range(31):
        key[i] = state & 0xFF
        # LFSR 步进: 右移 8 位 | (清除 bit0 后左移 23 位)
        state = ((state >> 8) | ((state & 0xFFFFFFFE) << 23)) & 0xFFFFFFFF
    return bytes(key)

def decrypt(data: bytes, seed: int) -> bytes:
    """使用 Cx 算法和简单顺序密钥解密"""
    key = generate_key_schedule(seed)
    # 密钥使用方式: key[position % 31]
    # 离线解密从文件头开始 position=0
    return bytes(data[i] ^ key[i % 31] for i in range(len(data)))
```

### 不同文件类型的解密流程

#### voice.xp3（.opus 音频文件）
```
直接 Cx LFSR 解密 → 有效 OGG Opus 文件
解密: bytes(data[i] ^ key[i % 31] for i in range(len(data)))
```

#### data.xp3 系统脚本（.ks/.tjs, UTF-16LE 编码）
```
zlib 解压 → Cx LFSR 解密 → UTF-16LE 文本
解密: dec = zlib.decompress(raw)
     plain = bytes(dec[i] ^ key[i % 31] for i in range(len(dec)))
     text = plain.decode('utf-16-le')
```

#### data.xp3 主场景脚本（.ks, UTF-8 编码）
```
zlib 解压 → Cx LFSR 解密 → UTF-8 文本
解密: dec = zlib.decompress(raw)
     plain = bytes(dec[i] ^ key[i % 31] for i in range(len(dec)))
     text = plain.decode('utf-8')
```

**关键：所有文件使用相同的 Cx LFSR 加密，但编码不同！**
- `scenario/avan/*.ks` 等系统脚本 → UTF-16LE
- `scenario/main/*.ks` 等主场景脚本 → UTF-8
- 编译后的 .tjs 字节码文件使用 `zlib + 4 字节 XOR` 加密（不同算法，本次未完全破解）

---

## 最终成果统计

| 项目 | 数量 |
|------|------|
| voice.xp3 解密 Opus 文件 | 14,467 |
| data.xp3 解密脚本 | 264 |
| 语音-文本配对 | 21,494 |
| 有标注的语音文件 | 13,502 |
| 对话语料总行数 | 49,791 |
| 角色数 | 13 |

### 语音文件命名规则

```
vo1_vo1_0001.opus ~ vo7_vo7_XXXX.opus  → vo1~vo7 角色语音
vos_vo8_0001.opus ~ vos_vo11_XXXX.opus  → vo8~vo11 角色语音
sys_voX_sys_X.opus                       → 系统音效
other_TestVo_X.opus                      → 测试语音
```

---

## 完整逆向流程

### 第一阶段：定位目标

1. **已知入口**: `tTVPXP3ArchiveStream::Read` @ `0x4391C0`（Ghidra）/ `0xC291C0`（运行时）
   - 验证方式：x64dbg 设断点，F9 运行命中 ✓

2. **尝试教程方法**: 从 Read 向上 step out 找动态生成代码
   - **结果**: 失败 — 调用链全在 EXE 内部，无动态代码跳转
   - **教训**: 该游戏不用动态生成汇编，Cx 实现在插件中

### 第二阶段：定位 Cx 实现

3. **搜索 VirtualAlloc**: 在 jielian.exe 中只找到 DirectShow 缓冲区分配
   - **结果**: 死胡同 — EXE 中没有可执行内存分配

4. **发现关键模块**: x64dbg Memory Map 显示 `koikari.tpm` 插件
   - 导出 `V2Link` / `V2Unlink`
   - 导入 `VirtualAlloc`
   - V2Link 调用 `TVPSetXP3ArchiveExtractionFilter`

5. **dump koikari.tpm**: 运行时内存 dump（文件加壳，磁盘上代码不同）
   ```bash
   # x64dbg 命令
   savedata "koikari_unpacked.bin", 61B30000, 10000
   ```

### 第三阶段：分析 Cx 算法

6. **Ghidra 分析 koikari.tpm**:
   - 过滤器函数 @ `0x61B31000` → 反编译确认算法流程
   - Mixer 函数 @ `0x61B374B0` → 64 位模运算（实际就是 `pos % 31`）
   - 基址必须设为 `61B30000`（与运行时一致）

7. **x64dbg 动态验证**:
   - 断点 @ `0x61B31000`（过滤器入口）
   - 捕获密钥调度表，与 Python 实现对比 → 完全匹配 ✓
   - 确认算法：LFSR 31 字节密钥流 + XOR

### 第四阶段：寻找密钥种子（核心难点）

8. **尝试 1 — segm[0x08] 作为种子**:
   - segm 数据 `[0x08:0x0C]` 字段有值，递增
   - 用于解密 → **失败**（产生垃圾数据）
   - 后来发现：该字段 = 文件偏移，不是密钥

9. **尝试 2 — 文件名哈希作为种子**:
   - CRC32/DJB2/XXH32 各种哈希 → **全部失败**

10. **尝试 3 — 已知明文攻击**:
    - 假设 Opus 文件头 = "OggS"，反推密钥字节
    - 前三字节解密为 "Ogg"，第四字节因 LFSR 状态位约束矛盾
    - 证明文件数据确实为加密状态

11. **尝试 4 — 暴力搜索**:
    - 在 32 位种子空间中搜索 → 超时
    - 对 segm 字段的各种变换 (XOR, ROT, MUL) → **全部失败**

12. **突破 — 搜索已知种子**:
    - x64dbg 捕获到的种子 = `0x8CC3BF70`
    - 在所有 XP3 文件中搜索此值
    - **在 data.xp3 中找到！** 位于 `adlr` 数据结束后 4 字节
    - 验证：voice.xp3 同样的位置也有对应的值

### 第五阶段：验证和修复

13. **尝试解密 — 失败（偏移计算错误）**:
    - 用累计偏移（从 0x28 开始累加 segm 大小）→ 解密失败
    - 原因：**sf 字段就是绝对偏移，不需要累计计算**

14. **最终突破 — sf 作为文件偏移**:
    - `File[1]` ("other/TestVo_1.opus") sf=0x3B7
    - 直接 seek 到 0x3B7，用 adlr key=0x9C5F8288 解密
    - **结果: "OggS..." 有效 OGG Opus 文件！** ✅

15. **批量验证**:
    - 14467/14467 文件全部解密成功 ✅

### 第六阶段：解密 data.xp3 脚本

16. **初始假设 — 同一种加密**:
    - 尝试直接 Cx LFSR 解密 raw 数据 → **失败**（乱码）
    - 发现 raw 数据以 `0x78` 开头 → zlib 压缩！

17. **尝试 zlib + Cx LFSR**:
    - `zlib.decompress(raw)` → Cx LFSR 解密 → **部分成功**
    - 系统脚本（UTF-16LE 编码）→ 完美解密 ✅
    - 主场景脚本（Shift-JIS 尝试）→ KAG 标签正确但日文乱码 ❌

18. **错误假设 — 4 字节 XOR 密钥**:
    - 观察到 `(state ^ CONST)` 能解密前 4 字节
    - CONST = 0x3268B4AB (.ks) 或 0x327CB4AB (.tjs)
    - 尝试用 `key[i%4]` 解密全部 → 每 4 字节只有第 1 字节正确 ❌
    - 尝试 LCG/LFSR/RC4 等各种 PRNG 推导后续密钥 → **全部失败**
    - **教训**: 这不是 4 字节 XOR，字节 4+ 的"正确值"是巧合

19. **x64dbg 运行时捕获验证**:
    - 在 `0x61B31000`（过滤器入口）和 `0xC292FC`（返回点）设断点
    - 捕获加密前/解密后的数据
    - XOR 对比得出：**31 字节密钥，与 Cx LFSR 生成结果完全一致** ✅
    - 确认 `offset_low=2` 时使用 `key[(2+i)%31]`

20. **编码问题排查**:
    - 系统脚本用 UTF-16LE 解码 → 完美 ✅
    - 主场景尝试 Shift-JIS → ASCII 标签正常但日文乱码 ⚠️
    - 字节频率分析发现 `E3, 81, 82, 80, 83` 高频 → **UTF-8 特征！**
    - 切换到 UTF-8 解码 → **完美！** ✅

21. **最终成功解密全部脚本**:
    - 264 个脚本全部解密
    - 提取 21,494 个语音-文本对

---

## 失败尝试总结

| # | 错误假设 | 正确结论 |
|---|---------|---------|
| 1 | Cx 代码动态生成（教程方法） | 该游戏 Cx 实现在 koikari.tpm 静态编译 |
| 2 | 密钥在 segm[0x08] | segm[0x08] = 文件偏移，不是密钥 |
| 3 | 文件偏移从 0x28 累计计算 | sf 字段直接就是绝对偏移量 |
| 4 | offset_low 需要从 segm 传入 | 该游戏 offset_low = 0，顺序使用密钥 |
| 5 | koikari.tpm 可直接在 Ghidra 分析 | 文件加壳，需要运行时 dump |
| 6 | voice.xp3 文件头应为 "OggS" | 前三字节 "Ogg" 正确，第四字节需 LFSR 状态匹配 |
| 7 | data.xp3 直接用 Cx LFSR 解密 | 需要先 zlib 解压再 Cx 解密 |
| 8 | XOR 密钥是 4 字节重复 | 实际是 31 字节 Cx LFSR 密钥 |
| 9 | `(state^CONST)` 是完整密钥 | 只是密钥生成的第一步 |
| 10 | 主场景用 Shift-JIS 编码 | 实际是 **UTF-8** 编码！ |
| 11 | 所有 .tjs 文件都是字节码 | 大部分 .tjs 是 UTF-16LE 源码，可直接解密 |
| 12 | GARbro 能解密此游戏 XP3 | GARbro 不支持 koikari.tpm 自定义加密 |

---

## 可复用方法论

### 对于 Krkr2/Cx 加密游戏

1. **先确认动态代码还是静态插件**
   - 看进程内存中是否有 `PAGE_EXECUTE_READWRITE` 区域
   - 检查 `plugin/` 目录下的 `.tpm` 文件

2. **定位 Cx 实现**
   - 静态：dump .tpm → Ghidra 分析（注意基址对齐）
   - 动态：VirtualAlloc + PAGE_EXECUTE_READWRITE

3. **验证算法**
   - x64dbg 在过滤器入口设断点
   - **捕获过滤器入口（加密）和出口（解密）的缓冲区数据**
   - XOR 两者得到实际密钥流，与 Python 实现对比
   - **这一步必须做，避免后续大量无效调试**

4. **寻找密钥种子**
   - 搜索 XP3 索引中 `adlr` 段后的 4 字节
   - 在已知明文上验证（如 PNG 头 `89 50 4E 47` 或 OGG 头 `4F 67 67 53`）
   - 如果 adlr 后的值不行，尝试其他字段组合

5. **确定文件偏移**
   - 先尝试 sf 字段作为绝对偏移
   - 不行再尝试累计计算

6. **处理 zlib 压缩**
   - 如果 raw 数据以 `0x78` 开头 → zlib 压缩
   - 解密流程：`zlib.decompress(raw)` → Cx LFSR 解密

7. **确定文本编码**
   - 尝试 UTF-16LE（BOM 为 FF FE）
   - 尝试 UTF-8（看字节频率：E3/E8/EF/81/82/83 多）
   - 尝试 Shift-JIS（0x81-0x9F 和 0xE0-0xFC 范围）
   - **不同目录的脚本可能使用不同编码！**

8. **批量解密**
   - 验证少量文件后再全量跑
   - 检查输出文件头是否有效

### 必备工具

| 工具 | 用途 |
|------|------|
| x64dbg | 动态调试，内存 dump，断点捕获加解密前后数据 |
| Ghidra/IDA | 静态反编译分析 |
| Python | 算法验证 + 批量解密脚本 |

### 运行时密钥捕获技巧

最可靠的方法：
1. 在过滤器函数入口设断点（koikari.tpm 的 Cx 函数）
2. 读取结构体获取 `[edi+0x0C]`（缓冲区指针）和 `[edi+0x10]`（大小）
3. 保存加密数据
4. 在过滤器返回地址设断点
5. 继续执行
6. 读取同一缓冲区 → 此时已解密
7. **XOR 加密和解密数据得到完整密钥流**

---

## 脚本使用

```bash
# 解密 voice.xp3
python3 decrypt_voice.py voice.xp3 output_dir

# 解密 data.xp3 全部脚本
python3 decrypt_all.py data.xp3 output_dir

# 预览不解密
python3 decrypt_voice.py voice.xp3 --dry-run

# 提取语音-文本对（需要先解密 data.xp3 脚本）
python3 extract_voice_text.py
```

### 最终解密脚本核心代码

```python
def generate_key_schedule(seed):
    """Cx LFSR 31 字节密钥调度"""
    state = (seed & 0x7FFFFFFF) | ((seed << 31) & 0xFFFFFFFF)
    state &= 0xFFFFFFFF
    key = bytearray(31)
    for i in range(31):
        key[i] = state & 0xFF
        state = ((state >> 8) | ((state & 0xFFFFFFFE) << 23)) & 0xFFFFFFFF
    return bytes(key)

def decrypt_file(raw_data, seed):
    """通用解密：zlib 解压 → Cx LFSR 解密"""
    dec = zlib.decompress(raw_data)
    key = generate_key_schedule(seed)
    return bytes(dec[i] ^ key[i % 31] for i in range(len(dec)))

# 文本解码（按目录选择编码）
# scenario/main/*.ks  → UTF-8
# 其他 .ks/.tjs      → UTF-16LE
```
