import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "bot"))
from clean_tts import clean_for_tts

def test_bold():     assert clean_for_tts("**жирный**") == "жирный"
def test_italic():   assert clean_for_tts("*курсив*") == "курсив"
def test_header():   assert clean_for_tts("# H1\ntext") == "H1. text"
def test_list():     assert clean_for_tts("- a\n- b") == "a. b"
def test_link():     assert clean_for_tts("[t](http://x)") == "t"
def test_code():     assert clean_for_tts("`c`") == "c"
def test_empty():    assert clean_for_tts("") == ""
def test_newlines(): assert clean_for_tts("a\n\n\nb") == "a. b"
