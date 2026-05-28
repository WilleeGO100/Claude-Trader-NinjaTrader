"""
Claude NQ Trading Agent - Main Orchestrator
Coordinates all system components for live trading, backtesting, and monitoring
"""

import argparse
import json
import logging
import sys
import time
import os
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

# Import modules
from src.fvg_analyzer import FVGAnalyzer
from src.level_detector import LevelDetector
from src.trading_agent import TradingAgent
from src.memory_manager import MemoryManager
from src.signal_generator import SignalGenerator
from src.backtest_engine import BacktestEngine
from src.market_analysis_manager import MarketAnalysisManager
from src.session_filter import SessionFilter
from src.news_filter import NewsFilter
from src.news_scanner import NewsScanner
from src.position_sizer import PositionSizer
from src.analytics import Analytics
from src.htf_analyzer import CombinedHTFAnalyzer
from src.live_trend_monitor import LiveTrendMonitor
from src.intrabar_watchdog import IntrabarWatchdog
from src.trade_notifier import TradeNotifier
from src.setup_quality import compute_setup_quality
from src.gamma_level_loader import GammaLevelLoader
from src.order_flow_reader import OrderFlowReader
from src.dom_analyzer import DOMAnalyzer
from src.gexbot_feed import GexbotFeed
from src.setup_detector import SetupDetector
from src.ha_trend import HATrendDetector

# Load environment variables
load_dotenv()

# Suppress noisy httpx request logs from Groq/Anthropic
logging.getLogger("httpx").setLevel(logging.WARNING)

# Configure logging
def setup_logging(log_level: str = "INFO", log_file: str = None):
    """Setup logging configuration"""
    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

    # Configure stdout handler with UTF-8 encoding for Windows
    import io
    stdout_handler = logging.StreamHandler(io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace'))
    handlers = [stdout_handler]

    if log_file:
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)
        # File handler with UTF-8 encoding
        handlers.append(logging.FileHandler(log_dir / log_file, encoding='utf-8', errors='replace'))

    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format=log_format,
        handlers=handlers
    )

logger = logging.getLogger(__name__)


