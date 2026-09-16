#!/usr/bin/env python3
"""geo.py : facts.md -> Q&A -> GEO 정적 사이트 -> LLM 인용 체크

  python3 geo.py init      facts.md 템플릿 생성
  python3 geo.py gen       facts.md -> qa.md, bios.md  (로그인된 claude CLI 사용, API 키 불필요)
  python3 geo.py build     -> docs/ (index.html + JSON-LD, llms.txt, robots.txt, sitemap.xml, profile-pack.md, CNAME)
  python3 geo.py publish   build + git commit + push. GitHub Pages(main:/docs) 가 곧 배포. 최초 1회 Pages 자동 활성화
  python3 geo.py check     LLM에 웹검색으로 물어보고 내 도메인 인용 여부를 checks.csv 에 기록
  python3 geo.py selftest

흐름: 사실은 사람이 facts.md 에 쓴다 -> AI 는 포맷만 한다 -> qa.md 를 사람이 검토한다
      -> "[확인 필요" 가 한 줄이라도 남아 있으면 build 가 거부한다.
LLM: 기본 claude CLI. GEO_LLM=codex 면 gen 에 codex exec 사용. check 는 웹검색 때문에 claude 고정.
"""
import csv
import datetime
import html
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
FACTS, QA, BIOS, SITE, CHECKS = (ROOT / n for n in ("facts.md", "qa.md", "bios.md", "docs", "checks.csv"))
TODO = "[확인 필요"

FACTS_TEMPLATE = """name: Muxo
type: Person
url: https://example.com
description: 한 줄 소개. 검색 결과와 AI 답변에 그대로 쓰인다.
jobTitle: 직함 (Person 일 때)
alternateName: 본명이나 다른 표기
email:
image: https://example.com/photo.jpg
genre:
sameAs: https://open.spotify.com/artist/...
sameAs: https://www.youtube.com/@...
sameAs: https://www.instagram.com/...
---
# 기본 정보
- 활동명, 활동 시작 연도, 거점 도시
- 무엇을 하는 사람인지 한 문단

# 작업물
- 곡/앨범 이름, 발매일, 장르, 링크, 만든 배경 한 줄씩

# 협업 / 연락
- 협업 이력, 문의 이메일, 어떤 제안을 환영하는지

# 자주 받는 질문
- 실제로 받아본 질문과 답
"""

GEN_PROMPT = """너는 아래 FACTS만을 근거로 {name}에 대한 Q&A와 소개문을 쓴다.

규칙:
1. FACTS에 없는 사실은 절대 만들지 않는다. 날짜, 이름, 숫자, 장르, 협업자를 추정하지 않는다.
2. 사람들이 검색엔진이나 AI에게 실제로 물어볼 법한 질문만 만든다. 일기형 질문(오늘 뭐 먹었어 등)은 금지.
3. 우선순위: 정체성(누구인지, 무엇을 하는지) -> 대표 작업물 -> 스타일/장르 -> 협업/연락 -> 기타.
4. 검색 가치가 있지만 FACTS로 답할 수 없는 질문은 답변 자리에 "[확인 필요: 필요한 정보]" 라고만 쓴다. 사람이 채운다.
5. 답변은 첫 문장에 결론, 2~4문장, 고유명사는 FACTS 표기 그대로.
6. 질문은 15~25개, 한국어, 질문 안에 "{name}"을 자연스럽게 포함한다.

출력 형식을 정확히 지킨다. 형식 밖의 말은 한 글자도 쓰지 않는다:

# QA
## 질문
답변
## 질문
답변

# BIO
## short
(80자 이내, SNS 프로필용)
## medium
(300자 이내, 스트리밍 플랫폼 아티스트 소개용)
## long
(800자 이내, 웹사이트/위키형 소개용)

FACTS:
{facts}
"""

PLATFORMS = """## 붙여넣기 체크리스트 (모든 곳에 같은 표기, 같은 링크)
- [ ] Spotify for Artists 소개 : medium
- [ ] YouTube 채널 정보 : medium
- [ ] Instagram / X 프로필 : short
- [ ] 멜론 / 지니 / 벅스 아티스트 페이지 : medium
- [ ] Bandcamp / SoundCloud 소개 : long
- [ ] MusicBrainz 아티스트 등록 (URL 관계에 공식 사이트 + 위 링크 전부) : 사실만
- [ ] Wikidata 항목 (공식 웹사이트, 장르, 활동 시작) : 사실만
- [ ] Genius 아티스트 인증 후 소개 : long
- [ ] Bing Webmaster Tools 에 사이트 등록 + sitemap 제출 (ChatGPT 검색은 Bing 인덱스)
- [ ] Google Search Console 에 사이트 등록 + sitemap 제출
"""


