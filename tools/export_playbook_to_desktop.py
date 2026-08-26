# pyright: reportMissingTypeStubs=false
"""把龙头接替手册打包到桌面: .md + 配图 + 单文件 HTML(图片 base64 内嵌, 双击即开)。

用法: python tools/export_playbook_to_desktop.py
零第三方依赖 —— 内置极简 Markdown 渲染 (标题/表格/代码块/图片/加粗/引用/列表/分隔线)。
"""
import os, re, sys, io, base64, shutil, html

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_MD = os.path.join(_ROOT, 'output', 'dragon_succession_playbook.md')
SRC_IMG = os.path.join(_ROOT, 'output', 'dragon_imgs')
DESKTOP = os.path.join(os.path.expanduser('~'), 'Desktop')
DEST = os.path.join(DESKTOP, '龙头接替培训手册')

CSS = """
:root{--bg:#0d1117;--panel:#161b22;--edge:#30363d;--txt:#e6edf3;--mute:#8b949e;
       --yellow:#d29922;--green:#3fb950;--red:#f85149;--blue:#58a6ff;}
*{box-sizing:border-box;}
body{margin:0;background:var(--bg);color:var(--txt);line-height:1.85;
  font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
  font-size:16px;}
.wrap{max-width:900px;margin:0 auto;padding:48px 24px 96px;}
h1{font-size:30px;color:var(--yellow);border-bottom:2px solid var(--edge);
   padding-bottom:16px;margin:0 0 8px;line-height:1.4;}
h2{font-size:24px;color:var(--yellow);margin:56px 0 18px;
   border-left:4px solid var(--yellow);padding-left:14px;}
h3{font-size:19px;color:var(--blue);margin:34px 0 12px;}
p{margin:14px 0;}
em{color:var(--mute);font-style:italic;}
strong{color:#fff;}
hr{border:0;border-top:1px solid var(--edge);margin:44px 0;}
blockquote{margin:22px 0;padding:16px 20px;background:var(--panel);
  border-left:4px solid var(--blue);border-radius:0 8px 8px 0;color:#c9d1d9;}
blockquote p{margin:6px 0;}
table{width:100%;border-collapse:collapse;margin:22px 0;font-size:14.5px;
  background:var(--panel);border-radius:8px;overflow:hidden;}
th{background:#1f2630;color:var(--yellow);font-weight:700;text-align:left;
   padding:11px 13px;border-bottom:2px solid var(--edge);white-space:nowrap;}
td{padding:10px 13px;border-bottom:1px solid var(--edge);vertical-align:top;}
tr:last-child td{border-bottom:0;}
tr:hover td{background:#1b222c;}
pre{background:var(--panel);border:1px solid var(--edge);border-radius:8px;
  padding:18px;overflow-x:auto;font-size:13.5px;line-height:1.7;
  font-family:"Cascadia Mono",Consolas,"Microsoft YaHei",monospace;color:#c9d1d9;}
code{background:#1f2630;padding:2px 6px;border-radius:4px;font-size:14px;
  color:#79c0ff;font-family:Consolas,monospace;}
pre code{background:none;padding:0;color:inherit;}
img{max-width:100%;display:block;margin:26px auto;border:1px solid var(--edge);
  border-radius:10px;box-shadow:0 6px 28px rgba(0,0,0,.55);}
ul{padding-left:26px;}
li{margin:8px 0;}
.toc{background:var(--panel);border:1px solid var(--edge);border-radius:10px;
  padding:20px 26px;margin:32px 0;}
.toc b{color:var(--yellow);display:block;margin-bottom:10px;font-size:15px;}
.toc a{color:var(--blue);text-decoration:none;display:block;padding:4px 0;font-size:15px;}
.toc a:hover{text-decoration:underline;}
"""


def _inline(s: str) -> str:
    """行内: 图片 → 加粗 → 行内代码 (先转义 HTML)。"""
    s = html.escape(s)
    s = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)',
               lambda m: f'<img src="{m.group(2)}" alt="{m.group(1)}">', s)
    s = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', s)
    s = re.sub(r'`([^`]+)`', r'<code>\1</code>', s)
    return s


