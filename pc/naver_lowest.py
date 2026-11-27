"""네이버 최저가 → 멀티마켓 서버 (PC에서 실행, 표준 라이브러리만)
- 서버에서 추천 후보 목록을 받아, 비로그인 공개 모바일 쇼핑검색 1페이지를 상품당 1번만 읽습니다.
- 요청 사이 8~14초 대기, 기본 User-Agent(위장 없음). 403/418/429/503 이나 캡차 안내가 오면 우회하지 않고 즉시 멈춥니다.
- 값은 도매매번호 기준으로 서버에 올립니다. 최저가가 공급가의 50% 미만이면 같은 상품이 아닐 가능성이 커서 올리지 않습니다.
사용: python naver_lowest.py  (같은 폴더의 pc_token.txt 를 읽음)"""
import json, os, random, re, sys, time, urllib.request, urllib.error, urllib.parse

SERVER = os.environ.get("MM_SERVER", "https://168-110-41-196.sslip.io")
NAVER = os.environ.get("NAVER_BASE", "https://m.search.naver.com/search.naver?where=m_shop&query=")
HERE = os.path.dirname(os.path.abspath(__file__))
BLOCK = (403, 418, 429, 503)
GAP = (float(os.environ.get("GAP_MIN", 8)), float(os.environ.get("GAP_MAX", 14)))

def log(*a):
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + " ".join(str(x) for x in a)
    print(line, flush=True)
    try:
        with open(os.path.join(HERE, "naver_lowest.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def query_of(name):
    """상품명에서 [..] (..) 홍보어를 빼고 앞 5단어"""
    n = re.sub(r"\[[^\]]*\]|\([^)]*\)|【[^】]*】", " ", name or "")
    n = re.sub(r"[/·,]+", " ", n)
    return " ".join(n.split()[:5])

def lowest_from(html):
    """응답을 "cardType":" 기준으로 잘라 광고(AD)는 빼고 가격 중 최저를 돌려줌. (가격, 상품명) 또는 (0, '')"""
    best = (0, "")
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
        if best[0] == 0 or v < best[0]:
            best = (v, nm.group(1) if nm else "")
    return best

def get(url, headers=None):
    req = urllib.request.Request(url, headers=dict({"Accept-Language": "ko-KR"}, **(headers or {})))
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read().decode("utf-8", "replace")

def main():
    try:
        token = open(os.path.join(HERE, "pc_token.txt"), encoding="utf-8").read().strip()
    except Exception:
        log("pc_token.txt 가 없습니다"); return 2
    try:
        items = json.loads(get(SERVER + "/api/finds", {"X-PC-Token": token}))
    except Exception as ex:
        log("서버에서 목록을 못 받음:", ex); return 3
    todo = [i for i in items if not i.get("has") and i.get("key")]
    log(f"후보 {len(items)}개 중 조회할 것 {len(todo)}개")
    out, done_q, stop, first = {}, {}, "", True
    for it in todo:
        q = query_of(it.get("name") or "")
        if not q:
            continue
        if q not in done_q:
            if not first:
                time.sleep(random.uniform(*GAP))
            first = False
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
            done_q[q] = lowest_from(html)
        v, nm = done_q[q]
        sp = float(it.get("supply") or 0)
        small = bool(v and sp and v < sp * 0.5)
        if v and not small:
            out[str(it["key"])] = v
        log(q, "→", v or "없음", "(공급가 절반 미만이라 제외)" if small else "")
    if out:
        req = urllib.request.Request(SERVER + "/api/mins", data=json.dumps({"mins_by_key": out}).encode("utf-8"),
                                     headers={"X-PC-Token": token, "Content-Type": "application/json"})
        try:
            log("서버 응답:", urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace"))
        except Exception as ex:
            log("서버에 올리기 실패:", ex); return 4
    if stop:
        log("중단:", stop, "(우회하지 않음)")
    log(f"끝: 올린 값 {len(out)}개")
    return 0

if __name__ == "__main__":
    sys.exit(main())