def die(msg):
    sys.exit(f"geo: {msg}")


def parse_facts():
    if not FACTS.exists():
        die("facts.md 가 없어. 먼저 init.")
    head, _, body = FACTS.read_text(encoding="utf-8").partition("\n---\n")
    meta = {"sameAs": []}
    for line in head.splitlines():
        k, _, v = line.partition(":")
        k, v = k.strip(), v.strip()
        if not k or not v:
            continue
        if k == "sameAs":
            meta["sameAs"].append(v)
        else:
            meta[k] = v
    for req in ("name", "url", "description"):
        if not meta.get(req):
            die(f"facts.md 헤더에 {req}: 가 필요해")
    meta.setdefault("type", "Person")
    meta["url"] = meta["url"].rstrip("/")
    return meta, body.strip()


def parse_sections(text, level):
    """'# ' 또는 '## ' 로 시작하는 헤딩 기준으로 (제목, 본문) 리스트."""
    marker = "#" * level + " "
    out = []
    for block in re.split(rf"^{re.escape(marker)}", text, flags=re.M)[1:]:
        title, _, body = block.partition("\n")
        if title.strip():
            out.append((title.strip(), body.strip()))
    return out


def parse_qa(text):
    return [(q, a) for q, a in parse_sections(text, 2) if a]


def llm(prompt, web=False):
    """GEO_LLM=claude(기본)|codex. 웹검색(web=True)은 claude 만."""
    if os.environ.get("GEO_LLM") == "codex" and not web:
        with tempfile.NamedTemporaryFile("r", suffix=".txt") as out:
            r = subprocess.run(["codex", "exec", "--skip-git-repo-check", "-o", out.name, "-"],
                               input=prompt, capture_output=True, text=True)
            if r.returncode != 0:
                die(f"codex 실패:\n{r.stderr}")
            return Path(out.name).read_text(encoding="utf-8").strip()
    cmd = ["claude", "-p", "--output-format", "text"]
    if web:
        cmd += ["--allowedTools", "WebSearch,WebFetch"]
    r = subprocess.run(cmd, input=prompt, capture_output=True, text=True)
    if r.returncode != 0:
        die(f"claude 실패:\n{r.stderr}")
    return r.stdout.strip()


# ---------- commands ----------

def init():
    if FACTS.exists():
        die("facts.md 가 이미 있어.")
    FACTS.write_text(FACTS_TEMPLATE, encoding="utf-8")
    print("facts.md 생성. 채운 뒤 `python3 geo.py gen`.")


def gen():
    meta, _ = parse_facts()
    print(f"{os.environ.get('GEO_LLM', 'claude')} 호출 중...")
    # 헤더(url, sameAs 링크)도 사실이다. "어디서 들을 수 있나요" 같은 질문에 필요.
    out = llm(GEN_PROMPT.format(name=meta["name"], facts=FACTS.read_text(encoding="utf-8")))
    sections = dict(parse_sections(out, 1))
    qa, bio = sections.get("QA", ""), sections.get("BIO", "")
    if not parse_qa(qa):
        die("출력 파싱 실패. 원문:\n" + out)
    for path, body, note in (
        (QA, qa, "검토 후 직접 수정. `[확인 필요` 가 남아 있으면 build 가 거부한다."),
        (BIOS, bio, "플랫폼 소개문. profile-pack.md 에 들어간다."),
    ):
        if path.exists():
            path.replace(path.with_suffix(".md.bak"))
        path.write_text(f"<!-- {note} -->\n\n{body}\n", encoding="utf-8")
    n = len(parse_qa(qa))
    todo = sum(TODO in a for _, a in parse_qa(qa))
    print(f"qa.md {n}개 질문 (확인 필요 {todo}개), bios.md 생성. qa.md 를 검토해.")


def paras(text):
    return "".join(f"<p>{html.escape(p.strip())}</p>" for p in text.split("\n\n") if p.strip())


