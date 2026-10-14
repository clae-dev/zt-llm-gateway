"""refs.py -> 레퍼런스 PDF와 Markdown.  python docs/references/build.py [PDF 저장 경로]"""
import html
import sys
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parent))
from refs import SECTIONS  # noqa: E402

HERE = Path(__file__).parent
out = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "레퍼런스.pdf"
total = sum(len(items) for _, _, items in SECTIONS)

CSS = """
@page { size: A4; margin: 17mm 18mm 17mm; }
:root { --ink:#1b2430; --sub:#66707c; --line:#dfdbd0; --accent:#0f766e; --soft:#e6f2f0; --warn:#b45309; }
* { box-sizing: border-box; }
body { margin:0; font-family: Pretendard, "Malgun Gothic", sans-serif; color:var(--ink); font-size:9.4pt;
       line-height:1.55; word-break:keep-all; -webkit-print-color-adjust:exact; print-color-adjust:exact; }
.kicker { font-size:8.6pt; font-weight:700; letter-spacing:.12em; color:var(--accent); }
h1 { margin:1.5mm 0 2mm; font-size:22pt; font-weight:800; letter-spacing:-.02em; }
.lead { margin:0 0 6mm; font-size:10.4pt; color:var(--sub); }
h2 { margin:7mm 0 1mm; font-size:12.4pt; font-weight:800; display:flex; align-items:baseline; gap:2.5mm;
     break-after:avoid; }
h2 .n { color:var(--accent); font-size:9.4pt; font-variant-numeric:tabular-nums; }
h2 .c { margin-left:auto; font-size:8.4pt; font-weight:600; color:var(--sub); }
.desc { margin:0 0 2mm; font-size:8.8pt; color:var(--sub); break-after:avoid; }
table { width:100%; border-collapse:collapse; table-layout:fixed; }
col.a { width:84mm; }
tr { break-inside:avoid; }
td { padding:1.7mm 2mm 1.7mm 0; border-top:1px solid var(--line); vertical-align:top; }
tr:first-child td { border-top:1.2px solid var(--ink); }
a { color:var(--ink); text-decoration:none; font-weight:700; }
.host { display:block; font-size:7.8pt; color:var(--accent); font-weight:500; overflow:hidden;
        text-overflow:ellipsis; white-space:nowrap; }
td.m { padding-left:3mm; padding-right:0; color:#333c47; }
.find { border-top:1.2px solid var(--ink); border-bottom:1.2px solid var(--ink); margin:0 0 2mm; }
.find div { display:grid; grid-template-columns:8mm 1fr; padding:2.6mm 0; }
.find div + div { border-top:1px solid var(--line); }
.find b.n { color:var(--accent); font-size:11pt; }
.find strong { display:block; font-size:10.4pt; }
.find span { color:#333c47; }
.toc { columns:2; column-gap:10mm; margin:0; padding:0; list-style:none; font-size:9.2pt; }
.toc li { display:flex; gap:2mm; padding:1mm 0; border-bottom:1px solid var(--line); break-inside:avoid; }
.toc .n { color:var(--accent); font-weight:700; width:6mm; }
.toc .c { margin-left:auto; color:var(--sub); font-variant-numeric:tabular-nums; }
.note { font-size:8.4pt; color:var(--sub); margin-top:3mm; }
"""

FINDINGS = [
    ("우리 연구 주제는 이미 연구되고 있다",
     "평범한 요청의 응답에서 문맥 속 비밀이 샌다는 것(Fairoze 외, 2026), 모델에 맡긴 방어는 뚫리고 출력 필터만 버틴다는 것(Deep 외, 2026), "
     "탐지 모델이 문맥을 고려한 상황에 약하다는 것(CAPTURE, 2025)이 모두 발표되어 있다. \"발견\"이라고 말할 수 없다."),
    ("남는 자리는 한국어다",
     "위 연구는 모두 영어다. 한국어 LLM과 한국어 탐지 모델에서 같은 현상을 재고, 한글 표기 변형(한글로 읽은 숫자, 자모 분리, 초성)으로 "
     "출력 필터를 빠져나가는 경우를 다룬 연구는 이번 검색에서 찾지 못했다."),
    ("시스템 프롬프트에 비밀을 넣는 시연은 약점이 된다",
     "OWASP는 시스템 프롬프트에 민감 정보를 넣지 말라고 한다. \"거기 넣지 않으면 된다\"는 반론을 피하려면 "
     "비밀이 있을 수밖에 없는 자리(검색해 온 사내 문서, 고객 정보)로 시나리오를 옮겨야 한다."),
    ("한국어 공개 가드레일 모델이 이미 있다",
     "카카오 Kanana Safeguard-Prompt(2.1B, Apache-2.0)와 삼성SDS SGuard-v1(2B, Apache-2.0). 우리는 카카오 모델을 2차 탐지기로 쓴다."),
    ("여러 턴을 순서대로 보는 탐지도 이미 나와 있다",
     "턴별 점수를 누적해 대화 단위로 판정하는 방법(Peak + Accumulation, DeepContext)이 2026년에 발표됐다. 대부분 탈옥이 대상이고 "
     "여러 턴 유출은 공격과 방어 비교(Agarwal 외, 2024)가 있고, 누적해서 탐지하는 것은 예비 결과 1편(Chauhan 외)이다. 한국어로 한 것은 찾지 못했다. 이후 계획으로만 말한다."),
    ("큰 회사들의 공식 입장은 \"겹쳐 막기\"다",
     "Google, Microsoft, 영국 NCSC 모두 완전 차단이 아니라 여러 겹의 방어와 피해 축소를 말한다."),
]


