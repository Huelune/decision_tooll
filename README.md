# Decision Tool

C 소스와 사람이 작성한 TC 표기를 비교해 분기 결과의 누락을 찾고, 경계값 TC 후보를 만드는 로컬 도구입니다. Windows에서 Python을 개별 설정하지 않고 uv로 실행합니다.

**실행 전 추정 도구입니다. 실제 실행 커버리지를 측정하거나 TC의 실행 가능성을 증명하지 않습니다.**

## Windows에서 시작하기

1. [uv 공식 설치 안내](https://docs.astral.sh/uv/getting-started/installation/)에 따라 uv를 설치합니다.
2. 저장소 폴더에서 다음을 실행합니다. 프로젝트는 Python 3.14를 선택하며, uv가 환경을 준비합니다.

```powershell
uv sync --locked
uv run --locked decision-tool-gui
```

첫 환경 구성 이후에는 `start.cmd`를 더블클릭해도 됩니다. Tkinter가 포함된 Python이 필요합니다. Tcl/Tk 오류가 발생하면 Tkinter가 포함된 Python을 설치하고 `uv sync --locked --python <python.exe 경로>`로 환경을 다시 만드세요.

예시 파일을 바로 열려면:

```powershell
uv run --locked decision-tool-gui examples/example.c --func example
```

명령줄 분석과 경계값 CSV 생성:

```powershell
uv run --locked decision-tool examples/example.c examples/example_tc.txt --func example
uv run --locked decision-tool examples/example.c --func example --csv boundary_tc.csv
```

기존 명령도 uv 환경에서 사용할 수 있습니다.

```powershell
uv run --locked python branch_cov.py examples/example.c
uv run --locked python branch_gui.py examples/example.c
```

초기 환경 구성에는 패키지 다운로드가 필요합니다. 환경 구성 후 분석 자체는 로컬에서 실행되며, 분석 입력을 외부 서비스로 보내는 기능은 없습니다.

명령줄 도움말과 출력은 UTF-8입니다. 영문 Windows에서도 한글을 출력할 수 있으며, 출력 내용을 파일로 저장한 경우 UTF-8로 여세요.

## 화면 사용 순서

1. C 파일과 분석할 함수를 선택합니다.
2. TC 파일을 열거나 왼쪽에 표기를 작성합니다. 빈 줄로 TC를 구분합니다.
3. 오른쪽 누락 분기를 선택해 필요한 경로를 확인합니다.
4. 경계값 생성 후 CSV와 추가된 TC 표기를 확인합니다. 저장을 취소하거나 실패하면 표기를 추가하지 않습니다.

```text
TC 이름
D1 [T] if(a == 1)

다른 TC
D1 [F] if(a == 1)
```

`*`는 명시한 결과가 아니라 구조에서 추론한 결과입니다. 동일한 조건이 여러 곳에 있으면 임의로 배정하지 않고 경고합니다. 먼저 함수 선택으로 범위를 좁히세요. 같은 함수 안의 중복 조건은 아직 위치를 지정할 수 없어 집계에서 제외됩니다.

CLI 요약과 달리 GUI, CSV, 저장한 TC와 임시 상세 보고서에는 소스에서 유래한 이름과 조건이 포함됩니다. 고객 코드와 상세 자료는 로컬에 보관하세요. 상세 보고서는 Python 임시 폴더의 `branch_cov_report.txt`를 사용하며 다음 실행에서 덮어씁니다.

## 자동 분석 범위

- 조건식의 C 연산자 우선순위, 정수 나눗셈/나머지, 단락 평가, 8진수·16진수 리터럴을 일부 지원합니다.
- 캐스트, 비트 연산, 정수 접미사, 일부 signed/unsigned 혼합은 타입 모델이 없어 수동 확인으로 남깁니다.
- 반복문 T에서 F를 자동 추론하지 않습니다. while의 초기 F 입력을 별도로 탐색합니다. 반복문 내부·for·do-while·선행 반복문 이후의 경로는 수동 확인 대상입니다.
- 공유 case와 fall-through 하위 경로는 현재 단일 경로로 표현할 수 없어 수동 확인 대상입니다.
- `unsigned char`와 인식하는 8비트 별칭은 후보를 0~255, bool은 0~1로 제한합니다. 다른 타입의 정확한 폭, 정수 승격·오버플로, float의 기계 정밀도는 모델링하지 않습니다.
- 숫자식은 Python `eval` 없이 해석합니다. 식 길이·토큰 수·깊이·정수 크기에 제한이 있고 시프트는 계산하지 않습니다.

**남은 제한:** 전처리 조건(`#if`, `#ifdef`)을 해석하지 않습니다. 헤더는 실제 include 관계 대신 같은 폴더의 `.h`에서 수집합니다. 복잡한 데이터 흐름, 반복 실행, 포인터를 통한 변경을 실행하지 않습니다. float의 0.1 간격은 사용자용 휴리스틱이고, 탐욕 탐색 결과가 최소 TC 수라는 보장도 없습니다. 실제 테스트 도구의 실행 결과로 검증하세요.

## 코드 구조

```text
src/decision_tool/
  analysis.py       # C 구조·TC 파싱과 커버리지 추론
  expressions.py    # 제한된 조건식 해석
  boundary.py       # 경계값 후보 탐색·CSV
  reports.py        # 화면·상세 보고서 렌더링
  storage.py        # 파일 읽기·헤더 수집
  cli.py            # 명령줄 실행 조립
  gui.py            # Windows Tkinter 화면
tests/              # 회귀·CLI·패키지·Windows GUI 테스트
examples/           # 합성 예시
scripts/            # 개발 검사
```

화면은 분석 엔진을 호출하며 분석 엔진은 Tkinter를 가져오지 않습니다. 파일 입출력과 보고서 표현은 분리되어 있습니다. 개발 규칙과 검사 명령은 [CONTRIBUTING.md](CONTRIBUTING.md)에 있습니다.
