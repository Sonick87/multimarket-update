"""시중 최저가 조회 → 멀티마켓 서버 (PC에서 실행, 표준 라이브러리만)
- 서버에서 추천 후보 목록을 받아, 상품마다 여러 곳(에누리·네이버·롯데ON)의 비로그인 공개 검색 1페이지를 1번씩 읽고
  그중 가장 싼 값을 최저가로 올립니다. 어느 곳 값인지(판매처)도 같이 올립니다. 쿠팡·G마켓·SSG·다나와는 이 PC에서도 막혀 있어 제외입니다.
  (에누리 값은 여러 쇼핑몰 가격을 모은 최저가이고, 네이버 값에는 11번가·G마켓·옥션·롯데ON·SSG 등이 들어 있습니다.)
- 한 번 실행에 소량(기본 20개)만 조회하고, 요청 사이 15~25초 대기합니다. 기본 User-Agent(위장 없음).
- 어느 한 곳이 403/418/429/503 이나 캡차 안내를 보내면 우회하지 않고 그 곳만 3시간 쉬고, 나머지 곳으로 계속합니다.
  모든 곳이 막히면 멈추고, 서버 화면에 「점검 대기」로 표시됩니다.
- 기본(상주)으로 켜 두면 1분마다 서버를 확인하고, 「지금 찾기」(자동 요청)나 「지금 조회 요청」이 있으면 바로 조회합니다. 평소에는 2시간에 한 번만 조회합니다. 설치 때 한 번 바로 조회하려면 python naver_lowest.py --now
- 이미 조회한 상품(결과 없음 포함)은 24시간 안에는 다시 조회하지 않고, 다음 실행에서 못 한 상품부터 이어서 합니다.
- 상품명에서 홍보어·수량·단위를 빼고 핵심 단어만 검색어로 쓰며, 결과 중 우리 상품명과 핵심 단어가 충분히 겹치는 것만 최저가로 씁니다.
- 값은 도매매번호 기준으로 서버에 올립니다. 최저가가 공급가의 50% 미만이면 같은 상품이 아닐 가능성이 커서 올리지 않습니다.
사용: python naver_lowest.py  (같은 폴더의 pc_token.txt 를 읽음)"""
import json, os, random, re, sys, time, urllib.request, urllib.error, urllib.parse

SERVER = os.environ.get("MM_SERVER", "https://168-110-41-196.sslip.io")
NAVER = os.environ.get("NAVER_BASE", "https://m.search.naver.com/search.naver?where=m_shop&query=")
ENURI = os.environ.get("ENURI_BASE", "https://price.enuri.com/search?keyword=")
LOTTE = os.environ.get("LOTTE_BASE", "https://www.lotteon.com/search/search/search.ecn?render=search&platform=pc&q=")
SOURCES = [("enuri", "에누리", ENURI), ("naver", "네이버", NAVER), ("lotteon", "롯데ON", LOTTE)]   # 앞의 것이 같은 값일 때 우선
HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "naver_state.json")
BLOCK = (403, 418, 429, 503)
GAP = (float(os.environ.get("GAP_MIN", 15)), float(os.environ.get("GAP_MAX", 25)))
BATCH = int(os.environ.get("BATCH", 20))                  # 평소 한 번 실행에 조회할 검색어 수
BLOCK_HOURS = float(os.environ.get("BLOCK_HOURS", 3))    # 막히면 쉬는 시간
RECHECK_HOURS = float(os.environ.get("RECHECK_HOURS", 24))
NORMAL_GAP_HOURS = float(os.environ.get("NORMAL_GAP_HOURS", 2))   # 평소 조회 간격(서버가 「지금 조회」를 요청하면 건너뜀)
PROMO = set("특가 무료배송 사은품 당일발송 당일배송 인기 신상 신상품 최저가 국내 정품 증정 이벤트 할인 대박 추천 hot new best 베스트 단독 한정 행사 무배 쿠폰".split())
UNIT = re.compile(r"^\d+([.,]\d+)?(개|매|입|p|ea|pcs|set|세트|ml|g|kg|cm|mm|m|l|cc|구|족|장|팩|롤|호|단|색|종)?$", re.I)