def build():
    meta, _ = parse_facts()
    if not QA.exists():
        die("qa.md 가 없어. 먼저 gen.")
    items = parse_qa(QA.read_text(encoding="utf-8"))
    if not items:
        die("qa.md 가 비어 있어.")
    todo = [q for q, a in items if TODO in a or TODO in q]
    if todo:
        die("[확인 필요] 가 남아 있어 build 거부:\n- " + "\n- ".join(todo))

    name, url, desc = meta["name"], meta["url"], meta["description"]
    # 헤더의 나머지 키(jobTitle, alternateName, email, worksFor, image, genre ...)는 그대로 schema.org 속성이 된다
    entity = {"@type": meta["type"], "@id": url + "#me", **{k: v for k, v in meta.items() if k not in ("type", "sameAs")}}
    if meta["sameAs"]:
        entity["sameAs"] = meta["sameAs"]
    faq = {
        "@type": "FAQPage",
        "about": {"@id": entity["@id"]},
        "mainEntity": [
            {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in items
        ],
    }
    ld = json.dumps({"@context": "https://schema.org", "@graph": [entity, faq]}, ensure_ascii=False, indent=1)

    links = "".join(
        f'<li><a href="{html.escape(u)}" rel="me">{html.escape(urlparse(u).netloc)}</a></li>' for u in meta["sameAs"]
    )
    img = f'<img src="{html.escape(meta["image"])}" alt="{html.escape(name)}" width="160">' if meta.get("image") else ""
    qa_html = "\n".join(f"<section><h2>{html.escape(q)}</h2>{paras(a)}</section>" for q, a in items)
    page = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(name)} | 소개와 자주 묻는 질문</title>
