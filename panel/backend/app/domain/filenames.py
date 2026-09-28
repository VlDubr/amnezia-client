"""The name a downloaded config file gets: the config's name, transliterated to plain ASCII."""

import re

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i",
    "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
    "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "",
    "э": "e", "ю": "yu", "я": "ya",
    # Ukrainian and Belarusian letters that Russian lacks.
    "є": "ye", "і": "i", "ї": "yi", "ґ": "g", "ў": "u",
}
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
MAX_STEM = 64


def _translit(name: str) -> str:
    out = []
    for i, ch in enumerate(name):
        low = ch.lower()
        latin = _TRANSLIT.get(low)
        if latin is None:
            out.append(ch)
        elif ch == low:
            out.append(latin)
        elif i + 1 < len(name) and name[i + 1].isupper():  # inside an upper-case word: ЩИТ -> SHCHIT
            out.append(latin.upper())
        else:
            out.append(latin.capitalize())
    return "".join(out)


def download_filename(name: str, ext: str) -> str:
    stem = _UNSAFE.sub("_", _translit(name)).strip("._-")[:MAX_STEM].rstrip("._-")
    return f"{stem or 'amnezia'}.{ext}"
