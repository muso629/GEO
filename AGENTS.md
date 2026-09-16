# GEO 프로젝트 : 에이전트 공통 규칙 (Claude Code, Codex 공용)

## 목적
사람이 쓴 facts.md 를 근거로 Q&A 페이지를 만들어 GitHub Pages 에 올리고,
LLM 검색 답변에서 이 도메인이 인용되는지 추적한다.
핵심 원칙: 사실은 사람이 쓴다. AI 는 포맷만 한다.

## 절대 규칙
1. 사실의 원천은 facts.md 뿐이다. 사람이 준 정보 없이 facts.md 에 사실을 추가하지 않는다.
2. qa.md 의 모든 답변은 facts.md 문장으로 추적 가능해야 한다. 근거 없는 날짜, 이름, 숫자, 장르, 협업자는 쓰지 않는다. 모르면 답변 자리에 `[확인 필요: 필요한 정보]` 만 쓴다.
3. `[확인 필요` 가 남아 있으면 build 는 실패한다. 이 가드를 우회하거나 지우지 않는다.
4. 다른 사이트(Reddit, 커뮤니티, Q&A 사이트 등)에 자동 게시하는 기능은 만들지 않는다. 봇 게시는 밴과 도메인 신뢰 하락으로 이어진다.
5. docs/ 는 빌드 산출물이다. 직접 수정하지 말고 `python3 geo.py build` 로 만든다.

## 파일
- facts.md   사람이 쓰는 사실. 헤더(name/type/url/description/image/genre/sameAs 여러 줄) + `---` + 본문 마크다운
- qa.md      gen 이 만들고 사람이 검토. `## 질문` 다음 줄부터 답변. 빈 줄 두 개는 문단 구분
- bios.md    플랫폼 소개문 short / medium / long
- docs/      GitHub Pages 산출물: index.html(+JSON-LD Person|MusicGroup + FAQPage), llms.txt, sitemap.xml, robots.txt, profile-pack.md, CNAME(커스텀 도메인일 때)
- checks.csv check 결과 누적 (날짜, 질문, 인용 여부, 답변 앞부분)

## 명령
```
python3 geo.py init      facts.md 템플릿
python3 geo.py gen       facts.md -> qa.md, bios.md   (GEO_LLM=codex 면 codex exec 사용, 기본 claude CLI)
python3 geo.py build     -> docs/                    ([확인 필요 남아 있으면 거부)
python3 geo.py publish   build + commit + push        (Pages 는 main:/docs, 최초 1회 자동 활성화, CI 없음)
python3 geo.py check     LLM 웹검색으로 물어보고 도메인 인용 여부 기록 (웹검색 때문에 claude 고정)
python3 geo.py selftest  코드 수정 후 반드시 통과
```

## 스택
Python 3.9 표준 라이브러리만. 새 의존성, 프레임워크, 빌드 도구 추가 금지. 파일 하나(geo.py) 유지.
커스텀 도메인은 facts.md 의 url 만 바꾸면 CNAME 이 생성된다. 그 뒤 도메인 DNS 에 CNAME -> muso629.github.io.