class TradingOrchestrator:
    """Main orchestrator for trading system"""

    def __init__(self, config_path: str = "config/agent_config.json"):
        """
        Initialize Trading Orchestrator

        Args:
            config_path: Path to configuration file
        """
        # Load configuration
        with open(config_path, 'r') as f:
            self.config = json.load(f)

        # Setup logging
        log_level = self.config.get('logging', {}).get('level', 'INFO')
        log_file = self.config.get('logging', {}).get('log_file', 'trading_agent.log')
        setup_logging(log_level, log_file)

        # Initializing silently
        pass

        # Initialize components
        self.fvg_analyzer = FVGAnalyzer(
            min_gap_size=self.config['trading_params']['min_gap_size'],
            max_gap_age=self.config['trading_params']['max_gap_age_bars']
        )

        self.level_detector = LevelDetector(
            level_intervals=self.config['levels']['psychological_intervals']
        )

        self.memory_manager = MemoryManager()
        self.signal_generator = SignalGenerator()
        self.analysis_manager = MarketAnalysisManager()
        self.session_filter = SessionFilter(self.config)
        self.news_filter = NewsFilter(self.config)
        self.position_sizer = PositionSizer(self.config)
        self.htf_analyzer   = CombinedHTFAnalyzer()
        self.live_trend     = LiveTrendMonitor()
        self.watchdog       = IntrabarWatchdog(self.config, self.signal_generator)
        self.notifier      = TradeNotifier()
        self.gamma         = GammaLevelLoader()
        self.order_flow    = OrderFlowReader()
        self.dom           = DOMAnalyzer()
        self.gexbot        = GexbotFeed(gamma_loader=self.gamma)
        self.gexbot.start()
        self.setup_detector = SetupDetector()
        self.ha_trend       = HATrendDetector()

        # Scan for upcoming news events at startup
        try:
            scanner = NewsScanner()
            scanner.refresh(days_ahead=3)
        except Exception as e:
            logger.warning(f"News scanner failed: {e}")

        # Clear stale analysis state on startup — delete file AND reset in-memory state
        analysis_file = Path("data/market_analysis.json")
        if analysis_file.exists():
            analysis_file.unlink()
        self.analysis_manager.current_analysis = self.analysis_manager._get_empty_analysis()
        logger.info("Cleared stale market analysis state on startup")

        # Initialize completed trade count after all components ready
        self._completed_trade_count = self._count_completed_trades()

        # Trading agent (requires API key)
        api_key = os.getenv('ANTHROPIC_API_KEY')
        if api_key:
            self.trading_agent = TradingAgent(self.config, api_key=api_key)
        else:
            self.trading_agent = None
            logger.warning("No API key found - trading agent not initialized")

        # State tracking
        self.daily_trades = 0
        self.daily_pnl = 0.0
        self.consecutive_losses = 0
        self.trading_paused = False

        # Open position tracking — must be set before anything checks it
        self.open_position = None
        self._completed_trade_count = 0

        # Initialization complete

    def check_risk_limits(self) -> tuple[bool, str]:
        """
        Check if risk management limits allow trading

        Returns:
            Tuple of (can_trade, reason)
        """
        max_daily_trades = self.config['risk_management']['max_daily_trades']
        max_daily_loss = self.config['risk_management']['max_daily_loss']
        max_consecutive_losses = self.config['risk_management']['max_consecutive_losses']

        if self.trading_paused:
            return False, "Trading is paused (manual intervention required)"

        if self.daily_trades >= max_daily_trades:
            return False, f"Daily trade limit reached ({max_daily_trades})"

        if abs(self.daily_pnl) >= max_daily_loss and self.daily_pnl < 0:
            return False, f"Daily loss limit reached ({max_daily_loss} points)"

        if self.consecutive_losses >= max_consecutive_losses:
            return False, f"Consecutive loss limit reached ({max_consecutive_losses})"

        return True, ""

    def _count_completed_trades(self) -> int:
        """Count completed trades in trades_taken.csv"""
        try:
            import csv as csv_mod
            with open('data/trades_taken.csv', 'r') as f:
                rows = list(csv_mod.DictReader(f))
            return sum(1 for r in rows if r.get('Exit_Price', '').strip() not in ('', 'None', '0.00'))
        except Exception:
            return 0

    def _check_position_closed(self) -> bool:
        """Returns True if a new exit appeared in trades_taken.csv since we opened"""
        current_count = self._count_completed_trades()
        if current_count > self._completed_trade_count:
            self._completed_trade_count = current_count
            return True
        return False

    def run_live_mode(self):
        """Run in live trading mode"""
        # Starting live mode silently

        if not self.trading_agent:
            logger.error("Trading agent not initialized - API key required")
            return

        # Import FairValueGaps display to access its state
        import sys
        import pandas as pd
        import os
        sys.path.insert(0, str(Path.cwd()))
        from FairValueGaps import FVGDisplay

        # Create FVG display instance (but don't run its main loop)
        min_gap_size = self.config.get('trading_params', {}).get('min_gap_size', 5.0)
        fvg_display = FVGDisplay(min_gap_size=min_gap_size)

        # Load historical FVGs
        fvg_display.load_historical_fvgs()

        logger.info(f"Loaded {len(fvg_display.active_fvgs)} active FVGs")
        logger.info("="*60)

        # Start intrabar watchdog (background thread)
        def on_watchdog_entry(position):
            self.open_position = position
            self.daily_trades += 1
            self._completed_trade_count = self._count_completed_trades()
            logger.info(f"Watchdog fired entry — position tracking active")
            risk   = abs(position['entry'] - position['stop'])
            reward = abs(position['target'] - position['entry'])
            rr     = reward / risk if risk > 0 else 0
            self.notifier.on_entry(
                direction=position['direction'],
                entry=position['entry'],
                stop=position['stop'],
                target=position['target'],
                confidence=position.get('confidence', 0.65),
                setup_type='watchdog',
                source='Groq Watchdog',
                contracts=1,
            )
            self.notifier.on_watchdog_fire(position['direction'], position['entry'], rr)

        self.watchdog.on_entry_fired = on_watchdog_entry
        self.watchdog.start()
        self.live_trend.start()

        # Track last processed bar and result
        last_bar_time = None
        last_result = None

        try:
            while True:
                # Reload historical data to check for updates
                historical_df = pd.read_csv('data/HistoricalData.csv')
                historical_df['DateTime'] = pd.to_datetime(historical_df['DateTime'])

                # Get latest bar timestamp
                current_bar_time = historical_df.iloc[-1]['DateTime']

                # Check if new bar arrived
                if current_bar_time != last_bar_time:
                    # NEW BAR DETECTED - Run full analysis
                    logger.info(f"\n{'='*60}")
                    logger.info(f"NEW BAR: {current_bar_time}")
                    logger.info(f"{'='*60}")

                    # Update last processed time
                    last_bar_time = current_bar_time

                    # Check if open position was closed by NinjaTrader
                    if self.open_position and self._check_position_closed():
                        logger.info("Position closed detected — resetting position state")
                        closed = self.open_position
                        self.open_position = None
                        # Read exit details from trades_taken.csv
                        try:
                            import csv as _csv
                            with open('data/trades_taken.csv') as f:
                                rows = list(_csv.DictReader(f))
                            last = rows[-1] if rows else {}
                            exit_price = float(last.get('Exit_Price', 0) or 0)
                            pnl        = float(last.get('PnL_Points', 0) or 0)
                            self.notifier.on_exit(
                                direction=closed['direction'],
                                entry=closed['entry'],
                                exit_price=exit_price or closed['entry'],
                                pnl=pnl or None,
                                exit_reason='NinjaTrader exit',
                                bars_held=closed.get('bars_in_trade', 0),
                            )
                        except Exception:
                            pass

                    # Increment bars-in-trade counter
                    if self.open_position:
                        self.open_position['bars_in_trade'] += 1

                    # Sync watchdog with current position state and new bar
                    self.watchdog.set_open_position(self.open_position)
                    self.watchdog.notify_new_bar(current_bar_time)

                    # Check for new bars
                    if fvg_display.check_historical_updated():
                        fvg_display.process_historical_bars()

                    # Get current price
                    current_price = fvg_display.read_current_price()

                    if current_price is None:
                        logger.warning("No current price available")
                        time.sleep(5)
                        continue

                    # Check live FVG fills
                    fvg_display.check_live_fvg_fills(current_price)

                    # Get active FVGs
                    active_fvgs = [fvg for fvg in fvg_display.active_fvgs if not fvg.get('filled', False)]

                    # Debug logging
                    total_fvgs = len(fvg_display.active_fvgs)
                    unfilled_fvgs = len(active_fvgs)
                    bullish_count = len([f for f in active_fvgs if f['type'] == 'bullish'])
                    bearish_count = len([f for f in active_fvgs if f['type'] == 'bearish'])

                    logger.info(f"FVG Status: Total={total_fvgs}, Unfilled={unfilled_fvgs} (Bullish={bullish_count}, Bearish={bearish_count})")

                    if active_fvgs:
                        # Show details of each FVG
                        logger.info("Active FVGs:")
                        for i, fvg in enumerate(active_fvgs[:5], 1):  # Show first 5
                            logger.info(f"  {i}. {fvg['type'].upper()}: {fvg['bottom']:.2f}-{fvg['top']:.2f} | "
                                      f"Current Price: {current_price:.2f} | "
                                      f"Relative: {'ABOVE' if fvg['bottom'] > current_price else 'BELOW' if fvg['top'] < current_price else 'AT'}")

                    if not active_fvgs:
                        logger.info("No active FVGs - waiting...")
                        time.sleep(5)
                        continue

                    # Build recent bars list for reversal confirmation
                    recent_bars = historical_df.tail(2).to_dict('records')

                    # Analyze market context (with reversal confirmation)
                    fvg_context = self.fvg_analyzer.analyze_market_context(current_price, active_fvgs, recent_bars)

                    # Debug: Show filtering results
                    logger.info(f"After filtering - Nearest Bullish: {fvg_context['nearest_bullish_fvg'] is not None}, "
                              f"Nearest Bearish: {fvg_context['nearest_bearish_fvg'] is not None}")

                    # Get latest bar from historical data for EMA/Stochastic values
                    current_bar = historical_df.iloc[-1]

                    # Extract market data (EMA and Stochastic indicators)
                    market_data = {
                        'ema21': current_bar.get('EMA21', 0),
                        'ema75': current_bar.get('EMA75', 0),
                        'ema150': current_bar.get('EMA150', 0),
                        'stochastic': current_bar.get('StochD', 50)
                    }

                    # Check session rules
                    session_ok, session_reason = self.session_filter.is_trading_allowed()
                    if not session_ok:
                        logger.info(f"Session blocked: {session_reason}")
                        time.sleep(60)
                        continue

                    # Check news blackouts
                    news_ok, news_reason = self.news_filter.is_trading_allowed()
                    if not news_ok:
                        logger.warning(f"News filter: {news_reason}")
                        time.sleep(60)
                        continue

                    # Check risk limits
                    can_trade, reason = self.check_risk_limits()
                    if not can_trade:
                        logger.warning(f"Trading blocked: {reason}")
                        time.sleep(60)
                        continue

                    # Get memory context
                    memory_context = self.memory_manager.get_memory_context()

                    # Get previous analysis for incremental updates
                    previous_analysis = self.analysis_manager.format_previous_analysis_for_prompt()

                    # Pause watchdog during Claude's API call to prevent conflicts
                    self.watchdog.pause()
                    htf_bias      = self.htf_analyzer.get_bias()
                    htf_bias      = self.live_trend.apply_gate_override(htf_bias)
                    gamma_section = self.gamma.get_prompt_section(current_price)
                    of_context    = self.order_flow.get_context(current_price)
                    dom_context   = self.dom.get_context(current_price)
                    try:
                        of_section  = of_context.get('prompt_section', '')
                        dom_section = dom_context.get('prompt_section', '')
                        detector_result = self.setup_detector.update(market_data, fvg_context)
                        detector_section = detector_result.get('prompt_section', '')
                        ha_result   = self.ha_trend.update(market_data)
                        ha_section  = ha_result.get('prompt_section', '')

                        result = self.trading_agent.analyze_setup(
                            fvg_context,
                            market_data,
                            memory_context,
                            previous_analysis,
                            htf_bias.get('prompt_section', '') + self.live_trend.get_prompt_section() + gamma_section + of_section + dom_section + detector_section + ha_section,
                            self.open_position
                        )
                        last_result = result

                        # Check if we have a tradeable decision
                        if result['success']:
                            decision_data = result['decision']

                            # Save updated analysis state
                            if 'long_assessment' in decision_data and 'short_assessment' in decision_data:
                                # Build analysis update from decision
                                analysis_update = {
                                    'current_bar_index': decision_data.get('current_bar_index', 0),
                                    'overall_bias': decision_data.get('overall_bias', 'neutral'),
                                    'waiting_for': decision_data.get('waiting_for', 'Analyzing market'),
                                    'long_assessment': decision_data['long_assessment'],
                                    'short_assessment': decision_data['short_assessment'],
                                    'bars_since_last_update': 0
                                }
                                self.analysis_manager.update_analysis(analysis_update)
                                logger.info(f"Analysis state saved: {decision_data.get('waiting_for', 'N/A')}")

                            primary = decision_data['primary_decision']

                            if primary == 'EXIT':
                                # Claude says thesis invalidated — close position
                                logger.warning("EXIT SIGNAL: Claude determined thesis is invalid — closing position")
                                try:
                                    success = self.signal_generator.generate_exit_signal()
                                    if success:
                                        self.open_position = None
                                        logger.warning("EXIT WRITTEN TO CSV — NinjaTrader will close position")
                                except Exception as e:
                                    logger.error(f"ERROR WRITING EXIT SIGNAL: {e}")

                            elif primary != 'NONE' and not self.open_position:
                                # Hard HTF trend gate — block counter-trend signals at signal layer
                                htf_bias_value = htf_bias.get('bias', 'unknown')
                                neutral_threshold = self.config.get('trading_params', {}).get('confidence_threshold', 0.65)
                                chosen_setup = decision_data['long_setup'] if primary == 'LONG' else decision_data['short_setup']
                                setup_confidence = chosen_setup.get('confidence', 0.0)

                                htf_blocked = False
                                htf_strength     = htf_bias.get('strength', 'none')
                                counter_conf_req = htf_bias.get('counter_conf_required', 0.75)
                                bias_4h          = htf_bias.get('bias_4h', 'unknown')
                                bias_1h          = htf_bias.get('bias_1h', 'unknown')

                                if htf_bias_value == 'bullish' and primary == 'SHORT':
                                    if counter_conf_req >= 1.0:
                                        logger.info(f"HTF gate: blocked SHORT — 4H={bias_4h} 1H={bias_1h} ({htf_strength})")
                                        htf_blocked = True
                                    elif setup_confidence < counter_conf_req:
                                        logger.info(f"HTF gate: blocked SHORT — HTF bullish ({htf_strength}), needs conf>={counter_conf_req:.2f} (got {setup_confidence:.2f})")
                                        htf_blocked = True
                                elif htf_bias_value == 'bearish' and primary == 'LONG':
                                    if counter_conf_req >= 1.0:
                                        logger.info(f"HTF gate: blocked LONG — 4H={bias_4h} 1H={bias_1h} ({htf_strength})")
                                        htf_blocked = True
                                    elif setup_confidence < counter_conf_req:
                                        logger.info(f"HTF gate: blocked LONG — HTF bearish ({htf_strength}), needs conf>={counter_conf_req:.2f} (got {setup_confidence:.2f})")
                                        htf_blocked = True
                                elif htf_bias_value in ('neutral', 'unknown') and setup_confidence < 0.75:
                                    logger.info(f"HTF gate: blocked {primary} — HTF {htf_bias_value} requires conf>=0.75 (got {setup_confidence:.2f})")
                                    htf_blocked = True

                                if not htf_blocked:
                                    confidence = chosen_setup.get('confidence', 0.65)

                                    # Deterministic setup quality gate (Pass 4)
                                    quality = compute_setup_quality(
                                        direction=primary,
                                        fvg_context=fvg_context,
                                        market_data=market_data,
                                        htf_bias=htf_bias_value,
                                        session_active=True,
                                        gamma_loader=self.gamma,
                                        order_flow_context=of_context,
                                        dom_context=dom_context,
                                    )
                                    if not quality['gate_pass']:
                                        logger.info(f"Quality gate blocked {primary}: score={quality['score']:.2f} — {quality['description']}")
                                        htf_blocked = True  # reuse flag to skip signal
                                    sizing = self.position_sizer.compute_trade_sizing(
                                        direction=primary,
                                        entry=chosen_setup['entry'],
                                        stop=chosen_setup['stop'],
                                        target=chosen_setup['target'],
                                        confidence=confidence
                                    )

                                    signal = {
                                        'decision': primary,
                                        'entry': chosen_setup['entry'],
                                        'stop': chosen_setup['stop'],
                                        'target': chosen_setup['target'],
                                        'risk_reward': chosen_setup['risk_reward'],
                                        'confidence': confidence,
                                        'reasoning': decision_data['overall_reasoning'],
                                        'setup_type': chosen_setup.get('setup_type', 'fvg_only'),
                                        'ema21_at_entry': market_data.get('ema21', 0),
                                        **sizing
                                    }

                                    logger.info(f"GENERATING TRADE SIGNAL: {primary} @ {signal['entry']:.0f}")
                                    logger.info(f"R:R {signal['risk_reward']:.2f}:1 | Confidence: {signal['confidence']:.2f}")

                                    try:
                                        success = self.signal_generator.generate_signal(signal)
                                        if success:
                                            self.daily_trades += 1
                                            self.analysis_manager.mark_trade_executed(primary)
                                            self.open_position = {
                                                'direction': primary,
                                                'entry': chosen_setup['entry'],
                                                'stop': chosen_setup['stop'],
                                                'target': chosen_setup['target'],
                                                'setup_type': chosen_setup.get('setup_type', 'unknown'),
                                                'confidence': confidence,
                                                'ema21_at_entry': market_data.get('ema21', 0),
                                                'bars_in_trade': 0,
                                            }
                                            self._completed_trade_count = self._count_completed_trades()
                                            logger.info(f"SIGNAL WRITTEN: {primary} @ {signal['entry']:.0f} | position tracking active")
                                            self.notifier.on_entry(
                                                direction=primary,
                                                entry=signal['entry'],
                                                stop=signal['stop'],
                                                target=signal['target'],
                                                confidence=signal['confidence'],
                                                setup_type=signal.get('setup_type', 'unknown'),
                                                source='Claude',
                                                contracts=signal.get('contracts', 1),
                                            )
                                        else:
                                            logger.warning("SIGNAL GENERATION FAILED")
                                    except Exception as e:
                                        logger.error(f"ERROR WRITING SIGNAL: {e}")
                                    import traceback
                                    logger.error(traceback.format_exc())

                            elif primary != 'NONE' and self.open_position:
                                logger.info(f"Signal {primary} ignored — already in {self.open_position['direction']} position")
                            else:
                                logger.info("NO TRADE: Primary decision is NONE")
                        else:
                            logger.error(f"VALIDATION FAILED: {result.get('validation_error', 'Unknown error')}")
                            logger.error(f"Full result: {result}")

                    except Exception as e:
                        logger.error(f"ERROR IN ANALYSIS: {e}")
                        import traceback
                        logger.error(traceback.format_exc())
                        last_result = {
                            'success': False,
                            'error': str(e),
                            'decision': {}
                        }
                    finally:
                        self.watchdog.resume()  # Always resume watchdog after Claude finishes

                    # Clear screen and show response
                    os.system('cls' if os.name == 'nt' else 'clear')
                    print(self.trading_agent.format_decision_display(result, current_price))
                    if self.open_position:
                        print(f"\n[POSITION OPEN] {self.open_position['direction']} @ {self.open_position['entry']:.2f} | Bars: {self.open_position['bars_in_trade']}")
                    print("\nWaiting for next bar")

                    # Brief pause to show result
                    time.sleep(2)

                else:
                    # WAITING FOR NEW BAR - Show live updates
                    current_price = fvg_display.read_current_price()

                    # Clear screen
                    os.system('cls' if os.name == 'nt' else 'clear')

                    # Show last decision with current price
                    if last_result:
                        print(self.trading_agent.format_decision_display(last_result, current_price))

                    # Static waiting message
                    print("\nWaiting for next bar")

                    # Wait 5 seconds before refreshing
                    time.sleep(5)

        except KeyboardInterrupt:
            self.watchdog.stop()
            self.live_trend.stop()
            logger.info("\nLive trading stopped by user")
        except Exception as e:
            logger.error(f"Error in live trading: {e}")
            import traceback
            logger.error(traceback.format_exc())

    def run_backtest_mode(self, days: int = 30, output_file: str = "backtest_results.json"):
        """
        Run in backtest mode

        Args:
            days: Number of days to backtest
            output_file: Output file for results
        """
        logger.info(f"Starting BACKTEST mode ({days} days)")

        api_key = os.getenv('ANTHROPIC_API_KEY')
        use_claude = api_key is not None

        if not use_claude:
            logger.warning("No API key - running backtest with simple logic")

        engine = BacktestEngine(self.config)
        results = engine.run_backtest(days=days, use_claude=use_claude, api_key=api_key)

        # Print summary
        logger.info("="*60)
        logger.info("BACKTEST RESULTS")
        logger.info("="*60)
        logger.info(f"Period: {results['backtest_period']}")
        logger.info(f"Total Bars: {results['total_bars']}")
        logger.info(f"Total Trades: {results['total_trades']}")
        logger.info(f"Wins: {results['wins']} | Losses: {results['losses']} | Breakeven: {results['breakeven']}")
        logger.info(f"Win Rate: {results['win_rate']:.1%}")
        logger.info(f"Total P&L: {results['total_pnl']:+.2f} points")
        logger.info(f"Average P&L: {results['avg_pnl']:+.2f} points")
        logger.info(f"Max Win: {results['max_win']:+.2f} points")
        logger.info(f"Max Loss: {results['max_loss']:+.2f} points")
        logger.info(f"Average Bars Held: {results['avg_bars_held']:.1f}")

        if results.get('by_setup_type'):
            logger.info("\nBy Setup Type:")
            for setup_type, stats in results['by_setup_type'].items():
                logger.info(f"  {setup_type}: {stats['trades']} trades, {stats['win_rate']:.1%} win rate, "
                          f"{stats['avg_pnl']:+.2f}pts avg")

        logger.info("="*60)

        # Export results
        engine.export_results(results, output_file)

    def run_monitor_mode(self):
        """Run in monitoring/dashboard mode"""
        analytics = Analytics()
        print(analytics.render_dashboard())

        # Recent signals
        recent_signals = self.signal_generator.get_recent_signals(10)
        if recent_signals:
            print("\nRECENT SIGNALS:")
            print("-" * 65)
            for s in recent_signals:
                print(f"  {s.get('DateTime','')} | {s.get('Direction',''):<5} | "
                      f"Entry: {s.get('Entry_Price',''):<9} | "
                      f"SL: {s.get('Stop_Loss',''):<9} | "
                      f"TP: {s.get('Target','')}")
            print(f"\n  Signals today: {self.signal_generator.count_signals_today()}")

        # Current analysis state
        analysis = self.analysis_manager.current_analysis
        print(f"\nCURRENT ANALYSIS STATE:")
        print(f"  Bias: {analysis.get('overall_bias','neutral').upper()}")
        print(f"  Waiting for: {analysis.get('waiting_for','N/A')}")
        long_s  = analysis.get('long_assessment',  {}).get('status', 'none')
        short_s = analysis.get('short_assessment', {}).get('status', 'none')
        print(f"  Long: {long_s.upper()}  |  Short: {short_s.upper()}")


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description='Claude NQ Trading Agent')
    parser.add_argument('--mode', choices=['live', 'backtest', 'monitor'],
                       default='monitor', help='Operating mode')
    parser.add_argument('--days', type=int, default=30,
                       help='Number of days for backtest (default: 30)')
    parser.add_argument('--config', type=str, default='config/agent_config.json',
                       help='Path to configuration file')
    parser.add_argument('--output', type=str, default='backtest_results.json',
                       help='Output file for backtest results')

    args = parser.parse_args()

    # Initialize orchestrator
    orchestrator = TradingOrchestrator(config_path=args.config)

    # Run in selected mode
    if args.mode == 'live':
        orchestrator.run_live_mode()
    elif args.mode == 'backtest':
        orchestrator.run_backtest_mode(days=args.days, output_file=args.output)
    elif args.mode == 'monitor':
        orchestrator.run_monitor_mode()


if __name__ == "__main__":
    main()
