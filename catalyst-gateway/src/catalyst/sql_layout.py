"""The same SQL, with layout removed and nothing else.

Two queries that differ only in whitespace or keyword case are the same query.
Judging them different is the defect class behind "Edited by hand" appearing on
queries nobody edited: the UI wrote the byte comparison out at seven call sites
before the rule got one owner, and this module is that owner's counterpart on
the gateway side — `POST /versions` used the same byte test to drop declared
columns and to decide whether a save is new content at all.

String literals and dollar-quoted bodies are copied through untouched: the
spaces in 'HIV viral load' and the digits in '990D9%' are data, and collapsing
them would silently change what the query asks for.

This mirrors ``normalizeSqlLayout`` in ``catalyst-ui``'s ``editorDigest.ts``.
The two are a behavioral contract, not shared code — each side carries its own
tests, and a change to what "layout" means must land in both.
"""

from __future__ import annotations

import re

_DOLLAR_TAG = re.compile(r"[A-Za-z_]\w*")


def normalize_sql_layout(sql: str) -> str:
    out: list[str] = []
    index = 0
    pending_space = False
    quote: str | None = None
    dollar_tag: str | None = None
    length = len(sql)

    def push(text: str) -> None:
        nonlocal pending_space
        if pending_space and out:
            out.append(" ")
        pending_space = False
        out.append(text)

    while index < length:
        char = sql[index]

        if dollar_tag is not None:
            if sql.startswith(dollar_tag, index):
                out.append(dollar_tag)
                index += len(dollar_tag)
                dollar_tag = None
            else:
                out.append(char)
                index += 1
            continue

        if quote is not None:
            out.append(char)
            index += 1
            if char == quote:
                # A doubled quote is an escape, not the end of the literal.
                if index < length and sql[index] == quote:
                    out.append(sql[index])
                    index += 1
                else:
                    quote = None
            continue

        if char in ("'", '"'):
            push(char)
            quote = char
            index += 1
            continue

        if char == "$":
            end = sql.find("$", index + 1)
            if end != -1:
                tag = sql[index + 1 : end]
                if tag == "" or _DOLLAR_TAG.fullmatch(tag):
                    dollar_tag = sql[index : end + 1]
                    push(dollar_tag)
                    index = end + 1
                    continue

        if char.isspace():
            pending_space = True
            index += 1
            continue

        push(char.lower())
        index += 1

    return "".join(out)


def sql_layout_matches(left: str, right: str) -> bool:
    """Whether two SQL texts are the same query up to layout."""
    return normalize_sql_layout(left) == normalize_sql_layout(right)
