# 프롬프트·응답 요약 — 폰술 건배방 서버 (phonesul-server)

> pipeline §6에 따라 단계별 프롬프트와 핵심 응답을 기록한다.
> 작성 기준일 2026-06-23. 각 STEP은 S1~S5 게이트(pipeline §2·§4)에 매핑된다.
> 표기: 사용자 프롬프트 요약 → 응답(한 일) → 산출물 → 검수 마커.

---

## STEP 1 — 부트 + 문서 정합성 (코드 금지)

**프롬프트(요약):** CLAUDE.md·pipeline·platform·context와 기능정의서 v0.3을 읽고
코드 없이 정합성만 확인. ① 현재 단계·게이트, ② platform 위반 금지(내부 wss vs
앱인토스 API 분리, 익명·휘발), ③ context 핵심 결정, ④ F-RT-01~07 중 1차/후순위,
⑤ 코드 검증 가능/불가 분리 + 마커 예고.

**응답(한 일):**
- 현재 위치 = **S1 산출물 검토 단계**(레포에 코드 없음). 직전 게이트 = 기능정의서
  v0.3 선우 검수(미통과). 다음 게이트 = S1→S2 사람 승인.
- platform §1(내부 wss) ↔ §2(앱인토스 API mTLS·QPM) 분리, §3 익명·휘발 확인.
- context 핵심: 방=wss 채널, 코드 식별, broadcast, in-memory 휘발.
- 1차 구현: F-RT-01·02·03·04·05·06(필수). 권장: F-RT-07(재연결).
  범위 밖: 다중 인스턴스, 채팅·음성, 짠 통계.
- 코드 검증 가능 vs 실기기 필요(Origin·실측 지연) 분리, 마커 예고.

**산출물:** 없음(확인만). **결론:** "정합성 확인 완료".

---

## STEP 2 — FastAPI 골격 + 방/소켓 관리 (S2)

**프롬프트(요약):** FastAPI+WebSocket 골격. ConnectionManager(방별 소켓 집합),
in-memory 모델(Room/Member), JSON 타입 분기(join/leave/cheers/roster/error),
헬스체크+빈 wss+연결/해제 로깅. 비즈니스 로직 최소. uvicorn 기동 가능하게.

**응답(한 일):**
- 골격 5파일 작성. 의존성 `requirements.txt`(pyproject 대신, 패키징 불필요).
- **이슈 발견:** 이 머신에 Python 런타임 없음(WindowsApps 스텁) → 기동·검증 불가
  보고(S3 환경 게이트).

**산출물:** `requirements.txt`, `app/__init__.py`, `app/main.py`,
`app/connection_manager.py`, `app/models.py`, `app/messages.py`, `README.md`.

**마커:** REVIEW(개인정보 저장 범위), REVIEW(wss), UNVERIFIED(토스 WebView Origin),
ASSUMPTION(코드 자릿수·문자셋·만료 N·쿨다운·단일 인스턴스).

---

## STEP 3 — 방 생성 + 코드 발급 (F-RT-01)

**프롬프트(요약):** 방 생성 엔드포인트(Room+랜덤 코드+방장 토큰). 코드 6자/혼동문자
제외, 충돌 방지(재발급), O(1) 조회. 남용 방지(시도 제한+유휴 만료 N분). 방장=서버
발급 토큰. 단위 테스트(생성·충돌·만료) 포함.

**응답(한 일):**
- `RoomStore`(O(1) 조회, 충돌 회피 재발급, 만료 sweep, 방장 토큰),
  `AttemptLimiter`(코드 추측 시도 슬라이딩 윈도우 — 정의·테스트만, enforce는 후속),
  `POST /rooms`. 시간 주입식 설계로 만료를 결정론적 테스트.
- 단위/엔드포인트 테스트 17케이스 작성(미실행 — Python 부재).

