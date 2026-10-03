import ast, re, sys

ok = True
for f in ["memory_system.py","actions.py","voice.py","integrations.py","server.py","run.py","generate.py"]:
    try:
        ast.parse(open(f,encoding="utf-8").read(), filename=f)
        print("AST OK ", f)
    except SyntaxError as e:
        ok = False
        print("SYNTAX FAIL", f, e)
print("Backend Python:", "OK" if ok else "FAIL")

print()
js = open("web/assets/app.js", encoding="utf-8").read()
checks = [
    ("setupFloatingAssistant fn", r"function\s+setupFloatingAssistant\s*\("),
    ("views.dashboard fn", r"async\s+dashboard\s*\("),
    ("views.assistant fn", r"async\s+assistant\s*\("),
    ("views.ask fn", r"async\s+ask\s*\("),
    ("views.actions fn", r"async\s+actions\s*\("),
    ("views.voice fn", r"async\s+voice\s*\("),
    ("views.integrations fn", r"async\s+integrations\s*\("),
    ("api helper", r"async\s+function\s+api\s*\("),
    ("runAuto fab logic", r"runAuto\s*=\s*async"),
    ("runA assistant logic", r"runA\s*=\s*async"),
    ("DOMContentLoaded init", r"DOMContentLoaded"),
    ("Web Speech mic wiring", r"webkitSpeechRecognition"),
    ("Send to Integrations handoff", r"pending_actions"),
]
all_ok = True
for name, pat in checks:
    found = bool(re.search(pat, js))
    print(("OK  " if found else "MISS "), name)
    if not found: all_ok = False

print()
css = open("web/assets/app.css", encoding="utf-8").read()
css_checks = [
    (".hero class", r"\.hero\s*\{"),
    (".fab class", r"\.fab\s*\{"),
    (".assistant-page class", r"\.assistant-page\s*\{"),
    (".aurora bg", r"\.aurora\s*\{"),
    (".chip-act class", r"\.chip-act\s*\{"),
    (".msg-bot class", r"\.msg-bot\s*\{"),
    ("@keyframes pulse-dot", r"@keyframes\s+pulse-dot"),
    (".convo-panel", r"\.convo-panel\s*\{"),
    (".btn-primary", r"\.btn-primary\s*\{"),
    ("inter font", r"--font:"),
    ("responsive media", r"@media.*max-width"),
]
for name, pat in css_checks:
    found = bool(re.search(pat, css))
    print(("CSS OK  " if found else "CSS MISS "), name)
    if not found: all_ok = False

print()
print("HTML checks:")
html = open("web/index.html", encoding="utf-8").read()
for name, pat in [
    ("FAB container id=fab", r'id="fab"'),
    ("fabPanel", r'id="fabPanel"'),
    ("assistant route", r'data-route="assistant"'),
    ("nav icons", r"nav-ic"),
    ("aurora layers", r'class="aurora"'),
    ("Google Fonts Inter", r"fonts\.googleapis\.com"),
    ("voice route", r'data-route="voice"'),
    ("actions route", r'data-route="actions"'),
    ("ask route", r'data-route="ask"'),
    ("integrations route", r'data-route="integrations"'),
    ("fabMic element", r'id="fabMic"'),
    ("fabInput element", r'id="fabInput"'),
]:
    found = bool(re.search(pat, html))
    print(("HTML OK  " if found else "HTML MISS "), name)
    if not found: all_ok = False

print()
print("=" * 56)
js_pass = sum(1 for _,p in checks if re.search(p,js))
css_pass = sum(1 for _,p in css_checks if re.search(p,css))
html_checks_list = [
    ("FAB", r'id="fab"'),
    ("panel", r'id="fabPanel"'),
    ("route", r'data-route="assistant"'),
    ("navic", r'nav-ic'),
    ("aurora", r'class="aurora"'),
    ("fonts", r'fonts\.googleapis\.com'),
    ("voice", r'data-route="voice"'),
    ("act", r'data-route="actions"'),
    ("ask", r'data-route="ask"'),
    ("intg", r'data-route="integrations"'),
    ("mic", r'id="fabMic"'),
    ("inp", r'id="fabInput"')
]
html_pass = sum(1 for _,p in html_checks_list if re.search(p,html))
print(f"JS checks   : {js_pass:2d}/{len(checks)}")
print(f"CSS checks  : {css_pass:2d}/{len(css_checks)}")
print(f"HTML checks : {html_pass:2d}/12")
print("=" * 56)
print("ALL STRUCTURE CHECKS:", "PASS" if all_ok else "HAS ISSUES")

# Bonus: light JS brace / paren balance heuristics
o, c = js.count("{"), js.count("}")
op, cp = js.count("("), js.count(")")
print(f"JS braces balance: {{ {o} }} {c}   parens ( {op} ) {cp}   diffs {abs(o-c)} / {abs(op-cp)}")
sys.exit(0 if all_ok else 1)
