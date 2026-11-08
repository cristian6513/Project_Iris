"""Bounded Gmail MIME to inert text. Standard library; also loaded by the isolated worker.

Never renders HTML, follows a URL, opens a file, or extracts an attached message.
Only unnamed, non-attachment text parts may use the optional body-part loader.
"""
from __future__ import annotations

import base64
import binascii
import re
from email.message import Message
from html.parser import HTMLParser

MAX_MESSAGE_BYTES = 16 * 1024 * 1024
MAX_TEXT_BYTES = 1024 * 1024
MAX_BODY_CHARS = 200_000
MAX_PARTS = 200
MAX_DEPTH = 20
SCHEMA = "iris.mail-body/v1"


class BodyError(ValueError):
    def __init__(self, code="body_unavailable"):
        super().__init__(code)
        self.code = code


# Bidi embedding/override/isolate controls: a U+202E inside a subject can make a file name read reversed.
BIDI_CONTROLS = frozenset("\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")


def clean_text(value):
    # Keep newlines; remove non-printing controls, including bidi overrides.
    return "".join(c for c in value.replace("\r\n", "\n").replace("\r", "\n")
                   if c in "\n\t" or (ord(c) >= 32 and ord(c) != 127 and c not in BIDI_CONTROLS))


class _HTMLText(HTMLParser):
    hidden = frozenset({"head", "script", "style", "template", "svg", "iframe", "object", "form"})
    blocks = frozenset({"p", "div", "br", "hr", "li", "tr", "table", "section", "article", "h1", "h2", "h3", "h4", "blockquote", "pre"})

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.suppressed = []
        # Open hidden tags by name: a closing tag no longer scans/copies the whole stack, which
        # made nested hidden tags quadratic (~10 s for a crafted 1 MB part; review 2026-10-19).
        self.open_hidden = {}
        self.output = []

    def handle_starttag(self, tag, attrs):
        if tag in self.hidden:
            self.suppressed.append(tag)
            self.open_hidden[tag] = self.open_hidden.get(tag, 0) + 1
        if not self.suppressed and tag in self.blocks:
            self.output.append("\n")

    def handle_startendtag(self, tag, attrs):
        if not self.suppressed and tag in self.blocks:
            self.output.append("\n")

    def handle_endtag(self, tag):
        if self.open_hidden.get(tag):
            # Close up to the innermost open one, as before; each tag is popped once overall.
            while self.suppressed:
                top = self.suppressed.pop()
                self.open_hidden[top] -= 1
                if top == tag:
                    break
        if not self.suppressed and tag in self.blocks:
            self.output.append("\n")

    def handle_data(self, data):
        if not self.suppressed:
            self.output.append(data)


def validate_body(value):
    """Recheck the worker's DTO at Core's boundary; allow-list fields only."""
    if (not isinstance(value, dict) or value.get("schema") != SCHEMA
            or value.get("source_format") not in ("plain", "html_text", "empty")
            or not isinstance(value.get("text"), str) or len(value["text"]) > MAX_BODY_CHARS
            or any(type(value.get(k)) is not bool for k in ("lossy", "attachments_omitted"))):
        raise BodyError()
    return {"schema": SCHEMA, "text": clean_text(value["text"]), "source_format": value["source_format"],
            "lossy": value["lossy"], "attachments_omitted": value["attachments_omitted"]}


def extract_body(payload, load_part=None):
    count = decoded_bytes = 0
    omitted = False

    def walk(part, depth=0):
        nonlocal count, decoded_bytes, omitted
        count += 1
        if count > MAX_PARTS or depth > MAX_DEPTH:
            raise BodyError("body_too_large")
        if not isinstance(part, dict):
            raise BodyError()
        headers = Message()
        raw_headers = part.get("headers") or []
        if not isinstance(raw_headers, list) or len(raw_headers) > 200:
            raise BodyError()
        for item in raw_headers:
            if isinstance(item, dict) and str(item.get("name", "")).lower() in ("content-type", "content-disposition"):
                value = item.get("value")
                if not isinstance(value, str) or len(value) > 4096:
                    raise BodyError()
                headers[str(item["name"])] = value
        mime = str(part.get("mimeType") or headers.get_content_type()).lower()
        if (part.get("filename") or headers.get_filename() or headers.get_content_disposition() == "attachment"
                or mime == "message/rfc822"):
            omitted = True
            return None
        if mime.startswith("multipart/"):
            if mime in ("multipart/encrypted",):
                return None
            children = part.get("parts") or []
            if not isinstance(children, list) or len(children) > MAX_PARTS:
                raise BodyError("body_too_large")
            # Pick one representation, not duplicate plain+HTML alternatives.
            if mime == "multipart/alternative":
                children = sorted(children, key=lambda child: child.get("mimeType") != "text/plain" if isinstance(child, dict) else True)
                for child in children:
                    result = walk(child, depth + 1)
                    if result is not None and result[0].strip():
                        return result
                return ("", "empty", False)
            result = [item for child in children if (item := walk(child, depth + 1)) is not None]
            if not result:
                return None
            return ("\n\n".join(item[0] for item in result if item[0]),
                    "html_text" if any(item[1] == "html_text" for item in result) else "plain", any(item[2] for item in result))
        if mime not in ("text/plain", "text/html"):
            omitted = True
            return None
        body = part.get("body") or {}
        if not isinstance(body, dict):
            raise BodyError()
        size = body.get("size", 0)
        if type(size) is not int or size < 0 or size > MAX_TEXT_BYTES:
            raise BodyError("body_too_large")
        encoded = body.get("data")
        if encoded is None and body.get("attachmentId"):
            identity = body["attachmentId"]
            if not isinstance(identity, str) or not 0 < len(identity) <= 512 or load_part is None:
                raise BodyError()
            encoded = load_part(identity, size)
        if encoded is None:
            if size:
                raise BodyError()
            encoded = ""
        if not isinstance(encoded, str) or len(encoded) > (MAX_TEXT_BYTES * 4 // 3 + 4):
            raise BodyError("body_too_large")
        try:
            raw = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
        except (ValueError, binascii.Error):
            raise BodyError() from None
        decoded_bytes += len(raw)
        if decoded_bytes > MAX_TEXT_BYTES:
            raise BodyError("body_too_large")
        charset = headers.get_content_charset() or "utf-8"
        lossy = False
        try:
            content = raw.decode(charset, errors="strict")
        except (LookupError, UnicodeError):
            try:
                content = raw.decode(charset, errors="replace")
            except (LookupError, UnicodeError):
                content = raw.decode("utf-8", errors="replace")
            lossy = True
        if mime == "text/html":
            parser = _HTMLText()
            parser.feed(content); parser.close()
            content = re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", "".join(parser.output)).strip()
        return clean_text(content), "html_text" if mime == "text/html" else "plain", lossy

    result = walk(payload)
    if result is None:
        raise BodyError("body_unsupported")
    content, source, lossy = result
    if len(content) > MAX_BODY_CHARS:
        raise BodyError("body_too_large")
    return validate_body({"schema": SCHEMA, "text": content, "source_format": source,
                          "lossy": lossy, "attachments_omitted": omitted})
