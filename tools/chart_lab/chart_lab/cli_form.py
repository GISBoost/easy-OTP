"""Turn an argparse (sub)parser into a form, and form values back into an argv list.

Same idea as transit_charts/registry.py for charts: the CLI parser is the single source of
truth, so a flag added to `family_a/cli.py` shows up in the GUI with no change here.

The introspection (`describe`, `to_argv`) has no Gradio dependency; `build_widgets` imports
Gradio lazily.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Field:
    dest: str
    flag: str | None          # first long option; None for a positional
    kind: str                 # "text" | "int" | "float" | "bool" | "choice" | "multi"
    default: Any
    choices: tuple | None
    help: str
    required: bool
    repeat: bool = False      # multi only: True = repeat the flag per value, False = one flag, many values
    path: str | None = None   # "file" | "dir" | "save" (overlay hint, informational for the widget)
    advanced: bool = False


def _subparser(parser: argparse.ArgumentParser, name: str) -> argparse.ArgumentParser:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices[name]
    raise KeyError(f"parser has no subcommands (wanted {name!r})")


def describe(
    parser: argparse.ArgumentParser,
    subcommand: str | None = None,
    overlay: dict[str, dict] | None = None,
) -> list[Field]:
    """One Field per argument of `parser` (or of its `subcommand`), in declaration order.

    `overlay` maps dest -> {"path": "file"|"dir"|"save", "advanced": bool}. Without an
    explicit "advanced", required arguments are basic and optional ones advanced.
    """
    if subcommand:
        parser = _subparser(parser, subcommand)
    overlay = overlay or {}
    formatter = parser._get_formatter()
    out: list[Field] = []
    for a in parser._actions:
        if isinstance(a, (argparse._HelpAction, argparse._SubParsersAction)):
            continue
        ov = overlay.get(a.dest, {})
        flag = next((o for o in a.option_strings if o.startswith("--")), None) or (
            a.option_strings[0] if a.option_strings else None
        )
        repeat = isinstance(a, argparse._AppendAction)
        if isinstance(a, (argparse._StoreTrueAction, argparse._StoreFalseAction)):
            kind = "bool"
        elif repeat or a.nargs in ("+", "*"):
            kind = "multi"
        elif a.choices:
            kind = "choice"
        elif a.type is int or (a.type is not float and isinstance(a.default, int) and not isinstance(a.default, bool)):
            kind = "int"
        elif a.type is float or isinstance(a.default, float):
            kind = "float"
        else:
            kind = "text"
        try:
            help_text = formatter._expand_help(a) if a.help else ""
        except (KeyError, TypeError, ValueError):
            help_text = a.help or ""
        default = a.default
        if default is argparse.SUPPRESS:
            default = None
        out.append(Field(
            dest=a.dest, flag=flag, kind=kind, default=default,
            choices=tuple(a.choices) if a.choices else None,
            help=help_text, required=bool(a.required) or not a.option_strings,
            repeat=repeat, path=ov.get("path"),
            advanced=ov.get("advanced", not (a.required or not a.option_strings)),
        ))
    return out


def _clean(s: str) -> str:
    """Trim spaces and the quotes Windows 'Copy as path' adds."""
    return s.strip().strip('"').strip()


def _is_empty(v: Any) -> bool:
    return v is None or v == "" or v == [] or v is False


def to_argv(fields: list[Field], values: dict[str, Any], subcommand: str | None = None) -> list[str]:
    """Form values -> argv. Empty widgets and values equal to the CLI default are omitted, so
    "not set" stays "not set" (the CLI tells those apart, e.g. transit_charts min_n_explicit).
    """
    argv: list[str] = [subcommand] if subcommand else []
    positionals: list[str] = []
    for f in fields:
        v = values.get(f.dest)
        if f.kind == "multi" and isinstance(v, str):
            v = [_clean(line) for line in v.splitlines() if _clean(line)]
        elif isinstance(v, str):
            v = _clean(v)
        if (f.kind == "text" and v == "" and f.flag and not f.required
                and f.default not in (None, "")):
            argv.extend([f.flag, ""])  # a cleared field with a non-empty default = explicit blank
            continue
        if _is_empty(v) or (not f.required and v == f.default):
            continue
        head = [f.flag] if f.flag else []  # a positional has no flag
        if f.kind == "bool":
            piece = head
        elif f.kind == "multi":
            vals = [str(item) for item in v]
            piece = [x for item in vals for x in head + [item]] if f.repeat else head + vals
        elif f.kind == "int":
            piece = head + [str(int(v))]
        else:
            piece = head + [str(v)]
        (argv if f.flag else positionals).extend(piece)
    return argv + positionals


def build_widgets(fields: list[Field]) -> tuple[list, list[Field]]:
    """Create Gradio components inside the current Blocks context.

    Basic fields first, then an "Advanced" accordion (closed). Returns (components, fields)
    in the same order, ready for `values = dict(zip(f.dest for f in fields, component_values))`.
    """
    import gradio as gr

    def make(f: Field):
        label = f.flag or f.dest
        if f.required:
            label += " *"
        common = {"label": label, "info": f.help or None}
        if f.kind == "bool":
            return gr.Checkbox(value=bool(f.default), **common)
        if f.kind == "choice":
            return gr.Dropdown(choices=list(f.choices), value=f.default, **common)
        if f.kind == "int":
            return gr.Number(value=f.default, precision=0, **common)
        if f.kind == "float":
            return gr.Number(value=f.default, **common)
        if f.kind == "multi":
            return gr.Textbox(value="\n".join(map(str, f.default or [])), lines=3,
                              placeholder="one value per line", **common)
        return gr.Textbox(value="" if f.default is None else str(f.default), **common)

    comps: dict[str, Any] = {}
    for f in (x for x in fields if not x.advanced):
        comps[f.dest] = make(f)
    adv = [x for x in fields if x.advanced]
    if adv:
        with gr.Accordion("Advanced", open=False):
            for f in adv:
                comps[f.dest] = make(f)
    ordered = [f for f in fields if not f.advanced] + adv
    return [comps[f.dest] for f in ordered], ordered
