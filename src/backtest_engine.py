"""
Backtest Engine Module
Tests trading strategy on historical data
"""

import pandas as pd
import logging
import json
from typing import Dict, List, Optional, Any
from datetime import datetime
from pathlib import Path

from .fvg_analyzer import FVGAnalyzer
from .level_detector import LevelDetector
from .trading_agent import TradingAgent
from .memory_manager import MemoryManager
from .session_filter import SessionFilter
from .news_filter import NewsFilter
from .position_sizer import PositionSizer
from .htf_analyzer import HTFAnalyzer

logger = logging.getLogger(__name__)


class BacktestEngine:
    """Backtests trading strategy on historical data"""

    def __init__(
        self,
        config: Dict[str, Any],
        historical_data_path: str = "data/HistoricalData.csv"
    ):
        """
        Initialize Backtest Engine

        Args:
            config: Configuration dictionary
            historical_data_path: Path to historical OHLC data
        """
        self.config = config
        self.historical_data_path = Path(historical_data_path)

        # Initialize components
        self.fvg_analyzer = FVGAnalyzer(
            min_gap_size=config['trading_params']['min_gap_size'],
            max_gap_age=config['trading_params']['max_gap_age_bars']
        )
        self.level_detector = LevelDetector(
            level_intervals=config['levels']['psychological_intervals']
        )
        self.memory_manager = MemoryManager()
        self.session_filter = SessionFilter(config)
        self.news_filter = NewsFilter(config)
        self.position_sizer = PositionSizer(config)
        self.htf_analyzer = HTFAnalyzer()

        # Note: TradingAgent requires API key, will initialize in run()

        # Backtest results
        self.trades = []
        self.current_position = None

        logger.info(f"BacktestEngine initialized")

    def load_historical_data(self, days: Optional[int] = None) -> pd.DataFrame:
        """
        Load historical OHLC data

        Args:
            days: Number of days to load (None = all)

        Returns:
            DataFrame with historical data
        """
        logger.info(f"Loading historical data from {self.historical_data_path}")

        if not self.historical_data_path.exists():
            raise FileNotFoundError(
                f"HistoricalData.csv not found at {self.historical_data_path}. "
                "Apply SecondHistoricalData.cs to an NQ chart in NinjaTrader first."
            )

        df = pd.read_csv(self.historical_data_path)

        required_cols = ['DateTime', 'Open', 'High', 'Low', 'Close']
        missing = [c for c in required_cols if c not in df.columns]
        if missing:
            raise ValueError(f"HistoricalData.csv missing columns: {missing}")

        df['DateTime'] = pd.to_datetime(df['DateTime'], errors='coerce')
        df = df.dropna(subset=['DateTime', 'Close'])
        df = df.sort_values('DateTime').reset_index(drop=True)

        if len(df) < 50:
            raise ValueError(f"Only {len(df)} bars in historical data — need at least 50")

        if days:
            bars_per_day = self.config.get('timeframe', {}).get('bars_per_day', 24)
            bars_to_load = days * bars_per_day
            df = df.tail(bars_to_load)
            logger.info(f"Loaded last {days} days ({len(df)} bars)")
        else:
            logger.info(f"Loaded all historical data ({len(df)} bars)")

        return df

    def detect_fvgs_historical(self, df: pd.DataFrame) -> List[Dict]:
        """
        Detect FVGs in historical data

        Args:
            df: Historical OHLC DataFrame

        Returns:
            List of FVG dictionaries with bar indices
        """
        fvgs = []

        for i in range(2, len(df)):
            candle1 = df.iloc[i - 2]
            candle2 = df.iloc[i - 1]
            candle3 = df.iloc[i]

            # Bullish FVG
            if candle3['Low'] > candle1['High']:
                gap_size = candle3['Low'] - candle1['High']
                if gap_size >= self.config['trading_params']['min_gap_size']:
                    fvgs.append({
                        'type': 'bullish',
                        'top': candle3['Low'],
                        'bottom': candle1['High'],
                        'gap_size': gap_size,
                        'datetime': candle3['DateTime'],
                        'index': i,
                        'filled': False,
                        'age_bars': 0
                    })

            # Bearish FVG
            elif candle3['High'] < candle1['Low']:
                gap_size = candle1['Low'] - candle3['High']
                if gap_size >= self.config['trading_params']['min_gap_size']:
                    fvgs.append({
                        'type': 'bearish',
                        'top': candle1['Low'],
                        'bottom': candle3['High'],
                        'gap_size': gap_size,
                        'datetime': candle3['DateTime'],
                        'index': i,
                        'filled': False,
                        'age_bars': 0
                    })

        logger.info(f"Detected {len(fvgs)} FVGs in historical data")
        return fvgs

    def update_fvg_status(self, fvgs: List[Dict], current_bar: pd.Series, current_index: int):
        """
        Update FVG filled status and age

        Args:
            fvgs: List of FVG dictionaries
            current_bar: Current bar data
            current_index: Current bar index
        """
        for fvg in fvgs:
            if fvg['filled']:
                continue

            # Don't process FVGs that haven't been created yet
            if fvg['index'] > current_index:
                continue

            # Update age
            fvg['age_bars'] = current_index - fvg['index']

            # Bullish FVG (gap up, zone below price): fully filled when price passes down through the bottom
            if fvg['type'] == 'bullish' and current_bar['Low'] <= fvg['bottom']:
                fvg['filled'] = True
            # Bearish FVG (gap down, zone above price): fully filled when price passes up through the top
            elif fvg['type'] == 'bearish' and current_bar['High'] >= fvg['top']:
                fvg['filled'] = True

    def get_active_fvgs(self, fvgs: List[Dict], current_index: int) -> List[Dict]:
        """
        Get active (unfilled, not too old) FVGs

        Args:
            fvgs: List of all FVGs
            current_index: Current bar index

        Returns:
            List of active FVGs
        """
        active = []
        max_age = self.config['trading_params']['max_gap_age_bars']

        for fvg in fvgs:
            if fvg['filled']:
                continue
            if fvg['index'] > current_index:  # FVG from future
                continue
            if fvg['age_bars'] > max_age:
                continue

            active.append(fvg)

        return active

    def check_exit_conditions(self, position: Dict, current_bar: pd.Series) -> Optional[Dict]:
        """
        Check if position should be exited. Simulates partial exits and
        breakeven stop movement when scale1 is hit.

        Returns exit data if fully closed, None otherwise.
        Mutates position dict in place for scale1 and stop updates.
        """
        entry = position['entry']
        stop = position['stop']
        target = position['target']
        direction = position['direction']
        scale1_price = position.get('scale1_price', 0)
        scale1_hit = position.get('scale1_hit', False)

        if direction == 'LONG':
            # Check if scale1 hit (and not already processed)
            if scale1_price and not scale1_hit and current_bar['High'] >= scale1_price:
                position['scale1_hit'] = True
                # Move stop to breakeven
                position['stop'] = entry
                stop = entry
                logger.info(f"Scale1 hit @ {scale1_price:.2f} — stop moved to breakeven {entry:.2f}")

            if current_bar['Low'] <= stop:
                reason = 'breakeven' if scale1_hit else 'stop_loss'
                result = 'BREAKEVEN' if scale1_hit else 'LOSS'
                return {'exit_price': stop, 'exit_reason': reason, 'result': result}

            elif current_bar['High'] >= target:
                return {'exit_price': target, 'exit_reason': 'target_hit', 'result': 'WIN'}

        elif direction == 'SHORT':
            if scale1_price and not scale1_hit and current_bar['Low'] <= scale1_price:
                position['scale1_hit'] = True
                position['stop'] = entry
                stop = entry
                logger.info(f"Scale1 hit @ {scale1_price:.2f} — stop moved to breakeven {entry:.2f}")

            if current_bar['High'] >= stop:
                reason = 'breakeven' if scale1_hit else 'stop_loss'
                result = 'BREAKEVEN' if scale1_hit else 'LOSS'
                return {'exit_price': stop, 'exit_reason': reason, 'result': result}

            elif current_bar['Low'] <= target:
                return {'exit_price': target, 'exit_reason': 'target_hit', 'result': 'WIN'}

        return None

    def run_backtest(
        self,
        days: Optional[int] = None,
        use_claude: bool = True,
        api_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Run backtest on historical data

        Args:
            days: Number of days to backtest (None = all)
            use_claude: Use Claude for decisions (False = simple logic for testing)
            api_key: Anthropic API key (required if use_claude=True)

        Returns:
            Backtest results dictionary
        """
        logger.info(f"Starting backtest (days={days}, use_claude={use_claude})")

        # Load data
        df = self.load_historical_data(days)

        # Detect all FVGs
        all_fvgs = self.detect_fvgs_historical(df)

        # Initialize Claude agent if needed
        trading_agent = None
        if use_claude:
            if not api_key:
                raise ValueError("API key required for Claude-based backtest")
            trading_agent = TradingAgent(self.config, api_key=api_key)

        # Build 4H HTF context from 1H data for backtest
        df_4h = self.htf_analyzer.resample_from_1h(df)
        if df_4h is not None:
            logger.info(f"HTF context: {len(df_4h)} 4H bars resampled")
        else:
            logger.info("HTF context unavailable (not enough 1H bars)")

        # Track trades
        trades = []
        current_position = None
        bars_in_position = 0
        previous_analysis = ""  # Carry context between bars like live mode does

        # Iterate through bars
        for i in range(3, len(df)):  # Start at bar 3 (need history for FVG detection)
            current_bar = df.iloc[i]
            current_price = current_bar['Close']
            current_index = i

            # Update FVG status
            self.update_fvg_status(all_fvgs, current_bar, current_index)

            # Get active FVGs
            active_fvgs = self.get_active_fvgs(all_fvgs, current_index)

            # Check if in position
            if current_position:
                bars_in_position += 1

                # Check exit conditions
                exit_data = self.check_exit_conditions(current_position, current_bar)

                if exit_data:
                    # Close position
                    profit_loss = (exit_data['exit_price'] - current_position['entry']) * \
                                  (-1 if current_position['direction'] == 'SHORT' else 1)

                    risk = abs(current_position['entry'] - current_position['stop'])
                    rr_achieved = abs(profit_loss) / risk if risk > 0 else 0

                    if profit_loss < -0.5:  # Small buffer for slippage
                        exit_data['result'] = 'LOSS'
                    elif profit_loss > 0.5:
                        exit_data['result'] = 'WIN'
                    else:
                        exit_data['result'] = 'BREAKEVEN'

                    trade_record = {
                        **current_position,
                        'exit_bar': i,
                        'exit_datetime': current_bar['DateTime'],
                        'exit_price': exit_data['exit_price'],
                        'exit_reason': exit_data['exit_reason'],
                        'result': exit_data['result'],
                        'profit_loss': profit_loss,
                        'risk_reward_achieved': rr_achieved if exit_data['result'] == 'WIN' else -1.0,
                        'bars_held': bars_in_position
                    }

                    trades.append(trade_record)
                    logger.info(f"Trade closed: {exit_data['result']} - P/L: {profit_loss:+.2f}")

                    current_position = None
                    bars_in_position = 0

                continue  # Skip signal generation while in position

            # Not in position - look for setups
            if not active_fvgs:
                continue

            # News filter only in backtest (session filter skipped — bar timestamps
            # from NinjaTrader are in local/exchange time, not ET, so it would
            # block most bars incorrectly. Enable via config if needed.)
            if self.config.get('session_rules', {}).get('apply_in_backtest', False):
                bar_dt = current_bar['DateTime'].to_pydatetime() if hasattr(current_bar['DateTime'], 'to_pydatetime') else current_bar['DateTime']
                session_ok, _ = self.session_filter.is_trading_allowed(bar_dt)
                if not session_ok:
                    continue

            # Log active FVGs every 50 bars
            if i % 50 == 0:
                logger.info(f"Bar {i}: {len(active_fvgs)} active FVGs")

            # Analyze market context
            fvg_context = self.fvg_analyzer.analyze_market_context(current_price, active_fvgs)

            # Extract market indicators from current bar
            market_data = {
                'ema21': current_bar.get('EMA21', 0),
                'ema75': current_bar.get('EMA75', 0),
                'ema150': current_bar.get('EMA150', 0),
                'stochastic': current_bar.get('StochD', 50)
            }

            # Check for trade signals
            if use_claude and trading_agent:
                memory_context = self.memory_manager.get_memory_context()

                # HTF bias for this bar
                htf_context = None
                if df_4h is not None:
                    bar_dt = current_bar['DateTime']
                    if hasattr(bar_dt, 'to_pydatetime'):
                        bar_dt = bar_dt.to_pydatetime()
                    htf_bias = self.htf_analyzer.get_bias_at_bar(df_4h, bar_dt)
                    htf_context = f"4H HIGHER TIMEFRAME BIAS: {htf_bias.upper()}\n"

                result = trading_agent.analyze_setup(fvg_context, market_data, memory_context, previous_analysis, htf_context)

                # Update previous analysis context for next bar
                if result['success'] and result['decision']:
                    decision_data = result['decision']
                    previous_analysis = (
                        f"PREVIOUS BAR ANALYSIS:\n"
                        f"Bias: {decision_data.get('overall_bias', 'neutral').upper()}\n"
                        f"Waiting for: {decision_data.get('waiting_for', 'N/A')}\n"
                        f"Long status: {decision_data.get('long_assessment', {}).get('status', 'none').upper()}\n"
                        f"Short status: {decision_data.get('short_assessment', {}).get('status', 'none').upper()}\n"
                        f"Reasoning: {decision_data.get('overall_reasoning', '')[:300]}"
                    )

                min_age = self.config.get('trading_params', {}).get('min_setup_age_bars', 2)
                if result['success'] and result['decision'].get('primary_decision', 'NONE') != 'NONE':
                    decision = result['decision']
                    primary = decision['primary_decision']
                    chosen_assessment = decision.get('long_assessment' if primary == 'LONG' else 'short_assessment', {})
                    setup_age = chosen_assessment.get('setup_age_bars', 0)
                    if setup_age < min_age:
                        logger.info(f"Bar {i}: {primary} setup too new ({setup_age} bars < {min_age} min) — waiting")
                        continue
                    chosen_setup = decision['long_setup'] if primary == 'LONG' else decision['short_setup']
                    confidence = chosen_setup.get('confidence', 0.65)
                    sizing = self.position_sizer.compute_trade_sizing(
                        direction=primary,
                        entry=chosen_setup['entry'],
                        stop=chosen_setup['stop'],
                        target=chosen_setup['target'],
                        confidence=confidence
                    )
                    current_position = {
                        'trade_id': f"{current_bar['DateTime']}",
                        'entry_bar': i,
                        'entry_datetime': current_bar['DateTime'],
                        'direction': primary,
                        'entry': chosen_setup['entry'],
                        'stop': chosen_setup['stop'],
                        'target': chosen_setup['target'],
                        'setup_type': 'fvg_only',
                        'confidence': confidence,
                        'reasoning': decision.get('overall_reasoning', ''),
                        'contracts': sizing['contracts'],
                        'scale1_price': sizing['scale1_price'],
                        'scale1_contracts': sizing['scale1_contracts'],
                        'trail_points': sizing['trail_points'],
                        'scale1_hit': False,
                    }
                    logger.info(f"Position opened: {primary} @ {chosen_setup['entry']:.2f} ({sizing['contracts']}c)")
                    previous_analysis = ""  # Reset context after trade entry

            else:
                # Simple logic for testing (without Claude)
                # Take trades based on FVG + EMA alignment
                ema21 = market_data['ema21']
                ema75 = market_data['ema75']

                trade_taken = False

                # Uptrend + bullish FVG above = LONG
                if ema21 > ema75 and fvg_context.get('nearest_bullish_fvg'):
                    fvg = fvg_context['nearest_bullish_fvg']
                    if abs(current_price - fvg['bottom']) < 100:  # Within 100pts
                        entry = current_price
                        stop = entry - 20
                        target = fvg['top']
                        trade_taken = True
                        logger.info(f"Bar {i}: LONG entry - EMA uptrend + bullish FVG target")

                        current_position = {
                            'trade_id': f"{current_bar['DateTime']}",
                            'entry_bar': i,
                            'entry_datetime': current_bar['DateTime'],
                            'direction': 'LONG',
                            'entry': entry,
                            'stop': stop,
                            'target': target,
                            'setup_type': 'fvg_ema_aligned',
                            'confidence': 0.6,
                            'reasoning': 'Uptrend + bullish FVG above'
                        }

                # Downtrend + bearish FVG below = SHORT
                if not trade_taken and ema21 < ema75 and fvg_context.get('nearest_bearish_fvg'):
                    fvg = fvg_context['nearest_bearish_fvg']
                    if abs(current_price - fvg['top']) < 100:  # Within 100pts
                        entry = current_price
                        stop = entry + 20
                        target = fvg['bottom']
                        logger.info(f"Bar {i}: SHORT entry - EMA downtrend + bearish FVG target")

                        current_position = {
                            'trade_id': f"{current_bar['DateTime']}",
                            'entry_bar': i,
                            'entry_datetime': current_bar['DateTime'],
                            'direction': 'SHORT',
                            'entry': entry,
                            'stop': stop,
                            'target': target,
                            'setup_type': 'fvg_ema_aligned',
                            'confidence': 0.6,
                            'reasoning': 'Downtrend + bearish FVG below'
                        }

        # Calculate statistics
        results = self.calculate_backtest_stats(trades, df)
        results['trades'] = trades
        results['total_bars'] = len(df)
        results['backtest_period'] = f"{df.iloc[0]['DateTime']} to {df.iloc[-1]['DateTime']}"

        logger.info(f"Backtest complete: {results['total_trades']} trades, "
                   f"{results['win_rate']:.1%} win rate")

        return results

    def calculate_backtest_stats(self, trades: List[Dict], df: pd.DataFrame) -> Dict[str, Any]:
        """
        Calculate backtest performance statistics

        Args:
            trades: List of trade dictionaries
            df: Historical data DataFrame

        Returns:
            Statistics dictionary
        """
        if not trades:
            return {
                'total_trades': 0,
                'wins': 0,
                'losses': 0,
                'breakeven': 0,
                'win_rate': 0.0,
                'total_pnl': 0.0,
                'avg_pnl': 0.0,
                'max_win': 0.0,
                'max_loss': 0.0,
                'avg_bars_held': 0.0
            }

        wins = sum(1 for t in trades if t['result'] == 'WIN')
        losses = sum(1 for t in trades if t['result'] == 'LOSS')
        breakeven = sum(1 for t in trades if t['result'] == 'BREAKEVEN')

        pnls = [t['profit_loss'] for t in trades]
        total_pnl = sum(pnls)
        avg_pnl = total_pnl / len(trades)

        bars_held = [t['bars_held'] for t in trades]
        avg_bars = sum(bars_held) / len(trades)

        # By setup type — dynamic, covers all types seen in this run
        by_type = {}
        all_setup_types = set(t.get('setup_type', 'unknown') for t in trades)
        for setup_type in all_setup_types:
            type_trades = [t for t in trades if t.get('setup_type') == setup_type]
            if type_trades:
                type_wins = sum(1 for t in type_trades if t['result'] == 'WIN')
                type_losses = sum(1 for t in type_trades if t['result'] == 'LOSS')
                type_pnl = sum(t['profit_loss'] for t in type_trades)
                by_type[setup_type] = {
                    'trades': len(type_trades),
                    'wins': type_wins,
                    'losses': type_losses,
                    'win_rate': type_wins / (type_wins + type_losses) if (type_wins + type_losses) > 0 else 0.0,
                    'total_pnl': type_pnl,
                    'avg_pnl': type_pnl / len(type_trades)
                }

        return {
            'total_trades': len(trades),
            'wins': wins,
            'losses': losses,
            'breakeven': breakeven,
            'win_rate': wins / (wins + losses) if (wins + losses) > 0 else 0.0,
            'total_pnl': total_pnl,
            'avg_pnl': avg_pnl,
            'max_win': max(pnls) if pnls else 0.0,
            'max_loss': min(pnls) if pnls else 0.0,
            'avg_bars_held': avg_bars,
            'by_setup_type': by_type
        }

    def export_results(self, results: Dict[str, Any], output_file: str = "backtest_results.json"):
        """
        Export backtest results to JSON

        Args:
            results: Results dictionary
            output_file: Output file path
        """
        output_path = Path("data") / output_file

        # Convert datetime objects to strings
        results_copy = json.loads(json.dumps(results, default=str))

        with open(output_path, 'w') as f:
            json.dump(results_copy, f, indent=2)

        logger.info(f"Results exported to {output_path}")


# Example usage
if __name__ == "__main__":
    import json

    logging.basicConfig(level=logging.INFO)

    # Load config
    with open('config/agent_config.json', 'r') as f:
        config = json.load(f)

    # Run backtest (without Claude for testing)
    engine = BacktestEngine(config)
    results = engine.run_backtest(days=30, use_claude=False)

    print(json.dumps({k: v for k, v in results.items() if k != 'trades'}, indent=2))
    engine.export_results(results)
