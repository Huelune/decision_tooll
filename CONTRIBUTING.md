# 개발 규칙

## 환경과 검사

Windows PowerShell에서 저장소 루트 기준으로 실행합니다.

```powershell
uv sync --locked
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked python -m unittest discover -s tests -v
uv build
```

자동 정리는 `uv run ruff check --fix .`와 `uv run ruff format .`을 사용합니다. 실행 의존성과 개발 의존성을 `pyproject.toml`에서 관리하고 `uv.lock`을 함께 커밋합니다. 의존성을 추가할 때는 `uv add` 또는 `uv add --dev`를 사용합니다. 잠금 파일을 손으로 편집하지 않습니다.

Windows GUI 테스트는 실제 Tk 창을 숨긴 상태에서 위젯과 파일 생성 흐름을 검사합니다. 다른 운영체제에서는 이 테스트만 명시적으로 건너뜁니다. GitHub Actions에는 Windows Python 3.12·3.14 검사와 빌드를 구성합니다.

## 모듈 경계

- C 구조·TC 분석은 `analysis`, 숫자식 처리는 `expressions`, 경계값 생성은 `boundary`에 둡니다.
- 엔진 모듈에서 화면 창, 대화상자 또는 명령줄 출력에 접근하지 않습니다.
- 화면은 엔진 결과를 표시하고 상태를 조립합니다. 새 파일은 읽기·파싱 성공 후 한 번에 교체합니다.
- 지원하지 않는 구문은 이유를 남깁니다. 실제 실행을 확인하지 않은 결과를 실행 커버리지로 표현하지 않습니다.
- 실제 고객 코드·경로·TC·캡처·분석 결과를 테스트나 PR에 넣지 않습니다. 합성 입력으로 재현합니다.

## 브랜치와 커밋

`fix/<설명>`, `feat/<설명>`, `refactor/<설명>` 브랜치를 사용합니다. 커밋 제목 형식은 다음과 같습니다.

```text
type(scope): 변경 내용
```

`scope`는 선택 사항입니다. 허용하는 type은 `feat`, `fix`, `refactor`, `test`, `docs`, `build`, `ci`, `chore`입니다. 호환성을 깨는 변경은 `type(scope)!:`로 표시하고 본문에 이행 방법을 적습니다. 커밋 본문은 한글로 작성해도 됩니다.

```text
fix(analysis): do not infer loop exit from entry
refactor(package): separate engine and presentation
build(uv): add locked Windows development environment
```

한 커밋은 한 가지 목적을 갖도록 합니다. 오류 수정에는 해당 오류를 잡는 테스트를 함께 넣고, 단순 파일 이동·서식 변경은 동작 수정과 분리합니다. 커밋 직전 관련 테스트가 통과해야 합니다.

PR의 새 커밋 제목은 다음으로 검사할 수 있습니다. 자동 생성되는 병합 커밋은 제외합니다.

```powershell
git fetch origin main
uv run --locked python scripts/check_commits.py origin/main..HEAD
```

PR은 문제, 바뀐 동작, 실행한 검증, 남은 제한을 설명합니다. 로컬 검사 통과와 GitHub CI 통과를 구분하고, 실제로 실행하지 않은 검사를 통과했다고 적지 않습니다.