**산출물:** `app/room_store.py`, `app/abuse.py`, `app/main.py`(+엔드포인트),
`tests/test_room_store.py`, `tests/test_abuse.py`, `tests/test_api.py`,
`requirements-dev.txt`, `pytest.ini`.

**마커:** ASSUMPTION(충돌 상한·토큰 길이·만료 N·시도제한 임계),
REVIEW(남용 방지), REVIEW(로그인 강제 여부).

---

## STEP 4 — 방 참여 + 인원 동기화 (F-RT-03/04, wss)

**프롬프트(요약):** wss 연결 시 코드로 방 식별→멤버 등록→roster broadcast.
잘못된/만료 코드→error+종료, 정원/상태 이상 처리. 퇴장(끊김) 감지→멤버 제거→roster
갱신. 익명 식별(memberId 서버 발급). CORS/Origin 위임. 통합 테스트(입장→roster→퇴장).

**응답(한 일):**
- wss 엔드포인트 재작성: 연결=참여, Origin 수동 검사(WebSocket은 CORSMiddleware
  미적용), 시도제한 enforce 연결, 정원/만료/잘못된 코드 거절, 끊김 시 정리+roster.
- `ConnectionManager`를 멤버 인지형으로 확장(소켓↔멤버 매핑).

**산출물:** `app/main.py`(재작성), `app/connection_manager.py`(매핑),
`app/models.py`(정원·memberId), `tests/test_ws_join.py`(4케이스).

**마커:** UNVERIFIED(토스 WebView Origin, 현재 "*"), ASSUMPTION(정원·memberId 길이),
REVIEW(남용 방지 IP 키).

---

## STEP 5 — 실시간 짠 broadcast (F-RT-05, 핵심)

**프롬프트(요약):** 방 안 누구나 cheers→전원 1회씩 broadcast→짠 연출. 원자성(중복·유실
최소). 연타 쿨다운(서버 디바운스). 경쟁·순위·집계 절대 금지(비게임). 문구 음주 톤 금지.
체감 지연은 실측 위임. 단위/통합 테스트(다중 클라이언트 모킹) 포함.

**응답(한 일):**
- `_handle_cheers`: 멤버별 leading-edge 쿨다운(쿨다운 내 후속은 조용히 드롭),
  방 전원 broadcast. 봉투에 sender/count 없음 → **비게임 구조적 보장**. 짠 "횟수"
  미저장(시각만). `cheers_allowed`는 now 주입 결정론 함수.

**산출물:** `app/models.py`(last_cheers_at·cheers_allowed), `app/main.py`(짠 처리),
`tests/test_cheers.py`(단위 3+통합 3).

**마커:** ASSUMPTION(쿨다운 값), REVIEW(실측 — 지연은 코드로 확정 불가).

---

## STEP 막간 — Python 런타임 설치 + 테스트 실행

**프롬프트(요약):** STEP 6 전에 Python 설치하고 테스트 실행해보자.

**응답(한 일):**
- winget으로 Python 3.12.10 설치(user scope) → `.venv` 생성 →
  `requirements-dev.txt` 설치.
- **pytest 27 passed**. uvicorn 기동 + `/health` → `{"status":"ok"}` 확인.
- S3 로컬 실행 게이트 ✅ 해소. 단 실기기 의존 항목(Origin·지연) 잔여.

---

## STEP 6 — 방 종료·만료 + 재연결 (F-RT-06/07)

**프롬프트(요약):** 방장 종료/유휴 만료 시 방·멤버·소켓 폐기, 코드 재사용 금지.
백그라운드 전환 소켓 처리 위임. (권장)재연결: 끊김 시 코드 재입장+roster 복구.
단일 인스턴스 가정.

**응답(한 일):**
- `POST /rooms/{code}/close`(X-Host-Token 검증, 방없음/토큰불일치 동일 404),
  `lifespan` 백그라운드 만료 sweep, `_terminate_room`(broadcast→소켓 close→폐기→
  코드 retire). 재연결=재입장+roster 자동 재전송(identity-resume 후순위).

