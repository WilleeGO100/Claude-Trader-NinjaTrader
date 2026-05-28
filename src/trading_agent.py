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
    """Claude-powered trading decision engine"""

    def __init__(self, config: Dict[str, Any], api_key: Optional[str] = None):
        """
        Initialize Trading Agent

        Args:
            config: Configuration dictionary with trading parameters
            api_key: Anthropic API key (or from environment)
        """
        self.config = config
        self.api_key = api_key or os.getenv('ANTHROPIC_API_KEY')

        if not self.api_key:
            raise ValueError("Anthropic API key required (set ANTHROPIC_API_KEY or pass api_key)")

        self.client = Anthropic(api_key=self.api_key)
        # Env var CLAUDE_MODEL overrides config (useful for quick switching)
        self.model = os.getenv('CLAUDE_MODEL') or config.get('claude', {}).get('model', 'claude-sonnet-4-6')

        # Extract config parameters
        self.min_risk_reward = config.get('trading_params', {}).get('min_risk_reward', 3.0)
        self.confidence_threshold = config.get('trading_params', {}).get('confidence_threshold', 0.65)
        self.stop_loss_min = config.get('risk_management', {}).get('stop_loss_min', 15)
        self.stop_loss_default = config.get('risk_management', {}).get('stop_loss_default', 20)
        self.stop_loss_max = config.get('risk_management', {}).get('stop_loss_max', 50)
        self.stop_buffer = config.get('risk_management', {}).get('stop_buffer', 5)

        # Built once at init — static for the session, eligible for prompt caching
        self._system_prompt = self._build_system_prompt()

        logger.info(f"TradingAgent initialized (model={self.model}, min_rr={self.min_risk_reward})")

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

    def query_claude_with_retry(self, user_message: str, max_retries: int = 5) -> Dict[str, Any]:
        """
        Query Claude API with exponential backoff retry logic.
        Sends the static system prompt with cache_control so Anthropic caches it
        for 5 minutes — only the dynamic user_message is billed in full each bar.
        """
        base_delay = 2  # Start with 2 second delay

        for attempt in range(max_retries):
            try:
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=8192,
                    temperature=0.3,
                    system=[{
                        "type": "text",
                        "text": self._system_prompt,
                        "cache_control": {"type": "ephemeral"}
                    }],
                    messages=[{
                        "role": "user",
                        "content": user_message
                    }]
                )
                return response

            except APIError as e:
                error_message = str(e)

                # Check if it's an overload error (529) or rate limit
                is_retryable = (
                    'overloaded' in error_message.lower() or
                    '529' in error_message or
                    'rate_limit' in error_message.lower() or
                    '429' in error_message
                )

                if is_retryable and attempt < max_retries - 1:
                    # Calculate exponential backoff delay
                    delay = base_delay * (2 ** attempt)

                    logger.warning(f"API Error (attempt {attempt + 1}/{max_retries}): {error_message}")
                    logger.warning(f"Retrying in {delay} seconds...")

                    # Show user-friendly message
                    print(f"\n[WAIT] API temporarily overloaded. Retrying in {delay}s... (attempt {attempt + 1}/{max_retries})")

                    time.sleep(delay)
                else:
                    # Last attempt or non-retryable error
                    logger.error(f"API Error (final attempt): {error_message}")
                    raise

            except Exception as e:
                # Non-API errors should not be retried
                logger.error(f"Unexpected error querying Claude: {e}")
                raise

        # Should never reach here, but just in case
        raise Exception("Max retries exceeded")

    def _build_system_prompt(self) -> str:
        """
        Static system prompt built once at init.
        Sent with cache_control so Anthropic caches it across bars — only the
        dynamic market snapshot (build_user_message) is billed in full each bar.
        """
        return f"""You are an expert NQ futures trader specializing in price action analysis using Fair Value Gaps, EMAs, and momentum indicators.

YOUR TRADING PHILOSOPHY:
========================
- PATIENCE IS KEY: It's perfectly acceptable to wait for quality setups
- Don't force trades - wait for confluence and proper setup development
- Maintain continuity in your analysis across bars
- Update your assessment incrementally based on what changed
- Track setups over multiple bars as they develop

TRADING INFORMATION AVAILABLE:
===============================
You have access to multiple sources of information to identify high-probability setups.
Use ALL available data to find the best trade opportunity.

1. FAIR VALUE GAPS (FVGs) - Price imbalances that attract fills
   - Bullish FVG BELOW = SHORT opportunity (price drawn down to fill gap)
   - Bearish FVG ABOVE = LONG opportunity (price drawn up to fill gap)

2. EMA STRUCTURE - Trend identification and dynamic support/resistance
   - EMA21, EMA75, EMA150 alignment shows trend strength
   - EMAs act as support in uptrends, resistance in downtrends
   - Pullbacks to EMAs offer entry opportunities

3. STOCHASTIC MOMENTUM - Overbought/oversold and momentum direction
   - >80 = Overbought (potential reversal or continuation)
   - <20 = Oversold (potential reversal or continuation)
   - Direction shows momentum alignment

4. PSYCHOLOGICAL LEVELS (EMS) - Round numbers attract price
   - 100-point intervals (e.g., 25500, 25600)
   - Act as magnets, support, and resistance

AVAILABLE SETUP TYPES:
======================
1. FVG_FILL      - Trading to fill a fair value gap
2. EMA_BOUNCE    - Pullback to EMA support/resistance
3. MOMENTUM      - Strong directional move with confluence
4. LEVEL_TRADE   - Break or rejection at psychological level
5. COUNTER_TREND - Mean reversion from extreme conditions
6. KELTNER_BOUNCE - Price at Keltner channel extreme + stochastic oversold/overbought (technical mean reversion)
7. SWEEP_FVG     - Liquidity sweep of a swing high/low followed by FVG confirmation (SMC entry)

UNIVERSAL TARGET BUFFER RULE:
=============================
For ALL trades, apply 5-point buffer to avoid needing perfect precision:
- LONG trades: Final Target = Raw Target - 5 points
- SHORT trades: Final Target = Raw Target + 5 points

This accounts for spread/slippage and protects against stop-hunting at exact levels.

CRITICAL INSTRUCTIONS FOR INCREMENTAL ANALYSIS:
===============================================
You are NOT doing a fresh analysis. You are UPDATING your previous assessment.

Ask yourself:
1. What changed with this new bar?
2. Is my previous setup still valid?
3. Should I continue waiting or has the setup improved/deteriorated?
4. Has price moved closer to or further from my planned entry?

SETUP ABANDONMENT RULES — check these EVERY bar when status is "waiting":
- If price has moved MORE THAN 30 points AWAY from your planned entry: ABANDON immediately
  → The setup is no longer valid at that level. Set status "none", clear entry_plan.
- If you have been "waiting" for MORE THAN 8 bars without triggering: ABANDON
  → Price has rejected the level. The setup is stale. Move on.
- After abandoning: do NOT say "setup missed". Immediately scan from current price.
  → Ask: given where price IS RIGHT NOW, what is the best available setup?
  → The move away from your entry may have CREATED a new setup in the other direction.

PLAN B REQUIREMENT — mandatory when status is "waiting":
- You must ALWAYS define what you do if Plan A does NOT trigger.
- "waiting_for" must name BOTH scenarios:
  → "Plan A: SHORT bounce to 30000. Plan B: LONG if price breaks above 30050 and holds."
- If the opposite direction is truly invalid, explain why in one sentence.
- A system with only one plan will go days without a trade. Always have a Plan B.

DECISION CRITERIA:
==================
- Minimum Risk/Reward: {self.min_risk_reward}:1 (calculated from actual prices — not self-reported)
- Stop Loss Range: {self.stop_loss_min}-{self.stop_loss_max} points
- Confidence Threshold: {self.confidence_threshold}

STOP LOSS PHILOSOPHY — DYNAMIC, STRUCTURE-BASED:
=================================================
Stops are placed at the level where the trade thesis is DEFINITIVELY WRONG.
Never use a fixed default. Size each stop to the current structure and setup type.

STEP 1 — Identify the invalidation level for this specific trade right now:
  FVG_FILL SHORT: The thesis breaks if price reclaims the level it was rejected from.
    → Stop = nearest resistance ABOVE entry (nearest psychological level, nearest EMA above, or recent swing high) + 5pt buffer.
    → If entry is within 15pts of a psych level: stop = psych_level + 5pts.
    → If no clear level nearby: stop = entry + (FVG_size × 1.2), minimum {self.stop_loss_min}pts.

  FVG_FILL LONG: The thesis breaks if price loses the support it bounced from.
    → Stop = nearest support BELOW entry (nearest psychological level, nearest EMA below, or recent swing low) - 5pt buffer.
    → If entry is within 15pts of a psych level: stop = psych_level - 5pts.
    → If no clear level nearby: stop = entry - (FVG_size × 1.2), minimum {self.stop_loss_min}pts.

  EMA_BOUNCE LONG: Stop = EMA that price bounced from - 10pts.
  EMA_BOUNCE SHORT: Stop = EMA that price bounced from + 10pts.

  LEVEL_TRADE: Stop = the key level ± 10pts (beyond the level being traded).

  MOMENTUM / COUNTER_TREND: Stop = most recent swing high (SHORT) or swing low (LONG) + 5pt buffer.

  KELTNER_BOUNCE LONG: Thesis is that price bounced off the Keltner lower band.
    → Stop = Keltner lower band value at entry - 8pts.
    → Target = Keltner midline (mean-reversion target).
    → If stochastic is not yet below 20, wait — do not pre-empt the signal.

  KELTNER_BOUNCE SHORT: Thesis is that price rejected at the Keltner upper band.
    → Stop = Keltner upper band value at entry + 8pts.
    → Target = Keltner midline.
    → If stochastic is not yet above 80, wait — do not pre-empt the signal.

  SWEEP_FVG LONG: Price swept a recent swing low (trapping shorts) then entered a bullish FVG.
    → Stop = the swing low level that was swept - 5pts (below the trap level).
    → Target = next bearish FVG above or next swing high.
    → The sweep must have occurred within the last 5 bars. Stale sweeps do not count.

  SWEEP_FVG SHORT: Price swept a recent swing high (trapping longs) then entered a bearish FVG.
    → Stop = the swing high level that was swept + 5pts (above the trap level).
    → Target = next bullish FVG below or next swing low.
    → The sweep must have occurred within the last 5 bars.

  HA_TREND LONG: First bullish Heikin Ashi flip (bearish → bullish candle) with EMA21 rising.
    → Stop = low of the first bullish HA candle in the sequence - 5pts.
    → Target = nearest resistance (FVG above, EMA level, or psychological level).
    → Only valid if EMA21 > EMA75 OR price is above EMA21. Do not trade HA flips into downtrends.

  HA_TREND SHORT: First bearish Heikin Ashi flip (bullish → bearish candle) with EMA21 falling.
    → Stop = high of the first bearish HA candle in the sequence + 5pts.
    → Target = nearest support (FVG below, EMA level, or psychological level).
    → Only valid if EMA21 < EMA75 OR price is below EMA21. Do not trade HA flips into uptrends.

STEP 2 — Verify the math before committing:
  risk  = abs(entry - stop)
  reward = abs(target - entry)
  R/R   = reward / risk

  If R/R < {self.min_risk_reward}: DO NOT take the trade. Do not argue exceptions.
  If R/R ≥ {self.min_risk_reward}: setup is valid.

STEP 3 — Report the stop you calculated, not a rounded guess.
  The risk_reward field in your JSON MUST match abs(target - entry) / abs(stop - entry) exactly.
  Do not write a different number in risk_reward than your math produces.

ANALYSIS REQUIRED:
==================
You MUST provide a COMPLETE response with both long_assessment and short_assessment.

IMPORTANT: If you don't see a quality setup, that's COMPLETELY ACCEPTABLE.
- Use status: "none" for assessments with no valid setup
- Use status: "waiting" for setups you're monitoring but not ready to trade
- Use status: "ready" for setups that meet all criteria and are tradeable NOW

For EACH assessment (long and short):
1. Determine status: "none", "waiting", or "ready"
2. If status is NOT "none", provide:
   - Setup Type: Choose ONE: FVG_FILL, EMA_BOUNCE, MOMENTUM, LEVEL_TRADE, COUNTER_TREND, KELTNER_BOUNCE, SWEEP_FVG, or HA_TREND
   - Entry price: Current price or nearby entry level
   - Raw Target: Your identified target level BEFORE buffer
   - Final Target: Apply 5pt buffer (LONG: raw - 5, SHORT: raw + 5)
   - Stop loss: Structure-based per the rules above — NOT a fixed number
   - Risk/Reward ratio: reward / risk using your actual prices — must match exactly
   - Confidence level (0.0-1.0)
   - Reasoning: Identify the invalidation level, explain stop placement, show R/R math
3. If status is "none", explain why no setup exists

Update Your Assessment Based On:
- What changed from previous analysis?
- Has the invalidation level shifted?
- FVG quality and proximity
- EMA trend alignment
- Stochastic momentum direction and level
- How long you've been tracking this setup (setup_age_bars)
- Whether you should keep waiting or abandon the setup

Respond in JSON format:
{{
    "current_bar_index": <increment from previous or 0 if first>,
    "overall_bias": "bullish" | "bearish" | "neutral",
    "waiting_for": "<describe what you're waiting for, or 'No quality setup' if none>",

    "long_assessment": {{
        "status": "none" | "waiting" | "ready",
        "setup_type": "FVG_FILL" | "EMA_BOUNCE" | "MOMENTUM" | "LEVEL_TRADE" | "COUNTER_TREND" | "KELTNER_BOUNCE" | "SWEEP_FVG" | "HA_TREND" | null,
        "entry_plan": <price or null>,
        "stop_plan": <price or null>,
        "raw_target": <target before buffer or null>,
        "target_plan": <final target WITH 5pt buffer applied or null>,
        "risk_reward": <ratio calculated with final target or null>,
        "confidence": <0.0-1.0>,
        "reasoning": "<explain setup type, confluence, why chosen>"
    }},

    "short_assessment": {{
        "status": "none" | "waiting" | "ready",
        "setup_type": "FVG_FILL" | "EMA_BOUNCE" | "MOMENTUM" | "LEVEL_TRADE" | "COUNTER_TREND" | "KELTNER_BOUNCE" | "SWEEP_FVG" | "HA_TREND" | null,
        "entry_plan": <price or null>,
        "stop_plan": <price or null>,
        "raw_target": <target before buffer or null>,
        "target_plan": <final target WITH 5pt buffer applied or null>,
        "risk_reward": <ratio calculated with final target or null>,
        "confidence": <0.0-1.0>,
        "reasoning": "<explain setup type, confluence, why chosen>"
    }},

    "primary_decision": "LONG" | "SHORT" | "NONE" | "EXIT",
    "overall_reasoning": "<incremental update: what changed from previous bar, should we trade or continue waiting>",

    "long_setup": {{
        "setup_type": <from long_assessment>,
        "entry": <entry_plan from long_assessment>,
        "stop": <stop_plan from long_assessment>,
        "target": <target_plan (WITH buffer) from long_assessment>,
        "risk_reward": <ratio from long_assessment>,
        "confidence": <confidence from long_assessment>,
        "reasoning": "<reasoning from long_assessment>"
    }},

    "short_setup": {{
        "setup_type": <from short_assessment>,
        "entry": <entry_plan from short_assessment>,
        "stop": <stop_plan from short_assessment>,
        "target": <target_plan (WITH buffer) from short_assessment>,
        "risk_reward": <ratio from short_assessment>,
        "confidence": <confidence from short_assessment>,
        "reasoning": "<reasoning from short_assessment>"
    }}
}}

IMPORTANT: The long_setup and short_setup fields must be populated for backward compatibility,
but your PRIMARY analysis should be in long_assessment and short_assessment.
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

        # Add previous analysis if available
        if previous_analysis:
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

        # Add bullish FVG info (SHORT opportunity)
        if fvg_context.get('nearest_bullish_fvg'):
            fvg = fvg_context['nearest_bullish_fvg']
            raw_target = fvg['bottom']  # Bottom of gap
            final_target = raw_target + 5  # Add 5pt buffer for SHORT
            prompt += f"""
Nearest Bullish FVG BELOW (SHORT opportunity - FVG_FILL setup):
  Zone: {fvg['bottom']:.2f} - {fvg['top']:.2f}
  Size: {fvg['size']:.2f} points
  Age: {fvg.get('age_bars', 0)} bars

  Raw Target: {raw_target:.2f} (bottom of gap)
  Final Target: {final_target:.2f} (bottom + 5pt buffer)
  Distance: {final_target - fvg_context['current_price']:.2f} points

  Setup Idea: Enter SHORT, ride price DOWN to fill gap
  This gap formed when price jumped UP, leaving unfilled space below.
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
            prompt += "  OVERBOUGHT - Potential COUNTER_TREND short (mean reversion)\n"
        elif stoch < 40:
            prompt += "  Below midpoint - Can support MOMENTUM long if trending up\n"
        elif stoch > 60:
            prompt += "  Above midpoint - Can support MOMENTUM short if trending down\n"
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

            # Stop animation
            waiting = False
            time.sleep(0.1)  # Let animation thread finish
            print('\r' + ' ' * 40 + '\r', end='', flush=True)  # Clear line

            # Extract response text
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