def log(*a):
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + " ".join(str(x) for x in a)
    print(line, flush=True)
    try:
        with open(os.path.join(HERE, "naver_lowest.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def core_words(name):
    """상품명에서 [..] (..) 홍보어·수량·단위를 빼고 핵심 단어 앞 5개"""
    n = re.sub(r"\[[^\]]*\]|\([^)]*\)|【[^】]*】", " ", name or "")
    n = re.sub(r"[/·,+_\-]+", " ", n)
    out = []
    for w in n.split():
        w = re.sub(r"[^0-9A-Za-z가-힣]", "", w)
        if len(w) < 2 or w.lower() in PROMO or UNIT.match(w):
            continue
        if w.lower() not in [x.lower() for x in out]:
            out.append(w)
        if len(out) >= 5:
            break
    return out

def query_of(name):
    return " ".join(core_words(name))

def similar(core, cand_name):
    """핵심 단어 중 60% 이상이 후보 상품명에 들어 있으면 같은 종류의 상품으로 본다(띄어쓰기 무시)"""
    if not core:
        return False
    c = re.sub(r"\s+", "", cand_name or "").lower()
    hit = sum(1 for w in core if w.lower() in c)
    need = max(1, -(-len(core) * 6 // 10))   # 올림
    return hit >= need

def lowest_from(html, core, supply=0):
    """응답을 "cardType":" 기준으로 잘라 광고(AD)는 빼고, 우리 상품과 비슷한 상품 중 최저가를 돌려줌.
    공급가의 50% 미만인 값은 낱개·부품 등 다른 상품일 가능성이 커서 건너뜀. (가격, 상품명, 본 상품 수, 건너뛴 수)"""
    best, seen, low = (0, ""), 0, 0
    for seg in html.split('"cardType":"')[1:]:
        if '"sourceType":"AD"' in seg[:3000]:
            continue
        m = re.search(r'"discountedSalePrice":"?(\d[\d,]*)"?', seg) or re.search(r'"salePrice":"?(\d[\d,]*)"?', seg)
        if not m:
            continue
        v = int(m.group(1).replace(",", ""))
        if v < 100:
            continue
        nm = re.search(r'"productName":"(.*?)"', seg)
        name = nm.group(1) if nm else ""
        seen += 1
        if not similar(core, name):
            continue
        if supply and v < supply * 0.5:
            low += 1
            continue
        if best[0] == 0 or v < best[0]:
            best = (v, name)
    return best[0], best[1], seen, low

def _num(x):
    try:
        return int(float(str(x).replace(",", "")))
    except Exception:
        return 0

def _walk(o, out):
    """JSON 안에서 itemListElement 목록을 모두 모음"""
    if isinstance(o, dict):
        for k, v in o.items():
            if k == "itemListElement" and isinstance(v, list):
                out.extend(v)
            else:
                _walk(v, out)
    elif isinstance(o, list):
        for v in o:
            _walk(v, out)

def enuri_lowest(html, core, supply=0):
    """에누리 검색 결과의 ld+json 상품 목록(상품명, 쇼핑몰 전체 최저가 lowPrice). (가격, 상품명, 본 수, 건너뛴 수)"""
    best, seen, low = (0, ""), 0, 0
    for m in re.finditer(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', html, re.S | re.I):
        try:
            data = json.loads(m.group(1))
        except Exception:
            continue
        els = []
        _walk(data, els)
        for el in els:
            it = el.get("item") if isinstance(el, dict) and isinstance(el.get("item"), dict) else el
            if not isinstance(it, dict):
                continue
            offers = it.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers and isinstance(offers[0], dict) else {}
            v = _num(offers.get("lowPrice") or offers.get("price"))
            if v < 100:
                continue
            name = str(it.get("name") or "")
            seen += 1
            if not similar(core, name):
                continue
            if supply and v < supply * 0.5:
                low += 1
                continue
            if best[0] == 0 or v < best[0]:
                best = (v, name)
    return best[0], best[1], seen, low

def lotteon_lowest(html, core, supply=0):
    """롯데ON 검색 결과 JSON: spdNm(상품명) 뒤의 discountPrice > final_price > price. 광고(cpc)는 뺀다."""
    best, seen, low = (0, ""), 0, 0
    for seg in re.split(r'"spdNm"\s*:\s*', html)[1:]:
        nm = re.match(r'"((?:[^"\\]|\\.)*)"', seg)
        if not nm:
            continue
        chunk = seg[:2500]
        if re.search(r'"ad"\s*:\s*"cpc"', chunk):
            continue
        pm = (re.search(r'"discountPrice"\s*:\s*"?(\d[\d,]*)', chunk) or re.search(r'"final_?[pP]rice"\s*:\s*"?(\d[\d,]*)', chunk)
              or re.search(r'"price"\s*:\s*"?(\d[\d,]*)', chunk))
        if not pm:
            continue
        v = _num(pm.group(1))
        if v < 100:
            continue
        try:
            name = json.loads('"' + nm.group(1) + '"')
        except Exception:
            name = nm.group(1)
        seen += 1
        if not similar(core, name):
            continue
        if supply and v < supply * 0.5:
            low += 1
            continue
        if best[0] == 0 or v < best[0]:
            best = (v, name)
    return best[0], best[1], seen, low

def parse_source(src, html, core, supply):
    if src == "enuri":
        return enuri_lowest(html, core, supply)
    if src == "lotteon":
        return lotteon_lowest(html, core, supply)
    return lowest_from(html, core, supply)

def blocked_page(src, html):
    """차단·캡차 안내 화면인지(상품 데이터가 없고 안내 문구만 있음)"""
    if not re.search(r"captcha|비정상적인 접근|자동입력 방지|robot|access denied", html, re.I):
        return False
    if src == "naver":
        return '"cardType":"' not in html
    if src == "enuri":
        return "application/ld+json" not in html
    return '"spdNm"' not in html

def get(url, headers=None):
    return get2(url, headers)[0]

def get2(url, headers=None):
    """(본문, 응답 헤더)"""
    req = urllib.request.Request(url, headers=dict({"Accept-Language": "ko-KR"}, **(headers or {})))
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read().decode("utf-8", "replace"), r.headers

def load_state():
    try:
        s = json.load(open(STATE, encoding="utf-8"))
        s.setdefault("done", {}); s.setdefault("blocked", {})
        if s.get("blocked_until", 0) > time.time() and "naver" not in s["blocked"]:
            s["blocked"]["naver"] = s["blocked_until"]   # 옛 형식(네이버만 막힘) 이어받기
        s.pop("blocked_until", None)
        return s
    except Exception:
        return {"done": {}, "blocked": {}}

def save_state(s):
    try:
        with open(STATE, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False)
    except Exception as ex:
        log("상태 저장 실패:", ex)

def post(token, payload):
    req = urllib.request.Request(SERVER + "/api/mins", data=json.dumps(payload).encode("utf-8"),
                                 headers={"X-PC-Token": token, "Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")

def hhmm(ts):
    return time.strftime("%m-%d %H:%M", time.localtime(ts))

def main():
    try:
        token = open(os.path.join(HERE, "pc_token.txt"), encoding="utf-8").read().strip()
    except Exception:
        log("pc_token.txt 가 없습니다"); return 2
    st = load_state()
    now = time.time()
    names = {k: n for k, n, _ in SOURCES}
    def active():
        return [k for k, _, _ in SOURCES if st["blocked"].get(k, 0) <= time.time()]
    if not active():
        log("모든 조회처가 점검 대기 중:", ", ".join(f"{names[k]} {hhmm(st['blocked'][k])}" for k in st["blocked"] if k in names), "이후에 다시 시도합니다(요청 안 함)")
        return 0
    try:
        body, hd = get2(SERVER + "/api/finds", {"X-PC-Token": token})
        items = json.loads(body)
    except Exception as ex:
        log("서버에서 목록을 못 받음:", ex); return 3
    rush = (hd.get("X-Rush") or "") == "1"
    force = rush or "--now" in sys.argv
    if not force and now - st.get("last_batch", 0) < NORMAL_GAP_HOURS * 3600 * 0.95:
        log("다음 조회 시각 전이라 쉽니다:", hhmm(st.get("last_batch", 0) + NORMAL_GAP_HOURS * 3600), "이후(서버에서 「지금 조회 요청」을 누르면 바로 조회)")
        return 0
    if rush:
        log("서버에서 「지금 조회」를 요청했습니다")
    fresh = now - RECHECK_HOURS * 3600
    todo = []
    for i in items:
        if i.get("has") or not i.get("key"):
            continue
        if (st["done"].get(str(i["key"])) or {}).get("t", 0) > fresh:
            continue   # 24시간 안에 이미 조회함
        todo.append(i)
    # 네이버가 쉬는 중이면 에누리·롯데ON만 쓰므로 조금 더 많이(캡차는 곳마다 따로 보고 막히면 그 곳만 쉼)
    batch, gap = (BATCH if "naver" in active() else BATCH * 2), GAP
    log(f"후보 {len(items)}개 중 조회할 것 {len(todo)}개 (이번에는 최대 {batch}개, 조회처: {', '.join(names[k] for k in active())})")
    out, srcs, qres, first, nq = {}, {}, {}, True, 0   # qres[검색어] = {조회처: html}
    why_stop = {}
    for it in todo:
        if not active():
            break
        q = query_of(it.get("name") or "")
        core = core_words(it.get("name") or "")
        key = str(it["key"])
        if not q:
            st["done"][key] = {"v": 0, "t": time.time(), "q": ""}
            continue
        if q not in qres:
            if nq >= batch:
                break   # 이번 실행 분량 끝, 나머지는 다음 실행에서
            if not first:
                time.sleep(random.uniform(*gap))
            first = False
            nq += 1
            qres[q] = {}
            for k, nmk, base in SOURCES:
                if k not in active():
                    continue
                try:
                    html = get(base + urllib.parse.quote(q))
                except urllib.error.HTTPError as ex:
                    if ex.code in BLOCK:
                        st["blocked"][k] = time.time() + BLOCK_HOURS * 3600
                        why_stop[k] = f"{nmk}가 막음 HTTP {ex.code}"
                        log("중단:", why_stop[k], f"(우회하지 않음) → {hhmm(st['blocked'][k])} 이후에 이 곳만 다시 시도")
                    else:
                        log("오류", nmk, q, ex.code)
                    continue
                except Exception as ex:
                    log("오류", nmk, q, ex); continue
                if blocked_page(k, html):
                    st["blocked"][k] = time.time() + BLOCK_HOURS * 3600
                    why_stop[k] = f"{nmk} 캡차/비정상 접근 안내"
                    log("중단:", why_stop[k], f"(우회하지 않음) → {hhmm(st['blocked'][k])} 이후에 이 곳만 다시 시도")
                    continue
                qres[q][k] = html   # 같은 검색어는 다시 요청하지 않고 이 응답을 재사용
                time.sleep(random.uniform(1, 2))   # 서로 다른 사이트라도 연달아 쏘지 않음
        sp = float(it.get("supply") or 0)
        cands, notes = [], []
        for k, nmk, _ in SOURCES:
            if k in qres[q]:
                v, nm, seen, low = parse_source(k, qres[q][k], core, sp)
                notes.append(f"{nmk} {v or '없음'}")
                if v:
                    cands.append((v, nmk))
        v, src = min(cands, key=lambda c: c[0]) if cands else (0, "")
        if not qres[q]:
            continue   # 이번에 어느 곳도 못 읽음(막힘 등): 조회한 것으로 치지 않고 다음에 다시
        if v:
            out[key] = v; srcs[key] = src
        st["done"][key] = {"v": v, "t": time.time(), "q": q, "src": src}
        log(q, "→", f"{v:,}원 ({src})" if v else "없음", "|", ", ".join(notes))
    remaining = len([i for i in todo if (st["done"].get(str(i["key"])) or {}).get("t", 0) <= fresh])
    st["last_batch"] = time.time()
    act = active()
    status = {"state": "blocked" if not act else "ok", "t": time.time(), "checked": nq, "found": len(out), "remaining": remaining}
    status["sources"] = [{"name": names[k], "until": st["blocked"].get(k, 0)} for k, _, _ in SOURCES if st["blocked"].get(k, 0) > time.time()]
    if rush and remaining == 0:
        status["rush_done"] = 1   # 다 조회했을 때만 서버의 「지금 조회 요청」 표시를 지움(남았으면 다음 확인 때 이어서)
    if not act:
        status["until"] = min(st["blocked"].values())
        status["why"] = " / ".join(why_stop.values()) or "모든 조회처 점검 대기"
    save_state(st)
    try:
        log("서버 응답:", post(token, {"mins_by_key": out, "src_by_key": srcs, "naver_status": status}))
    except Exception as ex:
        log("서버에 올리기 실패:", ex); return 4
    log(f"끝: 올린 값 {len(out)}개, 남은 조회 {remaining}개")
    return 0

def loop():
    """켜 둔 동안 1분마다 서버 요청을 확인해 「지금 찾기」 직후 바로 조회합니다. 같은 PC에서 두 개가 동시에 돌지 않게 잠금을 겁니다."""
    import socket
    lock = socket.socket()
    try:
        lock.bind(("127.0.0.1", 47831))
    except OSError:
        log("이미 다른 조회 프로그램이 실행 중이라 이 실행은 끝냅니다")
        return 0
    log("상주 모드 시작: 1분마다 서버 요청을 확인합니다")
    while True:
        try:
            main()
        except Exception as ex:
            log("오류:", ex)
        if "--now" in sys.argv:
            sys.argv.remove("--now")   # 처음 한 번만 바로 조회
        time.sleep(float(os.environ.get("LOOP_SECS", 60)))

if __name__ == "__main__":
    if "--once" in sys.argv:
        sys.exit(main())
    sys.exit(loop() or 0)   # 기본은 상주(예약작업이 옛 방식이어도 한 번 시작되면 계속 돕니다)