def md_to_html(md: str) -> str:
    out, lines, i = [], md.split('\n'), 0
    heads = []
    while i < len(lines):
        ln = lines[i]

        # 代码块
        if ln.startswith('```'):
            i += 1
            buf = []
            while i < len(lines) and not lines[i].startswith('```'):
                buf.append(html.escape(lines[i])); i += 1
            i += 1
            out.append('<pre><code>' + '\n'.join(buf) + '</code></pre>')
            continue

        # 表格 (当前行有 | 且下一行是分隔行)
        if ln.startswith('|') and i+1 < len(lines) and re.match(r'^\|[\s:\-|]+\|$', lines[i+1].strip()):
            cells = lambda r: [c.strip() for c in r.strip().strip('|').split('|')]
            head = cells(ln); i += 2
            rows = []
            while i < len(lines) and lines[i].startswith('|'):
                rows.append(cells(lines[i])); i += 1
            t = ['<table><thead><tr>'] + [f'<th>{_inline(c)}</th>' for c in head] + ['</tr></thead><tbody>']
            for r in rows:
                t.append('<tr>' + ''.join(f'<td>{_inline(c)}</td>' for c in r) + '</tr>')
            t.append('</tbody></table>')
            out.append(''.join(t))
            continue

        # 标题
        m = re.match(r'^(#{1,4})\s+(.*)$', ln)
        if m:
            lvl, txt = len(m.group(1)), m.group(2)
            if lvl == 2:
                aid = f'sec{len(heads)}'
                heads.append((aid, re.sub(r'\*\*|`', '', txt)))
                out.append(f'<h2 id="{aid}">{_inline(txt)}</h2>')
            else:
                out.append(f'<h{lvl}>{_inline(txt)}</h{lvl}>')
            i += 1
            continue

        # 分隔线
        if ln.strip() in ('---', '***'):
            out.append('<hr>'); i += 1; continue

        # 引用块
        if ln.startswith('>'):
            buf = []
            while i < len(lines) and lines[i].startswith('>'):
                buf.append(lines[i].lstrip('>').strip()); i += 1
            body = ''.join(f'<p>{_inline(b)}</p>' for b in buf if b)
            out.append(f'<blockquote>{body}</blockquote>')
            continue

        # 列表
        if re.match(r'^\s*[-*]\s+', ln):
            buf = []
            while i < len(lines) and re.match(r'^\s*[-*]\s+', lines[i]):
                buf.append(re.sub(r'^\s*[-*]\s+', '', lines[i])); i += 1
            out.append('<ul>' + ''.join(f'<li>{_inline(b)}</li>' for b in buf) + '</ul>')
            continue

        # 空行
        if not ln.strip():
            i += 1; continue

        # 段落 (斜体整行 → em)
        if ln.startswith('*') and ln.rstrip().endswith('*') and not ln.startswith('**'):
            out.append(f'<p><em>{_inline(ln.strip().strip("*"))}</em></p>')
        else:
            out.append(f'<p>{_inline(ln)}</p>')
        i += 1

    toc = '<div class="toc"><b>目录</b>' + ''.join(
        f'<a href="#{a}">{html.escape(t)}</a>' for a, t in heads) + '</div>'
    # 目录插到第一个 <hr> 之后 (即引言之后)
    body = ''.join(out)
    k = body.find('<hr>')
    body = body[:k+4] + toc + body[k+4:] if k >= 0 else toc + body
    return body


def embed_images(html_body: str, img_dir: str) -> str:
    """把 <img src="dragon_imgs/x.png"> 换成 base64 data URI, 单文件自包含。"""
    def rep(m):
        src = m.group(1)
        p = os.path.join(os.path.dirname(img_dir), src.replace('/', os.sep))
        if not os.path.exists(p):
            print(f"  [warn] 图片缺失: {src}")
            return m.group(0)
        b64 = base64.b64encode(open(p, 'rb').read()).decode()
        return m.group(0).replace(src, f'data:image/png;base64,{b64}')
    return re.sub(r'<img src="([^"]+)"', rep, html_body)


def main():
    md = open(SRC_MD, encoding='utf-8').read()
    body = md_to_html(md)

    os.makedirs(DEST, exist_ok=True)
    # 1) .md + 配图 (相对路径可用)
    shutil.copy2(SRC_MD, os.path.join(DEST, '龙头接替培训手册.md'))
    dst_img = os.path.join(DEST, 'dragon_imgs')
    if os.path.isdir(dst_img):
        shutil.rmtree(dst_img)
    shutil.copytree(SRC_IMG, dst_img)
    n_img = len([f for f in os.listdir(dst_img) if f.endswith('.png')])

    # 2) 单文件 HTML (图片内嵌, 双击即开)
    page = ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>龙头接替 · 监管周期实战培训手册</title>'
            f'<style>{CSS}</style></head><body><div class="wrap">'
            + embed_images(body, SRC_IMG) +
            '</div></body></html>')
    hp = os.path.join(DEST, '龙头接替培训手册.html')
    open(hp, 'w', encoding='utf-8').write(page)

    print(f"已导出到桌面: {DEST}")
    print(f"  龙头接替培训手册.md    (Markdown 原件, 配图走 dragon_imgs/)")
    print(f"  龙头接替培训手册.html  (单文件, 图片已内嵌, {os.path.getsize(hp)/1024/1024:.1f} MB, 双击即开)")
    print(f"  dragon_imgs/           ({n_img} 张 PNG)")


if __name__ == '__main__':
    main()
