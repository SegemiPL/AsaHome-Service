# XP3 解密工具集

基于对 Krkr2/KiriKiriZ 引擎「恋愛、借りちゃいました」的逆向分析，支持解密使用 **Cx LFSR** 加密的 XP3 归档文件。

目前已经测试成功的游戏有：
- 借恋（含FD）
- 恋爱成双（含FD）
- 恋爱决胜战（含FD）
- 八卦恋
- 与恋爱初邂逅
  
目前测试失败或未测试的游戏有：
- 寄宿之恋（加密方式不同）
- 寄宿恋之前的所有游戏（未尝试）

寄宿之恋以及之前的 AsaProject 游戏正在尝试逆向破解中

## 快速开始

```bash
# 1. 把游戏的所有 .xp3 文件放到当前目录

# 2. 解密全部文件
python3 decrypt_all.py

# 3. 查看输出
ls decrypted/
```

## 工具说明

| 脚本 | 用途 |
|------|------|
| `decrypt_all.py` | **主解密器** — 一键解密所有 XP3（语音/立绘/CG/音乐/脚本） |
| `decrypt_voice.py` | 专门解密语音文件（voice.xp3 → Opus 音频） |
| `extract_voice_text.py` | 从解密后的脚本中提取语音-文本对（TTS 训练用） |
| `extract_keys.py` | 从 XP3 索引中导出所有密钥种子（调试用） |

## 使用方法

### 一键解密全部

```bash
python3 decrypt_all.py
```

自动扫描当前目录下所有 `.xp3` 文件并解密到 `./decrypted/`。

### 解密单个文件

```bash
python3 decrypt_all.py voice.xp3
python3 decrypt_all.py data.xp3 stimage.xp3
```

### 指定输出目录

```bash
python3 decrypt_all.py -o ./my_output voice.xp3
```

### 预览不解密

```bash
python3 decrypt_all.py --dry-run
```

### 提取语音-文本对（TTS 训练）

```bash
# 先解密
python3 decrypt_all.py data.xp3

# 再提取
python3 extract_voice_text.py
```

## 输出结构

```
decrypted/
├── voice/          # 语音文件 (.opus)
│   ├── vos/
│   ├── vo1_vo1_0001.opus
│   └── ...
├── data/           # 脚本+音效+UI
│   ├── scenario/
│   │   ├── main/   # 主线场景 (.ks, UTF-8)
│   │   └── avan/   # 系统脚本 (.ks, UTF-16LE)
│   ├── sound/      # 音效 (.ogg)
│   └── rule/       # UI 图片 (.png)
├── stimage/        # 人物立绘 (.tlg)
├── evimage/        # 事件 CG (.png)
├── bgimage/        # 背景图片 (.png)
├── bgm/            # 背景音乐 (.ogg)
└── ...
```

## 支持的文件类型

| 文件类型 | 扩展名 | 解密方式 | 输出格式 |
|---------|--------|---------|---------|
| 语音 | `.opus` | Cx LFSR | OGG Opus |
| 音乐 | `.ogg` | Cx LFSR | OGG Vorbis |
| 人物立绘 | `.tlg` | Cx LFSR | TLG (Krkr2专有格式) |
| 事件CG | `.png` | Cx LFSR | PNG |
| 背景 | `.png` | Cx LFSR | PNG |
| UI 图片 | `.png` | Cx LFSR | PNG |
| 场景脚本 | `.ks` | zlib + Cx LFSR | UTF-8 文本 |
| 系统脚本 | `.tjs` | zlib + Cx LFSR | UTF-16LE 文本 |

## 技术原理

### 加密算法：Cx LFSR

游戏使用 31 字节的线性反馈移位寄存器（LFSR）密钥流，通过 XOR 对每个字节加密。

```
密钥生成 (Python):
  state = (seed & 0x7FFFFFFF) | ((seed & 1) << 31)
  重复 31 次:
    key[i] = state & 0xFF
    state = ((state >> 8) | ((state & 0xFFFFFFFE) << 23)) & 0xFFFFFFFF

解密:
  plain[i] = encrypted[i] ^ key[i % 31]
```

### 密钥种子位置

密钥种子（32-bit）藏在 XP3 文件末尾索引区，每个 File 条目的 `adlr` 段之后 4 字节。

### 两类加密

1. **直接 Cx LFSR** — 语音、图片、音乐等二进制文件直接 XOR 加密
2. **zlib + Cx LFSR** — 脚本文件先用 zlib 压缩，再 XOR 加密（因为纯文本压缩率高）

## 依赖

- Python 3.7+
- 标准库（无需 pip install）

## 适配其他游戏

本工具理论上可解密同公司（HOOKSOFT/SMEE）使用 `koikari.tpm` 或类似 Cx LFSR 插件的 Krkr2 游戏。

如果遇到解密失败：
1. 用 `--dry-run` 查看文件列表
2. 检查密钥种子是否被正确提取（对比 `adlr` 段后的 4 字节）
3. 部分游戏的密钥种子可能不在 `adlr` 段后，需要调整 `parse_xp3()` 中的种子提取逻辑

## 参考

- 详细逆向过程: `../experience.md`
- 科普技术报告: `../report.md`
- 通用解密器: `decrypt_all.py`
