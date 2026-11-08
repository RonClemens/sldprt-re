# Native-file checks in this repository

Checks N1-N3 (SolidWorks drawing sheet size) need real native files and run on the CUI side. The full work order (all of N1..N12, reporting rules, what a pass looks like) is `docs/native-file-checks.md` in `ipb_viz`.

Command: `python3 tools/survey_slddrw_sizes.py DIR --out r.json` and `python3 src/slddrw.py FILE`

Report: one **Native-file check** Issue in this repository, masked (counts, pass or fail, hashes, field names, generic error text). The tool prints nothing else; a person reads the Issue before it is posted. The public side turns the answer into code and a test.
