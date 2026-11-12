# -*- coding: utf-8 -*-
"""Build stimuli.json for the Hangul rewrite pilot (run once, by the author of the kit).

Needs: wordfreq, kiwipiepy (only for the "is this a real word" filter).
The pilot script itself only needs stimuli.json.
"""
import json
import os
import random
import re
import sys

random.seed(20261112)

CHO = list("ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ")
JUNG = list("ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ")
JONG = [""] + list("ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ")


def decompose(ch):
    code = ord(ch) - 0xAC00
    return code // 588, (code % 588) // 28, code % 28


def compose(c, j, k):
    return chr(0xAC00 + c * 588 + j * 28 + k)


# visually similar jamo (subtle change) -------------------------------------
CHO_SIM = {
    "ㄱ": ["ㅋ"], "ㅋ": ["ㄱ"], "ㄷ": ["ㅌ"], "ㅌ": ["ㄷ"], "ㅂ": ["ㅍ"], "ㅍ": ["ㅂ"],
    "ㅈ": ["ㅊ"], "ㅊ": ["ㅈ"], "ㅅ": ["ㅈ"], "ㄴ": ["ㄷ"], "ㅁ": ["ㅂ"], "ㅇ": ["ㅎ"], "ㅎ": ["ㅇ"],
}
JUNG_SIM = {
    "ㅏ": ["ㅓ", "ㅑ"], "ㅓ": ["ㅏ", "ㅕ"], "ㅗ": ["ㅜ", "ㅛ"], "ㅜ": ["ㅗ", "ㅠ"],
    "ㅐ": ["ㅔ"], "ㅔ": ["ㅐ"], "ㅑ": ["ㅕ"], "ㅕ": ["ㅑ"], "ㅛ": ["ㅠ"], "ㅠ": ["ㅛ"],
    "ㅚ": ["ㅟ"], "ㅟ": ["ㅚ"], "ㅘ": ["ㅝ"], "ㅝ": ["ㅘ"], "ㅡ": ["ㅜ"], "ㅣ": ["ㅏ"],
}
JONG_SIM = {
    "ㄱ": ["ㅋ"], "ㄴ": ["ㄷ"], "ㄷ": ["ㅌ", "ㄴ"], "ㅁ": ["ㅂ", "ㅇ"], "ㅂ": ["ㅍ", "ㅁ"],
    "ㅇ": ["ㅁ"], "ㅅ": ["ㅈ"], "ㅈ": ["ㅅ"], "ㄹ": ["ㄷ"],
}
# clearly different jamo (obvious change) -----------------------------------
CHO_DIS = {"ㄱ": "ㅁ", "ㄴ": "ㅎ", "ㄷ": "ㅅ", "ㄹ": "ㅂ", "ㅁ": "ㅈ", "ㅂ": "ㄴ", "ㅅ": "ㄹ", "ㅇ": "ㄱ",
           "ㅈ": "ㄴ", "ㅊ": "ㅁ", "ㅋ": "ㄴ", "ㅌ": "ㅇ", "ㅍ": "ㄱ", "ㅎ": "ㄷ"}
JUNG_DIS = {"ㅏ": "ㅜ", "ㅓ": "ㅗ", "ㅗ": "ㅣ", "ㅜ": "ㅏ", "ㅐ": "ㅗ", "ㅔ": "ㅜ", "ㅕ": "ㅜ", "ㅑ": "ㅗ",
            "ㅡ": "ㅏ", "ㅣ": "ㅗ", "ㅛ": "ㅣ", "ㅠ": "ㅏ", "ㅘ": "ㅣ", "ㅚ": "ㅏ", "ㅟ": "ㅓ", "ㅝ": "ㅣ"}
JONG_DIS = {"ㄱ": "ㅁ", "ㄴ": "ㅂ", "ㄹ": "ㅇ", "ㅁ": "ㄱ", "ㅂ": "ㄴ", "ㅇ": "ㄹ", "ㅅ": "ㅁ", "ㄷ": "ㅇ"}

