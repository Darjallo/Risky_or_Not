import base64
import requests

EXECUTOR_URL = "http://127.0.0.1:8000/execute"

tests = [
    ("python", "python:3.12-slim", "print(2+2)\n"),
    ("r", "r-executor:4.5.2", "print(2+2)\n"),
    ("maxima", "maxima-executor:latest",
    "display2d:false$\nshowtime:false$\nprint(2+2)$\n"),
    ]

for t, img, code in tests:
    payload = {
        "image": img,
        "type": t,
        "code_b64": base64.b64encode(code.encode("utf-8")).decode("utf-8"),
    }
    resp = requests.post(EXECUTOR_URL, json=payload, timeout=120)
    print(f"\n== {t} == {resp.status_code}")
    print(resp.json())

