import re

def clean_for_tts(text):
    if not text: return text
    t = text
    t = re.sub(r'\*\*(.+?)\*\*', r'\1', t)
    t = re.sub(r'__(.+?)__', r'\1', t)
    t = re.sub(r'(?<!\w)\*(.+?)\*(?!\w)', r'\1', t)
    t = re.sub(r'(?<!\w)_(.+?)_(?!\w)', r'\1', t)
    t = re.sub(r'^#{1,6}\s*', '', t, flags=re.MULTILINE)
    t = re.sub(r'^\s*[-*+]\s+', '', t, flags=re.MULTILINE)
    t = re.sub(r'^\s*\d+\.\s+', '', t, flags=re.MULTILINE)
    t = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', t)
    t = re.sub(r'`([^`]+)`', r'\1', t)
    t = t.replace('**', '').replace('__', '')
    t = re.sub(r'[*#`]', '', t)
    t = re.sub(r'\n{2,}', '. ', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t

def test_bold():     assert clean_for_tts("**жирный**") == "жирный"
def test_italic():   assert clean_for_tts("*курсив*") == "курсив"
def test_header():   assert clean_for_tts("# H1\ntext") == "H1 text"
def test_list():     assert clean_for_tts("- a\n- b") == "a b"
def test_link():     assert clean_for_tts("[ссылка](http://example.com)") == "ссылка"
def test_code():     assert clean_for_tts("`код`") == "код"
def test_empty():    assert clean_for_tts("") == ""
def test_newlines(): assert clean_for_tts("текст\n\n\nтекст") == "текст. текст"
