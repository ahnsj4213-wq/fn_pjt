# ADR-0007: 코딩테스트 runner와 샌드박스, 검증 전 비활성
- 상태: 채택(임시) — 샌드박스 미검증 시 면접 시작 차단은 팀 확인(OQ-32), 운영 런타임은 운영 호스트 SBX 통과 전까지 미검증
- 날짜: 2026-10-27
- 작성: 에이전트 E / 통합 F
- 관련: COD-03·04·05·06, RULE-6.5, RULE-6.6, AC-15, INT-14, R-06·07, OQ-14·32, consolidation-round1 C1·C8·T1·T13, ADR-0001·0010·0011, TRD `07-security.md` 13절

## 맥락

- COD-03은 지원자가 작성한 임의 Python 코드를 [실행]하고, COD-04는 공개·비공개 테스트로 채점한다. 이 코드는 서비스에서 가장 위험한 입력이다(탈출, 네트워크 접근, 자원 고갈, 비공개 테스트 유출).
- COD-06은 "네트워크 차단, CPU·메모리·시간·동시 실행 제한, 비공개 테스트는 어떤 화면·AI 입력에도 노출하지 않는다"를 요구한다.
- Docker 소켓은 호스트 root와 같은 권한이다(R-06). 개발은 Windows 10 + Docker Desktop(WSL2, runc, gVisor 없음), 운영은 Linux다(R-07).
- 리뷰에서 두 Critical이 나왔다. C8: 기대 출력이 runner·샌드박스 하니스로 들어가면 제출 코드가 읽거나 조작할 수 있다. C1: 운영 Docker 전체에 userns-remap·rootless를 강제하면 app(aiortc)의 host 네트워크와 충돌한다.

## 결정

1. **구조**: 별도 `runner` 프로세스만 Docker 소켓을 갖는다. runner는 DB 자격 증명이 없다. `app`·`worker`는 루프백·내부망 + `X-Runner-Token`으로 요청한다.
2. **계약(C8)**: runner 요청은 `{job_id, mode: run|grade, language: python, code(≤64 KiB), cases: [{case_id, stdin}], profile}`뿐이다. 기대 출력은 runner에 보내지 않는다. 응답은 사례별 `{status, exit_code, stdout ≤64 KiB, stderr ≤16 KiB, truncated, wall_ms}`와 `sandbox_fingerprint`. 비교는 `worker`가 runner 밖에서 한다.
3. **실행 단위**: 테스트 1건 = 컨테이너 1회, 실행마다 새 컨테이너, 종료 후 삭제. [실행]은 서버가 고른 공개 예시만 쓴다.
4. **컨테이너 옵션**: `--network none`, `--read-only`, `--tmpfs /tmp:rw,noexec,nosuid,nodev,size=16m`, `--cap-drop ALL`, `--security-opt no-new-privileges`, Docker 기본 seccomp, `--user 10001:10001`, `--pids-limit 64`, `--memory 256m --memory-swap 256m`, `--cpus 1` + 전용 코어, `--ulimit nofile=64:64 fsize=8388608 core=0 cpu=<제한+1>`, `--ipc none`, `--log-driver none`, 마운트·장치·GPU 없음, `--privileged`·host 네임스페이스 금지. 코드는 stdin 길이 접두 프레임으로 런처가 받는다. 출력은 스트림에서 상한까지만 읽고 kill한다. 값의 표는 TRD 07 13.2.
5. **자체 점검**: SBX-01~14(07 13.4)를 지원자 코드와 같은 실행 경로로 기동 시·매일·설정 변경 시 돌리고, SBX-01(inspect)은 매 실행 전 돌린다.
6. **검증 전 비활성**: `AIHIRE_CODE_EXECUTION_ENABLED` 기본 false. 유효 상태 = 플래그 ∧ SBX 전부 통과 ∧ 지문(runner 버전·이미지 다이제스트·엔진 버전·런타임·seccomp·제한값 해시) 일치 ∧ 24시간 이내 ∧ (운영) 승인 런타임. runner가 상태를 갖고 `GET /v1/health`로 알리며, app은 상태 변화를 감사 로그에 남긴다.
7. **미검증 시 동작(T13, OQ-32)**: 코딩 켠 공고의 면접 시작 차단(INT-14와 같은 위치), [실행] 503, 이미 제출된 코드는 채점 대기(0점 아님), 공고 편집은 경고만. 응시 도중 비활성되면 [실행]만 막는다. `SANDBOX_ERROR`는 0점 사유가 아니다.
8. **런타임(C1)**: 강화 런타임은 샌드박스 전용 데몬·런타임에만 적용한다. 운영 1순위 `--runtime=runsc`(gVisor), 불가 시 샌드박스 전용 rootless 데몬, 최후 수단은 샌드박스 전용 호스트의 rootful runc(ADR-0010). 공유 데몬의 userns-remap은 쓰지 않는다. 개발(Docker Desktop·runc) 결과는 "개발 검증"으로만 표시한다.
9. **출시 조건**: 운영 호스트의 승인 런타임에서 SBX-01~14 전부 통과와 지문 기록(QG-05, 08 7절 S4).