# base words (frequent everyday nouns) ---------------------------------------
BASE = {
    "food": """김치 불고기 비빔밥 냉면 라면 김밥 떡볶이 삼겹살 갈비탕 설렁탕 순두부 만두 칼국수 짜장면
               탕수육 된장국 미역국 잡채 갈비찜 닭갈비 볶음밥 삼계탕 감자탕 육개장 콩나물 깍두기 계란말이
               고구마 옥수수 바나나 복숭아 도토리묵 팥빙수 식혜 수정과 호떡 붕어빵 군밤 인절미 송편""".split(),
    "place": """학교 병원 약국 도서관 주차장 화장실 편의점 지하철 은행 우체국 경찰서 소방서 시청 공원
                시장 식당 서점 미용실 세탁소 부동산 놀이터 운동장 체육관 수영장 박물관 미술관 영화관
                백화점 주유소 정류장 버스터미널 기차역 공항 유치원 대학교 교무실 급식실 보건실 강당 휴게소""".split(),
    "object": """냉장고 세탁기 청소기 선풍기 전자레인지 우산 가방 지갑 열쇠 안경 시계 모자 신발 양말 바지
                 치마 장갑 목도리 손수건 수건 칫솔 치약 비누 거울 의자 책상 침대 이불 베개 달력 연필 지우개
                 공책 필통 가위 색종이 계산기 충전기 자전거 물통""".split(),
}

SENT = {
    "food": ["점심 메뉴로 {w} 하나 주문했습니다.", "오늘 저녁은 {w} 어때요?", "이 집은 {w} 맛있기로 유명하다."],
    "place": ["길 건너 {w} 앞에서 기다릴게요.", "내일 오후에 {w} 근처에서 만나자.", "이 버스는 {w} 방향으로 갑니다."],
    "object": ["방 한쪽에 {w} 하나가 놓여 있다.", "어제 새로 산 {w} 정말 마음에 든다.", "잃어버린 {w} 찾아 주셔서 감사합니다."],
    "name": ["그 가게 이름은 {w}입니다."],
}

