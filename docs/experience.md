# Krkr2 XP3 Cx 加密逆向工程全记录

> **游戏**: 恋愛、借りちゃいました (jielian.exe)
> **引擎**: Krkr2/KiriKiriZ v1.3.3.7，TJS2/2.4.28
> **加密插件**: `plugin/koikari.tpm`
> **目标**: 离线解密 voice.xp3（14,467 Opus）、data.xp3（264 脚本）、stimage.xp3（3,180 立绘）、bgm.xp3（30 BGM）、bgimage.xp3（145 背景）、evimage.xp3（727 CG）

---

## 前置知识

### XP3 归档格式

Krkr2 引擎的游戏资源打包在 `.xp3` 文件中。XP3 像一个 ZIP 包：文件数据放在前面，索引目录放在末尾 ~2MB 区域。索引区是**未加密**的明文，由若干 `File` 分组组成，每个分组包含三个子段：

| 子段   | 标记                     | 内容                                                      |
| ------ | ------------------------ | --------------------------------------------------------- |
| `info` | `info` + 4字节长度       | 文件名(UTF-16LE)、加密标志(0x80000000=已加密)、原始大小   |
| `segm` | `segm` + 4字节长度(0x1C) | 28字节数据，含 sf 字段(文件在XP3内的绝对偏移)和压缩后大小 |
| `adlr` | `adlr` + 4字节长度       | adlr 数据(通常4字节全0)，**后面紧跟4字节密钥种子**        |

### 为什么不能直接用 GARbro

GARbro 是常用的 Galgame 解包工具，但它只支持标准 XP3 和少数已知加密方案。这款游戏使用了自定义的 `koikari.tpm` 插件实现 Cx 加密，GARbro 没有适配这个插件，所以解出来的文件全是乱码。

---

## 第一阶段：找到加密代码在哪里

### 1.1 初步信息搜集

游戏目录结构：

```
jielian.exe          ← 主程序
plugin/
  koikari.tpm        ← 可疑！名字暗示是加密插件
  KAGParser.dll
  extKAGParser.dll
  ...
voice.xp3            ← 1.7GB，需要解密的目标
data.xp3             ← 脚本文件
```

`koikari.tpm` 这个名字引起了注意——"koikari" 是游戏名缩写，`.tpm` 是 Krkr2 的插件扩展名。这个文件只有 65KB，导出 `V2Link` 和 `V2Unlink` 两个函数，还导入了 `VirtualAlloc`（用于动态内存分配——可能在运行时生成代码）。

### 1.2 教程派方法（失败）

网上针对 Krkr2 Cx 加密的教程通常是这样：

> 在 `tTVPXP3ArchiveStream::Read` 设断点 → step out 找到动态生成的解密代码 → dump 分析

我找到了这个函数（Ghidra 地址 `0x4391C0`，运行时 `0xC291C0`），在 x32dbg 中设断点并按 F9——确实命中了，说明这个函数在文件读取时被调用。但 step out 后跳转的地址全在 `jielian.exe` 的静态代码段内，没有出现传说中的"动态生成代码"。

**为什么失败？** 这个游戏的 Cx 实现不是在运行时动态生成的，而是编译在 `koikari.tpm` 的静态代码里。教程覆盖的是另一种 Cx 变体。

### 1.3 定位到 koikari.tpm

既然 `jielian.exe` 里没有 Cx 代码，下一步自然想到查看插件。用 x32dbg 打开游戏的 Memory Map：

```
Address       Size     Name
61B30000     10000    koikari.tpm    ← 这就是加密插件
```

`koikari.tpm` 加载在 `0x61B30000`。这个地址很重要——后续所有 Ghidra 分析都要以此为基址。

**关键线索**：`koikari.tpm` 导入表中包含 `TVPSetXP3ArchiveExtractionFilter`，这是 Krkr2 引擎注册 XP3 解密过滤器的 API。它的 `V2Link` 函数在插件加载时被调用，里面会调用 `TVPSetXP3ArchiveExtractionFilter` 来注册解密回调函数。

### 1.4 Dump 运行时内存

直接把磁盘上的 `koikari.tpm` 拖进 Ghidra 分析——全是垃圾指令。这是因为它被加壳了（packed），磁盘上的代码是压缩/混淆的，只有在运行时内存中才会被解压成真正的代码。

解决方法：在 x32dbg 中，游戏运行起来后，用 `savedata` 命令 dump 内存：

```
savedata "koikari_unpacked.bin", 61B30000, 10000
```

把 `0x61B30000` 开始的 0x10000 字节保存到文件。这个 dump 文件包含了运行时解压后的真实代码。