**산출물:** `app/main.py`(close·lifespan·terminate), `app/room_store.py`
(close/is_retired/expired_rooms), `app/messages.py`(room_closed),
`app/connection_manager.py`(sockets_in), `tests/test_close_expiry.py`(7케이스).
**pytest 34 passed.**

**마커:** REVIEW(백그라운드 소켓 처리), ASSUMPTION(sweep 주기·단일 인스턴스·retired 누적).

---

## STEP 7 — 서버 테스트 (S3)

**프롬프트(요약):** 단위(코드 생성·충돌·만료, 입퇴장, 쿨다운, roster 계산), 통합(전체
시나리오), 안정성(다수 인원·연타·재연결 누수·크래시 없음, 끊긴 소켓 정리). 실측 의존은
마커 위임.

**응답(한 일):**
- S3 3축 보강: `test_messages`(roster 계산·비게임 봉투 불변식), `test_scenario`
  (생성→참여→짠→퇴장→종료 1본), `test_stability`(정원 거절·종료 후 누수 없음·재연결
  20회·연타 30회 무크래시). 누수는 종료 응답 후 manager 내부 비었음 직접 단언.
- **pytest 43 passed** (ResourceWarning=error 승격 포함 — 소켓 누수 없음).

**산출물:** `tests/test_messages.py`, `tests/test_scenario.py`,
`tests/test_stability.py`.

**위임(코드로 불가):** REVIEW(실측 지연), UNVERIFIED(토스 WebView Origin),
REVIEW(백그라운드 소켓 처리), REVIEW(wss) → 실기기·사람.

---

## STEP 막간 2 — 서버 기동

**프롬프트(요약):** 서버 켜고 주소 알려줘.

**응답(한 일):** `uvicorn ... --host 0.0.0.0 --port 8000` 백그라운드 기동,
`/health` 정상. 로컬 `http://localhost:8000`(+`/docs`), LAN
`http://192.168.123.8:8000`. 방화벽·Origin "*"(테스트용, 운영 아님) 주의 안내.

---

## STEP 8 — 운영 내구성 보강 (Redis 상태 저장소 + 단일 박스 튜닝)

**프롬프트(요약):** 기능 추가가 아니라 운영 내구성. 확정 결정 (A) RoomStore를 in-memory
→ Redis로 교체(재시작 내구성, 유휴·retired를 TTL로 자동화). (B) 다중 인스턴스+pub/sub·
Limiter 공유는 범위 밖. 단일 박스 유지. 작업 단위로: ①RoomStore Redis화(인터페이스 유지,
async) ②main.py 정리(await 전환·sweep 제거·lifespan을 Redis 풀 정리로) ③단일 박스 튜닝
(broadcast 병렬화·uvloop·FD 상한·가벼운 루프 지연 모니터링) ④의존성·설정 ⑤테스트(회귀 무손상).

