# K-Quant 연구 원칙

> 작성 2026-10-07. 이 프로젝트에서 "전략이 통했다"고 말하려면 무엇을 지켜야 하는지 정리했다.
> 지금까지 실제로 밟은 함정(복구 기획서 5장)과 퀀트 연구 방법론 문헌(맨 아래)을 합쳤다.
> 계산 도구는 gs-quant(Goldman Sachs, Apache-2.0) 시계열 정의를 따른다(`src/monitor/gsq.py`).
> gs-quant 는 계산 라이브러리일 뿐이고 투자 규칙을 주지 않는다. 규칙은 이 문서에서 나온다.

## 0. 한 줄 요약

**미리 적고, 한 번만 확인하고, 시도한 횟수만큼 깎고, 비용을 빼고, 실패도 남긴다.**

---

## 1. 데이터는 그 시점에 알 수 있던 것만

| 원칙 | 이 프로젝트에서 | 상태 |
|---|---|---|
| 생존편향 없는 유니버스 | 월초 시점별 시총 상위 200(`membership.py`), 상폐 종목 시세 포함(KRX 일별 스냅샷) | 지킴 |
| 공표 시차 | 수급 `lag=1`, 정규화 분모 `shift(1)` | 테스트 고정 |
| 수정주가와 총수익 구분 | 종목은 가격수익, KODEX200 시리즈는 분배 포함 총수익 → 같은 기준끼리만 비교 | 메모에 기록, 비교 시 주의 |
| 휴장일 NaN | 수익률 계산 전 `dropna(how="all")` | 테스트 고정 |

## 2. 누수는 테스트로 막는다

한 번 밟은 누수는 테스트 이름을 붙여 고정한다(기획서 5장 표). 새 피처나 라벨을 만들면
"t 시점 값이 t 이후 데이터를 쓰지 않는다"는 테스트를 **먼저** 쓴다.

- 겹치는 라벨(h일 수익률)로 학습·검증을 나눌 때는 경계 앞뒤 h일을 지운다
  (purging·embargo, López de Prado 2018 7장).
- 행 단위 부트스트랩 금지. 블록 부트스트랩, 블록 길이 = 지평 h.

## 3. 여러 번 시도했으면 그만큼 깎는다

같은 데이터로 여러 변형을 돌리면 아무 효과가 없어도 좋아 보이는 것이 반드시 나온다.

**3-1. 시도 기록부.** 돌린 변형은 성공·실패 모두 기록한다. 기록 위치는 `docs/prereg/`의
사전 등록 문서와 커밋 메시지다. "몇 개를 시도했는가"를 모르면 아래 보정을 할 수 없다.

**3-2. t 기준을 올린다.**
- 단일 가설 1.96 은 새 팩터에 부족하다. Harvey·Liu·Zhu(2016)는 새 팩터에 **t > 3.0** 을 권한다.
- 여러 개를 같이 보면 본페로니 기준을 쓴다: `overfit.bonferroni_t(n)`. 22개면 3.06, 16개면 2.96.

**3-3. 고른 전략의 샤프는 디플레이트한다.**
- `overfit.dsr(고른 전략 수익률, 시도한 모든 전략의 샤프)`: 시도 횟수와 샤프 분산으로 "우연히 나올
  최대 샤프"를 계산해 기준선으로 쓰는 PSR (Bailey & López de Prado 2014).
- DSR < 0.95 면 "선택 편향을 감안하면 우연과 구분되지 않는다"로 보고한다.
- 왜도·첨도도 반영된다. 꼬리가 두꺼운 전략일수록 같은 샤프라도 덜 믿는다.

**3-4. 다음 단계(미구현):** 백테스트 과최적화 확률 PBO(조합 대칭 교차검증, Bailey 외 2017),
여러 전략 동시 비교의 White(2000) Reality Check / Hansen(2005) SPA.

## 4. 검증 순서는 바꾸지 않는다

```
아이디어 → 사전 등록(규칙·기간·판정 기준을 커밋) → 인샘플 점검(진행 조건)
        → 홀드아웃 1회(--confirm, 잠금 파일) → 페이퍼 6개월 → 소액 → 확대
```

