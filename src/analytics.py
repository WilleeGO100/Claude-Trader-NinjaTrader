"""
Analytics Module
Reads trades_taken.csv and backtest_results.json to produce a comprehensive
performance dashboard. Handles both old (3-column) and new (5-column) CSV formats.
"""

import csv
import json
import logging
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)


class Analytics:
    """Computes and displays trading performance metrics"""

    def __init__(
        self,
        trades_csv: str = "data/trades_taken.csv",
        backtest_json: str = "data/backtest_results.json",
    ):
        self.trades_csv = Path(trades_csv)
        self.backtest_json = Path(backtest_json)

    # ── Data loading ──────────────────────────────────────────────────────

    def load_live_trades(self) -> List[Dict]:
        """Load trades from trades_taken.csv (NinjaTrader output)"""
        if not self.trades_csv.exists():
            return []
        trades = []
        try:
            with open(self.trades_csv, newline='') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    trade = {'source': 'live'}
                    trade['datetime'] = row.get('DateTime', '')
                    trade['direction'] = row.get('Direction', '')
                    trade['entry'] = self._float(row.get('Entry_Price'))
                    trade['exit'] = self._float(row.get('Exit_Price'))      # new format
                    trade['pnl'] = self._float(row.get('PnL_Points'))       # new format
                    # Old format has no exit/pnl — skip incomplete records
                    if trade['entry'] is not None:
                        trades.append(trade)
        except Exception as e:
            logger.error(f"Error loading trades CSV: {e}")
        return trades

    def load_backtest_trades(self) -> List[Dict]:
        """Load individual trade records from backtest_results.json"""
        if not self.backtest_json.exists():
            return []
        try:
            with open(self.backtest_json) as f:
                data = json.load(f)
            raw = data.get('trades', [])
            trades = []
            for t in raw:
                trades.append({
                    'source': 'backtest',
                    'datetime': str(t.get('entry_datetime', '')),
                    'direction': t.get('direction', ''),
                    'entry': t.get('entry'),
                    'exit': t.get('exit_price'),
                    'pnl': t.get('profit_loss'),
                    'result': t.get('result'),
                    'setup_type': t.get('setup_type', ''),
                    'confidence': t.get('confidence', 0),
                    'bars_held': t.get('bars_held', 0),
                    'exit_reason': t.get('exit_reason', ''),
                })
            return trades
        except Exception as e:
            logger.error(f"Error loading backtest results: {e}")
            return []

    # ── Core metrics ──────────────────────────────────────────────────────

    def compute_metrics(self, trades: List[Dict]) -> Dict[str, Any]:
        """Compute full performance metrics for a list of trades"""
        completed = [t for t in trades if t.get('pnl') is not None]
        if not completed:
            return self._empty_metrics()

        pnls = [t['pnl'] for t in completed]
        results = [t.get('result', 'WIN' if t['pnl'] > 0.5 else ('LOSS' if t['pnl'] < -0.5 else 'BREAKEVEN'))
                   for t in completed]

        wins      = results.count('WIN')
        losses    = results.count('LOSS')
        breakeven = results.count('BREAKEVEN')
        total     = len(completed)

        win_pnls  = [p for p, r in zip(pnls, results) if r == 'WIN']
        loss_pnls = [p for p, r in zip(pnls, results) if r == 'LOSS']

        avg_win  = sum(win_pnls)  / len(win_pnls)  if win_pnls  else 0
        avg_loss = sum(loss_pnls) / len(loss_pnls) if loss_pnls else 0

        gross_profit = sum(win_pnls)
        gross_loss   = abs(sum(loss_pnls))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')

        # Equity curve and max drawdown
        equity = 0.0
        peak   = 0.0
        max_dd = 0.0
        for p in pnls:
            equity += p
            if equity > peak:
                peak = equity
            dd = peak - equity
            if dd > max_dd:
                max_dd = dd

        # Streaks
        max_win_streak  = self._max_streak(results, 'WIN')
        max_loss_streak = self._max_streak(results, 'LOSS')

        # By direction
        by_dir = self._group_by(completed, results, 'direction')

        # By hour (if datetime available)
        by_hour = self._group_by_hour(completed, results)

        # By setup type
        by_setup = self._group_by(completed, results, 'setup_type')

        return {
            'total_trades': total,
            'wins': wins,
            'losses': losses,
            'breakeven': breakeven,
            'win_rate': wins / (wins + losses) if (wins + losses) > 0 else 0.0,
            'total_pnl': sum(pnls),
            'avg_pnl': sum(pnls) / total,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'profit_factor': profit_factor,
            'max_drawdown': max_dd,
            'max_win_streak': max_win_streak,
            'max_loss_streak': max_loss_streak,
            'by_direction': by_dir,
            'by_hour': by_hour,
            'by_setup_type': by_setup,
        }

    def _group_by(self, trades, results, key) -> Dict:
        groups = defaultdict(lambda: {'trades': 0, 'wins': 0, 'losses': 0, 'pnl': 0.0})
        for t, r in zip(trades, results):
            g = t.get(key, 'unknown') or 'unknown'
            groups[g]['trades'] += 1
            groups[g]['pnl'] += t.get('pnl', 0) or 0
            if r == 'WIN':
                groups[g]['wins'] += 1
            elif r == 'LOSS':
                groups[g]['losses'] += 1
        out = {}
        for k, v in groups.items():
            denom = v['wins'] + v['losses']
            v['win_rate'] = v['wins'] / denom if denom > 0 else 0.0
            out[k] = v
        return out

    def _group_by_hour(self, trades, results) -> Dict:
        groups = defaultdict(lambda: {'trades': 0, 'wins': 0, 'losses': 0, 'pnl': 0.0})
        for t, r in zip(trades, results):
            try:
                dt = datetime.fromisoformat(str(t.get('datetime', '')).split('.')[0])
                hour = dt.hour
            except Exception:
                continue
            groups[hour]['trades'] += 1
            groups[hour]['pnl'] += t.get('pnl', 0) or 0
            if r == 'WIN':
                groups[hour]['wins'] += 1
            elif r == 'LOSS':
                groups[hour]['losses'] += 1
        out = {}
        for h, v in sorted(groups.items()):
            denom = v['wins'] + v['losses']
            v['win_rate'] = v['wins'] / denom if denom > 0 else 0.0
            out[h] = v
        return out

    def _max_streak(self, results: List[str], target: str) -> int:
        best = cur = 0
        for r in results:
            if r == target:
                cur += 1
                best = max(best, cur)
            else:
                cur = 0
        return best

    def _empty_metrics(self) -> Dict:
        return {
            'total_trades': 0, 'wins': 0, 'losses': 0, 'breakeven': 0,
            'win_rate': 0.0, 'total_pnl': 0.0, 'avg_pnl': 0.0,
            'avg_win': 0.0, 'avg_loss': 0.0, 'profit_factor': 0.0,
            'max_drawdown': 0.0, 'max_win_streak': 0, 'max_loss_streak': 0,
            'by_direction': {}, 'by_hour': {}, 'by_setup_type': {},
        }

    # ── Display ───────────────────────────────────────────────────────────

    def render_dashboard(self) -> str:
        live_trades     = self.load_live_trades()
        backtest_trades = self.load_backtest_trades()
        all_trades      = live_trades + backtest_trades

        lines = []
        lines.append("=" * 65)
        lines.append("          CLAUDE TRADER — PERFORMANCE DASHBOARD")
        lines.append("=" * 65)

        # Live section
        live_complete = [t for t in live_trades if t.get('pnl') is not None]
        if live_complete:
            m = self.compute_metrics(live_complete)
            lines.append("\n── LIVE TRADES ─────────────────────────────────────────────")
            lines += self._format_metrics(m)
        else:
            lines.append("\nLive trades: none with P&L data yet (need Phase 2 NinjaScript)")

        # Backtest section
        if backtest_trades:
            m = self.compute_metrics(backtest_trades)
            lines.append("\n── BACKTEST TRADES ──────────────────────────────────────────")
            lines += self._format_metrics(m)

            if m['by_setup_type']:
                lines.append("\n  By Setup Type:")
                for setup, s in m['by_setup_type'].items():
                    if s['trades'] > 0:
                        lines.append(
                            f"    {setup:<20} {s['trades']:>3} trades  "
                            f"{s['win_rate']:>5.1%} WR  {s['pnl']:>+8.2f}pts"
                        )

            if m['by_direction']:
                lines.append("\n  By Direction:")
                for d, s in m['by_direction'].items():
                    if s['trades'] > 0:
                        lines.append(
                            f"    {d:<8} {s['trades']:>3} trades  "
                            f"{s['win_rate']:>5.1%} WR  {s['pnl']:>+8.2f}pts"
                        )

            if m['by_hour']:
                lines.append("\n  Best Hours (ET):")
                sorted_hours = sorted(m['by_hour'].items(),
                                      key=lambda x: x[1]['pnl'], reverse=True)[:5]
                for h, s in sorted_hours:
                    lines.append(
                        f"    {h:02d}:00  {s['trades']:>3} trades  "
                        f"{s['win_rate']:>5.1%} WR  {s['pnl']:>+8.2f}pts"
                    )

        lines.append("\n" + "=" * 65)
        return "\n".join(lines)

    def _format_metrics(self, m: Dict) -> List[str]:
        pf = f"{m['profit_factor']:.2f}" if m['profit_factor'] != float('inf') else "∞"
        return [
            f"  Trades: {m['total_trades']}  |  "
            f"W: {m['wins']}  L: {m['losses']}  BE: {m['breakeven']}",
            f"  Win Rate:  {m['win_rate']:>6.1%}   |  Profit Factor: {pf}",
            f"  Total P&L: {m['total_pnl']:>+8.2f}pts  |  Avg/trade: {m['avg_pnl']:>+7.2f}pts",
            f"  Avg Win:   {m['avg_win']:>+8.2f}pts  |  Avg Loss:  {m['avg_loss']:>+7.2f}pts",
            f"  Max DD:    {m['max_drawdown']:>8.2f}pts  |  "
            f"Streaks W:{m['max_win_streak']} L:{m['max_loss_streak']}",
        ]

    @staticmethod
    def _float(val) -> Optional[float]:
        try:
            return float(val) if val not in (None, '', 'None') else None
        except (ValueError, TypeError):
            return None
