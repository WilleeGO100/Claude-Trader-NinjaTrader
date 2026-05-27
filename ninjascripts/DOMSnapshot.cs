#region Using declarations
using System;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.IO;
using System.Collections.Generic;
using NinjaTrader.Cbi;
using NinjaTrader.NinjaScript;
using NinjaTrader.Data;
#endregion

// Apply to the same NQ chart as TickLogger.
// Writes a snapshot of the top 5 bid/ask levels every SnapshotSeconds.

namespace NinjaTrader.NinjaScript.Strategies
{
    public class DOMSnapshot : Strategy
    {
        private string outputPath;
        private DateTime lastSnapshot = DateTime.MinValue;

        // Track current DOM depth
        private SortedDictionary<double, long> bids = new SortedDictionary<double, long>(Comparer<double>.Create((a, b) => b.CompareTo(a))); // descending
        private SortedDictionary<double, long> asks = new SortedDictionary<double, long>();  // ascending

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description     = "Writes DOM depth snapshot every N seconds for order flow analysis";
                Name            = "DOMSnapshot";
                Calculate       = Calculate.OnEachTick;
                BarsRequiredToTrade = 1;
                IsExitOnSessionCloseStrategy = false;

                OutputPath       = @"C:\Users\jwmar\Claude-Trader-NinjaTrader\data\DOMSnapshot.csv";
                SnapshotSeconds  = 5;
                Levels           = 5;
                WallThreshold    = 15;  // contracts at single level = wall
            }
            else if (State == State.DataLoaded)
            {
                outputPath = OutputPath;
                WriteHeader();
                Print($"DOMSnapshot ready — writing to {outputPath} every {SnapshotSeconds}s");
            }
        }

        protected override void OnBarUpdate()
        {
            if ((DateTime.Now - lastSnapshot).TotalSeconds >= SnapshotSeconds)
            {
                WriteSnapshot();
                lastSnapshot = DateTime.Now;
            }
        }

        protected override void OnMarketDepth(MarketDepthEventArgs e)
        {
            if (e.MarketDataType == MarketDataType.Bid)
            {
                if (e.Volume == 0)
                    bids.Remove(e.Price);
                else
                    bids[e.Price] = e.Volume;
            }
            else if (e.MarketDataType == MarketDataType.Ask)
            {
                if (e.Volume == 0)
                    asks.Remove(e.Price);
                else
                    asks[e.Price] = e.Volume;
            }
        }

        private void WriteSnapshot()
        {
            try
            {
                var bidList = new List<KeyValuePair<double, long>>(bids);
                var askList = new List<KeyValuePair<double, long>>(asks);

                using (StreamWriter sw = new StreamWriter(outputPath, false))
                {
                    sw.WriteLine("Time,Side,Level,Price,Size,IsWall");
                    string ts = DateTime.Now.ToString("MM/dd/yyyy HH:mm:ss");

                    int count = Math.Min(Levels, bidList.Count);
                    for (int i = 0; i < count; i++)
                    {
                        bool isWall = bidList[i].Value >= WallThreshold;
                        sw.WriteLine($"{ts},BID,{i+1},{bidList[i].Key:F2},{bidList[i].Value},{(isWall ? "Y" : "N")}");
                    }

                    count = Math.Min(Levels, askList.Count);
                    for (int i = 0; i < count; i++)
                    {
                        bool isWall = askList[i].Value >= WallThreshold;
                        sw.WriteLine($"{ts},ASK,{i+1},{askList[i].Key:F2},{askList[i].Value},{(isWall ? "Y" : "N")}");
                    }
                }
            }
            catch (Exception ex)
            {
                Print($"DOMSnapshot write error: {ex.Message}");
            }
        }

        private void WriteHeader()
        {
            try
            {
                using (StreamWriter sw = new StreamWriter(outputPath, false))
                    sw.WriteLine("Time,Side,Level,Price,Size,IsWall");
            }
            catch { }
        }

        #region Properties
        [NinjaScriptProperty]
        [Display(Name = "Output Path", Order = 1, GroupName = "DOMSnapshot")]
        public string OutputPath { get; set; }

        [NinjaScriptProperty]
        [Range(1, 60)]
        [Display(Name = "Snapshot Interval (seconds)", Order = 2, GroupName = "DOMSnapshot")]
        public int SnapshotSeconds { get; set; }

        [NinjaScriptProperty]
        [Range(1, 10)]
        [Display(Name = "Depth Levels", Order = 3, GroupName = "DOMSnapshot")]
        public int Levels { get; set; }

        [NinjaScriptProperty]
        [Range(1, 100)]
        [Display(Name = "Wall Threshold (contracts)", Order = 4, GroupName = "DOMSnapshot")]
        public int WallThreshold { get; set; }
        #endregion
    }
}
