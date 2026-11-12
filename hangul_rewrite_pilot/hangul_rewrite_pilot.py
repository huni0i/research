#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""한글 고쳐 읽기(rewriting) 사전 실험 스크립트

이미지 속 한글이 '김치찌게'처럼 익숙한 단어와 한 끗 다르게 적혀 있을 때,
비전-언어 모델(VLM)이 보이는 대로 읽는지, 익숙한 단어('김치찌개')로 고쳐 읽는지 잽니다.

    python hangul_rewrite_pilot.py --preview        # 이미지 예시만 만들기 (모델 불필요)
    python hangul_rewrite_pilot.py --quick          # 약 15~20분짜리 축소 실험
    python hangul_rewrite_pilot.py                  # 전체 실험 (중간에 꺼도 이어서 실행됨)
    python hangul_rewrite_pilot.py --report-only    # 저장된 결과로 요약표만 다시 만들기

결과는 runs/<이름>/ 아래 results.jsonl(원자료), summary.md(요약표), run_info.json(실행 환경)에 저장됩니다.
"""
import argparse
import datetime
import io
import json
import math
import os
import platform
import random
import re
import sys
import time
import unicodedata
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
CANVAS = (1024, 256)          # 모든 이미지의 크기(가로, 세로). 32의 배수라 Qwen3-VL에서 크기 변환이 일어나지 않습니다.
MARGIN = 24
JITTER = 8                    # 글자 위치를 최대 ±8px 흔들어 패치 격자와의 정렬 효과를 평균냅니다.
REF_CHARS = "가나다라마바사아자차카타파하고노도로모보소오조초코토포호한글읽기밝힘강물숲밭집책꽃"   # 글자 높이를 맞출 때 쓰는 기준 음절
FONTS = {
    "gothic": os.path.join(HERE, "fonts", "NanumGothic-Regular.ttf"),      # 인쇄체
    "pen": os.path.join(HERE, "fonts", "NanumPenScript-Regular.ttf"),      # 손글씨풍
}
PROMPTS = {
    # 중립: 고치지 말라는 말을 하지 않는 기본 지시
    "neutral": "이 이미지에 적힌 글자를 읽고, 읽은 글자만 답하세요.",
    # 엄격: 선행 연구의 '고치지 말라' 프롬프트에 해당하는 비교 기준
    "strict": ("이 이미지에 적힌 글자를 보이는 그대로 옮겨 적으세요. "
               "맞춤법이 틀렸거나 없는 단어처럼 보여도 고치지 말고, 한 글자씩 보이는 대로 적으세요. "
               "옮겨 적은 글자만 답하세요."),
}
CHOICE_PROMPT = "이 이미지에 적힌 글자는 다음 중 어느 것입니까?\n1번: {a}\n2번: {b}\n숫자 1 또는 2만 답하세요."
COND_NAME = {"N": "정상 단어", "J": "한 자모 바꾼 가짜 단어", "M": "흔한 맞춤법 오류", "R": "무작위 음절"}


# --------------------------------------------------------------------------- 채점
_KEEP = re.compile(r"[^0-9A-Za-zㄱ-ㆎ가-힣]")
_THINK = re.compile(r"<think>.*?</think>", re.S)


def norm(s):
    """정규화: NFC로 합친 뒤 한글, 영문, 숫자만 남깁니다(공백, 문장부호, 따옴표 제거)."""
    return _KEEP.sub("", unicodedata.normalize("NFC", s or ""))


def clean_output(s):
    s = _THINK.sub("", s or "")
    if "</think>" in s:          # 생각 부분이 잘려 닫는 태그만 남은 경우
        s = s.split("</think>")[-1]
    return s.strip()


def score_read(item, output):
    """보이는 글자(target)와 익숙한 단어(lure) 중 무엇이 출력에 들어 있는지로 분류합니다."""
    o, t = norm(output), norm(item["target"])
    has_t = t in o
    if not item.get("lure"):
        return "correct" if has_t else "other"
    has_l = norm(item["lure"]) in o
    if has_t and has_l:
        return "both"        # 둘 다 적음 (예: "김치찌게(김치찌개의 오타)")
    if has_t:
        return "faithful"    # 보이는 대로 읽음
    if has_l:
        return "rewrite"     # 익숙한 단어로 고쳐 읽음
    return "other"           # 그 밖의 오독


def score_choice(item, output, order):
    """order 'TL': 1번이 보이는 글자, 2번이 익숙한 단어. 'LT'는 그 반대."""
    s = unicodedata.normalize("NFKC", output or "")
    o, t, l = norm(output), norm(item["target"]), norm(item["lure"])
    m = re.search(r"[12]", s)
    if not m:
        if t in o and l not in o:
            return "target"
        if l in o and t not in o:
            return "lure"
        return "invalid"
    return "target" if (m.group(0) == "1") == (order == "TL") else "lure"


# --------------------------------------------------------------------------- 이미지
_font_bytes = {}
_font_cache = {}


def _load_font(path, em):
    from PIL import ImageFont
    if path not in _font_bytes:
        with open(path, "rb") as f:
            _font_bytes[path] = f.read()     # 한글 경로에서도 열리도록 메모리로 읽습니다.
    key = (path, em)
    if key not in _font_cache:
        _font_cache[key] = ImageFont.truetype(io.BytesIO(_font_bytes[path]), em)
    return _font_cache[key]


_fit_cache = {}


def mean_ink_height(font):
    hs = []
    for ch in REF_CHARS:
        box = font.getmask(ch).getbbox()
        if box:
            hs.append(box[3] - box[1])
    return sum(hs) / len(hs)


def fit_font(path, height_px):
    """기준 음절들의 평균 높이(실제로 칠해진 부분)가 height_px에 가장 가까워지는 글꼴 크기(em)를 찾습니다.

    글꼴마다 같은 em 크기에서도 글자 실제 크기가 달라서(손글씨풍은 훨씬 작음) 칠해진 높이 기준으로 맞춥니다."""
    key = (path, height_px)
    if key not in _fit_cache:
        best = None
        for em in range(6, 240):
            h = mean_ink_height(_load_font(path, em))
            d = abs(h - height_px)
            if best is None or d < best[0]:
                best = (d, em)
            if h > height_px + 4:
                break
        _fit_cache[key] = best[1]
    return _load_font(path, _fit_cache[key]), _fit_cache[key]


def render(text, font_path, height_px, jitter_key):
    """흰 바탕에 검은 글자 한 줄. 같은 jitter_key면 항상 같은 이미지가 나옵니다."""
    from PIL import Image, ImageDraw
    font, em = fit_font(font_path, height_px)
    img = Image.new("RGB", CANVAS, "white")
    d = ImageDraw.Draw(img)
    l, t, r, b = d.textbbox((0, 0), text, font=font)
    w, h = r - l, b - t
    if w > CANVAS[0] - 2 * MARGIN or h > CANVAS[1] - 2 * MARGIN:
        raise ValueError(f"글자가 이미지 밖으로 나갑니다: {text!r} ({w}x{h}px). --sizes 값을 줄이세요.")
    rng = random.Random("jitter|" + jitter_key)
    jx, jy = rng.randint(-JITTER, JITTER), rng.randint(-JITTER, JITTER)
    x = (CANVAS[0] - w) // 2 - l + jx
    y = (CANVAS[1] - h) // 2 - t + jy
    d.text((x, y), text, font=font, fill="black")
    return img, {"em": em, "text_w": w, "text_h": h}


def missing_glyphs(font_path, chars):
    """글꼴에 없는 글자 찾기: 없는 글자는 모두 같은 '빈 상자' 모양으로 그려집니다."""
    font = _load_font(font_path, 40)
    blank = font.getmask("\U0010FFFD")
    ref = (blank.size, bytes(blank))
    out = []
    for ch in sorted(chars):
        if ch.isspace():
            continue
        m = font.getmask(ch)
        if (m.size, bytes(m)) == ref or m.size[0] == 0:
            out.append(ch)
    return out


def pixel_distance(a, b, font_path, height_px=64):
    """두 문자열 그림이 얼마나 다른지(0~1). 다른 픽셀 수 / 둘 중 하나라도 칠해진 픽셀 수."""
    from PIL import Image, ImageChops, ImageDraw
    font, _ = fit_font(font_path, height_px)
    ims = []
    for s in (a, b):
        im = Image.new("L", (height_px * (max(len(a), len(b)) + 2), height_px * 2), 0)
        ImageDraw.Draw(im).text((height_px // 2, height_px // 3), s, font=font, fill=255)
        ims.append(im.point(lambda v: 255 if v > 127 else 0))
    def ink(im):                     # 칠해진 픽셀 수 = 전체 - 값이 0인 픽셀
        return im.size[0] * im.size[1] - im.histogram()[0]

    u = ink(ImageChops.lighter(ims[0], ims[1]))
    return round(ink(ImageChops.difference(ims[0], ims[1])) / u, 4) if u else 0.0


# --------------------------------------------------------------------------- 모델
class MockBackend:
    """모델 없이 스크립트 동작만 점검하는 가짜 모델. 여기서 나온 숫자는 실험 결과가 아닙니다."""
    name = "mock"

    def __init__(self, seed=0):
        self.rng = random.Random(seed)
        self.info = {"backend": "mock"}

    def generate(self, image, prompt, max_new_tokens, trial=None, item=None):
        r = self.rng.random()
        if trial["task"] == "choice":
            return self.rng.choice(["1", "2", "1번", "②", "잘 모르겠습니다"])
        shown = item["target"] if trial["layout"] == "word" else item["sent_target"]
        if item.get("lure") and r < 0.3:
            shown = shown.replace(item["target"], item["lure"])
        elif r > 0.95:
            shown = "읽을 수 없습니다"
        return self.rng.choice(["{}", "{}", '"{}"', "{}\n"]).format(shown)


class HFBackend:
    """Hugging Face transformers로 VLM을 불러와 이미지 한 장과 지시문 하나를 넣고 답을 받습니다."""
    name = "hf"

    def __init__(self, model_id, load_4bit=False, device=None):
        import torch
        import transformers
        from transformers import AutoProcessor
        try:
            from transformers import AutoModelForImageTextToText as AutoModel
        except ImportError:
            raise SystemExit("transformers 버전이 낮습니다. `pip install -U transformers` 후 다시 실행하세요.")
        self.torch = torch
        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        if device == "cpu":
            print("[주의] GPU(CUDA)를 찾지 못해 CPU로 실행합니다. 매우 느립니다. "
                  "PyTorch가 CUDA 버전으로 설치됐는지 확인하세요.")
        dtype = torch.bfloat16 if device != "cpu" else torch.float32
        kw = {}
        if load_4bit:
            from transformers import BitsAndBytesConfig
            kw["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_use_double_quant=True,
                bnb_4bit_compute_dtype=torch.bfloat16)
        try:
            import accelerate  # noqa: F401
            kw["device_map"] = {"": device}    # 가중치를 곧바로 GPU에 올립니다(메인 메모리 절약).
        except ImportError:
            if load_4bit:
                raise SystemExit("--load-4bit에는 accelerate가 필요합니다: `pip install accelerate bitsandbytes`")
        print(f"모델을 불러옵니다: {model_id} (처음에는 내려받느라 오래 걸립니다)")
        t0 = time.time()
        self.processor = AutoProcessor.from_pretrained(model_id)
        try:
            self.model = AutoModel.from_pretrained(model_id, dtype=dtype, **kw)
        except TypeError:        # 옛 transformers는 인자 이름이 torch_dtype입니다.
            self.model = AutoModel.from_pretrained(model_id, torch_dtype=dtype, **kw)
        if "device_map" not in kw:
            self.model.to(device)
        self.model.eval()
        self.device = device
        self.first_shape = None
        self.info = {
            "backend": "hf", "model": model_id, "load_4bit": bool(load_4bit), "dtype": str(dtype),
            "device": device, "torch": torch.__version__, "transformers": transformers.__version__,
            "gpu": torch.cuda.get_device_name(0) if device != "cpu" and torch.cuda.is_available() else None,
            "load_seconds": round(time.time() - t0, 1),
        }
        if device != "cpu" and torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()

    def generate(self, image, prompt, max_new_tokens, trial=None, item=None):
        torch = self.torch
        messages = [{"role": "user", "content": [{"type": "image"}, {"type": "text", "text": prompt}]}]
        text = self.processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = self.processor(text=[text], images=[image], return_tensors="pt")
        if self.first_shape is None:
            self.first_shape = {k: list(v.shape) for k, v in inputs.items() if hasattr(v, "shape")}
            grid = inputs.get("image_grid_thw")
            if grid is not None:
                self.first_shape["image_grid_thw_values"] = grid.tolist()
            self.info["first_input_shapes"] = self.first_shape
        mdtype = next(self.model.parameters()).dtype
        moved = {}
        for k, v in inputs.items():
            if torch.is_tensor(v):
                v = v.to(self.model.device)
                if v.is_floating_point() and mdtype.is_floating_point:
                    v = v.to(mdtype)
            moved[k] = v
        with torch.inference_mode():
            # 탐욕 디코딩, 반복 벌점 없음: 같은 음절이 반복되는 단어(바나나)를 벌점이 바꾸지 않게 합니다.
            out = self.model.generate(**moved, max_new_tokens=max_new_tokens, do_sample=False,
                                      num_beams=1, repetition_penalty=1.0)
        new_tokens = out[0, moved["input_ids"].shape[1]:]
        return self.processor.tokenizer.decode(new_tokens, skip_special_tokens=True)

    def peak_gib(self):
        torch = self.torch
        if self.device != "cpu" and torch.cuda.is_available():
            return round(torch.cuda.max_memory_allocated() / 2 ** 30, 2)
        return None


# --------------------------------------------------------------------------- 통계
def wilson(k, n, z=1.96):
    """비율의 95% 신뢰구간(Wilson)."""
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def mcnemar_exact(b, c):
    """짝지은 두 조건 비교(정확 McNemar 검정, 양측 p값). b, c는 한쪽에서만 고쳐 읽은 횟수."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)


