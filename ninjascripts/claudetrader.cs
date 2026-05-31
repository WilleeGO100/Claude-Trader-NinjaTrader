#region Using declarations
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.IO;
using System.Windows.Media;
using NinjaTrader.Cbi;
using NinjaTrader.Gui;
using NinjaTrader.NinjaScript;
using NinjaTrader.NinjaScript.DrawingTools;
using NinjaTrader.Core.FloatingPoint;
#endregion

namespace NinjaTrader.NinjaScript.Strategies
{
    public class ClaudeTrader : Strategy
    {
        #region Variables

        // File paths
        private string signalsFilePath;
        private string tradesLogFilePath;

        // File monitoring
        private DateTime lastFileCheckTime  = DateTime.MinValue;
        private DateTime lastFileModified   = DateTime.MinValue;
        private HashSet<string> processedSignals = new HashSet<string>();

        // Current signal
        private string  currentSignalId    = "";
        private string  signalDirection    = "";
        private double  signalEntry        = 0;
        private double  signalStop         = 0;
        private double  signalTarget       = 0;
        private int     signalContracts    = 1;
        private double  signalScale1Price  = 0;
        private int     signalScale1Qty    = 0;
        private double  signalTrailPoints  = 0;
        private double  signalEMA21AtEntry = 0;

        // Position state
        private bool    inPosition         = false;
        private bool    hasOpenOrder       = false;
        private double  actualEntry        = 0;
        private bool    scale1Hit          = false;
        private int     remainingQty       = 0;
        private bool    trailActive        = false;
        private double  trailStopPrice     = 0;
        private double  trailPeak          = 0;

        // Thesis invalidation watchdog
        private double  invalidationLevel  = 0;   // EMA21 level at entry
        private int     adverseCloseCount  = 0;   // consecutive closes beyond invalidation
        private int     lastWatchdogBar    = -1;  // prevent double-counting on same bar
        private const int MaxAdverseCloses = 2;   // exit after this many consecutive adverse closes

        #endregion

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description             = "ClaudeTrader — CSV signal execution with partial exits and trailing stops";
                Name                    = "ClaudeTrader";
                Calculate               = Calculate.OnEachTick;
                EntriesPerDirection     = 1;
                EntryHandling           = EntryHandling.AllEntries;
                IsExitOnSessionCloseStrategy = true;
                ExitOnSessionCloseSeconds    = 30;
                MaximumBarsLookBack     = MaximumBarsLookBack.TwoHundredFiftySix;
                OrderFillResolution     = OrderFillResolution.Standard;
                StopTargetHandling      = StopTargetHandling.PerEntryExecution;
                StartBehavior           = StartBehavior.WaitUntilFlat;
                TimeInForce             = NinjaTrader.Cbi.TimeInForce.Gtc;
                BarsRequiredToTrade     = 1;