<meta name="description" content="{html.escape(desc)}">
<link rel="canonical" href="{url}/">
<meta property="og:title" content="{html.escape(name)}">
<meta property="og:description" content="{html.escape(desc)}">
<meta property="og:url" content="{url}/">
<script type="application/ld+json">
{ld}
</script>
<style>
body{{max-width:42rem;margin:0 auto;padding:1rem 1rem 4rem;font-family:system-ui,-apple-system,sans-serif;line-height:1.65;color:#1a1a1a;background:#fff}}
h1{{margin:.5rem 0 0}}h2{{font-size:1.1rem;margin:1.6rem 0 .3rem}}p{{margin:.3rem 0}}
ul{{padding-left:1.2rem}}img{{border-radius:8px;display:block}}
</style>
</head>
<body>
<header>{img}<h1>{html.escape(name)}</h1><p>{html.escape(desc)}</p>
<ul>{links}</ul></header>
<main>
{qa_html}
</main>
<footer><p><a href="llms.txt">llms.txt</a></p></footer>
</body>
</html>
"""
    llms = [f"# {name}", f"> {desc}", "", f"- 공식: {url}", *(f"- {u}" for u in meta["sameAs"]), "", "## 자주 묻는 질문"]
    for q, a in items:
        llms += [f"### {q}", a, ""]
    today = datetime.date.today().isoformat()
    sitemap = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"<url><loc>{url}/</loc><lastmod>{today}</lastmod></url></urlset>\n"
    )
    robots = f"User-agent: *\nAllow: /\n\nSitemap: {url}/sitemap.xml\n"
    bios = BIOS.read_text(encoding="utf-8") if BIOS.exists() else "(bios.md 없음. gen 을 돌려.)"
    pack = f"# {name} profile pack\n\n공식: {url}\n" + "".join(f"- {u}\n" for u in meta["sameAs"]) + f"\n{bios}\n\n{PLATFORMS}"

    files = [("index.html", page), ("llms.txt", "\n".join(llms)), ("sitemap.xml", sitemap),
             ("robots.txt", robots), ("profile-pack.md", pack)]
    host = urlparse(url).netloc
    if not host.endswith("github.io"):
        files.append(("CNAME", host + "\n"))  # 커스텀 도메인: facts.md url 만 바꾸면 된다
    SITE.mkdir(exist_ok=True)
    for fname, content in files:
        (SITE / fname).write_text(content, encoding="utf-8")
    print(f"docs/ 생성: 질문 {len(items)}개, 링크 {len(meta['sameAs'])}개.")


def publish():
    build()
    subprocess.run(["git", "add", "-A"], check=True, cwd=ROOT)
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode:
        subprocess.run(["git", "commit", "-q", "-m", f"publish {datetime.date.today()}"], check=True, cwd=ROOT)
    subprocess.run(["git", "push", "-q"], check=True, cwd=ROOT)
    repo = subprocess.run(["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
                          capture_output=True, text=True, cwd=ROOT).stdout.strip()
    # Pages 활성화. 이미 켜져 있으면 409 가 오고 무시한다.
    r = subprocess.run(["gh", "api", "-X", "POST", f"repos/{repo}/pages", "--input", "-"], capture_output=True, text=True,
                       input=json.dumps({"build_type": "legacy", "source": {"branch": "main", "path": "/docs"}}))
    if r.returncode and "409" not in r.stderr:
        print("Pages 활성화 실패. 저장소 Settings > Pages 에서 main / docs 를 직접 선택:", r.stderr.strip()[:200])
    print(f"push 완료. 1~2분 뒤 {parse_facts()[0]['url']}/ 에서 확인.")


def check():
    meta, _ = parse_facts()
    name = meta["name"]
    host = urlparse(meta["url"]).netloc
    host = host[4:] if host.startswith("www.") else host
    queries = [f"{name}이(가) 누구야?", f"{name}에 대해 알려줘", f"who is {name}"]
    if meta["type"] == "MusicGroup":
        queries.insert(1, f"{name} 음악은 어떤 스타일이야?")
    first = not CHECKS.exists()
    today = datetime.date.today().isoformat()
    with CHECKS.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if first:
            w.writerow(["date", "query", "cited", "answer"])
        for q in queries:
            ans = llm(f"웹 검색을 사용해서 답해. 답 마지막에 'SOURCES:' 아래 참고한 URL을 전부 나열해.\n\n질문: {q}", web=True)
            cited = host in ans
            w.writerow([today, q, "yes" if cited else "no", ans[:300].replace("\n", " ")])
            print(("O " if cited else "X "), q)
    print(f"checks.csv 에 기록. 기준 도메인: {host}")


def selftest():
    global FACTS, QA, BIOS, SITE
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        FACTS, QA, BIOS, SITE = d / "facts.md", d / "qa.md", d / "bios.md", d / "site"
        FACTS.write_text(
            "name: 테스트\ntype: MusicGroup\nurl: https://t.example/\ndescription: 설명\njobTitle: 직함\nsameAs: https://x.com/t\n---\n# 정보\n- a\n",
            encoding="utf-8",
        )
        QA.write_text("<!-- c -->\n\n## 테스트는 누구야?\n결론.\n\n둘째 문단.\n## 두 번째?\n[확인 필요: 뭔가]\n", encoding="utf-8")
        try:
            build()
            raise AssertionError("[확인 필요] 가 있는데 build 가 통과함")
        except SystemExit as e:
            assert "확인 필요" in str(e), e
        QA.write_text("## 테스트는 누구야?\n결론.\n\n둘째 문단.\n## 두 번째?\n답.\n", encoding="utf-8")
        build()
        page = (SITE / "index.html").read_text(encoding="utf-8")
        ld = json.loads(re.search(r'<script type="application/ld\+json">\n(.*?)\n</script>', page, re.S).group(1))
        ent, faq = ld["@graph"]
        assert ent["@type"] == "MusicGroup" and ent["url"] == "https://t.example" and ent["sameAs"] == ["https://x.com/t"]
        assert ent["jobTitle"] == "직함" and ent["name"] == "테스트"
        assert faq["@type"] == "FAQPage" and len(faq["mainEntity"]) == 2
        assert faq["mainEntity"][0]["acceptedAnswer"]["text"] == "결론.\n\n둘째 문단."
        assert "<p>결론.</p><p>둘째 문단.</p>" in page
        assert "### 두 번째?" in (SITE / "llms.txt").read_text(encoding="utf-8")
        assert "Sitemap: https://t.example/sitemap.xml" in (SITE / "robots.txt").read_text(encoding="utf-8")
        assert dict(parse_sections("# QA\n## q\na\n# BIO\n## short\ns\n", 1))["BIO"].startswith("## short")
    print("selftest ok")


if __name__ == "__main__":
    cmds = {f.__name__: f for f in (init, gen, build, publish, check, selftest)}
    if len(sys.argv) != 2 or sys.argv[1] not in cmds:
        sys.exit(__doc__)
    cmds[sys.argv[1]]()