**응답(한 일):**
- **RoomStore(Redis, async):** `redis.asyncio`, 접속은 `REDIS_URL`(기본 redis://localhost:6379/0).
  키 `room:{code}`(JSON: code·host_token·created_at, SET ex=유휴TTL) / `retired:{code}`(TTL 24h).
  `get`은 키 부재면 None(만료=TTL 소멸). `touch`는 값 재기록 없이 `PEXPIRE`로 TTL만 슬라이딩.
  `is_expired`는 "키 부재"로 단순화(now 인자는 시그니처 호환 위해 유지·무시). 인터페이스 보존.
- **main.py 정리:** RoomStore 호출부 전부 `await`. 유휴 sweep(`_sweep_once`·`_expiry_loop`·
  `SWEEP_INTERVAL`) 삭제 → TTL이 대체. `lifespan`은 모니터 태스크 기동 + 종료 시 Redis 풀
  `aclose`. 활동 시 `room.touch()` → `await rooms.touch(code)`로 일관화.
- **roster 진실 원천 이전:** async `get`이 매 연결 새 Room을 반환해 `Room.members`가
  연결별로 분리되는 문제 → roster·정원을 `ConnectionManager`(살아있는 소켓)에서 파생하도록
  `members_in` 추가·전환(설계 원칙 "멤버 미저장→재접속 재구성"과 정합).
- **단일 박스 튜닝:** `broadcast` 순차 await → `asyncio.gather` 동시 전송(개별 실패 흡수 유지).
  `/metrics`(connections·rooms·loop_lag_ms) + 주기 모니터 로깅(가벼운 형태). README에 uvloop
  (운영 Linux, `--loop uvloop --workers 1`)·FD 상한(`ulimit -n 65536`)·단일 프로세스 필수 명시.
- **의존성·설정:** `redis==5.2.1`(운영), `fakeredis==2.26.2`·`pytest-asyncio==0.24.0`(개발).
  README 실행 섹션에 Redis 컨테이너·REDIS_URL·Windows(Docker Desktop+WSL2)·자동 테스트는
  fakeredis라 Redis 불요 명시.
- **테스트:** `conftest.py`로 fakeredis 주입(앱·단위 공용 FakeServer, 동기 점검 클라이언트).
  test_room_store는 async+TTL로 재작성(시간주입 결정론 → TTL 직접 점검). 신규: 재시작 복구,
  TTL 만료로 입장 거절, retired TTL·재사용 거절, touch TTL 슬라이딩, broadcast 부분 실패
  전달(gather), **짠 핫패스 Redis 상태 I/O 0건(구조적)**. **pytest 51 passed, 회귀 무손상.**

**산출물:** `app/room_store.py`(재작성), `app/main.py`·`app/connection_manager.py`(수정),
`requirements.txt`·`requirements-dev.txt`·`pytest.ini`·`README.md`(갱신), `tests/conftest.py`·
`tests/test_broadcast.py`(신규), `tests/test_room_store.py`(재작성), `tests/test_close_expiry.py`·
`tests/test_cheers.py`·`tests/test_stability.py`(수정).

**마커:**
- `ASSUMPTION`: retired 코드 TTL(24h)·`REDIS_URL` 기본값·모니터 샘플링 주기(30s)·유휴 TTL 환산.
- `REVIEW(Redis 운영)`: 재시작 복구 보장하려면 Redis persistence(AOF/RDB) 활성화 필요 — 안
  켜면 내구성 의미 반감. Redis가 새 의존성이자 단일 박스의 보조 장애점(가용성은 단일 박스 수준).
- `REVIEW(만료 알림 경로)`: sweep 제거로 유휴 만료 시 연결 소켓에 room_closed(expired) 능동
  통지가 사라짐(TTL 수동 소멸). 통지 재설계는 별도 작업.
- `REVIEW(host_token URL 노출)`: 방장 식별 쿼리 파라미터 — 운영 로그 마스킹/첫 메시지 인증 검토.
- 기존 유지: `UNVERIFIED`(토스 WebView Origin), `REVIEW`(wss·백그라운드 소켓·남용 방지 IP 키).

---

## 누적 상태 (2026-06-24 기준)

- **구현 완료(1차 범위):** F-RT-01·03·04·05·06 필수 + F-RT-07 기본. 방장 명시적 leave 종료
  (F-RT-06) + 운영 내구성(Redis 상태 저장소·TTL 만료) + 단일 박스 튜닝.
- **테스트:** 51 passed (단위·통합·안정성·내구성). 자동 테스트는 fakeredis로 Redis 서버 불요.
- **미해소(머지·배포 금지):** 모든 검수 마커가 `ai:` 커밋 상태로 사람 검수(S4) 전. 실기기 의존
  4항목(Origin·지연·백그라운드·wss 재확인) + Redis 운영(persistence·가용성)·만료 통지 재설계.
- **다음 게이트:** 사람 실기기 검증 → S4 검수(마커 해소·UX 자연성) → S5 배포.
