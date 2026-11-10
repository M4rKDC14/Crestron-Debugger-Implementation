"""SIMPL Debugger lines on a Crestron processor's console: what may be sent, and how values come back.

Learnt from a capture of Crestron Toolbox's SIMPL Debugger and the processor's own help ('mdbgsignal ?',
'setsignal ?'):
    MDBGSIGNAL:<slot> -S:ON <numbers>      turn on the debug flag of those signals
    MDBGSIGNAL:<slot> -S:OFF <numbers>     ... and off again
    MDBGSIGNAL:<slot> -S:SYNC <numbers>    write their present values
    SETSIGNAL:<slot> <number> <value>      0 / 1, pXX (pulse XX ms), iXX (inverse pulse) for a digital; 0-65535 for an
                                           analog; a quoted string for a serial
The processor writes each value of a flagged signal as
    \\x01 \\r\\n <signal number as 8 hex>:<slot>=<value> \\r\\n \\x03
a digital or analog value in decimal, a serial one as hex bytes '[4C][69]...' (empty for an empty string).

check() allows only those lines (and 'info'). ALL ON and -R are deliberately not allowed: they change the debug flags
of every console session on the processor, including a technician's Toolbox.

Standard library only.
"""
import re

MAX_PER_LINE = 50                     # signal numbers in one MDBGSIGNAL line (Toolbox sends about 50)
MAX_SERIAL = 200                      # characters of a serial sent with SETSIGNAL
MAX_PULSE = 10000                     # ms
_NUMS = r"\d{1,6}(?: \d{1,6}){0,%d}" % (MAX_PER_LINE - 1)
_MDBG = re.compile(r"MDBGSIGNAL:(\d{1,2}) -S:(ON|OFF|SYNC) (" + _NUMS + ")")
_SET = re.compile(r'SETSIGNAL:(\d{1,2}) (\d{1,6}) (0|1|[pi]\d{1,5}|\d{1,5}|"[^"\x00-\x1f\x7f-\uffff]{0,%d}")' % MAX_SERIAL)
FRAME = re.compile(r"\x01\r?\n([0-9A-Fa-f]{8}):(\d{1,2})=([^\r\n\x01\x03]*)\r?\n\x03")
PROMPT = re.compile(r"(?:^|[\r\n])([A-Za-z0-9_\-.]+)>[ \t]*$")


def check(line):
    """'' when the line may be sent to the processor in a debug session, else why not."""
    line = str(line or "")
    if line == "info":
        return ""
    m = _MDBG.fullmatch(line)
    if m:
        return "" if 1 <= int(m.group(1)) <= 10 else "Program slot: 1 to 10"
    m = _SET.fullmatch(line)
    if m:
        if not 1 <= int(m.group(1)) <= 10:
            return "Program slot: 1 to 10"
        v = m.group(3)
        if v[0] in "pi" and not 1 <= int(v[1:]) <= MAX_PULSE:
            return f"Pulse: 1 to {MAX_PULSE} ms"
        if v[0].isdigit() and int(v) > 65535:
            return "Analog: 0 to 65535"
        return ""
    return "Not a signal debugging command - only MDBGSIGNAL -S:ON/OFF/SYNC, SETSIGNAL and info are sent"


def watch_lines(slot, op, numbers):
    """MDBGSIGNAL lines for many signals, MAX_PER_LINE at a time. op: ON / OFF / SYNC."""
    ns = sorted({int(n) for n in numbers})
    return [f"MDBGSIGNAL:{int(slot)} -S:{op} " + " ".join(str(n) for n in ns[i:i + MAX_PER_LINE])
            for i in range(0, len(ns), MAX_PER_LINE)]


def set_line(slot, number, kind, value):
    """The SETSIGNAL line. kind: 'd' (value 0 / 1 / 'pNN' / 'iNN'), 'a' (0-65535) or 's' (text).
    Raises ValueError saying what is wrong."""
    slot, number = int(slot), int(number)
    if kind == "d":
        v = str(value)
        if v not in ("0", "1") and not re.fullmatch(r"[pi]\d{1,5}", v):
            raise ValueError("A digital is set to 0 or 1, or pulsed")
    elif kind == "a":
        try:
            n = int(str(value).strip())
        except ValueError:
            raise ValueError("An analog takes a whole number, 0 to 65535")
        if not 0 <= n <= 65535:
            raise ValueError("An analog takes a whole number, 0 to 65535")
        v = str(n)
    elif kind == "s":
        t = str(value)
        if len(t) > MAX_SERIAL:
            raise ValueError(f"A serial sent from here is at most {MAX_SERIAL} characters")
        if any(c == '"' or not " " <= c <= "~" for c in t):
            raise ValueError('A serial sent from here is plain text: letters, digits, spaces and signs, no " and no '
                             'control characters')
        v = f'"{t}"'
    else:
        raise ValueError("This signal cannot be set")
    line = f"SETSIGNAL:{slot} {number} {v}"
    why = check(line)
    if why:
        raise ValueError(why)
    return line


def serial_text(raw):
    """A serial value as the processor writes it ('[4C][69]...') -> text; other bytes shown as \\xHH."""
    out = []
    for m in re.finditer(r"\[([0-9A-Fa-f]{2})\]|(.)", raw, re.S):
        if m.group(1):
            b = int(m.group(1), 16)
            out.append(chr(b) if 32 <= b < 127 else f"\\x{b:02X}")
        else:
            out.append(m.group(2))
    return "".join(out)


def frames(buf):
    """(values, rest): [(signal number, slot, raw value)] found in buf, and what is left to wait for more."""
    out, last = [], 0
    for m in FRAME.finditer(buf):
        out.append((int(m.group(1), 16), int(m.group(2)), m.group(3)))
        last = m.end()
    rest = buf[last:]
    k = rest.rfind("\x01")                      # keep a frame that has started; drop the rest of the console text
    rest = rest[k:] if k >= 0 else rest[-200:]
    return out, rest[-4096:]
