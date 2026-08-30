import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "bot"))

from clean_tts import clean_for_tts

def test_removes_bold():
    assert clean_for_tts("**жирный** текст") == "жирный текст"
    assert clean_for_tts("__жирный__") == "жирный"

def test_removes_italic():
    assert clean_for_tts("*курсив*") == "курсив"
    assert clean_for_tts("_курсив_") == "курсив"

def test_removes_headers():
    assert clean_for_tts("# Заголовок\nтекст") == "Заголовок. текст"
    assert clean_for_tts("## H2\n### H3") == "H2. H3"

def test_removes_lists():
    assert clean_for_tts("- пункт1\n- пункт2") == "пункт1. пункт2"
    assert clean_for_tts("1. первый\n2. второй") == "первый. второй"

def test_removes_links():
    assert clean_for_tts("[ссылка](http://example.com)") == "ссылка"

def test_removes_code():
    assert clean_for_tts("`код`") == "код"

def test_handles_empty():
    assert clean_for_tts("") == ""
    assert clean_for_tts(None) is None

def test_multiple_newlines():
    assert clean_for_tts("текст\n\n\nтекст") == "текст. текст"
