"""Static checks for this local demo; these are not native CK3 acceptance."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
ADAPTER_PATHS = (
    'common/scripted_triggers/ttr_miv_adapter_triggers.txt',
    'common/scripted_effects/ttr_miv_adapter_effects.txt',
)
LOC_ASSIGNMENTS = {'title', 'desc', 'custom_tooltip', 'selection_tooltip', 'confirm_text'}
LOC_LINE = re.compile(r'^\s+([A-Za-z0-9_.-]+):\d+\s+"((?:[^"\\]|\\.)*)"\s*(?:#.*)?$')


@dataclass(frozen=True)
class Token:
    value: str
    line: int
    quoted: bool = False


def lex(text: str, label: str) -> list[Token]:
    """Keep quoted strings intact; braces and # inside them are not syntax."""
    tokens: list[Token] = []
    i, line, opened = 0, 1, []
    while i < len(text):
        char = text[i]
        if char.isspace() or char == '\ufeff':
            line += char == '\n'
            i += 1
        elif char == '#':
            end = text.find('\n', i)
            i = len(text) if end < 0 else end
        elif char == '"':
            start, start_line = i + 1, line
            i += 1
            while i < len(text):
                if text[i] == '\\':
                    i += 2
                elif text[i] == '"':
                    tokens.append(Token(text[start:i], start_line, True))
                    i += 1
                    break
                else:
                    line += text[i] == '\n'
                    i += 1
            else:
                raise ValueError(f'{label}:{start_line}: unterminated quoted string')
        elif char in '{}=<>!':
            if char == '{':
                opened.append(line)
            elif char == '}':
                if not opened:
                    raise ValueError(f'{label}:{line}: unexpected closing brace')
                opened.pop()
            if char in '<>!' and i + 1 < len(text) and text[i + 1] == '=':
                tokens.append(Token(text[i:i+2], line))
                i += 2
            else:
                tokens.append(Token(char, line))
                i += 1
        else:
            start = i
            while i < len(text) and not text[i].isspace() and text[i] not in '#"{}=<>!':
                i += 1
            tokens.append(Token(text[start:i], line))
    if opened:
        raise ValueError(f'{label}:{opened[-1]}: unclosed brace')
    return tokens


def script_info(tokens: list[Token]) -> tuple[set[str], set[str]]:
    definitions, loc_refs, contexts = set(), set(), []
    for i, token in enumerate(tokens):
        if token.quoted:
            continue
        value = token.value
        if value == '{':
            contexts.append(tokens[i-2].value if i >= 2 and tokens[i-1].value == '=' else '')
        elif value == '}':
            contexts.pop()
        elif i + 2 < len(tokens) and tokens[i+1].value == '=':
            following = tokens[i+2]
            if not contexts and following.value == '{':
                definitions.add(value)
                if value.endswith('_decision'):
                    loc_refs.add(value)
            if following.value != '{' and (
                value in LOC_ASSIGNMENTS or value == 'name' and contexts and contexts[-1] == 'option'
            ):
                loc_refs.add(following.value)
    return definitions, loc_refs


def validate_dynamic_descriptions(tokens: list[Token], label: str) -> list[str]:
    errors, contexts = [], []
    for i, token in enumerate(tokens):
        if token.quoted:
            continue
        if token.value == '{':
            contexts.append(tokens[i-2].value if i >= 2 and tokens[i-1].value == '=' else '')
        elif token.value == '}':
            contexts.pop()
        elif i + 1 < len(tokens) and tokens[i+1].value == '=':
            if contexts and contexts[-1] == 'triggered_desc' and token.value not in {'trigger', 'desc'}:
                errors.append(f'{label}:{token.line}: triggered_desc permits only trigger/desc; wrap combined text in desc={{...}}')
    return errors


