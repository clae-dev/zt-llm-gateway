"""시연용: 시나리오별 문장을 게이트웨이로 보내고 판정·모델이 받은 글·응답을 차례로 찍는다.

게이트웨이를 시연 모드로 띄워 둔다:  .\\run-ollama.ps1 -Kanana -Demo
python scripts/demo.py [게이트웨이 주소] [시나리오 번호 ...]     예) python scripts/demo.py http://127.0.0.1:8787 S1

비밀이 검색 문서나 고객 기록에 들어 있는 상황(S1, S2)을 쓴다. 문장은 datasets/demo/cases.tsv 에 있다.
"""
import sys

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8787"
ONLY = set(sys.argv[2:])
LABEL = {"allow": "통과", "flag": "주의", "block": "차단"}

sys.stdout.reconfigure(encoding="utf-8")
with httpx.Client(base_url=BASE, timeout=180) as http:
    model = http.get("/v1/models").json()["data"][0]["id"]
    scenarios = http.get("/demo/scenarios").json()
    if not scenarios:
        sys.exit("시나리오가 없다. 게이트웨이를 시연 모드(DEMO=1)로 띄웠는지 확인한다.")
    print(f"연결 모델: {model}\n")
    for sc in scenarios:
        if not sc["cases"] or (ONLY and sc["id"] not in ONLY):
            continue
        print(f"━━ {sc['id']} {sc['name']} (지켜야 할 값 {len(sc['secrets'])}개)\n")
        for case in sc["cases"]:
            messages = [{"role": "system", "content": sc["system"]},
                        {"role": "system", "content": "[참고 자료]\n" + sc["context"]},
                        {"role": "user", "content": case["text"]}]
            inspected = http.post("/guard/inspect", json={"messages": messages}).json()
            res = http.post("/v1/chat/completions",
                            json={"model": model, "messages": messages, "temperature": 0}).json()
            if "guard" not in res:
                sys.exit(f"게이트웨이 오류: {res}")
            guard = res["guard"]
            at_output = guard.get("stage") == "output"
            print(f"■ {case['name']}")
            print(f"  입력        {case['text']}")
            print(f"  입력 검사   {LABEL['allow' if at_output else guard['decision']]} (점수 {guard['score']:.2f})"
                  + "".join(f"\n              {h['id']} {h['category']}" for h in guard["hits"]))
            if guard["decision"] == "block" and not at_output:
                print("  모델 전달   하지 않음")
                print(f"  응답        {res['error']['message']}")
            else:
                print(f"  모델이 받은 글  {inspected['masked_messages'][-1]['content']}")
                if at_output:
                    print(f"  응답 검사   차단 - {' · '.join(guard['output_leak'])}")
                    print(f"  응답        {res['error']['message']}")
                else:
                    print("  응답 검사   통과")
                    print(f"  응답        {res['choices'][0]['message']['content'].strip()}")
                print(f"  시간        검사 {guard['latency_ms']['guard']} ms / 모델 {guard['latency_ms']['upstream']:.0f} ms")
            print()