## 검토한 대안 (장단점)

| 대안 | 장점 | 단점 | 판정 |
|---|---|---|---|
| API가 Docker SDK 직접 호출 | 단순 | API 취약점 하나가 호스트 장악 | 기각 |
| 미리 띄운 컨테이너 풀 + exec 재사용 | 지연 감소 | 지원자 간 상태 잔존, exec API 필요 | 기각 |
| 테스트 파일(입력+기대 출력)을 컨테이너 안에서 비교 | 컨테이너 1회 | 기대 출력 열람·하니스 조작(C8) | 기각 |
| 제출 1건 = 컨테이너 1개, 내부 하니스가 입력 순차 실행 | 빠름 | 모든 비공개 입력이 한 경계에 모임 | 성능 문제 시 차선(기대 출력은 계속 밖) |
| nsjail | 가볍고 세밀 | 커널 공유, Docker 안 중첩 시 바깥 권한 상승, Windows 개발 불가 | 기각 |
| Firecracker | 가장 강한 경계 | KVM 필요, Windows 10 WSL2 불가, 운영 부담 | MVP 범위 밖 |
| 운영 Docker 전체 userns-remap·rootless | 데몬 전체 강화 | app host 네트워크와 비호환(C1) | 기각 |
| 컨테이너 없는 하위 프로세스 + rlimit | 가장 단순 | 네트워크·파일시스템 격리 없음 | 금지 |

## 근거 (실측·문서·재현 가능한 위험)

- 실측(`prototypes/results-sandbox-escape.txt`, Docker Desktop·runc): 강화 컨테이너가 네트워크·읽기 전용·noexec·비루트·CapEff 0·환경 비밀·포크 폭탄(63)·메모리(OOM 137)·무한 루프(5초 kill)·파일 크기·출력 절단 12/12 차단. 기동 약 0.45초 → 테스트 20건 약 9초로 테스트별 컨테이너가 가능하다.
- 스파이크의 한계: 출력을 전부 메모리에 읽은 뒤 잘랐다. `--log-driver`가 없어 출력이 Docker 로그 파일에 쌓인다. 진입점 `python -I -B -`는 stdin을 코드가 차지한다. tmpfs 크기는 fsize 상한이 먼저 걸려 검증되지 않았다. 이 ADR의 옵션·런처·출력 스트림 상한은 이 한계를 메운다.
- 재현 가능한 위험(C8): 기대 출력이 같은 경계에 있으면 제출 코드가 파일 시스템·`/proc`를 훑어 기대 출력을 그대로 내거나, 결과 줄을 위조한 뒤 종료해 통과로 세어질 수 있다. 시험 SBX-11과 AC-15 ⑤로 확인한다.
- 문서: userns-remap 데몬은 `--network=host`와 호환되지 않는다(Docker 문서, ADR-0010). 런타임 선택은 컨테이너 단위라 runsc는 다른 서비스의 host 네트워크와 무관하다.

## 결과와 영향 (코드·스키마·운영)

- 코드: `runner/` 패키지(DB 의존성 없음), 샌드박스 이미지(`python:3.12-slim` 다이제스트 고정, uid 10001, setuid 제거, 런처), `worker`의 채점 비교. `app/core/config.py`의 `code_execution_enabled=False`는 이미 구현.
- 스키마(구현됨): `coding_runs.verdict`(OK·RUNTIME_ERROR·TIMEOUT·MEMORY_LIMIT·OUTPUT_LIMIT·SANDBOX_ERROR), 지원 건당 진행 중 [실행] 1건 부분 유니크, 코드 64 KiB CHECK, 문제별 `time_limit_ms`·`memory_mb` CHECK. `coding_test_results`에는 통과 여부·상태만 둔다. 앱 롤이 비공개 테스트를 못 읽게 하는 공개 예시 뷰와 롤 분리는 아직 없다(07 G-09).
- 운영: runner 기동 순서상 SBX가 끝나야 실행 기능이 켜진다. 운영 호스트에 샌드박스 전용 런타임·데몬 구성, 전용 CPU 코어 설정, 이미지 반입(`docker save/load`).
- 지원자 경험: 미검증이면 코딩 켠 공고는 면접을 시작할 수 없다(안내 문구는 INT-14와 같은 방식).

## 남은 위험과 재검토 조건

- 커널 미공개 취약점을 통한 탈출은 시험으로 증명할 수 없다. 다층 방어와 커널·이미지 패치 주기로만 줄인다.
- 운영 호스트에 runsc를 설치할 수 없거나 runsc에서 Python 테스트가 비정상이면 rootless 전용 데몬으로 내리고 SBX를 다시 통과해야 한다.
- 테스트별 컨테이너 기동이 runsc에서 리포트 목표(8.2)를 위협하면 "제출 1건 = 컨테이너 1개(기대 출력은 밖)"로 바꾸고 이 ADR을 갱신한다.
- 팀이 OQ-32에서 "시작 허용 + [실행]만 비활성"을 고르면 결정 7을 고친다.
- 코딩 언어가 Python 외로 늘어나면(X-10 변경) 이미지·런처·SBX를 언어별로 다시 정한다.