def read_localization(folder: Path, language: str, errors: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    files = sorted((folder / 'localization' / language).glob('*.yml'))
    if not files:
        errors.append(f'{folder.name}: missing {language} localization')
    for path in files:
        raw = path.read_bytes()
        if not raw.startswith(b'\xef\xbb\xbf'):
            errors.append(f'{path}: localization must have UTF-8 BOM')
        try:
            lines = raw.decode('utf-8-sig').splitlines()
        except UnicodeDecodeError as exc:
            errors.append(f'{path}: invalid UTF-8: {exc}')
            continue
        if not lines or lines[0] != f'l_{language}:':
            errors.append(f'{path}: incorrect language header')
        for line_number, line in enumerate(lines[1:], 2):
            if not line.strip() or line.lstrip().startswith('#'):
                continue
            match = LOC_LINE.fullmatch(line)
            if not match:
                errors.append(f'{path}:{line_number}: malformed localization entry')
                continue
            key, value = match.groups()
            if key in values:
                errors.append(f'{path}:{line_number}: duplicate localization key {key}')
            values[key] = value
            if value.count('[') != value.count(']') or value.count('$') % 2:
                errors.append(f'{path}:{line_number}: unbalanced localization substitution')
            if re.search(r'\[\s*scope:', value):
                errors.append(f'{path}:{line_number}: localization uses saved scope names without the script-only scope: prefix')
            if re.search(r'\\(?![nrt"\\])', value):
                errors.append(f'{path}:{line_number}: unsupported localization escape')
    return values


def inventory(folder: Path) -> dict[str, str]:
    return {
        p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(folder.rglob('*')) if p.is_file()
    }


def check(source: Path, adapter: Path | None, miv: Path | None = None) -> dict:
    errors: list[str] = []
    snapshots, parsed, definitions, refs = {}, {}, set(), set()
    sources = [('base', source)] + ([('adapter', adapter)] if adapter else [])
    for label, folder in sources:
        if not folder.is_dir():
            errors.append(f'Missing {label} source: {folder}')
            continue
        snapshots[label] = inventory(folder)
        parsed[label] = {}
        for path in sorted(folder.rglob('*')):
            if not path.is_file() or path.suffix not in {'.txt', '.gui', '.mod'}:
                continue
            if path.suffix == '.txt' and not path.read_bytes().startswith(b'\xef\xbb\xbf'):
                errors.append(f'{path}: runtime script must have UTF-8 BOM')
            try:
                tokens = lex(path.read_text(encoding='utf-8-sig'), str(path))
            except (ValueError, UnicodeDecodeError) as exc:
                errors.append(str(exc))
                continue
            parsed[label][path.relative_to(folder).as_posix()] = tokens
            errors.extend(validate_dynamic_descriptions(tokens, str(path)))
            new_defs, new_refs = script_info(tokens)
            definitions.update(new_defs)
            refs.update(new_refs)
            for token in tokens:
                if re.search(r'\bagot_', token.value, re.I) or token.value.lower() == 'a game of thrones':
                    errors.append(f'{path}:{token.line}: unexpected AGOT dependency')
                if label == 'base' and re.match(r'miv_', token.value, re.I) and token.value not in {'miv_oathbreaker', 'miv_is_loaded'}:
                    errors.append(f'{path}:{token.line}: unexpected MIV-only reference in vanilla base: {token.value}')
    english = read_localization(source, 'english', errors)
    russian = read_localization(source, 'russian', errors)
    if english.keys() != russian.keys():
        errors.append('EN/RU key mismatch: ' + str(sorted(english.keys() ^ russian.keys())))
    for language, values in [('english', english), ('russian', russian)]:
        local_refs = refs | {
            ref for text in values.values() for ref in re.findall(r'\$([A-Za-z0-9_.-]+)\$', text)
        }
        missing = sorted(ref for ref in local_refs if ref.startswith('ttr') and ref not in values)
        if missing:
            errors.append(f'{language}: missing referenced localization: {missing}')
    for files in parsed.values():
        for relative, tokens in files.items():
            for token in tokens:
                if not token.quoted and re.fullmatch(r'ttr_(?:\w+_)?(?:effect|trigger)|ttr\.\d+', token.value):
                    if token.value not in definitions:
                        errors.append(f'{relative}:{token.line}: undefined local script reference {token.value}')
    if miv:
        opinion_file = source/'common/opinion_modifiers/000000_ttr_miv_compatibility_opinions.txt'
        try:
            fallback_tokens = lex(opinion_file.read_text(encoding='utf-8-sig'), str(opinion_file))
            matching_definitions = []
            for path in sorted((miv/'common/opinion_modifiers').glob('*.txt')):
                text = path.read_text(encoding='utf-8-sig')
                if 'miv_oathbreaker' not in text:
                    continue
                tokens = lex(text, str(path))
                for i, token in enumerate(tokens):
                    if token.value == 'miv_oathbreaker' and i + 2 < len(tokens) and tokens[i+1].value == '=' and tokens[i+2].value == '{':
                        depth, end = 1, i + 3
                        while depth and end < len(tokens):
                            depth += (tokens[end].value == '{') - (tokens[end].value == '}')
                            end += 1
                        matching_definitions.append([t.value for t in tokens[i:end]])
            if len(matching_definitions) != 1 or [t.value for t in fallback_tokens] != matching_definitions[0]:
                errors.append('Integrated oathbreaker definition differs from the installed MIV definition')
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
    if adapter:
        adapter_scripts = {p for p in parsed.get('adapter', {}) if p.endswith('.txt')}
        if adapter_scripts != set(ADAPTER_PATHS):
            errors.append(f'Adapter must override exactly the two same-path hooks: {sorted(adapter_scripts)}')
        for relative in ADAPTER_PATHS:
            base_tokens = parsed.get('base', {}).get(relative)
            adapter_tokens = parsed.get('adapter', {}).get(relative)
            if base_tokens is None or adapter_tokens is None:
                errors.append(f'Missing same-path base/adapter hook: {relative}')
            elif script_info(base_tokens)[0] != script_info(adapter_tokens)[0]:
                errors.append(f'Adapter changes public hook definitions: {relative}')
        trigger_tokens = parsed.get('base', {}).get(ADAPTER_PATHS[0], [])
        effect_tokens = parsed.get('base', {}).get(ADAPTER_PATHS[1], [])
        if ' '.join(t.value for t in trigger_tokens) != 'ttr_has_miv_political_reason_trigger = { $JUDGE$ = { always = no } }':
            errors.append('Vanilla MIV trigger stub must safely return no')
        if ' '.join(t.value for t in effect_tokens) != 'ttr_remove_miv_political_reasons_effect = { $JUDGE$ = { } }':
            errors.append('Vanilla MIV effect stub must be a no-op')
        descriptor = parsed.get('adapter', {}).get('descriptor.mod', [])
        names = {token.value for token in descriptor if token.quoted}
        if not {'[DEV] Traitor Trials', 'More Interactive Vassals'} <= names:
            errors.append('Adapter descriptor must declare base and MIV dependencies')
    return {
        'status': 'PASS_STATIC_ONLY' if not errors else 'FAIL',
        'source': source.as_posix(), 'adapter': adapter.as_posix() if adapter else None,
        'localization_keys': {'english': len(english), 'russian': len(russian)},
        'referenced_localization_keys': len({r for r in refs if r.startswith('ttr')}),
        'script_files': sum(len(p) for p in parsed.values()),
        'snapshots': snapshots, 'errors': sorted(set(errors)),
        'limits': ['Does not parse or execute the full CK3 language.', 'Native behavior and visual appearance require separate evidence.'],
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT/'mod/traitor_trials')
    parser.add_argument('--adapter', type=Path, help='Optional legacy same-path companion audit; not needed by the integrated demo')
    parser.add_argument('--miv', type=Path, help='Verify the integrated fallback against this installed MIV source')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = check(args.source.resolve(), args.adapter.resolve() if args.adapter else None, args.miv.resolve() if args.miv else None)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x', encoding='utf-8') as stream:
            json.dump(report, stream, indent=2, ensure_ascii=False)
            stream.write('\n')
    print(json.dumps({key: value for key, value in report.items() if key != 'snapshots'}, ensure_ascii=False, indent=2))
    sys.exit(bool(report['errors']))
