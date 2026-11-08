"""usage: python3 tests/test_survey_slddrw_sizes.py -- the masked .SLDDRW size survey: the helpers, and that its report carries no free text."""
import os, struct, sys, json
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
import survey_slddrw_sizes as s
ok = fail = 0
def t(c, m):
    global ok, fail
    ok += bool(c); fail += not c; print(("PASS  " if c else "FAIL  ") + m)
t(s._safe_name("Contents/Definition") == "Contents/Definition" and s._safe_name("Images/Sheet_3") == "Images/Sheet_N" and s._safe_name("SECRET-NAME") == "other", "stream names outside the allowlist are reported as 'other'")
t(s._style_hits(b"xx A3 - Landscape yy") == {"A3": 1} and s._style_hits("A (ANSI) Portrait".encode("utf-16le")).get("A") == 1 and s._style_hits(b"nothing here") == {}, "style text is counted in ASCII and UTF-16LE")
d = s._double_hits(struct.pack("<ddd", 0.297, 0.4200, 0.001))
t(d.get("A4") == 1 and d.get("A3") == 2, "doubles near paper sizes in metres are counted by style")
t(s._double_hits(struct.pack("<d", 0.5)) == {}, "other doubles are ignored")
src = open(os.path.join(HERE, "..", "tools", "survey_slddrw_sizes.py")).read()
t("print(" not in src.split("def main")[0] and "string" not in json.dumps(s.PAPER_MM), "the survey prints nothing from the file")
print(f"{ok} passed, {fail} failed"); sys.exit(1 if fail else 0)