- 사전 등록 문서는 실행 전에 커밋하고 이후 수정하지 않는다. 바꾸려면 새 ID.
- 홀드아웃 결과를 보고 규칙·가중치를 고치지 않는다. 고치면 그 홀드아웃은 오염된 것이다.
- 인샘플 진행 조건을 통과하지 못하면 홀드아웃을 쓰지 않고 멈춘다(지금까지 capw_exvol_v1,
  voltarget_v1 등이 여기서 멈췄다. 정상이다).
- 검증 단위는 워크포워드. 판단 기준은 비교 대상(EW 유니버스·같은 기준 지수) 대비 NW t (lags=2h).

## 5. 비용과 체결을 먼저 뺀다

- 국내 개인 왕복 약 0.33%(매도세 0.20% 포함). 비교 대상에도 같은 비용을 뺀다.
- 신호는 t 종가, 체결은 t+1 (시가 또는 종가). 같은 날 체결 가정 금지.
- 회전율이 높은 이상현상은 비용 뒤에 대부분 사라진다(Novy-Marx & Velikov 2016). 회전율을 같이 보고한다.

## 6. 표본 밖에서는 약해진다고 가정한다

- 학계에 발표된 이상현상은 표본 밖에서 약 26%, 발표 뒤 약 58% 줄었다(McLean & Pontiff 2016).
- 그래서 인샘플 성과의 **절반 이하**를 기대치로 쓴다. 절반으로 깎아도 비용 뒤 이득이 없으면 진행하지 않는다.
- 한 국면(급등장, 급락장)에서만 나온 결과는 일반화하지 않는다. 연도별 IC 부호 일관성을 같이 본다.

## 7. 보고 규칙

- 실패한 시도도 같은 형식으로 남긴다(무엇을, 몇 개, 왜 멈췄나).
- 수치는 기준과 함께: "전략 +X%" 가 아니라 "같은 비용·같은 기준 지수 대비 +X%, t, DSR".
- 오염을 안다면 적는다(예: 홀드아웃 시장 흐름을 이미 알고 있음).

---

## 체크리스트 (새 전략을 돌리기 전)

- [ ] 규칙·기간·판정 기준을 `docs/prereg/` 에 쓰고 커밋했다
- [ ] 유니버스가 시점별이고 상폐 종목을 포함한다
- [ ] 모든 피처에 공표 시차가 있고 누수 테스트가 있다
- [ ] 비용을 전략과 비교 대상 양쪽에 뺐다
- [ ] 이 아이디어로 지금까지 시도한 변형 수를 안다 (→ 본페로니·DSR)
- [ ] 인샘플 진행 조건을 미리 정했다
- [ ] 홀드아웃은 아직 손대지 않았다

## 참고 문헌

- Arnott, R., Harvey, C. R., Markowitz, H. (2019). A Backtesting Protocol in the Era of Machine Learning. *Journal of Financial Data Science* 1(1).
- Bailey, D. H., López de Prado, M. (2012). The Sharpe Ratio Efficient Frontier. *Journal of Risk* 15(2). — PSR
- Bailey, D. H., López de Prado, M. (2014). The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting and Non-Normality. *Journal of Portfolio Management* 40(5).
- Bailey, D. H., Borwein, J., López de Prado, M., Zhu, Q. J. (2017). The Probability of Backtest Overfitting. *Journal of Computational Finance* 20(4).
- Hansen, P. R. (2005). A Test for Superior Predictive Ability. *Journal of Business & Economic Statistics* 23(4).
- Harvey, C. R., Liu, Y., Zhu, H. (2016). …and the Cross-Section of Expected Returns. *Review of Financial Studies* 29(1).
- López de Prado, M. (2018). *Advances in Financial Machine Learning*. Wiley.
- McLean, R. D., Pontiff, J. (2016). Does Academic Research Destroy Stock Return Predictability? *Journal of Finance* 71(1).
- Newey, W. K., West, K. D. (1987). A Simple, Positive Semi-Definite, Heteroskedasticity and Autocorrelation Consistent Covariance Matrix. *Econometrica* 55(3).
- Novy-Marx, R., Velikov, M. (2016). A Taxonomy of Anomalies and Their Trading Costs. *Review of Financial Studies* 29(1).
- White, H. (2000). A Reality Check for Data Snooping. *Econometrica* 68(5).
- Goldman Sachs, gs-quant (Apache-2.0): https://github.com/goldmansachs/gs-quant — 시계열 계산 정의의 기준