def host(url: str) -> str:
    p = urlparse(url)
    path = p.path.rstrip("/")
    return (p.netloc.removeprefix("www.") + path)[:70]


def render_html() -> str:
    e = html.escape
    parts = [f"<!doctype html><html lang='ko'><head><meta charset='utf-8'><title>레퍼런스 자료집</title><style>{CSS}</style></head><body>",
             "<div class='kicker'>ZERO-TRUST LLM GATEWAY · 2026 CAMPUS LAB START-UP BRIDGE</div>",
             "<h1>레퍼런스 자료집</h1>",
             f"<p class='lead'>표준, 논문, 개발 블로그, 오픈소스, 데이터셋 {total}건. 제목을 누르면 원문이 열린다. 2026. 10. 14. 기준.</p>",
             "<h2 style='margin-top:0'>자료를 모으며 알게 된 것</h2><div class='find'>"]
    for i, (title, body) in enumerate(FINDINGS, 1):
        parts.append(f"<div><b class='n'>{i}</b><p style='margin:0'><strong>{e(title)}</strong><span>{e(body)}</span></p></div>")
    parts.append("</div><h2>차례</h2><ul class='toc'>")
    for i, (name, _, items) in enumerate(SECTIONS, 1):
        parts.append(f"<li><span class='n'>{i:02d}</span>{e(name)}<span class='c'>{len(items)}건</span></li>")
    parts.append("</ul><p class='note'>모든 주소는 2026. 10. 7.에 접속해 응답과 페이지 제목을 확인했다(S2W 1건은 자동 접속이 차단됨). 여러 턴 탐지 7건은 2026. 10. 14.에 더했고 arXiv에서 초록을 받아 확인했다. "
                 "메모에 \"본문 확인\" 또는 \"초록 확인\"이라고 적은 것만 내용을 직접 대조했고, 나머지는 제목과 검색 결과 수준이다. "
                 "발표나 논문에 인용하기 전에 원문을 읽어야 한다.</p>")
    for i, (name, desc, items) in enumerate(SECTIONS, 1):
        parts.append(f"<h2><span class='n'>{i:02d}</span>{e(name)}<span class='c'>{len(items)}건</span></h2><p class='desc'>{e(desc)}</p>")
        parts.append("<table><colgroup><col class='a'><col></colgroup>")
        for title, url, memo in items:
            parts.append(f"<tr><td><a href='{e(url, quote=True)}'>{e(title)}</a><span class='host'>{e(host(url))}</span></td>"
                         f"<td class='m'>{e(memo)}</td></tr>")
        parts.append("</table>")
    parts.append("</body></html>")
    return "".join(parts)


def render_md() -> str:
    lines = ["# 레퍼런스 자료집", "", f"Zero-Trust LLM Gateway · 2026. 10. 14. 기준 · {total}건", "",
             "## 자료를 모으며 알게 된 것", ""]
    lines += [f"{i}. **{t}** - {b}" for i, (t, b) in enumerate(FINDINGS, 1)]
    for i, (name, desc, items) in enumerate(SECTIONS, 1):
        lines += ["", f"## {i:02d}. {name}", "", desc, ""]
        lines += [f"- [{t}]({u}) - {m}" for t, u, m in items]
    return "\n".join(lines) + "\n"


FOOTER = ("<div style='width:100%;font-size:7.5pt;color:#66707c;padding:0 18mm;display:flex;justify-content:space-between;"
          "font-family:Malgun Gothic,sans-serif'><span>Zero-Trust LLM Gateway 레퍼런스 자료집</span>"
          "<span><span class='pageNumber'></span> / <span class='totalPages'></span></span></div>")

src = HERE / "references.html"
src.write_text(render_html(), encoding="utf-8")
(HERE / "references.md").write_text(render_md(), encoding="utf-8")
check = HERE / "_check"
check.mkdir(exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    page.goto(src.as_uri())
    page.evaluate("document.fonts.ready")
    page.pdf(path=str(out), format="A4", print_background=True, prefer_css_page_size=True,
             display_header_footer=True, header_template="<span></span>", footer_template=FOOTER)
    browser.close()

sys.stdout.reconfigure(encoding="utf-8")
print(f"{total}건 저장: {out}")
