"""Incremental extraction of TALK/THINK action prose from a streamed response.

The vendored act loop asks the model for a JSON document — a single
``{"action": {"type": ..., "content": ...}, "cognitive_state": {...}}`` (with an
optional top-level ``"reasoning"``) or a multi-action ``{"actions": [...], ...}``
— whose action objects carry ``"type"`` and ``"content"`` fields.  When that
document streams back token by token, forwarding the raw fragments as
``talk_delta`` leaks the JSON envelope into the speech feed (the defect this
scanner fixes).

:class:`StreamingActionScanner` folds the raw fragment stream into *prose*: it
emits only the incremental characters of a ``content`` value that belongs to a
``TALK`` or ``THINK`` action, routing them to ``on_talk`` / ``on_think``.  It
decodes JSON string escapes incrementally (robust to a chunk boundary landing
mid-escape, mid-``\\uXXXX``, or mid-key), and tolerates whitespace/formatting
variance.  When it cannot confidently lock onto such a content field — non-JSON
prose, an unexpected top-level shape, or malformed JSON — it emits **nothing**
rather than leaking: the full text is always still available via the M1
``*_completed`` events, so silent degradation is contract-correct while
raw-envelope leakage is not.

The scanner assumes each action object lists ``"type"`` before ``"content"`` —
the order the ``Action`` pydantic schema serializes — so it knows the action's
kind before its prose streams.  A ``content`` value seen *before* its ``type``
is not streamed (degrade, never leak).
"""

from __future__ import annotations

from collections.abc import Callable

#: Callback receiving an incremental prose fragment.
DeltaSink = Callable[[str], None]

# Object-frame expectations (what the next structural token should be).
_EXPECT_KEY = "key"
_EXPECT_COLON = "colon"
_EXPECT_VALUE = "value"
_EXPECT_COMMA = "comma"

_WHITESPACE = " \t\n\r"
_PRIMITIVE_START = "-0123456789tfn"  # number / true / false / null
_ESCAPES = {
    '"': '"',
    "\\": "\\",
    "/": "/",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
}

_TALK = "TALK"
_THINK = "THINK"


class _Frame:
    """One open ``{...}`` or ``[...]`` container while scanning."""

    __slots__ = ("kind", "expect", "key", "action_type")

    def __init__(self, kind: str) -> None:
        self.kind = kind  # "obj" | "arr"
        self.expect = _EXPECT_KEY if kind == "obj" else _EXPECT_VALUE
        self.key: str | None = None
        #: The value of this object's ``"type"`` field, once seen (TALK/THINK/…).
        self.action_type: str | None = None