def pct(k, n):
    return "-" if n == 0 else f"{100 * k / n:.1f}%"


def ci(k, n):
    if n == 0:
        return "-"
    lo, hi = wilson(k, n)
    return f"{100 * lo:.0f}~{100 * hi:.0f}%"


# --------------------------------------------------------------------------- 실험 구성
def load_items(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)["items"]


def quick_subset(items, seed):
    """축소 실험용 80개: 조건과 자모 위치가 고르게 들어가도록 뽑습니다."""
    want = {("N",): 16, ("R",): 8, ("M",): 20,
            ("J", "sim", "cho"): 8, ("J", "sim", "jung"): 8, ("J", "sim", "jong"): 8,
            ("J", "dis", "cho"): 4, ("J", "dis", "jung"): 4, ("J", "dis", "jong"): 4}
    groups = defaultdict(list)
    for it in items:
        key = (it["cond"], it["sim"], it["jamo_pos"]) if it["cond"] == "J" else (it["cond"],)
        groups[key].append(it)
    rng = random.Random(seed)
    out = []
    for key, n in want.items():
        g = sorted(groups[key], key=lambda it: it["id"])
        rng.shuffle(g)
        out += g[:n]
    return sorted(out, key=lambda it: it["id"])


def build_trials(items, args):
    trials = []
    for it in items:
        for font in args.fonts:
            for size in args.sizes:
                for layout in args.layouts:
                    for prompt in args.prompts:
                        trials.append({"task": "read", "item": it["id"], "font": font, "size": size,
                                       "layout": layout, "prompt": prompt, "order": "-"})
                if it.get("lure") and not args.no_choice:
                    for order in ("TL", "LT"):
                        trials.append({"task": "choice", "item": it["id"], "font": font, "size": size,
                                       "layout": "word", "prompt": "choice", "order": order})
    random.Random(args.seed).shuffle(trials)   # 섞어 두면 중간에 멈춰도 모든 조건이 고르게 들어갑니다.
    return trials