                // Exposed parameters
                SignalsFilePath  = @"C:\Users\jwmar\Claude-Trader-NinjaTrader\data\trade_signals.csv";
                TradesLogPath    = @"C:\Users\jwmar\Claude-Trader-NinjaTrader\data\trades_taken.csv";
                FileCheckSeconds = 2;
            }
            else if (State == State.DataLoaded)
            {
                signalsFilePath  = SignalsFilePath;
                tradesLogFilePath = TradesLogPath;
                processedSignals = new HashSet<string>();
                lastFileCheckTime = DateTime.MinValue;
                Print($"ClaudeTrader ready — watching {signalsFilePath} every {FileCheckSeconds}s");
            }
        }

        protected override void OnBarUpdate()
        {
            if (CurrentBar < BarsRequiredToTrade)
                return;

            // Poll signal file
            if ((DateTime.Now - lastFileCheckTime).TotalSeconds >= FileCheckSeconds)
            {
                CheckForSignal();
                lastFileCheckTime = DateTime.Now;
            }

            // Manual trailing stop logic
            if (trailActive && inPosition && signalTrailPoints > 0 && Position.MarketPosition != MarketPosition.Flat)
            {
                if (signalDirection == "LONG")
                {
                    if (High[0] > trailPeak)
                    {
                        trailPeak = High[0];
                        trailStopPrice = trailPeak - signalTrailPoints;
                        Print($"[TRAIL] New high {trailPeak:F2} — trail stop raised to {trailStopPrice:F2}");
                    }
                    if (Low[0] <= trailStopPrice && trailStopPrice > 0)
                    {
                        ExitLong(0, remainingQty > 0 ? remainingQty : Position.Quantity, "TrailStop", "CT_Long");
                        Print($"[TRAIL STOP] Long exit @ trail {trailStopPrice:F2}");
                    }
                }
                else if (signalDirection == "SHORT")
                {
                    if (Low[0] < trailPeak || trailPeak == 0)
                    {
                        trailPeak = Low[0];
                        trailStopPrice = trailPeak + signalTrailPoints;
                        Print($"[TRAIL] New low {trailPeak:F2} — trail stop lowered to {trailStopPrice:F2}");
                    }
                    if (High[0] >= trailStopPrice && trailStopPrice > 0)
                    {
                        ExitShort(0, remainingQty > 0 ? remainingQty : Position.Quantity, "TrailStop", "CT_Short");
                        Print($"[TRAIL STOP] Short exit @ trail {trailStopPrice:F2}");
                    }
                }
            }

            // LTF candle close watchdog — check on each new completed bar
            if (inPosition && !scale1Hit && invalidationLevel > 0 && CurrentBar != lastWatchdogBar && CurrentBar > 0)
            {
                lastWatchdogBar = CurrentBar;
                double prevClose = Close[1]; // most recently completed bar's close

                bool adverseClose = (signalDirection == "LONG" && prevClose < invalidationLevel)
                                 || (signalDirection == "SHORT" && prevClose > invalidationLevel);

                if (adverseClose)
                {
                    adverseCloseCount++;
                    Print($"[WATCHDOG] Adverse close #{adverseCloseCount}: {prevClose:F2} beyond level {invalidationLevel:F2}");

                    if (adverseCloseCount >= MaxAdverseCloses)
                    {
                        Print($"[WATCHDOG EXIT] {adverseCloseCount} consecutive closes beyond invalidation — exiting");
                        if (Position.MarketPosition == MarketPosition.Long)
                            ExitLong(0, Position.Quantity, "WatchdogExit", "CT_Long");
                        else if (Position.MarketPosition == MarketPosition.Short)
                            ExitShort(0, Position.Quantity, "WatchdogExit", "CT_Short");
                    }
                }
                else
                {
                    if (adverseCloseCount > 0)
                        Print($"[WATCHDOG] Price recovered — resetting adverse count");
                    adverseCloseCount = 0;
                }
            }

            // Clean up state when flat
            if (Position.MarketPosition == MarketPosition.Flat && inPosition)
                ResetState();
        }

        // ─── Signal reading ───────────────────────────────────────────────

        private void CheckForSignal()
        {
            if (!File.Exists(signalsFilePath))
                return;

            try
            {
                DateTime modTime = File.GetLastWriteTime(signalsFilePath);
                if (modTime <= lastFileModified)
                    return;
                lastFileModified = modTime;

                string[] lines = File.ReadAllLines(signalsFilePath);
                if (lines.Length <= 1)
                    return;

                string lastLine = lines[lines.Length - 1].Trim();
                if (string.IsNullOrWhiteSpace(lastLine))
                    return;

                ParseAndExecute(lastLine);
                ClearSignalFile();
            }
            catch (Exception ex)
            {
                Print($"[ERROR] Reading signal file: {ex.Message}");
            }
        }

        private void ParseAndExecute(string line)
        {
            string[] f = line.Split(',');
            if (f.Length < 5)
            {
                Print($"[ERROR] Invalid signal (need ≥5 fields): {line}");
                return;
            }

            string signalId = $"{f[0].Trim()}_{f[1].Trim()}";
            if (processedSignals.Contains(signalId))
                return;

            if (Position.MarketPosition != MarketPosition.Flat || hasOpenOrder)
            {
                Print($"[SKIP] Already in position — ignoring {signalId}");
                return;
            }

            // Handle EXIT signal from Claude thesis invalidation
            string dir = f[1].Trim().ToUpper();
            if (dir == "EXIT")
            {
                if (Position.MarketPosition == MarketPosition.Long)
                {
                    ExitLong(0, Position.Quantity, "ThesisExit", "CT_Long");
                    Print("[THESIS EXIT] Claude invalidated trade — closing LONG");
                }
                else if (Position.MarketPosition == MarketPosition.Short)
                {
                    ExitShort(0, Position.Quantity, "ThesisExit", "CT_Short");
                    Print("[THESIS EXIT] Claude invalidated trade — closing SHORT");
                }
                ClearSignalFile();
                return;
            }

            // Required fields
            signalDirection = dir;
            if (!double.TryParse(f[2].Trim(), out signalEntry))  return;
            if (!double.TryParse(f[3].Trim(), out signalStop))   return;
            if (!double.TryParse(f[4].Trim(), out signalTarget))  return;

            // Optional sizing fields (backward compatible)
            signalContracts   = f.Length > 5 && int.TryParse(f[5].Trim(), out int c)       ? Math.Max(1, c) : 1;
            signalScale1Price = f.Length > 6 && double.TryParse(f[6].Trim(), out double s1) ? s1 : 0;
            signalScale1Qty   = f.Length > 7 && int.TryParse(f[7].Trim(), out int s1q)      ? s1q : 0;
            signalTrailPoints = f.Length > 8 && double.TryParse(f[8].Trim(), out double tp) ? tp : 0;
            // EMA21 at entry — real thesis invalidation level (not the stop)
            signalEMA21AtEntry = f.Length > 9 && double.TryParse(f[9].Trim(), out double e21) && e21 > 0 ? e21 : 0;

            currentSignalId = signalId;
            scale1Hit       = false;
            trailActive     = false;

            if (signalDirection == "LONG")
                EnterLong(0, signalContracts, "CT_Long");
            else if (signalDirection == "SHORT")
                EnterShort(0, signalContracts, "CT_Short");
            else
            {
                Print($"[ERROR] Unknown direction: {signalDirection}");
                return;
            }

            hasOpenOrder = true;
            processedSignals.Add(signalId);

            Print($"[SIGNAL] {signalDirection} {signalContracts}c @ mkt | SL={signalStop:F2} TP={signalTarget:F2}");
            if (signalScale1Price > 0)
                Print($"  Scale1={signalScale1Price:F2} ({signalScale1Qty}c) | Trail={signalTrailPoints}pts");
        }

        private void ClearSignalFile()
        {
            try
            {
                using (StreamWriter sw = new StreamWriter(signalsFilePath, false))
                    sw.WriteLine("DateTime,Direction,Entry_Price,Stop_Loss,Target,Contracts,Scale1_Price,Scale1_Contracts,Trail_Points");
            }
            catch (Exception ex)
            {
                Print($"[ERROR] Clearing signal file: {ex.Message}");
            }
        }

        // ─── Execution handling ───────────────────────────────────────────

        protected override void OnExecutionUpdate(Execution exec, string execId, double price,
            int qty, MarketPosition mp, string orderId, DateTime time)
        {
            if (exec.Order == null || exec.Order.OrderState != OrderState.Filled)
                return;

            string name = exec.Order.Name;

            // Entry filled
            if (name == "CT_Long" || name == "CT_Short")
            {
                actualEntry  = exec.Price;
                inPosition   = true;
                hasOpenOrder = false;
                remainingQty = Position.Quantity;

                Print($"[FILLED] {signalDirection} {qty}c @ {actualEntry:F2} | Position: {Position.Quantity}/{signalContracts}");

                if (Position.Quantity == signalContracts)
                {
                    PlaceExitOrders();
                    // Use EMA21 at entry as invalidation level — a close beyond this
                    // means the trade thesis (EMA bounce/support) has broken.
                    // Falls back to stop only if EMA21 wasn't provided.
                    invalidationLevel = signalEMA21AtEntry > 0 ? signalEMA21AtEntry : signalStop;
                    adverseCloseCount = 0;
                    Print($"[WATCHDOG ARMED] Invalidation level: {invalidationLevel:F2} ({(signalEMA21AtEntry > 0 ? "EMA21" : "stop fallback")})");
                }
                else
                    Print($"[PARTIAL] {Position.Quantity}/{signalContracts} — waiting for full fill");
            }

            // Scale1 partial exit filled
            else if (name == "Scale1")
            {
                scale1Hit    = true;
                remainingQty = Position.Quantity;
                Print($"[SCALE1] {qty}c exited @ {price:F2} | {remainingQty}c remaining");

                // Move stop to breakeven on remaining contracts
                if (remainingQty > 0)
                {
                    if (signalDirection == "LONG")
                    {
                        ExitLongStopMarket(0, true, remainingQty, actualEntry, "SL", "CT_Long");
                        Print($"[BE STOP] Stop moved to breakeven {actualEntry:F2} for {remainingQty}c");
                    }
                    else
                    {
                        ExitShortStopMarket(0, true, remainingQty, actualEntry, "SL", "CT_Short");
                        Print($"[BE STOP] Stop moved to breakeven {actualEntry:F2} for {remainingQty}c");
                    }

                    // Enable manual trail in OnBarUpdate if configured.
                    // Cancel the existing TP limit first to prevent double-exit
                    // on remaining contracts (trail + TP both live = OCO risk).
                    if (signalTrailPoints > 0 && remainingQty > 0)
                    {
                        // Cancel open TP order on remaining contracts before arming trail
                        if (signalDirection == "LONG")
                            ExitLong(0, remainingQty, "TP_Cancel", "CT_Long");
                        else
                            ExitShort(0, remainingQty, "TP_Cancel", "CT_Short");

                        trailActive    = true;
                        trailPeak      = actualEntry;
                        trailStopPrice = signalDirection == "LONG"
                            ? actualEntry - signalTrailPoints
                            : actualEntry + signalTrailPoints;
                        Print($"[TRAIL ARMED] TP cancelled, trail stop={trailStopPrice:F2} trail={signalTrailPoints}pts");
                    }
                }
            }

            // All exit types — log them all
            else if (name == "TP" || name == "SL" || name == "Scale1" ||
                     name == "TrailStop" || name == "WatchdogExit" || name == "ThesisExit")
            {
                double pnl = signalDirection == "LONG"
                    ? (price - actualEntry) * qty
                    : (actualEntry - price) * qty;
                Print($"[EXIT {name}] {qty}c @ {price:F2} | P/L: {pnl:+F2}pts");
                LogTrade(signalDirection, actualEntry, price, pnl);
            }
        }

        private void PlaceExitOrders()
        {
            string entryOrder = signalDirection == "LONG" ? "CT_Long" : "CT_Short";
            int scaleQty = (signalScale1Price > 0 && signalScale1Qty > 0) ? signalScale1Qty : 0;
            int fullQty  = signalContracts;

            if (signalDirection == "LONG")
            {
                if (scaleQty > 0)
                {
                    // Partial exit at Scale1
                    ExitLongLimit(0, true, scaleQty, signalScale1Price, "Scale1", entryOrder);
                    // SL and TP on full position — NT will adjust when Scale1 fills
                    ExitLongStopMarket(0, true, fullQty, signalStop,   "SL", entryOrder);
                    ExitLongLimit(0, true, fullQty - scaleQty, signalTarget, "TP", entryOrder);
                    Print($"[ORDERS] SL={signalStop:F2} | Scale1={signalScale1Price:F2}({scaleQty}c) | TP={signalTarget:F2}({fullQty - scaleQty}c)");
                }
                else
                {
                    ExitLongStopMarket(0, true, fullQty, signalStop,   "SL", entryOrder);
                    ExitLongLimit(0, true, fullQty,      signalTarget,  "TP", entryOrder);
                    Print($"[ORDERS] SL={signalStop:F2} | TP={signalTarget:F2} ({fullQty}c)");
                }
            }
            else // SHORT
            {
                if (scaleQty > 0)
                {
                    ExitShortLimit(0, true, scaleQty, signalScale1Price, "Scale1", entryOrder);
                    ExitShortStopMarket(0, true, fullQty, signalStop,    "SL", entryOrder);
                    ExitShortLimit(0, true, fullQty - scaleQty, signalTarget, "TP", entryOrder);
                    Print($"[ORDERS] SL={signalStop:F2} | Scale1={signalScale1Price:F2}({scaleQty}c) | TP={signalTarget:F2}({fullQty - scaleQty}c)");
                }
                else
                {
                    ExitShortStopMarket(0, true, fullQty, signalStop,   "SL", entryOrder);
                    ExitShortLimit(0, true, fullQty,      signalTarget,  "TP", entryOrder);
                    Print($"[ORDERS] SL={signalStop:F2} | TP={signalTarget:F2} ({fullQty}c)");
                }
            }
        }

        protected override void OnOrderUpdate(Order order, double limitPrice, double stopPrice,
            int qty, int filled, double avgFill, OrderState state, DateTime time, ErrorCode err, string comment)
        {
            if (state == OrderState.Rejected || state == OrderState.Cancelled)
            {
                if (order.Name == "CT_Long" || order.Name == "CT_Short")
                {
                    hasOpenOrder = false;
                    Print($"[{state.ToString().ToUpper()}] Entry order {order.Name} — {comment}");
                }
            }
        }

        protected override void OnPositionUpdate(Position pos, double avgPrice, int qty, MarketPosition mp)
        {
            if (mp == MarketPosition.Flat)
                ResetState();
        }

        // ─── Helpers ─────────────────────────────────────────────────────

        private void ResetState()
        {
            inPosition        = false;
            hasOpenOrder      = false;
            scale1Hit         = false;
            trailActive       = false;
            trailPeak         = 0;
            trailStopPrice    = 0;
            remainingQty      = 0;
            currentSignalId   = "";
            invalidationLevel  = 0;
            adverseCloseCount  = 0;
            lastWatchdogBar    = -1;
            signalEMA21AtEntry = 0;
        }

        private void LogTrade(string dir, double entry, double exit, double pnl)
        {
            try
            {
                bool exists = File.Exists(tradesLogFilePath);
                using (StreamWriter sw = new StreamWriter(tradesLogFilePath, true))
                {
                    if (!exists)
                        sw.WriteLine("DateTime,Direction,Entry_Price,Exit_Price,PnL_Points");
                    sw.WriteLine($"{DateTime.Now:MM/dd/yyyy HH:mm:ss},{dir},{entry:F2},{exit:F2},{pnl:F2}");
                }
                Print($"Trade logged: {dir} {entry:F2}→{exit:F2} P/L={pnl:+F2}pts");
            }
            catch (Exception ex)
            {
                Print($"[ERROR] Logging trade: {ex.Message}");
            }
        }

        #region Properties

        [NinjaScriptProperty]
        [Display(Name = "Signals File Path", Order = 1, GroupName = "ClaudeTrader")]
        public string SignalsFilePath { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Trades Log Path", Order = 2, GroupName = "ClaudeTrader")]
        public string TradesLogPath { get; set; }

        [NinjaScriptProperty]
        [Range(1, 60)]
        [Display(Name = "File Check (seconds)", Order = 3, GroupName = "ClaudeTrader")]
        public int FileCheckSeconds { get; set; }

        #endregion
    }
}
