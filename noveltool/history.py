class HistoryManager:
    def __init__(self, window: int, overlap: float):
        self._window = window
        self._overlap = overlap
        self._pairs: list[tuple[str, str]] = []

    def add_turn(self, user_line: str, assistant_line: str) -> None:
        self._pairs.append((user_line, assistant_line))

    def should_summarize(self) -> bool:
        return len(self._pairs) > self._window

    def get_summary_context(self, has_prior_summary: bool) -> list[dict]:
        if has_prior_summary:
            start = len(self._pairs) // 2
            pairs = self._pairs[start:]
        else:
            pairs = self._pairs

        messages: list[dict] = []
        for user, assistant in pairs:
            messages.append({'role': 'user', 'content': user})
            messages.append({'role': 'assistant', 'content': assistant})
        return messages

    def trim_to_overlap(self) -> None:
        keep = max(1, int(len(self._pairs) * self._overlap))
        self._pairs = self._pairs[-keep:]

    def to_messages(self) -> list[dict]:
        messages: list[dict] = []
        for user, assistant in self._pairs:
            messages.append({'role': 'user', 'content': user})
            messages.append({'role': 'assistant', 'content': assistant})
        return messages