# natural misspellings: (wrong form shown in image, standard form = lure, sentence with wrong form)
MISSPELL = [
    ("김치찌게", "김치찌개", "점심 메뉴로 김치찌게 하나 주문했습니다."),
    ("된장찌게", "된장찌개", "오늘 저녁은 된장찌게 어때요?"),
    ("순두부찌게", "순두부찌개", "이 집은 순두부찌게 맛있기로 유명하다."),
    ("부대찌게", "부대찌개", "점심 메뉴로 부대찌게 둘 주문했습니다."),
    ("육계장", "육개장", "오늘 점심은 육계장 어때요?"),
    ("떡볶기", "떡볶이", "학교 앞에서 떡볶기 일 인분 샀다."),
    ("깍뚜기", "깍두기", "여기 깍뚜기 좀 더 주세요."),
    ("곱배기", "곱빼기", "짜장면 곱배기 하나 주세요."),
    ("설겆이", "설거지", "오늘 설겆이는 내가 할게."),
    ("몇일", "며칠", "몇일 동안 비가 계속 왔다."),
    ("금새", "금세", "아이들이 금새 다 먹어 버렸다."),
    ("어의없는", "어이없는", "정말 어의없는 일이 생겼다."),
    ("일부로", "일부러", "일부로 그런 것은 아니에요."),
    ("역활", "역할", "제 역활은 여기까지입니다."),
    ("희안한", "희한한", "참 희안한 일이 다 있네요."),
    ("오랫만에", "오랜만에", "오랫만에 친구를 만났다."),
    ("깨끗히", "깨끗이", "손을 깨끗히 씻으세요."),
    ("곰곰히", "곰곰이", "곰곰히 생각해 보았다."),
    ("일일히", "일일이", "서류를 일일히 확인했습니다."),
    ("어떻해", "어떡해", "이제 나는 어떻해."),
    ("됬다", "됐다", "숙제가 드디어 다 됬다."),
    ("안되요", "안돼요", "여기서 뛰면 안되요."),
    ("할께요", "할게요", "그 일은 제가 할께요."),
    ("갈께", "갈게", "나 먼저 갈께."),
    ("구지", "굳이", "구지 그럴 필요는 없다."),
    ("눈꼽", "눈곱", "아침에 눈꼽이 끼었다."),
    ("휴계소", "휴게소", "다음 휴계소에서 잠깐 쉬자."),
    ("휴계실", "휴게실", "직원 휴계실은 2층에 있습니다."),
    ("계시판", "게시판", "자세한 내용은 계시판을 확인하세요."),
    ("어서 오십시요", "어서 오십시오", "어서 오십시요. 몇 분이세요?"),
    ("안녕히 가십시요", "안녕히 가십시오", "감사합니다. 안녕히 가십시요."),
    ("알맞는", "알맞은", "빈칸에 알맞는 말을 고르시오."),
    ("걸맞는", "걸맞은", "분위기에 걸맞는 옷차림이다."),
    ("통채로", "통째로", "뱀이 먹이를 통채로 삼켰다."),
    ("댓가", "대가", "노력의 댓가를 받았다."),
    ("갯수", "개수", "사과의 갯수를 세어 보자."),
    ("촛점", "초점", "사진의 촛점이 맞지 않는다."),
    ("싯가", "시가", "오늘 회는 싯가로 판매합니다."),
    ("햇님", "해님", "햇님이 방긋 웃는다."),
    ("핑게", "핑계", "자꾸 핑게 대지 마."),
    ("낭떨어지", "낭떠러지", "이 앞은 낭떨어지이니 조심하세요."),
    ("닥달하지", "닦달하지", "아이를 너무 닥달하지 마세요."),
    ("뒷태", "뒤태", "한복의 뒷태가 참 곱다."),
    ("제작년", "재작년", "제작년 여름에 다녀왔다."),
    ("뵈요", "봬요", "그럼 내일 뵈요."),
    ("요세", "요새", "요세 날씨가 부쩍 춥다."),
    ("어느세", "어느새", "어느세 겨울이 왔다."),
    ("그세", "그새", "아이가 그세 많이 컸네."),
    ("웬지", "왠지", "오늘은 웬지 기분이 좋다."),
    ("왠일로", "웬일로", "왠일로 이렇게 일찍 왔어?"),
    ("왠만하면", "웬만하면", "왠만하면 참아라."),
    ("가르켰다", "가리켰다", "손가락으로 창문을 가르켰다."),
    ("건들이지", "건드리지", "전시품을 건들이지 마세요."),
    ("뒤치닥거리", "뒤치다꺼리", "뒤치닥거리는 늘 내 몫이다."),
    ("들어났다", "드러났다", "마침내 진실이 들어났다."),
    ("널부러져", "널브러져", "옷이 방바닥에 널부러져 있다."),
    ("째째하게", "쩨쩨하게", "그렇게 째째하게 굴지 마."),
    ("페쇄", "폐쇄", "이 도로는 공사로 페쇄 중입니다."),
    ("게좌번호", "계좌번호", "입금하실 게좌번호를 알려 드립니다."),
    ("재육볶음", "제육볶음", "오늘의 추천 메뉴는 재육볶음 정식입니다."),
    ("배게", "베개", "새로 산 배게 하나가 침대 위에 있다."),
]


def load_known_words():
    known = set()
    try:
        from wordfreq import get_frequency_dict
        freq = get_frequency_dict("ko", wordlist="best")
        known |= set(freq)
    except Exception as e:  # pragma: no cover
        print("wordfreq unavailable", e, file=sys.stderr)
        freq = {}
    try:
        import kiwipiepy_model
        p = os.path.dirname(kiwipiepy_model.__file__)
        for fn in ("default.dict", "multi.dict"):
            with open(os.path.join(p, fn), encoding="utf-8") as f:
                for line in f:
                    if line.startswith("#"):
                        continue
                    form = line.split("\t")[0].strip()
                    if form:
                        known.add(form)
                        for part in form.split():
                            known.add(part)
    except Exception as e:  # pragma: no cover
        print("kiwi dict unavailable", e, file=sys.stderr)
    return known, freq


HAN = re.compile(r"^[가-힣]+$")

