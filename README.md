# Crestron Debugger Implementation

A small, dependency-free Python implementation of what Crestron Toolbox's **SIMPL Debugger** does on a processor's
console: read a program's signal list, watch signal values live, and set digitals, analogs and serials.

> **Not affiliated with or endorsed by Crestron.** Crestron, SIMPL Windows and Toolbox are trademarks of Crestron
> Electronics, Inc. This is an independent implementation, written from the processor's own console help and from
> watching what Toolbox sends.
>
> **Setting a signal changes a running system.** A pulse on the wrong signal can switch off a room, lower a screen or
> open a door. Only use this on processors you are responsible for.

## What is here

| Module | What it does |
|---|---|
| `crestron_debugger/sigfile.py` | Reads a SIMPL Windows signal file (`.sig`), or finds it inside a `.zig`, an `.lpz` or a zip of a program folder. Gives every signal's number, name, type and module. |
| `crestron_debugger/proto.py` | Builds and checks the debugger's console lines, and parses the value frames the processor writes back. |

Both are standard library only and never connect to anything by themselves. A live session over SSH and a
command-line tool are planned next.

Requires Python 3.8 or later.

## Reading a signal list

```python
from crestron_debugger import sigfile

data = open("MyProgram.zig", "rb").read()          # a .sig, .zig, .lpz or folder zip
sig, where, seen = sigfile.find(data, name="MyProgram.zig")
info = sigfile.parse(sig)

for s in info["signals"][:5]:
    print(s["n"], s["t"], s["mod"] or "-", s["name"])
print(sigfile.counts(info["signals"]))              # {'d': ..., 'a': ..., 's': ..., 'modules': ...}
```

Each signal is a dict:

| Key | Meaning |
|---|---|
| `n` | the signal's number (what the processor knows it by) |
| `name` | its name; for a signal inside a module, the name without the module path |
| `t` | `d` digital, `a` analog, `s` serial, `c` the constants `0` / `1` |
| `mod` | the module's path as SIMPL Windows numbers it (`S-5.1`), or `''` for the program itself |
| `f` | the record's flags byte, kept as-is (its bits are not confirmed) |

`find` prefers a loose `.sig`, then a `.zig`, then one packed inside an `.lpz`. Anything wrong raises
`sigfile.SigError` with a plain message.

### The .sig format

```
"[LOGOSSIG001.000]"          ASCII header
then one record per signal:
    uint16 LE   record length, these two bytes included
    UTF-16 LE   the signal's name (length - 8 bytes)
    uint32 LE   the signal's number
    uint8       type: 0 digital, 1 analog, 2 serial, 128 constant
    uint8       flags
```

A signal inside a module is stored as `::<name>:<path>.<number as 8 hex digits>`, for example
`::level_fb:S-5.1.0000012A`.

On a 4-Series processor the file sits in the program slot's folder over SFTP: `Program01` for slot 1, and so on.

## The console protocol

What Toolbox sends on the processor's console (SSH or telnet), and what `proto.py` builds:

| Line | Meaning |
|---|---|
| `MDBGSIGNAL:<slot> -S:ON <numbers>` | start reporting those signals |
| `MDBGSIGNAL:<slot> -S:OFF <numbers>` | stop reporting them |
| `MDBGSIGNAL:<slot> -S:SYNC <numbers>` | report their present values now |
| `SETSIGNAL:<slot> <number> <value>` | set a signal: `0`/`1`, `pNN` (pulse NN ms) or `iNN` (inverse pulse) for a digital; `0`-`65535` for an analog; `"text"` for a serial |

The processor then writes each change of a reported signal as a frame:

```
\x01 \r\n <number as 8 hex>:<slot>=<value> \r\n \x03
```

Digital and analog values are decimal. Serial values come as hex bytes, `[48][69]` for `Hi`.

```python
from crestron_debugger import proto

proto.watch_lines(1, "ON", [12, 13, 300])   # ['MDBGSIGNAL:1 -S:ON 12 13 300'], 50 numbers per line
proto.set_line(1, 12, "d", "p500")          # 'SETSIGNAL:1 12 p500'
proto.set_line(1, 40, "a", 32768)           # 'SETSIGNAL:1 40 32768'
proto.set_line(1, 77, "s", "Hello")         # 'SETSIGNAL:1 77 "Hello"'

buf = "\x01\r\n0000000C:1=1\r\n\x03"
values, rest = proto.frames(buf)            # [(12, 1, '1')], ''
proto.serial_text("[48][69]")               # 'Hi'
```

`proto.check(line)` returns `''` for a line that may be sent and a reason otherwise. It allows only the lines above
and `info` (used to wake the console prompt). Limits: program slot 1-10, at most 50 numbers per `MDBGSIGNAL` line,
pulses up to 10,000 ms, serials up to 200 printable ASCII characters with no `"`.

**`ALL ON` and `-R` are never allowed.** They change the debug flags for every console session on the processor,
including a technician's Toolbox that is connected at the same time. Turn off the flags you turned on when you are
done, and release any digital you set high.

## Tested on

The protocol was taken from a capture of Toolbox talking to a 4-Series processor, and used live against a 4-Series
DIN-AP3 (program slot 1, about 1,400 signals traced). Other models and 3-Series processors have not been tried.

## License

MIT - see [LICENSE](LICENSE).