---

## 第二阶段：逆向分析 Cx LFSR 算法

### 2.1 在 Ghidra 中定位过滤器函数

把 dump 文件导入 Ghidra，**基址必须设为 `0x61B30000`**（与运行时地址对齐，否则所有交叉引用都会错位）。

`TVPSetXP3ArchiveExtractionFilter` 注册的回调函数就是解密过滤器。在 Ghidra 中，搜索对 `TVPSetXP3ArchiveExtractionFilter` 的调用：

```
V2Link → TVPSetXP3ArchiveExtractionFilter(某个函数指针)
```

这个函数指针指向 `0x61B31000`——这就是**Cx 解密过滤器的入口**。

### 2.2 反编译过滤器函数

在 Ghidra 中跳到 `0x61B31000`，按 F 创建函数，然后反编译。关键汇编代码及逐行解读：

```asm
; === 函数入口 ===
61B31000  sub esp, 0x24        ; 分配栈空间
61B31003  mov eax, [0x61B3B004]; 安全 Cookie
61B31008  xor eax, esp
61B3100A  mov [esp+0x20], eax

61B3100E  push esi
61B3100F  push edi
61B31010  mov edi, [esp+0x30]  ; EDI = 第一个参数（结构体指针）
61B31014  cmp [edi], 0x18      ; 检查结构体大小 == 0x18（24字节）
61B31017  jz  0x61B3103B       ; 如果大小正确，跳转到主逻辑
; 大小不对 → 错误处理...

; === 主逻辑：从结构体读取密钥种子 ===
61B3103B  mov eax, [edi+0x14]  ; EAX = 结构体偏移 0x14 处的值
                                 ; 这就是密钥种子（seed）！
61B3103E  and eax, 0x7FFFFFFF  ; 清除最高位（bit 31）
61B31043  mov ecx, eax         ; ECX = EAX
61B31045  shl ecx, 0x1F        ; ECX = EAX << 31（最低位移到最高位）
61B31048  or  eax, ecx         ; EAX = (seed & 0x7FFFFFFF) | ((seed & 1) << 31)
                                 ; 这就是 LFSR 的初始状态！
```

**解读**：结构体 `[edi]` 的第 `0x14` 偏移处存放着密钥种子。LFSR 的初始化公式是：

```
state = (seed & 0x7FFFFFFF) | ((seed & 1) << 31)
```

就是把种子的 bit 31 清零，然后把 bit 0 搬到 bit 31 的位置。

### 2.3 密钥调度表生成

继续往下看：

```asm
; === 密钥调度循环（生成 31 字节密钥） ===
61B3104A  xor ecx, ecx         ; ECX = 0（循环计数器 i）
61B3104C  lea esp, [esp]       ; 对齐 NOP

; 循环体开始 ↓
61B31050  mov edx, eax         ; EDX = 当前 state
61B31052  mov [esp+ecx+0x08], al; key[i] = state & 0xFF（取最低字节）
61B31056  and edx, 0xFFFFFFFE  ; EDX = state & 0xFFFFFFFE（清除 bit 0）
61B31059  shl edx, 0x17        ; EDX = (state & 0xFFFFFFFE) << 23
61B3105C  shr eax, 8           ; EAX = state >> 8
61B3105F  inc ecx              ; i++
61B31060  or  eax, edx         ; state = (state>>8) | ((state&~1)<<23)
61B31062  cmp ecx, 0x1F        ; i < 31 ?
61B31065  jl  0x61B31050       ; 是 → 继续循环
```

**解读**：循环 31 次，每次：
1. `key[i] = state & 0xFF`——取 state 的最低 8 位
2. `state = (state >> 8) | ((state & 0xFFFFFFFE) << 23)`——LFSR 步进

这就生成了一个 31 字节的密钥表。注意 31 是个**质数**——这不是巧合，质数长度的密钥可以防止与文件长度的公约数导致密钥模式暴露。

### 2.4 XOR 解密循环

