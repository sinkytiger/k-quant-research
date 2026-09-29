# K-Quant-Daily

[![tests](https://github.com/sinkytiger/k-quant-research/actions/workflows/tests.yml/badge.svg)](https://github.com/sinkytiger/k-quant-research/actions/workflows/tests.yml)

생존편향 없는 한국 주식 퀀트 리서치 파이프라인. **공식 API만** 쓴다(KRX Open API, 한국투자증권 KIS Open API).
로그인 스크래핑은 쓰지 않는다.

## 데이터
| 데이터 | 출처 | 비고 |
|---|---|---|
| 일봉(수정주가)·시가총액·종목명 | KRX Open API 유가증권·코스닥 일별매매 | 하루 1회 호출로 그날 상장된 **전 종목**(이후 상폐 포함). 분할은 전일대비(수정 기준가)로 조정 |
| 벤치마크 | KRX Open API 지수·ETF 일별 | ETF 수정가는 분배금 포함(총수익), 지수는 가격 |
| 시점별 유니버스 | 위 시가총액으로 직접 구성 | 직전 거래일 시총 상위 200 보통주, 버퍼 220, 월초 스냅샷 |
| 투자자별 순매수 | KIS 종목별 투자자매매동향(일별) | 기준일 지정 30거래일씩 거꾸로 페이징해 10년. 상장 전 채움 행 제거 |
| 현재 지수 편입 | KIS 종목마스터 | KOSPI200 / KOSDAQ150 |

## 누수 방지 규칙 (테스트로 고정)
- 유동성 필터·수급 정규화 분모는 `shift(1)`, 수급 공표 시차 `lag=1`
- 유니버스는 그 날짜 이전 스냅샷만, 라벨 전에 적용 (`load_panel → restrict_universe → attach_labels`)
- 휴장일 전 종목 NaN 행은 수익률 전에 제거, winsorize 는 expanding
- 백테스트는 t 신호 → t+1 종가 체결, 비대칭 비용(매도세 0.20%)
- 중첩 수익률 t 는 Newey-West(lag 2h), 연율화는 ×(252/h)
- 홀드아웃은 사전 등록(`docs/prereg/`) 후 `--confirm` 한 번

## 실행
```bat
python -m pip install -r requirements.txt
copy .env.example .env            & REM 키 입력
python scripts\collect_universe.py --check
python scripts\collect_universe.py --backfill --period 11y --markets stk ksq
python scripts\collect_flows.py --backfill --years 11
python scripts\run_ic.py
python scripts\run_backtest.py
python -m pytest
```
운영: `run_daily.bat`(증분 수집 + 페이퍼 NAV), `run_weekly.bat`, `run_monthly.bat`,
작업 스케줄러 등록 `scripts\setup_schedule.ps1`.

## 현재 결론 (2026-09-28, 인샘플 2016~2025-09)
- `low_vol` IC NW t +5.3(5일), 시총 중립화 후에도 유지. 다만 가장 변동성 높은 분위가 망하는 데서 나온 신호라
  롱온리 상위 선택으로는 수익화되지 않았다(사전 고정 4개 변형 모두 동일가중 대비 음수).
- 시총가중 − 고변동 20% 제외(`capw_exvol_v1`)도 인샘플 사전 점검 불통과 → 폐기, 홀드아웃 미사용.
- 권고: 코어 ETF 보유, 위성 0%. 페이퍼 트래킹은 `paper/portfolios.json`.
