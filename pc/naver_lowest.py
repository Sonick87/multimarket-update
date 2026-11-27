"""네이버 최저가 → 멀티마켓 서버 (PC에서 실행, 표준 라이브러리만)
- 서버에서 추천 후보 목록을 받아, 비로그인 공개 모바일 쇼핑검색 1페이지를 상품당 1번만 읽습니다.
- 한 번 실행에 소량(기본 8개)만 조회하고, 요청 사이 30~60초 대기합니다. 기본 User-Agent(위장 없음).
- 403/418/429/503 이나 캡차 안내가 오면 우회하지 않고 즉시 멈추고, 3시간 동안은 네이버에 다시 요청하지 않습니다.
  멈춘 상태와 재시도 시각은 서버 화면에 「네이버 점검 대기」로 표시됩니다.
- 예약작업은 30분마다 이 파일을 실행하지만, 평소에는 2시간에 한 번만 조회합니다. 서버 화면에서 「지금 조회 요청」을 누르면
  다음 실행(30분 안)에 바로 한 번 조회합니다. 설치 때 한 번 바로 조회하려면 python naver_lowest.py --now
- 이미 조회한 상품(결과 없음 포함)은 24시간 안에는 다시 조회하지 않고, 다음 실행에서 못 한 상품부터 이어서 합니다.
- 상품명에서 홍보어·수량·단위를 빼고 핵심 단어만 검색어로 쓰며, 결과 중 우리 상품명과 핵심 단어가 충분히 겹치는 것만 최저가로 씁니다.
- 값은 도매매번호 기준으로 서버에 올립니다. 최저가가 공급가의 50% 미만이면 같은 상품이 아닐 가능성이 커서 올리지 않습니다.
사용: python naver_lowest.py  (같은 폴더의 pc_token.txt 를 읽음)"""
import json, os, random, re, sys, time, urllib.request, urllib.error, urllib.parse

SERVER = os.environ.get("MM_SERVER", "https://168-110-41-196.sslip.io")
NAVER = os.environ.get("NAVER_BASE", "https://m.search.naver.com/search.naver?where=m_shop&query=")
HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "naver_state.json")
BLOCK = (403, 418, 429, 503)
GAP = (float(os.environ.get("GAP_MIN", 30)), float(os.environ.get("GAP_MAX", 60)))
BATCH = int(os.environ.get("BATCH", 8))                  # 한 번 실행에 조회할 검색어 수
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
        s.setdefault("done", {}); s.setdefault("blocked_until", 0)
        return s
    except Exception:
        return {"done": {}, "blocked_until": 0}

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
    if st["blocked_until"] > now:
        log("네이버 점검 대기 중:", hhmm(st["blocked_until"]), "이후에 다시 시도합니다(요청 안 함)")
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
    log(f"후보 {len(items)}개 중 조회할 것 {len(todo)}개 (이번에는 최대 {BATCH}개)")
    out, qres, stop, first, nq = {}, {}, "", True, 0
    for it in todo:
        q = query_of(it.get("name") or "")
        core = core_words(it.get("name") or "")
        key = str(it["key"])
        if not q:
            st["done"][key] = {"v": 0, "t": time.time(), "q": ""}
            continue
        if q not in qres:
            if nq >= BATCH:
                break   # 이번 실행 분량 끝, 나머지는 다음 실행에서
            if not first:
                time.sleep(random.uniform(*GAP))
            first = False
            nq += 1
            try:
                html = get(NAVER + urllib.parse.quote(q))
            except urllib.error.HTTPError as ex:
                if ex.code in BLOCK:
                    stop = f"네이버가 막음 HTTP {ex.code}"; break
                log("오류", q, ex.code); continue
            except Exception as ex:
                log("오류", q, ex); continue
            if re.search(r"captcha|비정상적인 접근|자동입력 방지", html, re.I) and '"cardType":"' not in html:
                stop = "네이버 캡차/비정상 접근 안내"; break
            qres[q] = html   # 같은 검색어는 다시 요청하지 않고 이 응답을 재사용
        sp = float(it.get("supply") or 0)
        v, nm, seen, low = lowest_from(qres[q], core, sp)
        if v:
            out[key] = v
        st["done"][key] = {"v": v, "t": time.time(), "q": q}
        why = "" if v else f"(조건에 맞는 비슷한 상품 없음: 본 {seen}개, 너무 싸서 뺀 {low}개)"
        log(q, "→", v or "없음", why)
    remaining = len([i for i in todo if (st["done"].get(str(i["key"])) or {}).get("t", 0) <= fresh])
    st["last_batch"] = time.time()
    status = {"state": "blocked" if stop else "ok", "t": time.time(), "checked": nq, "found": len(out), "remaining": remaining}
    if rush:
        status["rush_done"] = 1   # 서버의 「지금 조회 요청」 표시를 지움
    if stop:
        st["blocked_until"] = time.time() + BLOCK_HOURS * 3600
        status["until"] = st["blocked_until"]
        status["why"] = stop
        log("중단:", stop, f"(우회하지 않음) → {hhmm(st['blocked_until'])} 이후에 이어서 조회")
    save_state(st)
    try:
        log("서버 응답:", post(token, {"mins_by_key": out, "naver_status": status}))
    except Exception as ex:
        log("서버에 올리기 실패:", ex); return 4
    log(f"끝: 올린 값 {len(out)}개, 남은 조회 {remaining}개")
    return 0

if __name__ == "__main__":
    sys.exit(main())
