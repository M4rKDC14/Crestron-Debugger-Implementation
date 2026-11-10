"""SIMPL Windows signal files (.sig), and the .zig / .lpz archives that carry them.

A .sig is written by the SIMPL Windows compiler next to the .lpz: every signal of the program with its number, which
is what the processor knows signals by. On a processor it sits in the program slot's folder (ProgramNN over SFTP) as
the .sig itself, or as a .zig - a zip archive holding the .sig - and a .zig can also be packed inside the .lpz. This
module reads any of those, and a zip of a whole folder.

The format (worked out from compiled programs; every byte accounted for):
    "[LOGOSSIG001.000]"                     ASCII header
    then one record per signal:
        uint16 LE   record length, these two bytes included
        UTF-16 LE   the signal's name (length - 8 bytes)
        uint32 LE   the signal's number
        uint8       type: 0 digital, 1 analog, 2 serial, 128 the constants '0' and '1'
        uint8       flags (seen: digital 0 / 1 / 3, analog and serial 7; '//' commented signals 0) - kept, not
                    interpreted: what each bit means is not confirmed
A signal inside a module is named '::<name>:<path>.<number as 8 hex digits>', the path being the module's place in
the program as SIMPL Windows numbers it ('S-5.1'; a module inside a module: 'S-3.4.1.1:S-1.6'). The hex equals the
record's number.

Standard library only; nothing here touches a processor.
"""
import io
import re
import struct
import zipfile

HEADER = b"[LOGOSSIG"
MAX_SIG = 32 * 1024 * 1024            # a large program's .sig is a few hundred kB
MAX_SIGNALS = 300_000
MAX_NEST = 3                          # a folder zip -> .lpz -> .zig -> .sig
TYPES = {0: "d", 1: "a", 2: "s", 128: "c"}
MODULE = re.compile(r"^::(.+?):((?:S-[\d.]+)(?::S-[\d.]+)*)\.([0-9A-Fa-f]{8})$")


class SigError(ValueError):
    pass


def is_sig(data):
    return data[:len(HEADER)] == HEADER


def parse(data):
    """{'version': '001.000', 'signals': [{'n', 'name', 't', 'f', 'mod'}]} from a .sig's bytes.
    't': 'd' / 'a' / 's' / 'c' (constant); 'name': the signal's own name (for a module signal, without '::', the
    path and the number); 'mod': the module's path ('' for a signal of the program itself). Sorted by number.
    Raises SigError naming what is wrong."""
    if not is_sig(data):
        raise SigError("Not a SIMPL Windows signal file (.sig): it does not start with [LOGOSSIG")
    if len(data) > MAX_SIG:
        raise SigError(f"The signal file is {len(data) / 1e6:.0f} MB - more than the {MAX_SIG // 2**20} MB allowed")
    end = data.find(b"]", 0, 64)
    if end < 0:
        raise SigError("The signal file's header is not closed")
    version = data[len(HEADER):end].decode("ascii", "replace").lstrip("SIG")
    i, out, seen = end + 1, [], set()
    while i < len(data):
        if i + 2 > len(data):
            raise SigError(f"The signal file ends inside a record (byte {i})")
        n = struct.unpack_from("<H", data, i)[0]
        if n < 8 or n % 2 or i + n > len(data):
            raise SigError(f"Signal record {len(out) + 1} at byte {i} is damaged (length {n})")
        body = data[i + 2:i + n]
        try:
            raw = body[:-6].decode("utf-16-le")
        except UnicodeDecodeError:
            raise SigError(f"Signal record {len(out) + 1} at byte {i} has a name that is not text")
        num, typ, flags = struct.unpack_from("<IBB", body, len(body) - 6)
        name, mod = raw, ""
        m = MODULE.match(raw)
        if m and int(m.group(3), 16) == num:
            name, mod = m.group(1), m.group(2)
        out.append({"n": num, "name": name, "t": TYPES.get(typ, "?"), "f": flags, "mod": mod})
        seen.add(num)
        if len(out) > MAX_SIGNALS:
            raise SigError(f"More than {MAX_SIGNALS:,} signals - not a signal file this reads")
        i += n
    if not out:
        raise SigError("The signal file holds no signals")
    out.sort(key=lambda s: s["n"])
    return {"version": version, "signals": out}


def _zip(data):
    try:
        return zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError):
        return None


def _read_member(z, info):
    if info.file_size > MAX_SIG * 4:
        raise SigError(f"'{info.filename}' is {info.file_size / 1e6:.0f} MB unpacked - too large to be read")
    with z.open(info) as f:
        data = f.read(MAX_SIG * 4 + 1)
    if len(data) > MAX_SIG * 4:
        raise SigError(f"'{info.filename}' unpacks to more than it says - not read")
    return data


def find(data, name="", prefer=""):
    """The signal file in what was read: (sig bytes, where it was found, [every signal file seen]).
    data: a .sig, a .zig, an .lpz, or a zip of a program slot's folder. A .sig beats a .zig beats one packed in an
    .lpz; among several, the one named like `prefer` (the program's own name, without extension) wins, then the
    newest. Raises SigError saying what the folder holds when there is none."""
    if is_sig(data):
        return data, name or "the .sig", [name or ".sig"]
    found, others = [], []

    def walk(z, where, depth):
        for info in z.infolist():
            if info.is_dir():
                continue
            low = info.filename.lower()
            label = f"{where}{info.filename}"
            if low.endswith(".sig"):
                found.append((0, info.filename, info.date_time, label, lambda z=z, info=info: _read_member(z, info)))
            elif low.endswith((".zig", ".lpz", ".zip")) and depth < MAX_NEST:
                inner = _zip(_read_member(z, info))
                if inner is None and "/" not in info.filename:
                    others.append(info.filename)
                if inner is not None:
                    before = len(found)
                    walk(inner, f"{label} › ", depth + 1)
                    for k in range(before, len(found)):          # a .zig ranks after a loose .sig, an .lpz after both
                        r, *rest = found[k]
                        found[k] = (r + (1 if low.endswith(".zig") else 2), *rest)
            elif "/" not in info.filename:
                others.append(info.filename)
    z = _zip(data)
    if z is None:
        raise SigError("Not a signal file: neither a .sig nor a zip archive (.zig / .lpz)")
    walk(z, (name + " › ") if name else "", 0)
    if not found:
        held = sorted(others)
        raise SigError("No signal file (.sig or .zig) " + (f"in {name}" if name else "there") + ". "
                       + (f"It holds: {', '.join(held[:12])}{' …' if len(held) > 12 else ''}." if held else "It is empty.")
                       + " Compile the program with SIMPL Windows and load it with its .sig (or a .zig), or upload the .sig here.")
    p = prefer.lower().rsplit(".", 1)[0] if prefer else ""

    def stem(fn):
        return fn.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
    found.sort(key=lambda x: (x[0], 0 if p and stem(x[1]) == p else 1, tuple(-v for v in x[2])))
    best = found[0]
    sig = best[4]()
    if not is_sig(sig):
        raise SigError(f"'{best[3]}' is not a SIMPL Windows signal file")
    return sig, best[3], [x[3] for x in found]


def counts(signals):
    c = {"d": 0, "a": 0, "s": 0, "modules": len({s["mod"] for s in signals if s["mod"]})}
    for s in signals:
        if s["t"] in c:
            c[s["t"]] += 1
    return c
