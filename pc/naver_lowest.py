"""네이버 최저가 → 멀티마켓 서버 목록 채우기 (PC에서 실행, 표준 라이브러리만).
사용: python naver_lowest.py <서버주소> <토큰>
- 사람이 보는 공개 검색 결과 1페이지만, 키워드당 6초 간격으로 가져옵니다(낮은 빈도).
- 네이버가 막으면(418·403·429 등) 우회하지 않고 즉시 멈추고 이유를 출력합니다."""
import json, re, sys, time, urllib.request, urllib.error, urllib.parse

UA = "Mozilla/5.0 (compatible; MultiMarketPC/1.0)"

def get(url, headers=None):
    req = urllib.request.Request(url, headers=dict({"User-Agent": UA, "Accept-Language": "ko"}, **(headers or {})))
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", "replace")

def prices_from(html):
    """검색 결과 페이지의 __NEXT_DATA__ JSON 에서 상품 가격(원) 후보를 모음"""
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    out = []
    if m:
        try:
            data = json.loads(m.group(1))
        except Exception:
            data = None
        def walk(o):
            if isinstance(o, dict):
                for k in ("lowPrice", "price", "mobileLowPrice", "salePrice"):
                    v = o.get(k)
                    if isinstance(v, (int, float)) and 100 <= v < 10**8:
                        out.append(int(v)); break
                    if isinstance(v, str) and v.replace(",", "").isdigit() and 100 <= int(v.replace(",", "")) < 10**8:
                        out.append(int(v.replace(",", ""))); break
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)
        if data:
            walk(data)
    return out

def main():
    if len(sys.argv) < 3:
        print("사용: python naver_lowest.py <서버주소> <토큰>"); return 2
    server, token = sys.argv[1].rstrip("/"), sys.argv[2]
    finds = json.loads(get(f"{server}/api/finds", {"X-PC-Token": token}))
    print(f"후보 {len(finds)}개")
    mins, seen = {}, {}
    for f in finds:
        kw = f["keyword"]
        if kw in seen:
            if seen[kw]:
                mins[f["id"]] = seen[kw]
            continue
        url = "https://msearch.shopping.naver.com/search/all?" + urllib.parse.urlencode({"query": kw})
        try:
            html = get(url)
        except urllib.error.HTTPError as e:
            print(f"네이버가 막았습니다: HTTP {e.code} ({kw}). 우회하지 않고 멈춥니다."); break
        except Exception as ex:
            print(f"접속 실패: {ex}"); break
        ps = sorted(prices_from(html))
        low = ps[0] if ps else 0
        seen[kw] = low
        print(f"{kw}: 가격 {len(ps)}개 → 최저 {low}")
        if low:
            mins[f["id"]] = low
        time.sleep(6)
    if mins:
        req = urllib.request.Request(f"{server}/api/mins", data=json.dumps({"mins": mins}).encode(), headers={"Content-Type": "application/json", "X-PC-Token": token})
        print(urllib.request.urlopen(req, timeout=30).read().decode())
    else:
        print("채울 최저가가 없습니다(막혔거나 가격을 못 읽음).")
    return 0

if __name__ == "__main__":
    sys.exit(main())