# One-jamo pseudo-words, reviewed by hand (2026-11-12). (familiar word = lure, shown string = target, kind)
# kind "sim": the changed jamo looks similar to the original, "dis": clearly different.
# Candidates were generated with the *_SIM / *_DIS tables above, then every string that is a real
# word, a real phrase, a place or person name, or a crude reading was removed by hand.
J_FIXED = [
    ("김치", "킴치", "sim"), ("김치", "긱치", "dis"), ("불고기", "볼고기", "sim"), ("비빔밥", "비빔밤", "sim"),
    ("냉면", "냉변", "sim"), ("냉면", "냉문", "dis"), ("라면", "랴면", "sim"), ("김밥", "깅밥", "sim"),
    ("떡볶이", "딱볶이", "sim"), ("떡볶이", "떰볶이", "dis"), ("삼겹살", "잠겹살", "sim"), ("갈비탕", "갇비탕", "sim"),
    ("설렁탕", "절렁탕", "sim"), ("설렁탕", "설벙탕", "dis"), ("순두부", "숟두부", "sim"), ("만두", "만듀", "sim"),
    ("칼국수", "칼쿡수", "sim"), ("칼국수", "칼묵수", "dis"), ("짜장면", "짜잠면", "sim"), ("탕수육", "탕수욕", "sim"),
    ("된장국", "된잠국", "sim"), ("된장국", "단장국", "dis"), ("미역국", "마역국", "sim"), ("잡채", "잡재", "sim"),
    ("갈비찜", "갈비짬", "sim"), ("갈비찜", "갈비찍", "dis"), ("닭갈비", "닭갇비", "sim"), ("볶음밥", "볶흠밥", "sim"),
    ("삼계탕", "상계탕", "sim"), ("삼계탕", "숨계탕", "dis"), ("감자탕", "감자당", "sim"), ("육개장", "육개정", "sim"),
    ("콩나물", "콤나물", "sim"), ("콩나물", "콩하물", "dis"), ("깍두기", "깍투기", "sim"), ("계란말이", "계런말이", "sim"),
    ("고구마", "구구마", "sim"), ("고구마", "고가마", "dis"), ("옥수수", "옥주수", "sim"), ("바나나", "바너나", "sim"),
    ("복숭아", "복숨아", "sim"), ("복숭아", "복숭가", "dis"), ("도토리묵", "토토리묵", "sim"), ("팥빙수", "팥빔수", "sim"),
    ("식혜", "식예", "sim"), ("식혜", "심혜", "dis"), ("수정과", "슈정과", "sim"), ("호떡", "호딱", "sim"),
    ("붕어빵", "붕어빰", "sim"), ("붕어빵", "눙어빵", "dis"), ("군밤", "굳밤", "sim"), ("인절미", "인절비", "sim"),
    ("송편", "솜편", "sim"), ("송편", "솔편", "dis"), ("학교", "헉교", "sim"), ("병원", "병훤", "sim"),
    ("약국", "약곡", "sim"), ("약국", "옥국", "dis"), ("도서관", "도저관", "sim"), ("주차장", "주차잠", "sim"),
    ("화장실", "화잠실", "sim"), ("화장실", "화낭실", "dis"), ("편의점", "편희점", "sim"), ("지하철", "지허철", "sim"),
    ("은행", "은햄", "sim"), ("은행", "읍행", "dis"), ("우체국", "오체국", "sim"), ("경찰서", "경찰저", "sim"),
    ("소방서", "소밤서", "sim"), ("소방서", "소붕서", "dis"), ("시청", "사청", "sim"), ("공원", "콩원", "sim"),
    ("시장", "시쟝", "sim"), ("시장", "시잘", "dis"), ("식당", "식담", "sim"), ("서점", "서첨", "sim"),
    ("미용실", "비용실", "sim"), ("미용실", "지용실", "dis"), ("세탁소", "세턱소", "sim"), ("부동산", "부돔산", "sim"),
    ("놀이터", "놀아터", "sim"), ("놀이터", "놀오터", "dis"), ("운동장", "훈동장", "sim"), ("체육관", "체육콴", "sim"),
    ("수영장", "수영잠", "sim"), ("수영장", "수영잘", "dis"), ("박물관", "벅물관", "sim"), ("미술관", "미숟관", "sim"),
    ("영화관", "영와관", "sim"), ("영화관", "웅화관", "dis"), ("백화점", "백화접", "sim"), ("주유소", "조유소", "sim"),
    ("정류장", "정류잠", "sim"), ("정류장", "정류낭", "dis"), ("버스터미널", "벼스터미널", "sim"), ("기차역", "기차혁", "sim"),
    ("공항", "곰항", "sim"), ("공항", "공훙", "dis"), ("유치원", "요치원", "sim"), ("대학교", "대학쿄", "sim"),
    ("교무실", "쿄무실", "sim"), ("교무실", "묘무실", "dis"), ("급식실", "굽식실", "sim"), ("보건실", "보걷실", "sim"),
    ("강당", "강덩", "sim"), ("강당", "갈당", "dis"), ("휴게소", "휴케소", "sim"), ("냉장고", "냉잠고", "sim"),
    ("세탁기", "새탁기", "sim"), ("세탁기", "레탁기", "dis"), ("청소기", "정소기", "sim"), ("선풍기", "선품기", "sim"),
    ("전자레인지", "전자레힌지", "sim"), ("전자레인지", "존자레인지", "dis"), ("우산", "우삳", "sim"), ("가방", "가벙", "sim"),
    ("지갑", "지갚", "sim"), ("지갑", "지맙", "dis"), ("열쇠", "혈쇠", "sim"), ("안경", "안걍", "sim"),
    ("시계", "시켸", "sim"), ("시계", "리계", "dis"), ("모자", "모쟈", "sim"), ("신발", "싣발", "sim"),
    ("양말", "양멀", "sim"), ("양말", "얄말", "dis"), ("바지", "뱌지", "sim"), ("치마", "치먀", "sim"),
    ("장갑", "장갚", "sim"), ("장갑", "중갑", "dis"), ("목도리", "목토리", "sim"), ("손수건", "존수건", "sim"),
    ("수건", "수걷", "sim"), ("수건", "수겁", "dis"), ("칫솔", "짓솔", "sim"), ("치약", "지약", "sim"),
    ("비누", "피누", "sim"), ("비누", "니누", "dis"), ("거울", "거올", "sim"), ("의자", "의쟈", "sim"),
    ("책상", "책삼", "sim"), ("책상", "촉상", "dis"), ("침대", "칭대", "sim"), ("이불", "이붇", "sim"),
    ("베개", "페개", "sim"), ("베개", "베매", "dis"), ("달력", "달략", "sim"), ("연필", "현필", "sim"),
    ("지우개", "지우게", "sim"), ("지우개", "지우고", "dis"), ("공책", "곰책", "sim"), ("필통", "필톰", "sim"),
    ("가위", "가휘", "sim"), ("색종이", "섹종이", "sim"), ("계산기", "계삳기", "sim"), ("충전기", "충전키", "sim"),
    ("충전기", "충접기", "dis"), ("자전거", "자젼거", "sim"), ("물통", "물톰", "sim"),
]

