from app.domain.filenames import download_filename


def test_cyrillic_is_transliterated():
    assert download_filename("Мой телефон", "conf") == "Moy_telefon.conf"
    assert download_filename("Щука Жёлтая", "ovpn") == "Shchuka_Zheltaya.ovpn"
    assert download_filename("ЩИТ", "conf") == "SHCHIT.conf"


def test_ukrainian_letters():
    assert download_filename("Їжак Ґанок Єва Ірина", "conf") == "Yizhak_Ganok_Yeva_Irina.conf"


def test_unsafe_characters_become_underscores():
    assert download_filename("../etc/passwd", "conf") == "etc_passwd.conf"
    assert download_filename('a:b*c?"d<e>f|g\\h', "txt") == "a_b_c_d_e_f_g_h.txt"
    assert download_filename("nl-1 AmneziaWG", "conf") == "nl-1_AmneziaWG.conf"


def test_nothing_left_falls_back():
    assert download_filename("🙂🙂", "conf") == "amnezia.conf"
    assert download_filename("...", "conf") == "amnezia.conf"
    assert download_filename("", "conf") == "amnezia.conf"


def test_long_names_are_cut():
    stem = download_filename("щ" * 100, "conf").removesuffix(".conf")
    assert len(stem) == 64