class StreamingActionScanner:
    """Stream TALK/THINK ``content`` prose out of the act-loop JSON envelope.

    Construct one **per model call** (each ``send_message`` is a fresh JSON
    document): feed it the raw visible-text fragments, and it invokes ``on_talk``
    / ``on_think`` with decoded prose increments.  Call :meth:`close` when the
    call ends to flush any trailing buffered characters.
    """

    def __init__(
        self,
        *,
        on_talk: DeltaSink | None = None,
        on_think: DeltaSink | None = None,
    ) -> None:
        self._on_talk = on_talk
        self._on_think = on_think
        self._stack: list[_Frame] = []
        #: Disengaged: a non-envelope top level or a structural error → silence.
        self._broken = False
        # -- string-scanning state (persists across feed() calls) --
        self._in_string = False
        self._is_key = False
        self._str_role: str | None = None  # "talk" | "think" | "type" | None
        self._escape = False
        self._unicode: list[str] | None = None
        self._key_accum: list[str] = []
        self._type_accum: list[str] = []
        self._value_frame: _Frame | None = None
        # -- pending decoded prose to flush to a sink --
        self._emit_buffer: list[str] = []
        self._emit_sink: DeltaSink | None = None
        # -- bare primitive (number/true/false/null) scanning --
        self._in_primitive = False
        self._primitive_frame: _Frame | None = None

    # -- public API ---------------------------------------------------------

    def feed(self, chunk: str) -> None:
        """Consume a raw visible-text fragment, emitting any prose increments."""
        if self._broken or not chunk:
            return
        for ch in chunk:
            self._consume(ch)
            if self._broken:
                break
        self._flush()

    def close(self) -> None:
        """Flush any buffered prose at the end of the call."""
        if not self._broken:
            self._flush()

    # -- flush / degrade ----------------------------------------------------

    def _flush(self) -> None:
        if self._emit_buffer and self._emit_sink is not None:
            self._emit_sink("".join(self._emit_buffer))
        self._emit_buffer = []

    def _fail(self) -> None:
        """Disengage: emit nothing further (non-envelope or malformed input)."""
        self._broken = True
        self._emit_buffer = []
        self._emit_sink = None

    # -- character dispatch -------------------------------------------------

    def _consume(self, ch: str) -> None:
        if self._in_string:
            self._consume_string_char(ch)
            return
        if self._in_primitive:
            self._consume_primitive_char(ch)
            return
        if ch in _WHITESPACE:
            return
        if not self._stack:
            # The act-loop envelope is always a top-level object; anything else
            # (non-JSON prose, a bare value) is not lockable → stay silent.
            if ch == "{":
                self._stack.append(_Frame("obj"))
            else:
                self._fail()
            return
        frame = self._stack[-1]
        if frame.kind == "obj":
            self._consume_obj(frame, ch)
        else:
            self._consume_arr(frame, ch)

    def _consume_obj(self, frame: _Frame, ch: str) -> None:
        if frame.expect == _EXPECT_KEY:
            if ch == '"':
                self._start_string(frame, is_key=True)
            elif ch == "}":
                self._pop_frame()
            else:
                self._fail()
        elif frame.expect == _EXPECT_COLON:
            if ch == ":":
                frame.expect = _EXPECT_VALUE
            else:
                self._fail()
        elif frame.expect == _EXPECT_VALUE:
            self._start_value(frame, ch)
        else:  # _EXPECT_COMMA
            if ch == ",":
                frame.expect = _EXPECT_KEY
            elif ch == "}":
                self._pop_frame()
            else:
                self._fail()

    def _consume_arr(self, frame: _Frame, ch: str) -> None:
        if frame.expect == _EXPECT_VALUE:
            if ch == "]":
                self._pop_frame()
            else:
                self._start_value(frame, ch)
        else:  # _EXPECT_COMMA
            if ch == ",":
                frame.expect = _EXPECT_VALUE
            elif ch == "]":
                self._pop_frame()
            else:
                self._fail()

    def _start_value(self, frame: _Frame, ch: str) -> None:
        if ch == "{":
            self._stack.append(_Frame("obj"))
        elif ch == "[":
            self._stack.append(_Frame("arr"))
        elif ch == '"':
            self._start_string(frame, is_key=False)
        elif ch in _PRIMITIVE_START:
            self._in_primitive = True
            self._primitive_frame = frame
        else:
            self._fail()

    # -- string scanning ----------------------------------------------------

    def _start_string(self, frame: _Frame, *, is_key: bool) -> None:
        self._in_string = True
        self._is_key = is_key
        self._escape = False
        self._unicode = None
        self._value_frame = frame
        if is_key:
            self._key_accum = []
            return
        key = frame.key
        if key == "content" and frame.action_type in (_TALK, _THINK):
            self._str_role = "talk" if frame.action_type == _TALK else "think"
            self._emit_sink = (
                self._on_talk if frame.action_type == _TALK else self._on_think
            )
        elif key == "type":
            self._str_role = "type"
            self._type_accum = []
        else:
            self._str_role = None

    def _consume_string_char(self, ch: str) -> None:
        if self._escape:
            self._apply_escape(ch)
            return
        if self._unicode is not None:
            self._unicode.append(ch)
            if len(self._unicode) == 4:
                hex_digits = "".join(self._unicode)
                self._unicode = None
                try:
                    self._emit_char(chr(int(hex_digits, 16)))
                except ValueError:
                    pass  # not valid hex: drop the escape, keep scanning
            return
        if ch == "\\":
            self._escape = True
            return
        if ch == '"':
            self._end_string()
            return
        self._emit_char(ch)

    def _apply_escape(self, ch: str) -> None:
        self._escape = False
        if ch == "u":
            self._unicode = []
            return
        self._emit_char(_ESCAPES.get(ch, ch))

    def _emit_char(self, decoded: str) -> None:
        if self._is_key:
            self._key_accum.append(decoded)
        elif self._str_role == "type":
            self._type_accum.append(decoded)
        elif self._str_role in ("talk", "think"):
            self._emit_buffer.append(decoded)
        # role None → a value we don't surface (reasoning, cognitive_state, …)

    def _end_string(self) -> None:
        self._in_string = False
        frame = self._value_frame
        if self._is_key:
            if frame is not None:
                frame.key = "".join(self._key_accum)
                frame.expect = _EXPECT_COLON
        else:
            if self._str_role == "type" and frame is not None:
                frame.action_type = "".join(self._type_accum)
            elif self._str_role in ("talk", "think"):
                self._flush()  # emit the tail of this content value
            if frame is not None:
                frame.expect = _EXPECT_COMMA
            self._emit_sink = None
        self._str_role = None
        self._is_key = False

    # -- primitives & container close --------------------------------------

    def _consume_primitive_char(self, ch: str) -> None:
        if ch in _WHITESPACE or ch in ",]}":
            self._in_primitive = False
            if self._primitive_frame is not None:
                self._primitive_frame.expect = _EXPECT_COMMA
            self._consume(ch)  # re-process the delimiter in structural context
        # otherwise: still inside the bare token → ignore

    def _pop_frame(self) -> None:
        self._stack.pop()
        if self._stack:
            # The just-closed container was a value in its parent.
            self._stack[-1].expect = _EXPECT_COMMA


__all__ = ["StreamingActionScanner", "DeltaSink"]