def trial_key(t):
    return "|".join(str(t[k]) for k in ("item", "task", "font", "size", "layout", "prompt", "order"))


def image_for(t, item, font_paths):
    text = item["target"] if t["layout"] == "word" else item["sent_target"]
    # 같은 (문항, 글꼴, 크기, 배치)는 지시문이 달라도 픽셀까지 같은 이미지를 씁니다.
    jitter_key = f'{item["id"]}|{t["font"]}|{t["size"]}|{t["layout"]}'
    return render(text, font_paths[t["font"]], t["size"], jitter_key)


# --------------------------------------------------------------------------- 요약
def load_results(path):
    rows, seen = [], set()
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue       # 중간에 꺼져 깨진 마지막 줄은 건너뜁니다.
            if r["key"] in seen:
                continue
            seen.add(r["key"])
            rows.append(r)
    return rows


def table(header, rows):
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def summarize(rows, items, info):
    by_id = {it["id"]: it for it in items}
    L = []
    add = L.append
    reads = [r for r in rows if r["task"] == "read"]
    choices = [r for r in rows if r["task"] == "choice"]
    fonts = sorted({r["font"] for r in rows})
    sizes = sorted({r["size"] for r in rows})
    layouts = [x for x in ("word", "sent") if any(r["layout"] == x for r in reads)]
    prompts = [x for x in ("neutral", "strict") if any(r["prompt"] == x for r in reads)]
    lay_name = {"word": "단어만", "sent": "문장 속"}
    font_name = {"gothic": "인쇄체", "pen": "손글씨풍"}
    pr_name = {"neutral": "중립", "strict": "엄격"}

    add("# 한글 고쳐 읽기 사전 실험 요약")
    add("")
    if info.get("backend") == "mock":
        add("> **가짜 모델(mock) 결과입니다. 스크립트 동작 점검용이며 실험 결과가 아닙니다.**")
        add("")
    add(f"- 모델: {info.get('model', info.get('backend'))}"
        + (" (4bit)" if info.get("load_4bit") else "")
        + (f", {info.get('dtype')}" if info.get("dtype") else ""))
    add(f"- 실행 환경: GPU {info.get('gpu')}, torch {info.get('torch')}, transformers {info.get('transformers')}")
    add(f"- 완료한 시행: 읽기 {len(reads)}건, 양자택일 {len(choices)}건"
        + (f" / 계획 {info['planned_trials']}건" if info.get("planned_trials") else ""))
    if info.get("peak_gib") is not None:
        add(f"- GPU 메모리 최고 사용량: {info['peak_gib']} GiB, 시행당 평균 {info.get('mean_latency', '-')}초")
    if info.get("first_input_shapes"):
        add(f"- 첫 입력 형태(크기 변환 확인용): {json.dumps(info['first_input_shapes'], ensure_ascii=False)}")
    add("")

    # 1. 읽기 능력 점검 ------------------------------------------------------
    add("## 1. 읽기 능력 점검 (정상 단어와 무작위 음절)")
    add("")
    add("정상 단어 정확도가 낮은 조건(대략 90% 미만)은 글자 자체가 안 읽히는 조건이므로, 고쳐 읽기 수치를 해석할 때 조심해야 합니다.")
    add("")
    tr = []
    for font in fonts:
        for size in sizes:
            for lay in layouts:
                row = [font_name.get(font, font), f"{size}px", lay_name[lay]]
                for cond in ("N", "R"):
                    for pr in prompts:
                        sel = [r for r in reads if by_id[r["item"]]["cond"] == cond and r["font"] == font
                               and r["size"] == size and r["layout"] == lay and r["prompt"] == pr]
                        k = sum(r["outcome"] == "correct" for r in sel)
                        row.append(f"{pct(k, len(sel))} ({k}/{len(sel)})")
                tr.append(row)
    head = ["글꼴", "글자 높이", "배치"] + [f"{COND_NAME[c]} 정확도 ({pr_name[p]})" for c in ("N", "R") for p in prompts]
    add(table(head, tr))
    add("")

    # 2. 고쳐 읽기 -----------------------------------------------------------
    lure_reads = [r for r in reads if by_id[r["item"]].get("lure")]

    def group_of(r):
        it = by_id[r["item"]]
        return "M 흔한 맞춤법 오류" if it["cond"] == "M" else (
            "J 가짜 단어(비슷한 자모)" if it["sim"] == "sim" else "J 가짜 단어(다른 자모)")

    groups = ["J 가짜 단어(비슷한 자모)", "J 가짜 단어(다른 자모)", "M 흔한 맞춤법 오류"]

    def rr_row(sel):
        n = len(sel)
        c = Counter(r["outcome"] for r in sel)
        return [n, pct(c["rewrite"], n), ci(c["rewrite"], n), pct(c["faithful"], n), pct(c["both"], n), pct(c["other"], n)]

    add("## 2. 고쳐 읽기 비율 (핵심 결과)")
    add("")
    add("고쳐 읽기 = 이미지에는 틀린 글자가 있는데 출력에는 익숙한 단어만 있는 경우. "
        "그대로 읽기 = 보이는 글자만 출력. 둘 다 = 두 형태를 모두 출력. 기타 = 둘 다 아닌 오독.")
    add("")
    add("### 2-1. 지시문과 배치별 (글꼴과 크기는 합침)")
    add("")
    tr = []
    for g in groups:
        for pr in prompts:
            for lay in layouts:
                sel = [r for r in lure_reads if group_of(r) == g and r["prompt"] == pr and r["layout"] == lay]
                tr.append([g, pr_name[pr], lay_name[lay]] + rr_row(sel))
    head2 = ["n", "고쳐 읽기", "95% 신뢰구간", "그대로 읽기", "둘 다", "기타"]
    add(table(["자극", "지시문", "배치"] + head2, tr))
    add("")
    base = pr_name[prompts[0]]       # 기준 지시문(보통 중립)
    add(f"### 2-2. 글꼴과 글자 높이별 ({base} 지시문, J와 M 합침)")
    add("")
    tr = []
    for font in fonts:
        for size in sizes:
            for lay in layouts:
                sel = [r for r in lure_reads if r["font"] == font and r["size"] == size
                       and r["layout"] == lay and r["prompt"] == prompts[0]]
                tr.append([font_name.get(font, font), f"{size}px", lay_name[lay]] + rr_row(sel))
    add(table(["글꼴", "글자 높이", "배치"] + head2, tr))
    add("")

    # 3. 짝비교 --------------------------------------------------------------
    add("## 3. 조건 사이 짝비교 (같은 문항끼리, 정확 McNemar 검정)")
    add("")
    add("한쪽 조건에서만 고쳐 읽은 문항 수를 비교합니다. p가 0.05보다 작으면 두 조건의 고쳐 읽기 비율이 다르다고 볼 근거가 있습니다.")
    add("")
    factors = [("layout", "word", "sent", "단어만", "문장 속"),
               ("prompt", "neutral", "strict", "중립 지시문", "엄격 지시문"),
               ("font", "gothic", "pen", "인쇄체", "손글씨풍")]
    if len(sizes) >= 2:
        factors.append(("size", sizes[-1], sizes[0], f"{sizes[-1]}px", f"{sizes[0]}px"))
    tr = []
    stats = {}
    for f, a, b, an, bn in factors:
        others = [k for k in ("item", "font", "size", "layout", "prompt") if k != f]
        idx = {}
        for r in lure_reads:
            idx[tuple(r[k] for k in others) + (r[f],)] = r["outcome"] == "rewrite"
        n = ka = kb = only_a = only_b = 0
        for key, va in idx.items():
            if key[-1] != a:
                continue
            vb = idx.get(key[:-1] + (b,))
            if vb is None:
                continue
            n += 1
            ka += va
            kb += vb
            only_a += va and not vb
            only_b += vb and not va
        if n:
            p = mcnemar_exact(only_a, only_b)
            tr.append([f"{an} 대 {bn}", n, pct(ka, n), pct(kb, n), only_a, only_b, f"{p:.4f}" if p >= 0.0001 else "<0.0001"])
            stats[f] = {"n": n, "rr_a": ka / n, "rr_b": kb / n, "p": p, "a": an, "b": bn}
    add(table(["비교 (A 대 B)", "짝 수", "A 고쳐 읽기", "B 고쳐 읽기", "A에서만", "B에서만", "p"], tr))
    add("")

    # 4. 자모 위치 -----------------------------------------------------------
    add(f"## 4. 바꾼 자모의 위치와 모양 유사도별 (가짜 단어 J, {base} 지시문)")
    add("")
    tr = []
    for sim, sn in (("sim", "비슷한 자모"), ("dis", "다른 자모")):
        for pos, pn in (("cho", "초성"), ("jung", "중성"), ("jong", "종성")):
            sel = [r for r in lure_reads if by_id[r["item"]]["cond"] == "J" and by_id[r["item"]]["sim"] == sim
                   and by_id[r["item"]]["jamo_pos"] == pos and r["prompt"] == prompts[0]]
            tr.append([sn, pn] + rr_row(sel))
    add(table(["모양", "위치"] + head2, tr))
    add("")

    # 5. 양자택일 ------------------------------------------------------------
    if choices:
        add("## 5. 양자택일 검사 (봤는데 고쳐 썼는가, 못 봤는가)")
        add("")
        add("같은 이미지를 보여 주고 보이는 글자와 익숙한 단어 중 하나를 고르게 합니다. 순서 치우침을 없애려고 "
            "보기 순서를 바꿔 두 번 묻고, 두 번 모두 맞히면 '구별함'으로 봅니다.")
        add("")
        pair = defaultdict(dict)
        for r in choices:
            pair[(r["item"], r["font"], r["size"])][r["order"]] = r["outcome"]

        def verdict(d):
            if len(d) < 2:
                return None
            v = (d["TL"], d["LT"])
            if "invalid" in v:
                return "무효"
            if v == ("target", "target"):
                return "구별함"
            if v == ("lure", "lure"):
                return "익숙한 단어 선택"
            return "순서에 따라 바뀜"

        verd = {k: verdict(d) for k, d in pair.items()}
        verd = {k: v for k, v in verd.items() if v}
        names = ["구별함", "익숙한 단어 선택", "순서에 따라 바뀜", "무효"]
        tr = []
        for font in fonts:
            for size in sizes:
                c = Counter(v for k, v in verd.items() if k[1] == font and k[2] == size)
                n = sum(c.values())
                tr.append([font_name.get(font, font), f"{size}px", n] + [pct(c[x], n) for x in names])
        add(table(["글꼴", "글자 높이", "문항 수"] + names, tr))
        add("")
        free = {(r["item"], r["font"], r["size"]): r["outcome"] for r in lure_reads
                if r["layout"] == "word" and r["prompt"] == prompts[0]}
        add(f"자유 읽기(단어만, {base} 지시문) 결과와 겹쳐 보기:")
        add("")
        tr = []
        cross = {}
        for fo in ("rewrite", "faithful", "both", "other"):
            c = Counter(verd[k] for k, v in free.items() if v == fo and k in verd)
            n = sum(c.values())
            cross[fo] = dict(c)
            tr.append([{"rewrite": "고쳐 읽음", "faithful": "그대로 읽음", "both": "둘 다", "other": "기타"}[fo], n]
                      + [pct(c[x], n) for x in names])
        add(table(["자유 읽기 결과", "문항 수"] + names, tr))
        add("")
        add("고쳐 읽은 문항 가운데 '구별함'이 많으면, 모델이 글자를 보고도 고쳐 쓴 것입니다(선행 연구의 주장과 같은 방향). "
            "'익숙한 단어 선택'이 많으면 구별 자체를 못 한 것입니다.")
        add("")
    else:
        cross = {}

    # 6. 판단 보조 -----------------------------------------------------------
    add("## 6. 판단에 쓸 숫자")
    add("")
    key = {}
    for name, sel in (
        (f"{base} 지시문 전체(J+M)", [r for r in lure_reads if r["prompt"] == prompts[0]]),
        (f"{base}, 문장 속(J+M)", [r for r in lure_reads if r["prompt"] == prompts[0] and r["layout"] == "sent"]),
        (f"{base}, 단어만(J+M)", [r for r in lure_reads if r["prompt"] == prompts[0] and r["layout"] == "word"]),
        ("엄격 지시문 전체(J+M)", [r for r in lure_reads if r["prompt"] == "strict"]),
    ):
        if not sel:
            continue
        k = sum(r["outcome"] == "rewrite" for r in sel)
        key[name] = {"n": len(sel), "rewrite": k}
        add(f"- {name}: 고쳐 읽기 {pct(k, len(sel))} ({k}/{len(sel)}, 95% 신뢰구간 {ci(k, len(sel))})")
    nsel = [r for r in reads if by_id[r["item"]]["cond"] == "N" and r["prompt"] == prompts[0]]
    k = sum(r["outcome"] == "correct" for r in nsel)
    key["정상 단어 정확도"] = {"n": len(nsel), "correct": k}
    add(f"- 정상 단어 정확도({base} 지시문): {pct(k, len(nsel))} ({k}/{len(nsel)})")
    add("")
    add("기준(사전에 정한 것): 중립 지시문에서 J나 M의 고쳐 읽기 비율이 10% 이상인 조건이 있고 정상 단어 정확도가 90% 이상이면 "
        "이 주제로 진행합니다. 엄격 지시문을 쓴 뒤에도 고쳐 읽기가 남아 있으면 새 완화 기법이 들어갈 자리가 있습니다.")
    add("")

    # 7. 예시 ----------------------------------------------------------------
    ex = [r for r in lure_reads if r["outcome"] == "rewrite"][:15]
    if ex:
        add("## 7. 고쳐 읽은 예시 (최대 15건)")
        add("")
        add(table(["이미지에 적힌 글자", "모델 출력", "글꼴", "높이", "배치", "지시문"],
                  [[(by_id[r["item"]]["target"] if r["layout"] == "word" else by_id[r["item"]]["sent_target"]),
                    r["output"].replace("\n", " ").replace("|", "/")[:60], font_name.get(r["font"], r["font"]),
                    f'{r["size"]}px', lay_name[r["layout"]], pr_name[r["prompt"]]] for r in ex]))
        add("")
    oth = [r for r in lure_reads if r["outcome"] == "other"][:10]
    if oth:
        add("## 8. 기타 오독 예시 (최대 10건, 직접 확인용)")
        add("")
        add(table(["이미지에 적힌 글자", "모델 출력", "글꼴", "높이"],
                  [[(by_id[r["item"]]["target"] if r["layout"] == "word" else by_id[r["item"]]["sent_target"]),
                    r["output"].replace("\n", " ").replace("|", "/")[:60], font_name.get(r["font"], r["font"]),
                    f'{r["size"]}px'] for r in oth]))
        add("")
    return "\n".join(L), {"key": key, "paired": stats, "choice_cross": cross}


