"""A PDF content stream as bytes: its tokens, and the line moves (T*, ', ",
TD) rewritten as explicit Td moves. Bytes in, bytes out.
"""

from __future__ import annotations

# --- explicit line moves -------------------------------------------------------
#
# MuPDF 1.28 (PyMuPDF 1.28.0-1.28.2) mangles text positioning when it
# rewrites a page for a redaction: it drops TL (the leading) and turns Td into
# TD, so the lines that follow with T*, ' or " jump, often off the page, and
# the dates on them are not even removed. AutoCAD and Word title blocks use
# exactly those operators. So before the redaction the page's own line moves
# are made explicit ("0 -17 Td" instead of T* with a leading of 17), which
# draws the same page and does not depend on the leading at all. The
# before/after check still compares with the untouched original.

_DELIMS = b"()<>[]{}/%"
_SPACE = b" \t\r\n\f\x00"


def _num(value: float) -> bytes:
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return b"0" if text in ("", "-0") else text.encode()


def _tokens(data: bytes):
    """Yield (start, end, kind, value) over a content stream; kind is "num",
    "op" or "other" (strings, names, arrays, dictionaries, inline images)."""
    i, n = 0, len(data)
    while i < n:
        c = data[i]
        if c in _SPACE:
            i += 1
        elif c == 0x25:  # % comment
            while i < n and data[i] not in b"\r\n":
                i += 1
        elif c == 0x28:  # ( literal string, nested parentheses and escapes
            start, depth = i, 0
            while i < n:
                ch = data[i]
                if ch == 0x5C:
                    i += 2
                    continue
                if ch == 0x28:
                    depth += 1
                elif ch == 0x29:
                    depth -= 1
                    if depth == 0:
                        i += 1
                        break
                i += 1
            yield start, i, "other", None
        elif c == 0x3C and data[i + 1:i + 2] != b"<":  # <hex string>
            start = i
            i = data.find(b">", i) + 1 or n
            yield start, i, "other", None
        elif c in b"<>[]{}":  # dictionary and array brackets: no operators inside
            yield i, i + (2 if data[i:i + 2] in (b"<<", b">>") else 1), "other", None
            i += 2 if data[i:i + 2] in (b"<<", b">>") else 1
        else:
            start = i
            i += 1
            while i < n and data[i] not in _SPACE and data[i] not in _DELIMS:
                i += 1
            word = data[start:i]
            if c == 0x2F:  # /Name
                yield start, i, "other", None
                continue
            try:
                yield start, i, "num", float(word)
                continue
            except ValueError:
                pass
            yield start, i, "op", word
            if word == b"ID":  # inline image data: skip to EI
                j = i + 1
                while True:
                    j = data.find(b"EI", j)
                    if j < 0:
                        i = n
                        break
                    after = data[j + 2:j + 3]
                    if data[j - 1:j] in _SPACE and (not after or after in _SPACE):
                        i = j
                        break
                    j += 2


def explicit_line_moves(data: bytes, leading: float = 0.0):
    """Rewrite T*, ', " and TD in a content stream as explicit Td moves.
    Return (new data or None if unchanged, leading at the end)."""
    out, last = [], 0
    operands = []          # (start, end, kind, value) since the last operator
    stack = []             # leading saved by q, restored by Q
    changed = False
    for start, end, kind, value in _tokens(data):
        if kind != "op":
            operands.append((start, end, kind, value))
            continue
        op = value
        new = None
        if op == b"q":
            stack.append(leading)
        elif op == b"Q":
            leading = stack.pop() if stack else 0.0
        elif op == b"TL" and operands and operands[-1][2] == "num":
            leading = operands[-1][3]
        elif op == b"TD" and len(operands) >= 2 and operands[-1][2] == operands[-2][2] == "num":
            tx, ty = operands[-2][3], operands[-1][3]
            leading = -ty
            new = (operands[-2][0], _num(-ty) + b" TL " + _num(tx) + b" " + _num(ty) + b" Td")
        elif op == b"T*":
            new = (start, b"0 " + _num(-leading) + b" Td")
        elif op == b"'" and operands:
            s = operands[-1]
            new = (s[0], b"0 " + _num(-leading) + b" Td " + data[s[0]:s[1]] + b" Tj")
        elif op == b'"' and len(operands) >= 3 and operands[-3][2] == operands[-2][2] == "num":
            aw, ac, s = operands[-3], operands[-2], operands[-1]
            new = (aw[0], data[aw[0]:aw[1]] + b" Tw " + data[ac[0]:ac[1]] + b" Tc 0 "
                   + _num(-leading) + b" Td " + data[s[0]:s[1]] + b" Tj")
        if new is not None:
            out.append(data[last:new[0]])
            out.append(new[1])
            last = end
            changed = True
        operands = []
    if not changed:
        return None, leading
    out.append(data[last:])
    return b"".join(out), leading


