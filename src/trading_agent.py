"""
Claude Trading Agent Module
Main reasoning engine for NQ trading decisions
"""

import json
import logging
from typing import Dict, Optional, Any
from datetime import datetime
import os
import time
from anthropic import Anthropic, APIError

logger = logging.getLogger(__name__)


class TradingAgent:
    """AI-powered trading decision engine — supports Claude (Anthropic) and Groq (free)."""

    def __init__(self, config: Dict[str, Any], api_key: Optional[str] = None):
        self.config = config

        # Provider: "claude" (default) or "groq"
        # Set via config["claude"]["provider"] or env var AI_PROVIDER
        self.provider = (
            os.getenv('AI_PROVIDER')
            or config.get('claude', {}).get('provider', 'claude')
        ).lower()

        if self.provider == 'openrouter':
            from openai import OpenAI
            or_key = os.getenv('OPENROUTER_API_KEY')
            if not or_key:
                raise ValueError("OPENROUTER_API_KEY env var required when provider=openrouter")
            self.client = OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=or_key,
            )
            self.model = (
                os.getenv('OPENROUTER_MODEL')
                or config.get('claude', {}).get('openrouter_model', 'openrouter/owl-alpha')
            )
            logger.info(f"TradingAgent using OPENROUTER — model={self.model}")
        elif self.provider == 'groq':
            from groq import Groq
            groq_key = os.getenv('GROQ_API_KEY')
            if not groq_key:
                raise ValueError("GROQ_API_KEY env var required when provider=groq")
            self.client = Groq(api_key=groq_key)
            self.model = (
                os.getenv('GROQ_MODEL')
                or config.get('claude', {}).get('groq_model', 'llama-3.3-70b-versatile')
            )
            logger.info(f"TradingAgent using GROQ — model={self.model} (free tier)")
        else:
            self.api_key = api_key or os.getenv('ANTHROPIC_API_KEY')
            if not self.api_key:
                raise ValueError("ANTHROPIC_API_KEY required when provider=claude")
            self.client = Anthropic(api_key=self.api_key)
            self.model = (
                os.getenv('CLAUDE_MODEL')
                or config.get('claude', {}).get('model', 'claude-sonnet-4-6')
            )
            logger.info(f"TradingAgent using CLAUDE — model={self.model}")

        self.min_risk_reward = config.get('trading_params', {}).get('min_risk_reward', 3.0)
        self.confidence_threshold = config.get('trading_params', {}).get('confidence_threshold', 0.65)
        self.stop_loss_min = config.get('risk_management', {}).get('stop_loss_min', 15)
        self.stop_loss_default = config.get('risk_management', {}).get('stop_loss_default', 20)
        self.stop_loss_max = config.get('risk_management', {}).get('stop_loss_max', 50)
        self.stop_buffer = config.get('risk_management', {}).get('stop_buffer', 5)

        self._system_prompt = self._build_system_prompt()

        logger.info(f"TradingAgent initialized (provider={self.provider}, model={self.model}, min_rr={self.min_risk_reward})")

    def _find_psychological_levels(self, current_price: float, interval: int = 100) -> Dict[str, float]:
        """
        Find nearest psychological levels above and below current price

        Args:
            current_price: Current market price
            interval: Level interval (default: 100 points)

        Returns:
            Dict with 'above' and 'below' levels
        """
        # Round to nearest level
        nearest_level = round(current_price / interval) * interval

        if current_price >= nearest_level:
            level_above = nearest_level + interval
            level_below = nearest_level
        else:
            level_above = nearest_level
            level_below = nearest_level - interval

        return {
            'above': level_above,
            'below': level_below
        }

    def query_claude_with_retry(self, user_message: str, max_retries: int = 5) -> Any:
        """Query the configured AI provider with exponential backoff retry."""
        base_delay = 2

        for attempt in range(max_retries):
            try:
                if self.provider == 'groq':
                    return self._query_groq(user_message)
                elif self.provider == 'openrouter':
                    return self._query_openrouter(user_message)
                else:
                    return self._query_claude(user_message)

            except Exception as e:
                error_message = str(e)
                is_retryable = any(x in error_message.lower() for x in
                                   ['overloaded', '529', 'rate_limit', '429', 'rate limit'])

                if is_retryable and attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    logger.warning(f"API error (attempt {attempt+1}/{max_retries}), retrying in {delay}s: {error_message}")
                    print(f"\n[WAIT] API busy. Retrying in {delay}s... ({attempt+1}/{max_retries})")
                    time.sleep(delay)
                else:
                    logger.error(f"API error (final attempt): {error_message}")
                    raise

        raise Exception("Max retries exceeded")

    def _query_claude(self, user_message: str):
        """Anthropic Claude — system prompt cached for 5 min to save tokens."""
        return self.client.messages.create(
            model=self.model,
            max_tokens=8192,
            temperature=0.3,
            system=[{
                "type": "text",
                "text": self._system_prompt,
                "cache_control": {"type": "ephemeral"}
            }],
            messages=[{"role": "user", "content": user_message}]
        )

    def _query_groq(self, user_message: str):
        """Groq free-tier — OpenAI-compatible interface."""
        return self.client.chat.completions.create(
            model=self.model,
            max_tokens=8192,
            temperature=0.3,
            messages=[
                {"role": "system", "content": self._system_prompt},
                {"role": "user",   "content": user_message}
            ]
        )

    def _query_openrouter(self, user_message: str):
        """OpenRouter — OpenAI-compatible interface."""
        return self.client.chat.completions.create(
            model=self.model,
            max_tokens=8192,
            temperature=0.3,
            messages=[
                {"role": "system", "content": self._system_prompt},
                {"role": "user",   "content": user_message}
            ]
        )

    def _build_system_prompt(self) -> str:
        """
        Static system prompt built once at init.
        Sent with cache_control so Anthropic caches it across bars — only the
        dynamic market snapshot (build_user_message) is billed in full each bar.
        """
        return f"""NQ futures trader. Analyze bar-by-bar, update incrementally, wait for quality setups.

TREND RULE (most important): If price is above EMA21+EMA75 = bullish trend. In a bull trend, FVGs below are SUPPORT not short targets. Do NOT short just because a bullish FVG exists below. Follow the trend.

SETUPS: FVG_FILL | EMA_BOUNCE | MOMENTUM | LEVEL_TRADE | COUNTER_TREND | KELTNER_BOUNCE | SWEEP_FVG | HA_TREND

STOPS (structure-based, never fixed):
- FVG_FILL LONG: below nearest support - 5pts (min {self.stop_loss_min}pts)
- FVG_FILL SHORT: above nearest resistance + 5pts
- EMA_BOUNCE: EMA level ± 10pts
- LEVEL_TRADE: key level ± 10pts
- MOMENTUM/COUNTER: recent swing ± 5pts
- SWEEP_FVG: swept level ± 5pts (sweep must be within 5 bars)
- HA_TREND LONG: low of first bullish HA candle - 5pts (only if EMA21>EMA75 or price>EMA21)
- HA_TREND SHORT: high of first bearish HA candle + 5pts (only if EMA21<EMA75 or price<EMA21)

TARGET BUFFER: LONG final target = raw - 5pts. SHORT final target = raw + 5pts.

RULES:
- Min R/R: {self.min_risk_reward}:1 (calculate from actual prices, must match risk_reward field exactly)
- Stop range: {self.stop_loss_min}-{self.stop_loss_max}pts. Confidence threshold: {self.confidence_threshold}
- Abandon "waiting" setup if price moves 30pts away OR after 8 bars
- Always have Plan A and Plan B in waiting_for
- Stoch 60-80 in uptrend = bullish continuation, NOT a short signal
- Positive gamma alone does NOT justify counter-trend shorts in a bull trend

Respond ONLY with JSON:
{{"current_bar_index":<int>,"overall_bias":"bullish"|"bearish"|"neutral","waiting_for":"<plan A and B>",
"long_assessment":{{"status":"none"|"waiting"|"ready","setup_type":"<type>"|null,"entry_plan":<price>|null,"stop_plan":<price>|null,"raw_target":<price>|null,"target_plan":<price>|null,"risk_reward":<float>|null,"confidence":<0-1>,"reasoning":"<brief>"}},
"short_assessment":{{"status":"none"|"waiting"|"ready","setup_type":"<type>"|null,"entry_plan":<price>|null,"stop_plan":<price>|null,"raw_target":<price>|null,"target_plan":<price>|null,"risk_reward":<float>|null,"confidence":<0-1>,"reasoning":"<brief>"}},
"primary_decision":"LONG"|"SHORT"|"NONE"|"EXIT","overall_reasoning":"<what changed, why trade or wait>",
"long_setup":{{"setup_type":"<type>","entry":<price>|null,"stop":<price>|null,"target":<price>|null,"risk_reward":<float>|null,"confidence":<0-1>,"reasoning":"<brief>"}},
"short_setup":{{"setup_type":"<type>","entry":<price>|null,"stop":<price>|null,"target":<price>|null,"risk_reward":<float>|null,"confidence":<0-1>,"reasoning":"<brief>"}}
}}
Only set primary_decision to LONG/SHORT if the corresponding assessment status is "ready".
"""

    def build_prompt(
        self,
        fvg_context: Dict[str, Any],
        market_data: Dict[str, Any],
        memory_context: Optional[Dict[str, Any]] = None,
        previous_analysis: Optional[str] = None,
        htf_context: Optional[str] = None,
        open_position: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Build dynamic per-bar user message. The static system prompt lives in
        _build_system_prompt() and is sent separately with cache_control.
        """
        prompt = ""

        # Groq free tier: cap previous analysis to keep total request under 12K tokens
        if previous_analysis:
            if self.provider == 'groq' and len(previous_analysis) > 800:
                previous_analysis = previous_analysis[:800] + "\n...[trimmed for token limit]\n"
            prompt += previous_analysis + "\n"

        if htf_context:
            prompt += f"\n{htf_context}\n"

        # Explicit flat state — prevents Claude from inheriting ghost position narrative
        if not open_position:
            prompt += "\nPOSITION STATUS: FLAT — You are NOT in any open trade. Do NOT manage or validate any fictional position. Focus ONLY on finding new entries.\n"

        # Inject open position context — critical for thesis management
        if open_position:
            direction   = open_position['direction']
            entry       = float(open_position.get('entry')       or 0)
            stop        = float(open_position.get('stop')        or 0)
            target      = float(open_position.get('target')      or 0)
            setup_type  = open_position.get('setup_type', 'unknown')
            ema21_entry = float(open_position.get('ema21_at_entry') or 0)
            bars_held   = open_position.get('bars_in_trade', 0)
            current_ema21 = market_data.get('ema21') or 0
            current_price = fvg_context['current_price']

            ema_status = ""
            if direction == 'LONG' and current_ema21 > 0:
                ema_status = f"EMA21 now at {current_ema21:.2f} — price is {'ABOVE' if current_price > current_ema21 else 'BELOW'} EMA21"
            elif direction == 'SHORT' and current_ema21 > 0:
                ema_status = f"EMA21 now at {current_ema21:.2f} — price is {'BELOW' if current_price < current_ema21 else 'ABOVE'} EMA21"

            prompt += f"""
OPEN POSITION — THESIS CHECK REQUIRED:
=======================================
YOU ARE CURRENTLY IN A {direction} TRADE. Do NOT look for new entries.
Your ONLY job this bar is to evaluate whether the trade thesis is still valid.

  Entry:      {entry:.2f}
  Stop Loss:  {stop:.2f}
  Target:     {target:.2f}
  Setup Type: {setup_type}
  EMA21 at Entry: {ema21_entry:.2f}
  Bars Held:  {bars_held}
  {ema_status}

THESIS INVALIDATION RULES — set primary_decision to "EXIT" if ANY of these are true:
  - {direction} EMA_BOUNCE trade: price has CLOSED BELOW EMA21 (for LONG) or ABOVE EMA21 (for SHORT)
  - Two or more consecutive bars have closed beyond the EMA level
  - Price structure has fundamentally broken (e.g. lower lows forming in a LONG)
  - The reason you entered no longer exists

If the thesis is still valid, set primary_decision to "NONE" and explain why you're staying in.
DO NOT set primary_decision to LONG or SHORT while in this position.

"""

        # Inject reversal confirmation status
        long_conf  = fvg_context.get('long_confirmation')
        short_conf = fvg_context.get('short_confirmation')
        if long_conf or short_conf:
            prompt += "\nZONE REVERSAL CONFIRMATION:\n"
            if long_conf:
                status = "CONFIRMED" if long_conf['confirmed'] else "NOT CONFIRMED"
                prompt += f"  LONG setup: {status} — {long_conf['reason']}\n"
            if short_conf:
                status = "CONFIRMED" if short_conf['confirmed'] else "NOT CONFIRMED"
                prompt += f"  SHORT setup: {status} — {short_conf['reason']}\n"
            prompt += "  NOTE: Only set status 'ready' if confirmation is CONFIRMED or you have other strong confluence.\n\n"

        prompt += f"""
CURRENT MARKET CONTEXT (NEW BAR):
==================================

Price: {fvg_context['current_price']:.2f}

FAIR VALUE GAPS:
"""

        # Add bullish FVG info (gap fill SHORT target OR support in uptrend)
        if fvg_context.get('nearest_bullish_fvg'):
            fvg = fvg_context['nearest_bullish_fvg']
            raw_target = fvg['bottom']  # Bottom of gap
            final_target = raw_target + 5  # Add 5pt buffer for SHORT
            prompt += f"""
Nearest Bullish FVG BELOW (gap fill SHORT target OR long support in uptrend):
  Zone: {fvg['bottom']:.2f} - {fvg['top']:.2f}
  Size: {fvg['size']:.2f} points
  Age: {fvg.get('age_bars', 0)} bars

  If SHORT: Raw Target {raw_target:.2f}, Final Target {final_target:.2f} (+5pt buffer), Distance {final_target - fvg_context['current_price']:.2f}pts
  If LONG (uptrend): this gap is support — price may bounce from this zone, not fill it.
  NOTE: In a strong uptrend (price above EMA21+EMA75), counter-trend FVG fills are LOW probability.
"""
        else:
            prompt += "\nNo bullish FVGs BELOW current price\n"

        # Add bearish FVG info (LONG opportunity)
        if fvg_context.get('nearest_bearish_fvg'):
            fvg = fvg_context['nearest_bearish_fvg']
            raw_target = fvg['top']  # Top of gap
            final_target = raw_target - 5  # Subtract 5pt buffer for LONG
            prompt += f"""
Nearest Bearish FVG ABOVE (LONG opportunity - FVG_FILL setup):
  Zone: {fvg['bottom']:.2f} - {fvg['top']:.2f}
  Size: {fvg['size']:.2f} points
  Age: {fvg.get('age_bars', 0)} bars

  Raw Target: {raw_target:.2f} (top of gap)
  Final Target: {final_target:.2f} (top - 5pt buffer)
  Distance: {final_target - fvg_context['current_price']:.2f} points

  Setup Idea: Enter LONG, ride price UP to fill gap
  This gap formed when price dropped DOWN, leaving unfilled space above.
"""
        else:
            prompt += "\nNo bearish FVGs ABOVE current price\n"

        # Add EMA trend analysis
        prompt += f"""
EMA STRUCTURE & POTENTIAL SETUPS:
==================================
Current Price: {fvg_context['current_price']:.2f}
EMA21:  {(market_data.get('ema21') or 0):.2f} (distance: {fvg_context['current_price'] - (market_data.get('ema21') or 0):+.2f})
EMA75:  {(market_data.get('ema75') or 0):.2f} (distance: {fvg_context['current_price'] - (market_data.get('ema75') or 0):+.2f})
EMA150: {(market_data.get('ema150') or 0):.2f} (distance: {fvg_context['current_price'] - (market_data.get('ema150') or 0):+.2f})

Trend & Setup Opportunities:
"""
        current_price = fvg_context['current_price']
        ema21  = market_data.get('ema21')  or 0
        ema75  = market_data.get('ema75')  or 0
        ema150 = market_data.get('ema150') or 0

        if ema21 > ema75 > ema150:
            prompt += "  Strong UPTREND (EMA21 > EMA75 > EMA150)\n"
            if current_price > ema21:
                prompt += f"  EMA_BOUNCE setup: LONG on pullback to EMA21 @ {ema21:.2f}\n"
        elif ema21 < ema75 < ema150:
            prompt += "  Strong DOWNTREND (EMA21 < EMA75 < EMA150)\n"
            if current_price < ema21:
                prompt += f"  EMA_BOUNCE setup: SHORT on bounce to EMA21 @ {ema21:.2f}\n"
        elif ema21 > ema75:
            prompt += "  Weak uptrend (EMA21 > EMA75)\n"
        elif ema21 < ema75:
            prompt += "  Weak downtrend (EMA21 < EMA75)\n"
        else:
            prompt += "  Neutral/Choppy - Avoid trend trades\n"

        # Add Stochastic momentum with setup ideas
        stoch = market_data.get('stochastic') or 50
        prompt += f"""
MOMENTUM INDICATOR & SETUPS:
=============================
Stochastic: {stoch:.2f}
"""
        if stoch < 20:
            prompt += "  OVERSOLD - Potential COUNTER_TREND long (mean reversion)\n"
        elif stoch > 80:
            prompt += "  OVERBOUGHT - Potential COUNTER_TREND short OR bullish continuation in strong uptrend\n"
        elif stoch < 40:
            prompt += "  Below midpoint - Can support MOMENTUM long if trending up\n"
        elif stoch > 60:
            prompt += "  Above midpoint - Bullish continuation in uptrend; counter-trend short only if HTF bearish AND price below EMA21\n"
        else:
            prompt += "  Neutral zone\n"

        # Add psychological level analysis
        nearest_levels = self._find_psychological_levels(current_price)
        prompt += f"""
PSYCHOLOGICAL LEVELS (EMS):
============================
Current Price: {current_price:.2f}
Nearest Level Above: {nearest_levels['above']} ({nearest_levels['above'] - current_price:+.2f}pts)
Nearest Level Below: {nearest_levels['below']} ({nearest_levels['below'] - current_price:+.2f}pts)

LEVEL_TRADE opportunities:
  - Break above {nearest_levels['above']} with retest (LONG continuation)
  - Rejection at {nearest_levels['above']} (SHORT reversal)
  - Break below {nearest_levels['below']} with retest (SHORT continuation)
  - Bounce at {nearest_levels['below']} (LONG reversal)
"""

        # Add memory context if available
        if memory_context:
            prompt += f"""
HISTORICAL PERFORMANCE:
"""
            if memory_context.get('fvg_only_stats'):
                stats = memory_context['fvg_only_stats']
                prompt += f"""
FVG-Only Trades: {stats['total_trades']} trades, {stats['win_rate']*100:.1f}% win rate
Average R/R: {stats['avg_rr']:.2f}:1
"""

        return prompt

    def parse_claude_response(self, response_text: str) -> Optional[Dict[str, Any]]:
        """
        Parse Claude's JSON response

        Args:
            response_text: Raw response text from Claude

        Returns:
            Parsed decision dictionary or None if parsing fails
        """
        try:
            # Extract JSON from response (handle markdown code blocks)
            text = response_text.strip()
            if '```json' in text:
                text = text.split('```json')[1].split('```')[0].strip()
            elif '```' in text:
                text = text.split('```')[1].split('```')[0].strip()

            decision = json.loads(text)

            # AUTO-CONVERT: If agent returned new format but not old format, convert automatically
            if 'long_assessment' in decision and 'short_assessment' in decision:
                # Convert assessments to setups for backward compatibility
                if 'long_setup' not in decision:
                    decision['long_setup'] = self._assessment_to_setup(decision['long_assessment'])
                if 'short_setup' not in decision:
                    decision['short_setup'] = self._assessment_to_setup(decision['short_assessment'])

                # Set primary_decision based on assessment status
                if 'primary_decision' not in decision:
                    if decision['long_assessment'].get('status') == 'ready':
                        decision['primary_decision'] = 'LONG'
                    elif decision['short_assessment'].get('status') == 'ready':
                        decision['primary_decision'] = 'SHORT'
                    else:
                        decision['primary_decision'] = 'NONE'

                # Set overall_reasoning from waiting_for if missing
                if 'overall_reasoning' not in decision:
                    decision['overall_reasoning'] = decision.get('waiting_for', 'No reasoning provided')

            return decision
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Claude response: {e}")
            logger.error(f"Response text: {response_text[:500]}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error parsing response: {e}")
            return None

    def _assessment_to_setup(self, assessment: Dict[str, Any]) -> Dict[str, Any]:
        """Convert assessment format to setup format for backward compatibility"""
        return {
            'setup_type': assessment.get('setup_type'),
            'entry': assessment.get('entry_plan'),
            'stop': assessment.get('stop_plan'),
            'raw_target': assessment.get('raw_target'),  # Include for validation
            'target': assessment.get('target_plan'),
            'risk_reward': assessment.get('risk_reward'),
            'confidence': assessment.get('confidence', 0.0),
            'reasoning': assessment.get('reasoning', '')
        }

    def validate_decision(self, decision: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """
        Validate Claude's trading decision

        Args:
            decision: Parsed decision dictionary

        Returns:
            Tuple of (is_valid, error_message)
        """
        # Check required fields for new format
        # Support both old format (market_bias) and new format (overall_bias)
        if 'overall_bias' not in decision and 'market_bias' not in decision:
            return False, "Missing required field: overall_bias or market_bias"

        # Normalize to overall_bias for consistency
        if 'market_bias' in decision and 'overall_bias' not in decision:
            decision['overall_bias'] = decision['market_bias']

        required_fields = ['primary_decision', 'long_setup', 'short_setup', 'overall_reasoning']
        for field in required_fields:
            if field not in decision:
                return False, f"Missing required field: {field}"

        # Validate each setup
        for setup_name in ['long_setup', 'short_setup']:
            if setup_name not in decision:
                return False, f"Missing {setup_name} in decision"

            setup = decision[setup_name]
            if not isinstance(setup, dict):
                return False, f"{setup_name} is not a dictionary: {type(setup)}"

            setup_fields = ['entry', 'stop', 'target', 'risk_reward', 'confidence', 'reasoning']
            for field in setup_fields:
                if field not in setup:
                    return False, f"Missing field in {setup_name}: {field}"

        # EXIT and NONE always pass validation
        if decision['primary_decision'] in ('NONE', 'EXIT'):
            return True, None

        # Get the chosen setup
        chosen_setup = decision['long_setup'] if decision['primary_decision'] == 'LONG' else decision['short_setup']

        # Validate stop loss range
        entry = chosen_setup['entry']
        stop = chosen_setup['stop']
        stop_distance = abs(entry - stop)

        if stop_distance < self.stop_loss_min:
            logger.warning(f"STOP REJECTED (too tight): {stop_distance:.2f}pts < {self.stop_loss_min}pt min | entry={entry} stop={stop}")
            return False, f"Stop loss too tight: {stop_distance:.2f}pts (min: {self.stop_loss_min}pts)"

        if stop_distance > self.stop_loss_max:
            logger.warning(f"STOP REJECTED (too wide): {stop_distance:.2f}pts > {self.stop_loss_max}pt max | entry={entry} stop={stop} — consider raising stop_loss_max in config")
            return False, f"Stop loss too wide: {stop_distance:.2f}pts (max: {self.stop_loss_max}pts)"

        # Validate stop direction
        if decision['primary_decision'] == 'LONG' and stop >= entry:
            return False, "Invalid LONG stop: stop must be below entry"

        if decision['primary_decision'] == 'SHORT' and stop <= entry:
            return False, "Invalid SHORT stop: stop must be above entry"

        # Recalculate R/R from actual prices — do not trust Claude's self-reported value
        target = chosen_setup['target']
        actual_risk = abs(entry - stop)
        actual_reward = abs(target - entry)
        actual_rr = actual_reward / actual_risk if actual_risk > 0 else 0

        if actual_rr < self.min_risk_reward:
            return False, f"Risk/reward too low: {actual_rr:.2f} (min: {self.min_risk_reward})"

        # Validate confidence
        if chosen_setup['confidence'] < self.confidence_threshold:
            return False, f"Confidence too low: {chosen_setup['confidence']:.2f} (min: {self.confidence_threshold})"

        return True, None

    def analyze_setup(
        self,
        fvg_context: Dict[str, Any],
        market_data: Dict[str, Any],
        memory_context: Optional[Dict[str, Any]] = None,
        previous_analysis: Optional[str] = None,
        htf_context: Optional[str] = None,
        open_position: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Main analysis method - queries Claude for trading decision

        Args:
            fvg_context: FVG market context
            market_data: Market indicators (EMA, Stochastic, etc.)
            memory_context: Past trade performance data
            previous_analysis: Previous analysis state (formatted string)

        Returns:
            Decision dictionary with validation status
        """
        # Build dynamic per-bar user message (system prompt is cached separately)
        prompt = self.build_prompt(fvg_context, market_data, memory_context, previous_analysis, htf_context, open_position)

        try:
            # Show full prompt (debug only)
            logger.debug("="*60)
            logger.debug("SENDING TO CLAUDE:")
            logger.debug("="*60)
            logger.debug(self._system_prompt)
            logger.debug("--- USER MESSAGE ---")
            logger.debug(prompt)
            logger.debug("="*60)

            # Show waiting message
            print("\nWaiting for Agent response", end='', flush=True)

            import threading
            import time

            # Animation flag
            waiting = True

            def animate_dots():
                while waiting:
                    for i in range(6):
                        if not waiting:
                            break
                        print('.', end='', flush=True)
                        time.sleep(0.5)
                    if waiting:
                        print('\r' + ' ' * 40 + '\r', end='', flush=True)
                        print("Waiting for Agent response", end='', flush=True)

            # Start animation in background
            anim_thread = threading.Thread(target=animate_dots, daemon=True)
            anim_thread.start()

            # Query Claude with retry logic (system prompt cached, user message dynamic)
            response = self.query_claude_with_retry(prompt, max_retries=5)

            # Log cache stats so we can verify caching is working
            usage = getattr(response, 'usage', None)
            if usage:
                cache_read    = getattr(usage, 'cache_read_input_tokens', 0)
                cache_written = getattr(usage, 'cache_creation_input_tokens', 0)
                input_tokens  = getattr(usage, 'input_tokens', 0)
                output_tokens = getattr(usage, 'output_tokens', 0)
                if cache_read:
                    logger.info(f"[CACHE HIT] read={cache_read} tokens | input={input_tokens} | output={output_tokens}")
                elif cache_written:
                    logger.info(f"[CACHE WRITE] written={cache_written} tokens | input={input_tokens} | output={output_tokens}")
                else:
                    logger.info(f"[CACHE MISS] input={input_tokens} | output={output_tokens}")

            # Stop animation
            waiting = False
            time.sleep(0.1)  # Let animation thread finish
            print('\r' + ' ' * 40 + '\r', end='', flush=True)  # Clear line

            # Extract response text
            if self.provider in ('groq', 'openrouter'):
                response_text = response.choices[0].message.content
            else:
                response_text = response.content[0].text

            # Show full response (debug only)
            logger.debug("="*60)
            logger.debug("CLAUDE RESPONSE:")
            logger.debug("="*60)
            logger.debug(response_text)
            logger.debug("="*60)

            # Parse response
            decision = self.parse_claude_response(response_text)

            if not decision:
                logger.error("="*60)
                logger.error("PARSING FAILED - RAW RESPONSE:")
                logger.error("="*60)
                logger.error(response_text)
                logger.error("="*60)
                return {
                    'success': False,
                    'error': 'Failed to parse Claude response',
                    'raw_response': response_text
                }

            # Validate decision
            is_valid, error_msg = self.validate_decision(decision)

            result = {
                'success': is_valid,
                'decision': decision,
                'timestamp': datetime.now().isoformat(),
                'validation_error': error_msg,
                'fvg_context': fvg_context,  # Store for display
                'market_data': market_data   # Store for display
            }

            # Log validation result
            if is_valid:
                primary = decision.get('primary_decision', 'NONE')
                if primary != 'NONE':
                    chosen = decision['long_setup'] if primary == 'LONG' else decision['short_setup']
                    logger.info(f"VALIDATION PASSED: {primary} @ {chosen['entry']:.0f} | R:R {chosen['risk_reward']:.2f}:1 | Conf {chosen['confidence']:.2f}")
                else:
                    logger.info("VALIDATION PASSED: No trade recommended")
            else:
                logger.warning(f"VALIDATION FAILED: {error_msg}")

            return result

        except Exception as e:
            logger.error(f"Error querying Claude: {e}")
            return {
                'success': False,
                'error': str(e)
            }

    def format_decision_display(
        self,
        result: Dict[str, Any],
        current_price: float = None
    ) -> str:
        """Format decision for clean display"""
        if not result.get('success', False):
            error_msg = result.get('validation_error') or result.get('error') or 'Unknown error'

            # Show clean error with decision context if available
            lines = []
            lines.append("="*60)
            lines.append("VALIDATION FAILED")
            lines.append("="*60)
            lines.append(f"Error: {error_msg}")

            # Try to show what was attempted
            decision = result.get('decision', {})
            if decision:
                primary = decision.get('primary_decision', 'UNKNOWN')
                lines.append(f"\nAttempted: {primary}")

                if primary in ['LONG', 'SHORT']:
                    setup = decision.get('long_setup' if primary == 'LONG' else 'short_setup', {})
                    if setup:
                        lines.append(f"Entry: {setup.get('entry') or 0:.0f}")
                        lines.append(f"Stop: {setup.get('stop') or 0:.0f}")
                        lines.append(f"Target: {setup.get('target') or 0:.0f}")
                        lines.append(f"R:R: {setup.get('risk_reward') or 0:.2f}:1")
                        lines.append(f"Confidence: {setup.get('confidence') or 0:.2f}")

            lines.append("\n" + "="*60)
            lines.append("Trade rejected - criteria not met")
            lines.append("="*60)

            return "\n".join(lines)

        decision = result['decision']
        fvg_context = result.get('fvg_context', {})
        market_data = result.get('market_data', {})

        # Use current price if provided, otherwise from context
        price = current_price or fvg_context.get('current_price', 0)

        # FVG info
        bull_fvg = fvg_context.get('nearest_bullish_fvg')
        bear_fvg = fvg_context.get('nearest_bearish_fvg')

        bull_str = f"UP {bull_fvg['bottom']:.0f}-{bull_fvg['top']:.0f} ({bull_fvg['distance']:+.0f}pts)" if bull_fvg else "None"
        bear_str = f"DN {bear_fvg['bottom']:.0f}-{bear_fvg['top']:.0f} ({bear_fvg['distance']:+.0f}pts)" if bear_fvg else "None"

        # Trend
        ema21 = market_data.get('ema21', 0)
        ema75 = market_data.get('ema75', 0)
        ema150 = market_data.get('ema150', 0)

        if ema21 > ema75 > ema150:
            trend = "Strong UP"
        elif ema21 < ema75 < ema150:
            trend = "Strong DN"
        elif ema21 > ema75:
            trend = "Weak UP"
        elif ema21 < ema75:
            trend = "Weak DN"
        else:
            trend = "Neutral"

        stoch = market_data.get('stochastic', 50)

        # Build display
        lines = []
        lines.append("="*60)
        lines.append(f"NQ @ {price:.2f}")
        lines.append("="*60)
        lines.append(f"FVG: {bull_str} | {bear_str}")
        lines.append(f"EMA: {trend} | Stoch: {stoch:.0f}")
        # Support both old and new format
        bias = decision.get('overall_bias') or decision.get('market_bias', 'unknown')
        lines.append(f"Market Bias: {bias.upper()}")
        lines.append("="*60)

        # Show both setups
        long_setup = decision.get('long_setup', {})
        short_setup = decision.get('short_setup', {})

        lines.append("\nLONG SETUP:")
        lines.append(f"  Entry: {long_setup.get('entry') or 0:.0f} | Stop: {long_setup.get('stop') or 0:.0f} | Target: {long_setup.get('target') or 0:.0f}")
        lines.append(f"  R:R {long_setup.get('risk_reward') or 0:.1f}:1 | Confidence: {long_setup.get('confidence') or 0:.2f}")
        lines.append(f"  {long_setup.get('reasoning', 'N/A')}")

        lines.append("\nSHORT SETUP:")
        lines.append(f"  Entry: {short_setup.get('entry') or 0:.0f} | Stop: {short_setup.get('stop') or 0:.0f} | Target: {short_setup.get('target') or 0:.0f}")
        lines.append(f"  R:R {short_setup.get('risk_reward') or 0:.1f}:1 | Confidence: {short_setup.get('confidence') or 0:.2f}")
        lines.append(f"  {short_setup.get('reasoning', 'N/A')}")

        lines.append("\n" + "="*60)

        # Primary decision
        primary = decision.get('primary_decision', 'NONE')
        if primary == 'NONE':
            lines.append(f"PRIMARY DECISION: NONE")
        else:
            chosen = long_setup if primary == 'LONG' else short_setup
            lines.append(f"PRIMARY DECISION: {primary} @ {chosen.get('entry') or 0:.0f} -> {chosen.get('target') or 0:.0f}")
            lines.append(f"Confidence: {chosen.get('confidence') or 0:.2f}")

        lines.append(f"\nOVERALL ANALYSIS:")
        lines.append(decision.get('overall_reasoning', 'N/A'))

        lines.append("\n" + "="*60)

        # Show trade signal status
        if primary != 'NONE':
            lines.append("STATUS: TRADE SIGNAL WRITTEN TO CSV")
        else:
            lines.append("STATUS: NO TRADE SIGNAL")

        lines.append("="*60)

        return "\n".join(lines)

    def get_decision_summary(self, result: Dict[str, Any]) -> str:
        """
        Generate human-readable summary of decision

        Args:
            result: Result dictionary from analyze_setup()

        Returns:
            Summary string
        """
        if not result['success']:
            return f"ANALYSIS FAILED: {result.get('error', 'Unknown error')}"

        decision = result['decision']

        if decision['decision'] == 'NONE':
            return f"NO TRADE\nReason: {decision['reasoning']}"

        lines = []
        lines.append(f"=== TRADE SIGNAL: {decision['decision']} ===")
        lines.append(f"Entry: {decision['entry']:.2f}")
        lines.append(f"Stop: {decision['stop']:.2f} ({abs(decision['entry'] - decision['stop']):.2f}pts)")
        lines.append(f"Target: {decision['target']:.2f} ({abs(decision['target'] - decision['entry']):.2f}pts)")
        lines.append(f"Risk/Reward: {decision['risk_reward']:.2f}:1")
        lines.append(f"Confidence: {decision['confidence']:.2%}")
        lines.append(f"Setup Type: {decision['setup_type']}")
        lines.append(f"\nReasoning:\n{decision['reasoning']}")

        return "\n".join(lines)


# Example usage
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Sample config
    config = {
        'trading_params': {
            'min_risk_reward': 3.0,
            'confidence_threshold': 0.65
        },
        'risk_management': {
            'stop_loss_min': 15,
            'stop_loss_default': 20,
            'stop_loss_max': 50,
            'stop_buffer': 5
        }
    }

    # Sample contexts
    fvg_context = {
        'current_price': 14685.50,
        'nearest_bullish_fvg': {
            'top': 14715, 'bottom': 14710, 'size': 5.0,
            'distance': 29.50, 'age_bars': 12
        },
        'nearest_bearish_fvg': {
            'top': 14655, 'bottom': 14650, 'size': 5.0,
            'distance': 30.50, 'age_bars': 45
        }
    }

    level_context = {
        'nearest_level_above': 14700,
        'distance_to_level_above': 14.50,
        'nearest_level_below': 14600,
        'distance_to_level_below': 85.50,
        'on_level': False,
        'nearby_levels': [14700, 14600, 14800]
    }

    # NOTE: Requires ANTHROPIC_API_KEY environment variable
    # agent = TradingAgent(config)
    # result = agent.analyze_setup(fvg_context, level_context)
    # print(agent.get_decision_summary(result))
