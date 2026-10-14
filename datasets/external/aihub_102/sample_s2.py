"""AI Hub '소상공인 고객 주문 질의-응답 텍스트'에서 S2(쇼핑몰 상담)용 고객 질문을 뽑는다.

사람이 고르지 않는다. 아래 규칙과 고정 시드로 무작위 추출한다. 문장은 화면에 찍지 않는다.
  원본   : 디지털가전_validation.csv (발화자 c, QA여부 q)
  의도   : 배송 60, 교환|반품|환불 60, 결제 15, AS 15  (S2 기록에 배송 지연과 환불 계좌가 있어 두 묶음을 크게 잡았다)
  제외   : 상품명 칸이 채워진 줄(특정 상품 질문), 10자 미만·80자 초과, '#' 가림 표시가 든 줄, 같은 문장
  시드   : 20261014
출처: 한국지능정보사회진흥원 AI Hub. 원문을 팀 밖에 공유하거나 저장소에 올리지 않는다.
"""
import csv
import random
from pathlib import Path

from openpyxl import Workbook

HERE = Path(__file__).parent
QUOTA = {"배송": 60, "교환|반품|환불": 60, "결제": 15, "AS": 15}
COLUMNS = ["번호", "묶음", "세부 유형", "시나리오", "요청 문장", "의도", "노리는 기밀", "작성자", "메모"]

rows = list(csv.DictReader(open(HERE / "디지털가전_validation.csv", encoding="utf-8")))
pool: dict[str, list[dict]] = {k: [] for k in QUOTA}
seen = set()
for r in rows:
    text = r["발화문"].strip()
    top = r["인텐트"].split("_")[0]
    if (r["발화자"] == "c" and r["QA여부"] == "q" and top in QUOTA and not r["상품명"].strip()
            and 10 <= len(text) <= 80 and "#" not in text and text not in seen):
        seen.add(text)
        pool[top].append(r)

rng = random.Random(20261014)
wb = Workbook()
ws = wb.active
ws.title = "평가셋"
ws.append(COLUMNS)
n = 0
for top, k in QUOTA.items():
    for r in rng.sample(pool[top], k):
        n += 1
        ws.append([n, "정상 - 일반 업무", r["인텐트"], "S2", r["발화문"].strip(), "정상", "",
                   "AI Hub 소상공인 고객 주문 질의-응답 (실제 고객 발화)", f"IDX {r['IDX']}"])
out = HERE / "평가셋_AIHub_S2.xlsx"
wb.save(out)
print({k: len(v) for k, v in pool.items()}, "->", n, "건", out.name)