```asm
; === 解密循环 ===
61B31067  xor esi, esi         ; ESI = 0（数据索引）
61B31069  cmp [edi+0x10], esi  ; 比较：size > 0？
61B3106C  jbe 0x61B31098       ; size <= 0 → 跳过

; 循环体开始 ↓
61B31070  mov eax, [edi+0x0C]  ; EAX = buffer 指针
61B31073  xor edx, edx
61B31075  mov ecx, esi
61B31077  add ecx, [edi+0x04]  ; ECX = i + offset_low（计算绝对位置）
61B3107A  push 0
61B3107C  adc edx, [edi+0x08]  ; 处理 64 位进位
61B3107F  push 0x1F            ; 参数：模数 31
61B31081  push edx             ; 参数：位置高 32 位
61B31082  push ecx             ; 参数：位置低 32 位
61B31083  lea ebx, [eax+esi]   ; EBX = &buffer[i]
61B31086  call 0x61B374B0      ; 调用 Mixer(pos, 31)
61B3108B  mov al, [esp+esi+0x0C]; AL = key[result]（从栈上取密钥字节）
61B3108F  xor [ebx], al        ; buffer[i] ^= key_byte
61B31091  inc esi              ; i++
61B31092  cmp esi, [edi+0x10]  ; i < size？
61B31095  jb  0x61B31070       ; 是 → 继续
```

**解读**：
1. 对每个字节位置，计算**绝对文件位置** = `offset_low + i`
2. 调用 `Mixer(absolute_position, 31)` 获取密钥索引
3. 从栈上取 `key[index]`，与 `buffer[i]` 做 XOR
4. 解密在**原地**进行（输入和输出是同一个 buffer）

### 2.5 Mixer 函数验证

Mixer 函数在 `0x61B374B0`，反编译后发现它就是 **64 位取模运算**：

```
result = absolute_position % 31
```

在常规调用场景下 `offset_low = 0`（从文件开头读取），所以 `result = i % 31`，与我们的 Python 实现完全一致。

### 2.6 转换为 Python

将上面的汇编逻辑翻译成 Python：

```python
def generate_key_schedule(seed: int) -> bytes:
    """从 32-bit 种子生成 31 字节密钥"""
    state = (seed & 0x7FFFFFFF) | ((seed & 1) << 31)
    state &= 0xFFFFFFFF      # 保持 32 位
    key = bytearray(31)
    for i in range(31):
        key[i] = state & 0xFF
        # LFSR 步进
        state = ((state >> 8) | ((state & 0xFFFFFFFE) << 23)) & 0xFFFFFFFF
    return bytes(key)

def decrypt(data: bytes, seed: int) -> bytes:
    key = generate_key_schedule(seed)
    return bytes(data[i] ^ key[i % 31] for i in range(len(data)))
```

### 2.7 动态验证算法

虽然 Ghidra 反编译结果看起来对，但必须通过**动态验证**确认。方法：

1. 在 `0x61B31000` 设断点
2. 进入游戏 → 断点命中
3. 读取 `[edi+0x14]` 获取 seed
4. 用 Python 生成密钥表
5. 在 `0x61B31052`（`mov [esp+ecx+8], al`）设断点，单步 31 次，每次记录 AL
6. 对比 Python 和 x32dbg 的 31 字节密钥 → **完全一致**

这一步验证至关重要——它确认了算法理解是正确的，后续所有调试都基于这个前提。

---

## 第三阶段：找到密钥种子——整个逆向的难点

### 3.1 问题的本质

已知：
- ✅ 加密算法（Cx LFSR）
- ✅ 结构体偏移 `[edi+0x14]` 存放 seed
- ❌ **seed 是从哪里来的？**

没有 seed，算法毫无用处。需要从 XP3 文件中找到每个文件对应的 seed。

### 3.2 x32dbg 捕获已知种子

在断点命中时，读取 `[edi+0x14]`，得到 seed = `0x8CC3BF70`。记下此时正在读取的文件名（从 `info` 段解析出来）。

### 3.3 搜索种子来源——尝试 1：segm 字段（失败）

直觉：seed 应该存储在 XP3 索引区的某个字段里。最先怀疑的是 `segm` 数据，因为它有多个未知字段。

```
segm 数据布局（28字节）：
[0x00-0x07]: 全零
[0x08-0x0B]: sf 字段（文件偏移）
[0x0C-0x0F]: 全零
[0x10-0x13]: 值 A
[0x14-0x17]: 全零
[0x18-0x1B]: 值 B（= 压缩后大小）
```

`[0x10-0x13]` 的值看起来像个 32-bit 整数。用它做 seed → 解密出一堆乱码。**结论：不是**。

### 3.4 尝试 2：文件名哈希（失败）

如果 seed 不在索引里，也许是从文件名派生出来的？尝试 CRC32、DJB2、XXH32 等各种哈希算法对文件名做计算 → 全部不匹配 `0x8CC3BF70`。

### 3.5 尝试 3：已知明文攻击（失败但排除了一个方向）

假设 Opus 音频文件解密后应以 `OggS`（`4F 67 67 53`）开头。如果算法正确，那么：

