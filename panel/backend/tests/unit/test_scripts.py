from app.drivers import scripts


def test_script_normalizes_line_endings(tmp_path, monkeypatch):
    (tmp_path / "proto").mkdir()
    (tmp_path / "proto" / "start.sh").write_bytes(b"#!/bin/bash\r\necho $VAR\r\n")
    monkeypatch.setenv("PANEL_SCRIPTS_DIR", str(tmp_path))
    assert scripts.script("proto", "start.sh") == "#!/bin/bash\necho $VAR\n"


def test_replace_vars_keeps_shell_variables():
    assert scripts.replace_vars("$A $pm $B_C", {"A": "1", "B_C": "2"}) == "1 $pm 2"
    assert scripts.replace_vars("x=$UNKNOWN", {}, keep_unknown=False) == "x="
