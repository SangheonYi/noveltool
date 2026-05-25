from .config import Config
from .preprocessor.namuwiki import CharacterProfile

_CHAR_HEADER = '[등장인물 프로필]'
_RULES_HEADER = '[추가 번역 규칙]'
_SUMMARY_HEADER = '[이야기 요약]'
_AFTER_HEADER = '[후속 문맥 참고]'
_RECOVERY_HEADER = '[복구 태스크 안내]'


def _build_character_section(characters: list[CharacterProfile]) -> str | None:
    if not characters:
        return None
    by_work: dict[str, list[CharacterProfile]] = {}
    for c in characters:
        by_work.setdefault(c.work, []).append(c)

    lines = [_CHAR_HEADER]
    for work, chars in by_work.items():
        if len(by_work) > 1:
            lines.append(f'\n# {work}')
        for c in chars:
            entry = f'- {c.original} ({c.korean})'
            if c.desc:
                entry += f': {c.desc}'
            lines.append(entry)
    return '\n'.join(lines)


def build_system_prompt(config: Config, characters: list[CharacterProfile]) -> str:
    parts = [config.system_prompt.base.rstrip()]

    char_section = _build_character_section(characters)
    if char_section:
        parts.append(char_section)

    if config.system_prompt.extra_rules:
        lines = [_RULES_HEADER]
        lines.extend(f'- {r}' for r in config.system_prompt.extra_rules)
        parts.append('\n'.join(lines))

    return '\n\n'.join(parts)


def build_recovery_system_prompt(
    config: Config,
    characters: list[CharacterProfile],
    missing_lines: int,
    after_context: list[str],
    summary: str | None = None,
) -> str:
    parts = [config.system_prompt.base.rstrip()]

    recovery_notice = (
        f'{_RECOVERY_HEADER}\n'
        f'이 소설의 일부 데이터가 소실되었습니다.\n'
        f'소실된 분량은 약 {missing_lines}줄입니다.\n'
        '앞뒤 문맥을 참고하여 소실된 내용을 한 줄씩 자연스럽게 복구해 주세요.\n'
        '한 번에 반드시 한 줄만 출력하세요.'
    )
    parts.append(recovery_notice)

    if after_context:
        after_block = _AFTER_HEADER + '\n' + '\n'.join(after_context)
        parts.append(after_block)

    char_section = _build_character_section(characters)
    if char_section:
        parts.append(char_section)

    if config.system_prompt.extra_rules:
        lines = [_RULES_HEADER]
        lines.extend(f'- {r}' for r in config.system_prompt.extra_rules)
        parts.append('\n'.join(lines))

    if summary:
        parts.append(f'{_SUMMARY_HEADER}\n{summary}')

    return '\n\n'.join(parts)


def update_summary(prompt: str, summary: str) -> str:
    marker = f'\n\n{_SUMMARY_HEADER}\n'
    if _SUMMARY_HEADER in prompt:
        idx = prompt.index(_SUMMARY_HEADER)
        return prompt[:idx] + _SUMMARY_HEADER + '\n' + summary
    return prompt + marker + summary