```
key[0] = enc[0] ^ 0x4F   →  得到 key[0] = 某个值
key[1] = enc[1] ^ 0x67   →  得到 key[1] = 某个值
key[2] = enc[2] ^ 0x67   →  得到 key[2] = 某个值
key[3] = enc[3] ^ 0x53   →  得到 key[3] = 某个值
```

然后用这些 key 字节反推 LFSR 状态 → 发现 key[3] 对应的 LFSR 状态与 key[0-2] 推导出的状态矛盾。这证明了**文件确实是加密的**，而且算法确实是 LFSR（不是随机密钥）——因为 LFSR 的状态约束导致了矛盾。

### 3.6 尝试 4：暴力搜索（半途放弃）

32 位种子空间是 42 亿——在 Python 里暴力搜索太慢了。尝试对 segm 字段做各种数学变换（XOR 常量、循环移位、乘法取模）→ 全部失败。

### 3.7 突破——搜索已知种子

换个思路：既然 x32dbg 给了我一个已知的 seed（`0x8CC3BF70`），而我手上有多个 XP3 文件，为什么不直接在 XP3 文件里搜索这个值？

```bash
# 在所有 XP3 文件中搜索 0x8CC3BF70 的字节模式
grep -oba $'\x70\xBF\xC3\x8C' *.xp3
```

在 `data.xp3` 中找到了！位置就在**每个 File 条目的 `adlr` 段之后 4 字节**。

验证：查看 `voice.xp3` 对应位置，也有类似的值。且不同的 File 条目，该位置的值不同——这正是每个文件独立的密钥种子！

### 3.8 为什么藏在 adlr 段后面

在 Krkr2 引擎源码中，`adlr` 段通常存储 Adler-32 校验和（4 字节）。但这个游戏把 adlr 数据设为了全 0，然后把密钥种子**藏在 adlr 段之后**——夹在 adlr 数据和下一个 File 标记之间。这是一种简单但有效的隐藏方式：

```
"adlr" + 4字节size + 4字节全零(废弃的校验和) + 4字节密钥种子 + "File" + ...
```

如果不特意去找，很难注意到 adlr 段后面多出了 4 个字节。

---

## 第四阶段：验证并修复偏移计算

### 4.1 sf 字段的发现

segm 数据的 `[0x08:0x0C]` 字段（sf）在多个 File 条目中递增。最初以为是需要从某个基址（如 0x28）开始累加的偏移量。

**测试方法**：从 0x28 开始，逐个累加 segm 中的压缩大小，与 sf 字段对比：
- 累加值 ≠ sf

**结论**：sf 字段本身就是**XP3 文件内的绝对偏移**，不需要累加。

### 4.2 第一个成功解密

```
voice.xp3 中 File[1]（"other/TestVo_1.opus"）
  sf = 0x3B7
  adlr seed = 0x9C5F8288
  
→ seek(0x3B7) → read(99309) → Cx LFSR decrypt
→ 结果: b'OggS...'  ✅ 有效的 OGG Opus 音频！
```

第一个文件成功后，批量跑 14,467 个语音文件：
```
14467/14467 全部成功 ✅
```

---

## 第五阶段：解密 data.xp3 脚本

### 5.1 zlib 压缩的发现

直接用 Cx LFSR 解密 data.xp3 中的 `.ks` 脚本 → 乱码。

检查加密数据的第一字节 → `0x78`。这是 zlib 压缩的魔数！说明脚本文件在加密前先经过了 zlib 压缩。

### 5.2 修正解密流程

```
旧的（错误）: Cx LFSR(raw) → 乱码
新的（正确）: zlib.decompress(raw) → Cx LFSR → UTF-16LE 文本 ✅
```

但这一步只成功了解密系统脚本（`scenario/avan/*.ks`）。主场景脚本（`scenario/main/*.ks`）解密后，ASCII 标签正确但日文乱码。

### 5.3 错误方向：4 字节 XOR 假说

观察到一个巧合：`(state ^ 0x3268B4AB)` 能正确解密 `.ks` 文件的**前 4 字节**为 `TJS2`。

于是花了很多时间验证"4 字节重复 XOR"假说：
- 尝试 LCG 推导后续密钥 → 失败
- 尝试 RC4 推导后续密钥 → 失败
- 尝试 xorshift/SplitMix64 等各种 PRNG → 全部失败
- 尝试在二进制中搜索后续密钥字节的模式 → 不存在

**事后分析**：`(state ^ 0x3268B4AB)` 恰好让前 4 字节变成 `TJS2` 纯属巧合——`TJS2` 恰好是 `state ^ CONST` 的结果，但这个 CONST 并不用于密钥生成。

