import re

content = open('mobile_app/index.html', encoding='utf-8').read()
for m in re.finditer(r'<section[^>]+id=["\']pane-([^"\']+)["\'][^>]*>', content):
    line_no = content[:m.start()].count('\n') + 1
    print(f'Line {line_no}: pane-{m.group(1)} -> {m.group(0)}')