# --- one character per string in a TJ array ------------------------------------
#
# MuPDF 1.28.6 (PyMuPDF 2.1) miscounts the gap it leaves when a redaction
# removes glyphs from the middle of one string of a TJ array: the text that
# follows on the line comes one glyph closer (measured on 10/11/2026: 5 pt at
# 9 pt, the dash of "DATA: 14/08/2026 - OK" under the new date). It counts
# right with Tj, and with TJ when the glyphs removed begin a string of the
# array. So for an engine that miscounts (the app asks it: app/pdf.py) every
# string of a TJ array is split, before the redaction, into one string per
# character code: "[(DATA: 14/08/2026 - OK)] TJ" becomes "[<44><41>...] TJ",
# which draws the same page, and whatever is removed then begins a string.
# The split needs the bytes of a character code in the font in use (1 in a
# simple font, 2 in a composite font with an Identity encoding): where the
# font is not known the array stays as it is, and the before/after check has
# the last word, as it always has.

_ESCAPES = {0x6E: 0x0A, 0x72: 0x0D, 0x74: 0x09, 0x62: 0x08, 0x66: 0x0C, 0x28: 0x28, 0x29: 0x29, 0x5C: 0x5C}


def _string_bytes(token: bytes) -> bytes | None:
    """The bytes a string of a content stream stands for: (literal), with its
    escapes, or <hex>. None when it is neither."""
    if token.startswith(b"<") and token.endswith(b">") and not token.startswith(b"<<"):
        digits = bytes(c for c in token[1:-1] if c not in _SPACE)
        if len(digits) % 2:
            digits += b"0"                      # a missing last digit reads as 0
        try:
            return bytes.fromhex(digits.decode("ascii"))
        except (ValueError, UnicodeDecodeError):
            return None
    if not (token.startswith(b"(") and token.endswith(b")")):
        return None
    body, out, i = token[1:-1], bytearray(), 0
    while i < len(body):
        c = body[i]
        if c == 0x5C:                           # a backslash
            i += 1
            if i >= len(body):
                break
            c = body[i]
            if c in _ESCAPES:
                out.append(_ESCAPES[c])
                i += 1
            elif 0x30 <= c <= 0x37:             # \ddd, one to three octal digits
                value, j = 0, i
                while j < len(body) and j < i + 3 and 0x30 <= body[j] <= 0x37:
                    value = value * 8 + body[j] - 0x30
                    j += 1
                out.append(value & 0xFF)
                i = j
            elif c == 0x0D:                     # a line continued: backslash, end of line
                i += 2 if body[i + 1:i + 2] == b"\n" else 1
            elif c == 0x0A:
                i += 1
            else:                               # any other: the backslash is ignored
                out.append(c)
                i += 1
        elif c == 0x0D:                         # an end of line inside a string reads as one LF
            out.append(0x0A)
            i += 2 if body[i + 1:i + 2] == b"\n" else 1
        else:
            out.append(c)
            i += 1
    return bytes(out)


def split_shown_strings(data: bytes, code_bytes: dict, font: str | None = None):
    """Rewrite the strings of every TJ array as one string per character
    code. `code_bytes` gives, for the name of a font of the page ("F1"), the
    bytes of one of its character codes; an array shown in a font that is
    not there, or holding a string that is not a whole number of codes, is
    left as it is. Return (new data or None if unchanged, the font in use at
    the end: the next stream of the page starts with it)."""
    out, last = [], 0
    operands = []          # (start, end, kind, value) since the last operator
    stack = []             # the font saved by q, restored by Q
    changed = False
    for start, end, kind, value in _tokens(data):
        if kind != "op":
            operands.append((start, end, kind, value))
            continue
        op = value
        if op == b"q":
            stack.append(font)
        elif op == b"Q":
            font = stack.pop() if stack else font
        elif op == b"Tf" and len(operands) >= 2 and data[operands[-2][0]:operands[-2][0] + 1] == b"/":
            font = data[operands[-2][0] + 1:operands[-2][1]].decode("latin-1")
        elif op == b"TJ" and operands and data[operands[-1][0]:operands[-1][1]] == b"]":
            width = code_bytes.get(font)
            opening = next((k for k in range(len(operands) - 1, -1, -1)
                            if data[operands[k][0]:operands[k][1]] == b"["), None)
            pieces = _one_code_per_string(data, operands[opening + 1:-1], width) if width and opening is not None else None
            if pieces is not None:
                out.append(data[last:operands[opening][0]])
                out.append(b"[" + pieces + b"]")
                last = operands[-1][1]
                changed = True
        operands = []
    if not changed:
        return None, font
    out.append(data[last:])
    return b"".join(out), font


def _one_code_per_string(data: bytes, elements, width: int) -> bytes | None:
    """The inside of a TJ array with each string split into strings of one
    character code (`width` bytes), the numbers where they were. None when
    there is nothing to split or something that cannot be."""
    pieces, split = [], False
    for start, end, kind, _value in elements:
        token = data[start:end]
        if kind == "num":
            pieces.append(b" " + token + b" ")
            continue
        raw = _string_bytes(token)
        if raw is None or len(raw) % width:
            return None
        if len(raw) > width:
            split = True
        pieces.append(b"".join(b"<" + raw[k:k + width].hex().encode("ascii") + b">" for k in range(0, len(raw), width)))
    return b"".join(pieces) if split else None
