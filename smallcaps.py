import string
import unicodedata as ud

_mapping = {
    ord(ud.lookup("LATIN LETTER SMALL CAPITAL " + ch)): ch
    for ch in string.ascii_uppercase if ch != "X"
}
_table = str.maketrans(_mapping)

def sc(text: str) -> str:
    return text.upper().translate(_table)