# --------------------------------------------------------------------------- 미리보기
def make_preview(items, font_paths, sizes, out_dir):
    from PIL import Image
    os.makedirs(out_dir, exist_ok=True)
    by_cond = defaultdict(list)
    for it in items:
        by_cond[it["cond"]].append(it)
    picks = by_cond["J"][:3] + by_cond["M"][:3] + by_cond["N"][:1] + by_cond["R"][:1]
    tiles = []
    for it in picks:
        for font in font_paths:
            for size in sizes:
                for lay in ("word", "sent"):
                    t = {"font": font, "size": size, "layout": lay}
                    img, _ = image_for(t, it, font_paths)
                    name = f'{it["id"]}_{font}_{size}px_{lay}.png'
                    img.save(os.path.join(out_dir, name))
                    tiles.append(img)
    cols = 4
    rows = math.ceil(len(tiles) / cols)
    sheet = Image.new("RGB", (cols * CANVAS[0] // 2, rows * CANVAS[1] // 2), "gray")
    for i, im in enumerate(tiles):
        small = im.resize((CANVAS[0] // 2 - 4, CANVAS[1] // 2 - 4))
        sheet.paste(small, ((i % cols) * CANVAS[0] // 2 + 2, (i // cols) * CANVAS[1] // 2 + 2))
    sheet.save(os.path.join(out_dir, "_contact_sheet.png"))
    print(f"예시 이미지 {len(tiles)}장을 {out_dir} 에 저장했습니다. (_contact_sheet.png 는 한눈에 보는 모음)")


# --------------------------------------------------------------------------- 실행
def parse_args():
    ap = argparse.ArgumentParser(description="한글 고쳐 읽기 사전 실험")
    ap.add_argument("--model", default="Qwen/Qwen3-VL-8B-Instruct", help="Hugging Face 모델 이름")
    ap.add_argument("--backend", default="hf", choices=["hf", "mock"], help="mock은 모델 없이 동작만 점검")
    ap.add_argument("--load-4bit", action="store_true", help="4bit(NF4) 양자화로 불러오기 (bitsandbytes 필요)")
    ap.add_argument("--quick", action="store_true", help="문항 80개만 쓰는 축소 실험")
    ap.add_argument("--minutes", type=float, default=0, help="이 시간(분)이 지나면 멈추고 요약 (0이면 끝까지)")
    ap.add_argument("--out", default=None, help="결과 폴더 (기본: runs/<모델이름>[-4bit][-quick])")
    ap.add_argument("--stimuli", default=os.path.join(HERE, "stimuli.json"))
    ap.add_argument("--fonts", default="gothic,pen", help="gothic(인쇄체), pen(손글씨풍) 또는 글꼴 파일 경로")
    ap.add_argument("--sizes", default="16,32", help="글자 높이(px), 쉼표로 구분")
    ap.add_argument("--layouts", default="word,sent", help="word(단어만), sent(문장 속)")
    ap.add_argument("--prompts", default="neutral,strict")
    ap.add_argument("--no-choice", action="store_true", help="양자택일 검사 생략")
    ap.add_argument("--max-new-tokens", type=int, default=64)
    ap.add_argument("--seed", type=int, default=20261112)
    ap.add_argument("--preview", action="store_true", help="예시 이미지만 만들고 끝내기")
    ap.add_argument("--report-only", action="store_true", help="저장된 결과로 요약만 다시 만들기")
    ap.add_argument("--save-images", action="store_true", help="모든 자극 이미지를 결과 폴더에 저장")
    args = ap.parse_args()
    args.sizes = sorted(int(x) for x in args.sizes.split(",") if x.strip())
    args.layouts = [x.strip() for x in args.layouts.split(",") if x.strip()]
    args.prompts = [x.strip() for x in args.prompts.split(",") if x.strip()]
    args.fonts = [x.strip() for x in args.fonts.split(",") if x.strip()]
    for x in args.layouts:
        if x not in ("word", "sent"):
            ap.error(f"--layouts 값이 잘못됐습니다: {x}")
    for x in args.prompts:
        if x not in PROMPTS:
            ap.error(f"--prompts 값이 잘못됐습니다: {x}")
    return args


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")      # 콘솔이 못 찍는 글자가 있어도 멈추지 않게
        except Exception:
            pass
    args = parse_args()
    items = load_items(args.stimuli)
    font_paths = {}
    for f in args.fonts:
        path = FONTS.get(f, f)
        if not os.path.exists(path):
            raise SystemExit(f"글꼴 파일이 없습니다: {path}\nfonts 폴더가 스크립트와 같은 곳에 있는지 확인하세요.")
        font_paths[os.path.splitext(os.path.basename(f))[0] if f not in FONTS else f] = path
    args.fonts = list(font_paths)
    chars = set("".join((it["target"] + it["sent_target"]) for it in items))
    for name, path in font_paths.items():
        miss = missing_glyphs(path, chars)
        if miss:
            raise SystemExit(f"글꼴 {name}에 없는 글자가 있습니다: {''.join(miss)}\n모든 한글 음절을 지원하는 글꼴을 쓰세요.")

    if args.preview:
        make_preview(items, font_paths, args.sizes, os.path.join(HERE, "preview"))
        return

    tag = re.sub(r"[^0-9A-Za-z._-]+", "_", args.model.split("/")[-1]) if args.backend == "hf" else "mock"
    out_dir = args.out or os.path.join(HERE, "runs", tag + ("-4bit" if args.load_4bit else "") + ("-quick" if args.quick else ""))
    os.makedirs(out_dir, exist_ok=True)
    res_path = os.path.join(out_dir, "results.jsonl")
    info_path = os.path.join(out_dir, "run_info.json")

    if args.report_only:
        info = json.load(open(info_path, encoding="utf-8")) if os.path.exists(info_path) else {}
        rows = load_results(res_path)
        if not rows:
            raise SystemExit(f"결과 파일이 비어 있습니다: {res_path}")
        text, stats = summarize(rows, items, info)
        with open(os.path.join(out_dir, "summary.md"), "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(text)
        return

    run_items = quick_subset(items, args.seed) if args.quick else items
    by_id = {it["id"]: it for it in items}
    trials = build_trials(run_items, args)
    done = {r["key"] for r in load_results(res_path)}
    todo = [t for t in trials if trial_key(t) not in done]
    print(f"문항 {len(run_items)}개, 시행 {len(trials)}건 (이미 끝난 것 {len(trials) - len(todo)}건, 남은 것 {len(todo)}건)")
    print(f"결과 폴더: {out_dir}")

    # 그림 사이 거리(시각 증거의 세기)를 문항별로 한 번 계산해 둡니다.
    dist = {}
    for it in run_items:
        if it.get("lure") and len(it["target"]) == len(it["lure"]):
            dist[it["id"]] = {f: pixel_distance(it["target"], it["lure"], p) for f, p in font_paths.items()}

    backend = MockBackend(args.seed) if args.backend == "mock" else HFBackend(args.model, args.load_4bit)
    info = dict(backend.info)
    info.update({"started": datetime.datetime.now().isoformat(timespec="seconds"), "quick": args.quick,
                 "planned_trials": len(trials), "canvas": list(CANVAS), "sizes": args.sizes, "fonts": args.fonts,
                 "layouts": args.layouts, "prompts": {k: PROMPTS[k] for k in args.prompts},
                 "choice_prompt": CHOICE_PROMPT, "max_new_tokens": args.max_new_tokens, "seed": args.seed,
                 "python": platform.python_version(), "platform": platform.platform(),
                 "decoding": "greedy, repetition_penalty=1.0"})

    # 시작 전 점검: 정상 단어 하나를 큰 글자로 읽혀 봅니다.
    probe_item = next(it for it in items if it["cond"] == "N")
    probe_trial = {"task": "read", "item": probe_item["id"], "font": args.fonts[0], "size": max(args.sizes),
                   "layout": "word", "prompt": args.prompts[0], "order": "-"}
    img, _ = image_for(probe_trial, probe_item, font_paths)
    ans = clean_output(backend.generate(img, PROMPTS[args.prompts[0]], args.max_new_tokens, probe_trial, probe_item))
    print(f"시작 전 점검: 이미지 글자 '{probe_item['target']}' -> 모델 출력 '{ans}'")
    if args.backend != "mock" and norm(probe_item["target"]) not in norm(ans):
        print("[주의] 정상 단어를 못 읽었습니다. 계속 진행하지만, 요약표 1번(읽기 능력 점검)을 먼저 확인하세요.")
    info["sanity"] = {"shown": probe_item["target"], "output": ans}

    if args.save_images:
        os.makedirs(os.path.join(out_dir, "images"), exist_ok=True)
    t_start = time.time()
    lat = []
    n_lure = n_rw = 0
    stopped = False
    with open(res_path, "a", encoding="utf-8") as fout:
        for i, t in enumerate(todo, 1):
            item = by_id[t["item"]]
            img, meta = image_for(t, item, font_paths)
            if args.save_images:
                img.save(os.path.join(out_dir, "images", f'{item["id"]}_{t["font"]}_{t["size"]}_{t["layout"]}.png'))
            if t["task"] == "read":
                prompt = PROMPTS[t["prompt"]]
            else:
                a, b = (item["target"], item["lure"]) if t["order"] == "TL" else (item["lure"], item["target"])
                prompt = CHOICE_PROMPT.format(a=a, b=b)
            t0 = time.time()
            raw = backend.generate(img, prompt, args.max_new_tokens if t["task"] == "read" else 8, t, item)
            dt = time.time() - t0
            out = clean_output(raw)
            outcome = score_read(item, out) if t["task"] == "read" else score_choice(item, out, t["order"])
            row = dict(t)
            row.update({"key": trial_key(t), "cond": item["cond"], "target": item["target"], "lure": item.get("lure"),
                        "output": out, "outcome": outcome, "exact": norm(out) == norm(item["target"] if t["layout"] == "word" else item["sent_target"]),
                        "latency": round(dt, 3), "em": meta["em"], "text_w": meta["text_w"], "text_h": meta["text_h"],
                        "pix_dist": dist.get(item["id"], {}).get(t["font"])})
            fout.write(json.dumps(row, ensure_ascii=False) + "\n")
            fout.flush()
            lat.append(dt)
            if t["task"] == "read" and item.get("lure") and t["prompt"] == args.prompts[0]:
                n_lure += 1
                n_rw += outcome == "rewrite"
            if i % 50 == 0 or i == len(todo):
                el = time.time() - t_start
                eta = el / i * (len(todo) - i)
                print(f"[{i}/{len(todo)}] 경과 {el / 60:.1f}분, 남은 시간 약 {eta / 60:.0f}분, "
                      f"지금까지 고쳐 읽기({args.prompts[0]}) {pct(n_rw, n_lure)} ({n_rw}/{n_lure})", flush=True)
            if args.minutes and (time.time() - t_start) > args.minutes * 60:
                print(f"--minutes {args.minutes} 에 도달해 멈춥니다. 같은 명령을 다시 실행하면 이어서 진행합니다.")
                stopped = True
                break

    info["finished"] = datetime.datetime.now().isoformat(timespec="seconds")
    info["stopped_early"] = stopped
    info["mean_latency"] = round(sum(lat) / len(lat), 3) if lat else None
    if hasattr(backend, "peak_gib"):
        info["peak_gib"] = backend.peak_gib()
    info.update({k: v for k, v in backend.info.items() if k not in info})
    rows = load_results(res_path)
    text, stats = summarize(rows, items, info)
    info["summary_numbers"] = stats
    with open(info_path, "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    with open(os.path.join(out_dir, "summary.md"), "w", encoding="utf-8") as f:
        f.write(text + "\n")
    print()
    print(text)
    print()
    print(f"저장 위치: {out_dir}")
    print("이 폴더의 summary.md, results.jsonl, run_info.json 세 파일을 보내 주시면 됩니다.")


if __name__ == "__main__":
    main()