# Random-syllable control strings (hand-checked: no familiar word nearby)
R_FIXED = """삼터모 텁린 학밤 부물행신 식깍솔 유김풀 토위볶 강렁 팥당콩찜 빔풀필학 밤절열 관항군 깍항짜 우점밤주
             술숭레 누침양 국란발 행순편누 라학레 빵옥육 복보찜가 체상닭 두칼실영 수쇠차 붕누위울 고볶 렁음
             불역박육 빔숭칼 휴영솔""".split()


def syl_common(ch):
    """True if the syllable is in KS X 1001 (the 2,350 everyday syllables)."""
    try:
        # Python's euc_kr codec also encodes the other 8,822 syllables (as an 8-byte
        # jamo sequence), so check the length instead of relying on an exception.
        return len(ch.encode("euc_kr")) == 2
    except UnicodeEncodeError:
        return False


def jamo_diff(lure, target):
    """Return (syl_index, jamo_pos, from_jamo, to_jamo) for a one-jamo substitution."""
    assert len(lure) == len(target), (lure, target)
    idx = [i for i in range(len(lure)) if lure[i] != target[i]]
    assert len(idx) == 1, (lure, target)
    i = idx[0]
    a, b = decompose(lure[i]), decompose(target[i])
    diff = [k for k in range(3) if a[k] != b[k]]
    assert len(diff) == 1, (lure, target)
    k = diff[0]
    table = (CHO, JUNG, JONG)[k]
    return i, ("cho", "jung", "jong")[k], table[a[k]], table[b[k]]


