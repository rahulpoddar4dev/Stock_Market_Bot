# Trading Bot Live Readiness Notes

Date: 2026-06-03  
Purpose: Personal reading notes before using the bot in live/auto mode.

## Current Mode

The dashboard runtime switch is currently set to:

- Trading mode: LIVE_AUTO
- Paper trade: false
- Live auto enabled: true
- Fresh analysis interval: 15 seconds
- Open trade monitoring interval: 1 second

This means the bot is prepared to place real orders if the Fyers session is active and all trade rules pass. Switch back to Paper from the dashboard if real orders should not be placed.

## What This Bot Does

The bot watches NIFTY and SENSEX, checks chart momentum, confirms the final signal through the decision engine, selects an index option, sizes the position, places a limit buy order, and then manages exits automatically.

It buys options only. A BUY signal means it buys CE. A SELL signal means it buys PE. It does not short-sell options.

## Market Analysis Rules

The bot analyzes:

- NIFTY and SENSEX 5-minute candles
- EMA 9 and EMA 21 trend
- VWAP position
- RSI
- ATR volatility
- ADX trend strength
- Setup score
- News sentiment as reference only
- Market depth before option entry

Current important thresholds:

- Minimum setup score: 8
- Minimum ADX: 18
- Breakout filter: disabled
- No fresh trade before: 09:25
- No fresh trade after: 15:00
- Square-off time: 15:20

News is enabled, but it is currently reference-only. News does not block trades unless the config is changed.

## Index Confirmation

The bot compares NIFTY and SENSEX.

Preferred behavior:

- If both indices support the same direction, the trade is stronger.
- If only one index gives a strong setup, single-index trade is allowed.
- Single-index trades may use reduced size depending on the rule path.
- Conflicting signals are avoided.

Current config:

- Require index confirmation: true
- Single-index trade allowed: true
- Single-index size percent: 50
- Single-index minimum setup score: 25
- Single-index minimum ADX: 25

## Option Selection

The bot selects index options automatically.

Current option selection mode:

- AUTO

In AUTO mode, the bot usually selects an ITM-style option based on signal and confidence. For BUY it chooses CE. For SELL it chooses PE.

## Entry Order Rules

The bot uses limit orders for entry.

Current entry order settings:

- Entry order type: LIMIT
- Limit buffer: 0.5%
- Market depth enabled: true
- Max bid/ask spread: 2%

The bot checks:

- Best bid
- Best ask
- Bid/ask spread
- Ask quantity
- Top depth sufficiency

If spread is too high or top ask quantity is not enough, the trade is blocked.

## Position Sizing And Fund Rules

Current fund/risk settings:

- Max fund usage: 60%
- Max single-trade fund usage: 60%
- Max risk per trade: Rs 2000
- Max daily loss: Rs 3000
- Max daily profit: Rs 4000
- Max daily stop losses: 2
- Max daily failed trades: 2
- Max open trades: 1

The bot calculates lots using:

- Available balance
- Option entry price
- Lot size
- Stop-loss distance
- Risk per lot
- Fund usage limit
- Risk limit

If one lot exceeds the risk or fund limits, the trade is blocked.

## Exit Strategy

Current exit logic:

At buy price example 100:

- T1: +12%, price 112
- T2: +21%, price 121
- T3: +30%, price 130
- Initial SL: -12%, price 88

When T1 hits:

- Partial quantity is sold
- SL moves to buy price +2%
- Example SL becomes 102

When T2 hits:

- Another partial quantity is sold
- SL moves to buy price +15%
- Example SL becomes 115

When T3 hits:

- All remaining quantity is sold

Partial exit settings:

- T1 partial exit: 40% of original quantity, adjusted by lot size
- T2 partial exit: 30% of original quantity, adjusted by lot size
- T3 exit: all remaining quantity

If only one lot is open, partial exit may not be possible. In that case, the bot manages the trade with SL and final exit rules.

Trailing stop is disabled so it does not interfere with this staged exit logic.

## Daily Risk Stop Rules

The bot stops opening fresh trades if daily risk limits are hit.

It can stop fresh trades after:

- 2 negative stop losses
- 2 failed trades
- Rs 3000 daily loss
- Rs 4000 daily profit

After risk is hit, the bot can still log/analyze signals, but it should not open fresh trades.

## Backtest Summary

The latest 10-year index-proxy backtest with the current staged exit logic showed:

- NIFTY trades: 11270
- NIFTY win rate: 50.03%
- NIFTY points: +1509195.58
- SENSEX trades: 11482
- SENSEX win rate: 50.19%
- SENSEX points: +5314966.96
- Combined win rate: about 50.11%

Daily behavior from the same test:

- Average trades per day: 9.29
- Daily profit ratio: 90.78%
- Average negative trades per day: 4.63
- Average positive trades per day: 4.65
- T1 hit average per day: 4.35
- T2 hit average per day: 0.44
- T3 hit average per day: 0.30

Important note: this is an index-candle proxy backtest, not a perfect real-option backtest. Live results can differ because of option premium movement, spread, slippage, liquidity, and order fill delay.

## What The Bot Learned From Past Data

The older target setup was too far. Many trades failed before reaching T1.

The improved setup moved T1 closer:

- Old style had win rate around 38%
- New T1 at +12% improved win rate to about 50%

The key lesson:

- Profit protection must start early.
- T1 should be close enough to hit often.
- After T1, the trade should not be allowed to become a negative trade.
- T2 should lock stronger profit.
- T3 should be treated as a bonus move, not the only profit plan.

## What To Watch During Live Trading

Watch these carefully today:

- Did the limit order fill?
- Was the selected option liquid?
- Was the spread acceptable?
- Did price reach T1?
- After T1, did SL move to +2%?
- Did partial sell happen at T1?
- After T2, did SL move to +15%?
- Did partial sell happen at T2?
- Did final exit happen at T3 or SL?
- Was final P/L positive or negative?

Win/loss should be judged by final P/L:

- P/L positive means win
- P/L negative means loss
- SL hit can still be counted as win if P/L is positive

## Go-Live Checklist

Before market:

- Fyers login/session is active
- Dashboard shows LIVE_AUTO only if real orders are intended
- Current fund is correct
- No old open trade is stuck
- Market depth data is visible
- Runtime mode is correct
- Internet connection is stable

During market:

- Do not manually interfere unless something looks wrong
- Watch first trade carefully
- Confirm entry order fills as expected
- Confirm partial exits happen correctly
- Stop the bot if order behavior does not match the dashboard

After market:

- Review trade history
- Check T1/T2/T3 hit counts
- Check negative SL count
- Check final P/L
- Compare real paper/live behavior with backtest expectation

## Personal Caution

The bot has improved based on historical testing, but live market behavior can be different. The first live day should be treated as validation, not as proof. The most important thing is to confirm that entry, partial exit, SL movement, and final exit all work correctly in real conditions.
