#region Using declarations
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.IO;
using NinjaTrader.Cbi;
using NinjaTrader.NinjaScript;
using NinjaTrader.Data;
#endregion

// Apply to ANY chart of NQ (same instrument as your trading chart).
// Writes a rolling 2-minute window of T&S prints to TimeAndSales.csv.
// File is rewritten every FlushSeconds to prevent unbounded growth.

namespace NinjaTrader.NinjaScript.Strategies
{
    public class TickLogger : Strategy
    {
        private string outputPath;
        private Queue<string> tickBuffer = new Queue<string>();
        private DateTime lastFlush       = DateTime.MinValue;
        private readonly object lockObj  = new object();

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description     = "Logs T&S prints to rolling CSV window for order flow analysis";
                Name            = "TickLogger";
                Calculate       = Calculate.OnEachTick;
                BarsRequiredToTrade = 1;
                IsExitOnSessionCloseStrategy = false;

                OutputPath   = @"C:\Users\jwmar\Claude-Trader-NinjaTrader\data\TimeAndSales.csv";
                WindowSeconds = 120;  // rolling 2-minute window
                FlushSeconds  = 5;    // rewrite file every 5 seconds
                LargePrintThreshold = 10;  // contracts — flag prints >= this
            }
            else if (State == State.DataLoaded)
            {
                outputPath = OutputPath;
                WriteHeader();
                Print($"TickLogger ready — writing to {outputPath} ({WindowSeconds}s window)");
            }
        }

        protected override void OnBarUpdate()
        {
            // Flush buffer to file periodically
            if ((DateTime.Now - lastFlush).TotalSeconds >= FlushSeconds)
            {
                FlushToFile();
                lastFlush = DateTime.Now;
            }
        }

        protected override void OnMarketData(MarketDataEventArgs e)
        {
            if (e.MarketDataType != MarketDataType.Last)
                return;

            string side = "U";  // Unknown
            if (e.Price >= e.Ask)       side = "A";  // Ask-side (buyer aggressive)
            else if (e.Price <= e.Bid)  side = "B";  // Bid-side (seller aggressive)

            string flag = e.Volume >= LargePrintThreshold ? "L" : "";

            string row = $"{e.Time:MM/dd/yyyy HH:mm:ss.fff},{e.Price:F2},{e.Volume},{side},{flag}";

            lock (lockObj)
            {
                tickBuffer.Enqueue(row);
            }
        }

        private void FlushToFile()
        {
            DateTime cutoff = DateTime.Now.AddSeconds(-WindowSeconds);
            var recentTicks = new List<string>();

            lock (lockObj)
            {
                // Drain the queue, keeping only ticks within the window
                while (tickBuffer.Count > 0)
                {
                    string row = tickBuffer.Dequeue();
                    try
                    {
                        // Parse time from first field to check window
                        string timeStr = row.Split(',')[0];
                        if (DateTime.TryParse(timeStr, out DateTime t) && t >= cutoff)
                            recentTicks.Add(row);
                    }
                    catch { recentTicks.Add(row); }
                }
                // Put recent ticks back
                foreach (var r in recentTicks)
                    tickBuffer.Enqueue(r);
            }

            try
            {
                using (StreamWriter sw = new StreamWriter(outputPath, false))
                {
                    sw.WriteLine("Time,Price,Size,Side,Flag");
                    foreach (var r in recentTicks)
                        sw.WriteLine(r);
                }
            }
            catch (Exception ex)
            {
                Print($"TickLogger flush error: {ex.Message}");
            }
        }

        private void WriteHeader()
        {
            try
            {
                using (StreamWriter sw = new StreamWriter(outputPath, false))
                    sw.WriteLine("Time,Price,Size,Side,Flag");
            }
            catch { }
        }

        #region Properties
        [NinjaScriptProperty]
        [Display(Name = "Output Path", Order = 1, GroupName = "TickLogger")]
        public string OutputPath { get; set; }

        [NinjaScriptProperty]
        [Range(30, 600)]
        [Display(Name = "Window (seconds)", Order = 2, GroupName = "TickLogger")]
        public int WindowSeconds { get; set; }

        [NinjaScriptProperty]
        [Range(1, 30)]
        [Display(Name = "Flush Interval (seconds)", Order = 3, GroupName = "TickLogger")]
        public int FlushSeconds { get; set; }

        [NinjaScriptProperty]
        [Range(1, 100)]
        [Display(Name = "Large Print Threshold (contracts)", Order = 4, GroupName = "TickLogger")]
        public int LargePrintThreshold { get; set; }
        #endregion
    }
}
