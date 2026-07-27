import re

def clean_for_tts(text):
    """Удаляет markdown-разметку перед озвучиванием."""
    if not text:
        return text
    t = text
    t = re.sub(r'\*\*(.+?)\*\*', r'\1', t)      # **жирный** -> жирный
    t = re.sub(r'__(.+?)__', r'\1', t)          # __жирный__ -> жирный
    t = re.sub(r'(?<!\w)\*(.+?)\*(?!\w)', r'\1', t)  # *курсив*
    t = re.sub(r'(?<!\w)_(.+?)_(?!\w)', r'\1', t)    # _курсив_
    t = re.sub(r'^#{1,6}\s*', '', t, flags=re.MULTILINE)   # # заголовки
    t = re.sub(r'^\s*[-*+]\s+', '', t, flags=re.MULTILINE) # - списки
    t = re.sub(r'^\s*\d+\.\s+', '', t, flags=re.MULTILINE) # 1. нумерация
    t = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', t)         # [ссылка](url)
    t = re.sub(r'`([^`]+)`', r'\1', t)          # `код`
    t = t.replace('**', '').replace('__', '')   # остаточные
    t = re.sub(r'[*#`]', '', t)                 # одиночные * # `
    t = re.sub(r'\n{2,}', '. ', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t