### 5.4 运行时验证打破错误假说

在 x32dbg 中重新设断点 @ `0x61B31000`（入口）和 `0xC292FC`（返回点），捕获加密前/解密后的数据。XOR 两者得到实际密钥流：

```
实际密钥流 = 加密数据 XOR 解密数据
           = Cx LFSR 31 字节密钥（第2轮调用时 offset_low=2）
```

**结论**：密钥就是 Cx LFSR 生成的 31 字节表，不是什么 4 字节 XOR。

### 5.5 编码问题的最终解决

系统脚本用 UTF-16LE 解码完美。但主场景脚本：

- Shift-JIS 解码 → ASCII 标签正确，日文乱码
- 字节频率分析 → 前 5 个最常见字节中包括 `E3, 81, 82` 等

`0xE3`、`0x81`、`0x82` 是 **UTF-8 多字节序列的标志字节**：
- `0xE3` 是最常见的 UTF-8 三字节前导码之一（对应 CJK 范围 U+3000-U+3FFF）
- `0x81`、`0x82` 是 UTF-8 后续字节

切换到 UTF-8 → **完美！**

```
[咲希 vo=vo6_0001]
[>>]ねー、夏休みどうする？　最後の夏だよ！
```

**教训**：同一个游戏的脚本文件可能使用不同的编码，取决于文件在哪个目录。

---

## 关键决策点和转折总结

| 阶段       | 问题                   | 错误尝试                        | 突破口                                     |
| ---------- | ---------------------- | ------------------------------- | ------------------------------------------ |
| 定位代码   | Cx 代码在哪？          | 教程方法（动态生成）            | x32dbg Memory Map → koikari.tpm            |
| 分析代码   | koikari.tpm 代码不可读 | 直接拖入 Ghidra                 | 运行时 dump 去壳                           |
| 理解算法   | 过滤器函数逻辑         | —                               | Ghidra 反编译 + 逐行汇编解读               |
| 验证算法   | Python 实现是否正确？  | —                               | x32dbg 断点单步对比 31 字节密钥            |
| **找种子** | seed 存在哪？          | segm 字段、文件名哈希、暴力搜索 | 用已知 seed 反搜 XP3 文件 → adlr 后 4 字节 |
| 文件定位   | 偏移量怎么算？         | 从 0x28 开始累加                | sf 字段 = 绝对偏移                         |
| 脚本解密   | .ks 文件解密后乱码     | 直接 Cx LFSR                    | 发现 0x78 = zlib 魔数                      |
| 主场景脚本 | 日文字符乱码           | 4字节XOR假说（耗时最多）        | x32dbg 捕获实际密钥流 + UTF-8 编码发现     |

---

## 最终算法总结

### Cx LFSR 密钥生成

```python
def generate_key_schedule(seed):
    state = (seed & 0x7FFFFFFF) | ((seed & 1) << 31)
    state &= 0xFFFFFFFF
    key = bytearray(31)
    for i in range(31):
        key[i] = state & 0xFF
        state = ((state >> 8) | ((state & 0xFFFFFFFE) << 23)) & 0xFFFFFFFF
    return bytes(key)
```

### 按文件类型选择解密方式

| 文件类型   | 扩展名   | 加密方式                          | 编码        |
| ---------- | -------- | --------------------------------- | ----------- |
| 语音       | .opus    | Cx LFSR                           | —（二进制） |
| BGM        | .ogg     | Cx LFSR                           | —           |
| 音效       | .ogg     | Cx LFSR                           | —           |
| 立绘       | .tlg     | Cx LFSR                           | —           |
| CG/背景/UI | .png     | Cx LFSR                           | —           |
| 系统脚本   | .ks/.tjs | zlib解压 → Cx LFSR                | UTF-16LE    |
| 主场景脚本 | .ks      | zlib解压 → Cx LFSR                | **UTF-8**   |
| 编译字节码 | .tjs     | zlib解压 → 4字节XOR（**未破解**） | —           |

---

## 成果统计

| 项目         | 数量                         |
| ------------ | ---------------------------- |
| 解密文件总数 | ~19,000                      |
| voice.xp3    | 14,467 Opus                  |
| data.xp3     | 482 PNG + 197 OGG + 264 脚本 |
| stimage.xp3  | 3,180 TLG 立绘               |
| evimage.xp3  | 727 PNG 事件CG               |
| bgimage.xp3  | 144 PNG 背景                 |
| bgm.xp3      | 30 OGG BGM                   |
| 语音-文本对  | 21,494                       |
