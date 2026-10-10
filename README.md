# DrugsPRO_to_PsyLogJournal_python

Convert a DrugsPRO journal export (`drugspro-journal-YYYY-MM-DD.json`) into the
import format of the journal app (PsyLog, a.k.a. "Android Journal",
<https://codeberg.org/PsyLog/PsyLog-android>).

把 DrugsPRO 的日记导出转换成 journal app(PsyLog)的导入格式。

[English](#english) · [简体中文](#简体中文)

## English

### Files

| File | What it is |
| --- | --- |
| `DrugsPRO_to_PsyLogJournal.py` | the converter |
| `journal_file.py` | reader for the four backup containers; required, keep it next to the script |

Standard library only, no install step. Python 3.9 or newer.

### Quick start

```powershell
python DrugsPRO_to_PsyLogJournal.py drugspro-journal-2026-10-10.json
```

The result lands next to the input as `drugspro-journal-2026-10-10.journal-app.json`.
Import it with the journal app's **Settings → Import**, which replaces everything
the app currently holds. Add `--pretty` for an indented file, or `-o out.json`
to pick the path yourself.

### Interactive use

Started without a file - double clicked, or just `python DrugsPRO_to_PsyLogJournal.py` -
it asks for the path first:

```
DrugsPRO 导出文件 -> journal app(PsyLog)导入格式
DrugsPRO export -> journal app (PsyLog) import format
可以把文件直接拖进这个窗口,也可以粘贴或输入完整路径。
Drag the file into this window, or paste / type the full path.
备份文件路径 / Backup file path (回车即退出 / Enter to quit): _
```

The prompt accepts a dragged file (quotes and all), a pasted path, a `file://`
URL or `~`. A path that does not exist, or a directory, is reported and asked
for again; Enter on its own cancels. After an interactive run it waits for Enter
before closing, so a double-clicked window does not vanish before you can read
the summary. `--no-pause` skips that wait, and passing the file on the command
line keeps the run non-interactive.

### Options

| Option | Effect |
| --- | --- |
| `input` | DrugsPRO export; optional, without it the script asks for one |
| `-o`, `--out` | output path, default `<backup>.journal-app.json` next to the input |
| `--pretty` | indent the JSON |
| `--dedupe` | collapse same-day rows that repeat substance, dose and route |
| `--stdout` | print instead of writing a file (ignores `-o`) |
| `--no-pause` | do not wait for Enter after an interactive run |
| `--self-test` | run the built-in checks |
| `-h`, `--help` | bilingual help, with examples |

The exit code is `0` on success and `1` when the schema check fails, the file
cannot be read, or you cancel at the prompt.

### Reading the summary

```
Converted / 已转换  drugspro-journal-2026-10-10.json -> ...\drugspro-journal-2026-10-10.journal-app.json
  experiences / 体验         183 (from the journal / 来自导出中的体验)
  standalone rows / 独立记录 366 -> 135 day(s) / 天
  skipped rows / 跳过记录    460 (an experience's projection / 属于某次体验的投影行)
  ingestions / 摄入记录      625 in total / 共
  duplicate rows / 重复记录  164 (same day, substance, dose and route / 同日、同物质、同剂量、同途径; --dedupe 合并)
  size / 大小                235126 bytes / 字节
  not carried over / 未迁移: n, x
  schema                     ok / 结构检查通过
```

- `experiences / 体验` - experiences carried over from the DrugsPRO journal.
- `standalone rows / 独立记录` - usage-log rows that hang off no experience,
  grouped into one experience per day.
- `skipped rows / 跳过记录` - rows the app generated from an experience's own
  substances (`__fromExperience`). The experience already carries them, so
  keeping them would duplicate every intake.
- `duplicate rows / 重复记录` - rows repeating substance, dose and route on the
  same day (see below).
- `not carried over / 未迁移` - DrugsPRO sections the journal app has nowhere
  to put, here the day notes `n` and the settings `x`.
- `schema / 结构检查` - the file is checked against what the journal app's
  decoder needs; `ok` means it will load.

### Mapping

| DrugsPRO | journal app |
| --- | --- |
| experience + its substances | experience + one ingestion per substance |
| `date` + substance `timeHHMM` (`day2` when past midnight) | `sortDate`, ingestion `time` |
| substance `doseMult` | ingestion `dose` |
| substance `roaName` / `roa` | `administrationRoute` |
| experience `title`, `description` | `title`, `text` |
| experience `__updatedAt` | `creationDate` |
| experience `favorite` | `isFavorite` |
| free-standing usage rows, grouped by day | one experience per day, ingestion per row |

- Timestamps are epoch milliseconds; a record with no clock time lands at noon
  local time.
- `administrationRoute` is the enum name in upper case. The journal app has no
  vaporized route, so DrugsPRO's `vaporized` maps to `SMOKED`.
- `units` is taken from the matching usage row when its dose string has one
  (`"600 mg"`), otherwise it defaults to `mg`, the app's own default unit.
- Ingestions inside an experience use the exact `HH:MM`. Free-standing rows
  carry no clock time, so they are laid out from noon in one-minute steps to
  keep their original order.
- The file is written as UTF-8 without a BOM, with LF line endings, in the same
  shape the journal app exports itself.

### Duplicates

Free-standing rows sometimes repeat the same substance, dose and route on the
same day. Without a clock time those could equally be a genuine re-dose, so they
are kept by default and only reported. `--dedupe` collapses them, which drops
information the raw export still holds.

### Verifying

```powershell
python DrugsPRO_to_PsyLogJournal.py --self-test
```

Runs 16 checks over a built-in payload - route mapping, `__fromExperience`
skipping, day grouping, dose parsing, schema shape - and exits non-zero if one
fails.

### Input containers

`journal_file.py` reads every container the app can produce: plain JSON, gzip
bytes, `DRUGSPRO-GZ:v1:<base64 gzip>` (what localStorage and cloud sync hold)
and the mobile backup shape with its ten-character digest line before the JSON.

### Troubleshooting

| Symptom | Fix |
| --- | --- |
| `ModuleNotFoundError: No module named 'journal_file'` | `journal_file.py` must sit next to the script; run `python DrugsPRO_to_PsyLogJournal.py` from this folder |
| Chinese text shows as boxes | Use Windows Terminal, or run `chcp 65001` first; unprintable characters are replaced instead of raising |
| `Unsupported backup version: None` | the file is not a DrugsPRO export - export one from DrugsPRO first |
| Importing seems to erase data | the journal app's import replaces everything it holds, so back up first |

## 简体中文

### 文件

| 文件 | 说明 |
| --- | --- |
| `DrugsPRO_to_PsyLogJournal.py` | 转换脚本 |
| `journal_file.py` | 备份文件读取模块(支持四种容器格式),必须和脚本放在同一目录 |

只用标准库,不需要安装任何依赖,Python 3.9 及以上。

### 快速开始

```powershell
python DrugsPRO_to_PsyLogJournal.py drugspro-journal-2026-10-10.json
```

结果默认写在输入文件旁边:`drugspro-journal-2026-10-10.journal-app.json`。
在 journal app 里用 **设置 → 导入** 选中它即可,导入会覆盖 app 里现有的全部数据。
加 `--pretty` 输出带缩进的 JSON,加 `-o out.json` 自己指定输出路径。

### 交互式使用

不带文件运行(直接双击,或者只输入 `python DrugsPRO_to_PsyLogJournal.py`),脚本会先询问路径:

```
DrugsPRO 导出文件 -> journal app(PsyLog)导入格式
DrugsPRO export -> journal app (PsyLog) import format
可以把文件直接拖进这个窗口,也可以粘贴或输入完整路径。
Drag the file into this window, or paste / type the full path.
备份文件路径 / Backup file path (回车即退出 / Enter to quit): _
```

路径可以是从资源管理器拖进来的文件(带引号也没问题)、粘贴的路径、`file://` 链接或 `~`。
路径不存在或指向目录时会提示并重新询问;直接回车就是取消。
交互式运行结束后会再等一次回车才关窗口,这样双击打开的控制台不会来不及看摘要就消失;
加 `--no-pause` 可以跳过等待,而在命令行里直接给出文件时完全不会进入交互。

### 参数

| 参数 | 作用 |
| --- | --- |
| `input` | DrugsPRO 导出文件;可以省略,省略时会先询问 |
| `-o`、`--out` | 输出路径,默认为输入文件旁的 `<备份文件>.journal-app.json` |
| `--pretty` | 输出缩进后的 JSON |
| `--dedupe` | 合并同一天里物质、剂量、途径都相同的记录 |
| `--stdout` | 只打印到终端、不写文件(此时忽略 `-o`) |
| `--no-pause` | 交互式运行结束后不等待回车 |
| `--self-test` | 运行内置自检 |
| `-h`、`--help` | 中英双语帮助和示例 |

成功返回 0;结构校验不通过、文件读不了或在提示处取消时返回 1。

### 看懂输出

```
Converted / 已转换  drugspro-journal-2026-10-10.json -> ...\drugspro-journal-2026-10-10.journal-app.json
  experiences / 体验         183 (from the journal / 来自导出中的体验)
  standalone rows / 独立记录 366 -> 135 day(s) / 天
  skipped rows / 跳过记录    460 (an experience's projection / 属于某次体验的投影行)
  ingestions / 摄入记录      625 in total / 共
  duplicate rows / 重复记录  164 (same day, substance, dose and route / 同日、同物质、同剂量、同途径; --dedupe 合并)
  size / 大小                235126 bytes / 字节
  not carried over / 未迁移: n, x
  schema                     ok / 结构检查通过
```

- `experiences / 体验`:从 DrugsPRO 日记里带过来的体验条数。
- `standalone rows / 独立记录`:没有挂在体验下的用药记录,按天归并成"一天一条体验"。
- `skipped rows / 跳过记录`:app 根据体验自身的物质自动生成的记录(`__fromExperience`),
  体验里已经体现过,再保留会把每次摄入都算两遍。
- `duplicate rows / 重复记录`:同一天里物质、剂量、途径都相同的记录(见下文)。
- `not carried over / 未迁移`:journal app 结构里没有对应位置的 DrugsPRO 段落,
  这里是日记正文 `n` 和设置 `x`。
- `schema / 结构检查`:按 journal app 解码要求校验文件,`ok / 结构检查通过` 表示可以正常导入。
  真的有问题时,报错文本沿用目标格式的英文字段名,方便对照 journal app 的 schema。

### 字段对应关系

| DrugsPRO | journal app |
| --- | --- |
| 体验 + 它的物质 | 体验 + 每种物质一条摄入记录 |
| `date` + 物质的 `timeHHMM`(跨零点用 `day2`) | `sortDate`、摄入记录 `time` |
| 物质 `doseMult` | 摄入记录 `dose` |
| 物质 `roaName` / `roa` | `administrationRoute` |
| 体验 `title`、`description` | `title`、`text` |
| 体验 `__updatedAt` | `creationDate` |
| 体验 `favorite` | `isFavorite` |
| 独立的用药记录,按天分组 | 一天一条体验,一行一条摄入记录 |

- 时间戳是 epoch 毫秒;没有具体时刻的记录落在当地时间的当天中午。
- `administrationRoute` 用枚举名大写。journal app 没有 vaporized 途径,
  DrugsPRO 的 `vaporized` 映射成 `SMOKED`。
- `units` 取自对应用药记录里剂量字符串所带单位(`"600 mg"`),没有就默认 `mg`,这也是 app 自己的默认单位。
- 体验里的物质用精确的 `HH:MM`;独立记录没有时刻,于是从中午起按每分钟一条排列,保持原来的顺序。
- 输出文件是 UTF-8(无 BOM)、LF 换行,和 journal app 自己导出的形状一致。

### 重复记录

独立记录里有时会出现同一天、同一种物质、同一剂量、同一途径的多条。因为都没有时刻,
它们既可能是真的补服,也可能是重复录入,所以默认全部保留、只做提示。
加 `--dedupe` 才会合并,这会丢掉原始导出里仍然存在的信息。

### 自检

```powershell
python DrugsPRO_to_PsyLogJournal.py --self-test
```

用内置样例跑 16 项检查(途径映射、跳过 `__fromExperience`、按天归并、剂量解析、结构合法性),
有任何一项不过就以非 0 退出。

### 支持的输入格式

`journal_file.py` 能读 app 可能产出的全部容器格式:纯 JSON、gzip 字节、
`DRUGSPRO-GZ:v1:<base64 gzip>`(localStorage 和云同步里存的样子),
以及移动端备份(JSON 前面有一行 10 个字符的摘要)。

### 常见问题

| 现象 | 处理 |
| --- | --- |
| `ModuleNotFoundError: No module named 'journal_file'` | `journal_file.py` 必须和脚本在同一目录,请在本目录下运行 `python DrugsPRO_to_PsyLogJournal.py` |
| 中文显示成方块 | 用 Windows Terminal,或先执行 `chcp 65001`;无法输出的字符会被替换而不是报错 |
| `Unsupported backup version: None` | 这不是 DrugsPRO 导出文件,请先在 DrugsPRO 里导出一次 |
| 导入后发现数据被清空 | journal app 的导入会覆盖现有全部数据,导入前先备份 |

## License

MIT, see [LICENSE](LICENSE).
