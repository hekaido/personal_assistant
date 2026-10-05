"""Форматирование текста под ограничения Telegram."""

TELEGRAM_MAX_MESSAGE_LENGTH = 4096
_SAFE_CHUNK_SIZE = 3800  # с запасом от лимита — сам текст может расширяться при markdown-парсинге


def split_for_telegram(text: str, max_len: int = _SAFE_CHUNK_SIZE) -> list[str]:
    """Режет длинный текст на части, укладывающиеся в лимит Telegram (4096 символов).

    Режет по границам блоков ("\\n\\n---\\n\\n", как в брифе дня), а не посередине —
    если и один блок сам по себе длиннее max_len, режет его пожёстче по строкам.
    """
    if len(text) <= max_len:
        return [text]

    blocks = text.split("\n\n---\n\n")
    chunks: list[str] = []
    current = ""

    for block in blocks:
        candidate = f"{current}\n\n---\n\n{block}" if current else block
        if len(candidate) <= max_len:
            current = candidate
            continue

        if current:
            chunks.append(current)
        if len(block) <= max_len:
            current = block
        else:
            # Один блок сам по себе слишком длинный — режем по строкам.
            for line in block.split("\n"):
                piece = f"{current}\n{line}" if current else line
                if len(piece) <= max_len:
                    current = piece
                else:
                    if current:
                        chunks.append(current)
                    current = line

    if current:
        chunks.append(current)

    return chunks