def main():
    known, freq = load_known_words()
    print("known forms:", len(known))
    items = []
    all_base = [(w, cat) for cat, ws in BASE.items() for w in ws]
    assert len(set(w for w, _ in all_base)) == len(all_base), "duplicate base word"
    sent_of = {}
    for bi, (w, cat) in enumerate(all_base):
        assert HAN.match(w), w
        sent = SENT[cat][bi % len(SENT[cat])]
        sent_of[w] = (sent, cat)
        items.append({"id": f"N{bi:03d}", "cond": "N", "target": w, "lure": None, "category": cat,
                      "sent_target": sent.format(w=w), "sent_lure": None, "freq": freq.get(w)})

    for ji, (lure, target, kind) in enumerate(J_FIXED):
        sent, cat = sent_of[lure]
        si, pos, a, b = jamo_diff(lure, target)
        table = {"sim": {"cho": CHO_SIM, "jung": JUNG_SIM, "jong": JONG_SIM},
                 "dis": {"cho": CHO_DIS, "jung": JUNG_DIS, "jong": JONG_DIS}}[kind][pos]
        alts = table.get(a, [])
        assert b in alts or b == alts or (kind == "sim" and pos == "jong" and (a, b) == ("ㅂ", "ㅍ")), (lure, target, kind, a, b)
        if target in known:
            print("WARNING target is a known form:", target)
        items.append({"id": f"J{ji:03d}", "cond": "J", "target": target, "lure": lure, "category": cat,
                      "sent_target": sent.format(w=target), "sent_lure": sent.format(w=lure),
                      "syl_index": si, "jamo_pos": pos, "from": a, "to": b, "sim": kind,
                      "syl_target": target[si], "syl_lure": lure[si], "syl_common": syl_common(target[si]),
                      "freq": freq.get(lure)})

    for mi, (wrong, right, sent) in enumerate(MISSPELL):
        assert wrong in sent, (wrong, sent)
        it = {"id": f"M{mi:03d}", "cond": "M", "target": wrong, "lure": right, "category": "misspell",
              "sent_target": sent, "sent_lure": sent.replace(wrong, right),
              "freq": freq.get(right), "target_freq": freq.get(wrong)}
        a, b = wrong.replace(" ", ""), right.replace(" ", "")
        if len(a) == len(b):
            diff = [i for i in range(len(a)) if a[i] != b[i]]
            if len(diff) == 1:
                it.update({"syl_target": a[diff[0]], "syl_lure": b[diff[0]], "syl_common": syl_common(a[diff[0]])})
        it["all_common"] = all(syl_common(ch) for ch in a)
        items.append(it)

    for r, s in enumerate(R_FIXED):
        if s in known:
            print("WARNING random string is a known form:", s)
        items.append({"id": f"R{r:03d}", "cond": "R", "target": s, "lure": None, "category": "name",
                      "sent_target": SENT["name"][0].format(w=s), "sent_lure": None, "freq": None})

    for it in items:
        t, l = it["target"], it["lure"]
        assert it["sent_target"].count(t) == 1, it
        if l:
            assert t not in l and l not in t, it
            assert l not in it["sent_target"], it
            assert it["sent_lure"].count(l) == 1 and t not in it["sent_lure"], it
    assert len({it["target"] for it in items}) == len(items), "duplicate target"

    out = {"version": "2026-11-12",
           "note": "Hangul rewrite pilot stimuli. cond: N normal word, J one-jamo pseudo-word, M natural misspelling, "
                   "R random syllables. 'target' is the string drawn in the image; 'lure' is the familiar word "
                   "the model may rewrite it into. 'freq' is the wordfreq frequency of the familiar word.",
           "items": items}
    with open("stimuli.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    from collections import Counter
    print(Counter(i["cond"] for i in items))
    print("J by kind/pos:", Counter((i["sim"], i["jamo_pos"]) for i in items if i["cond"] == "J"))
    print("J substitutions:", Counter((i["jamo_pos"], i["from"] + ">" + i["to"]) for i in items if i["cond"] == "J").most_common(12))
    print("rare-syllable J:", [i["target"] for i in items if i["cond"] == "J" and not i["syl_common"]])
    print("M with a rare syllable:", [i["target"] for i in items if i["cond"] == "M" and not i["all_common"]])
    print("M targets that are known forms:", [m[0] for m in MISSPELL if m[0] in known])
    print("M one-syllable-diff:", sum(1 for i in items if i["cond"] == "M" and "syl_target" in i))
    print("longest sentence:", max((len(i["sent_target"]), i["sent_target"]) for i in items))
    print("longest word:", max((len(i["target"]), i["target"]) for i in items))


if __name__ == "__main__":
    main()
