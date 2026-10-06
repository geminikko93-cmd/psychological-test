"""JP 숏폼 작업실 실행: 127.0.0.1에서만 열리는 로컬 서버를 띄우고 브라우저를 연다."""
import os
import socket
import sys
import threading
import webbrowser

import uvicorn


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def main() -> None:
    port = int(os.environ.get("JPSS_PORT", "8765"))
    url = f"http://127.0.0.1:{port}/"
    if not port_free(port):
        print(f"이미 실행 중인 것 같습니다. 브라우저에서 {url} 를 엽니다.")
        webbrowser.open(url)
        return
    if "--no-browser" not in sys.argv:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    print("=" * 60)
    print(" JP 숏폼 작업실")
    print(f" 브라우저에서 {url} 이 열립니다.")
    print(" 이 창을 닫으면 프로그램이 종료됩니다.")
    print("=" * 60)
    uvicorn.run("app.main:app", host="127.0.0.1", port=port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
